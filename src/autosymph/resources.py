"""Resource pool — prevents collisions when multiple agents run concurrently.

Manages pools of singleton resources (iOS Simulators, ports, etc.) with
async acquire/release semantics. Resources are passed to agents as env vars.

Simulators are auto-detected: if the workspace has .xcodeproj or
capacitor.config.ts, the verify agent gets a simulator without needing
explicit labels. The orchestrator boots the sim before dispatch and
shuts it down on release.

Usage:
    pool = ResourcePool(config.resources)
    resources = await pool.acquire_for_issue(issue, workflow_state, workspace_path=path)
    # ... run agent with resources as env vars ...
    pool.release_all(issue.id)  # in finally block
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from autosymph.config import ResourcesConfig

logger = logging.getLogger(__name__)


def _resolve_udid(sim_name: str) -> str | None:
    """Resolve a simulator name to its UDID via xcrun simctl."""
    try:
        result = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "available", "-j"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        import json
        data = json.loads(result.stdout)
        for runtime, devices in data.get("devices", {}).items():
            for dev in devices:
                if dev.get("name") == sim_name and dev.get("isAvailable", False):
                    return dev["udid"]
    except Exception:
        logger.warning("Failed to resolve UDID for %s", sim_name, exc_info=True)
    return None


def _boot_simulator(udid: str, name: str) -> bool:
    """Boot a simulator by UDID and open Simulator.app.

    Simulator.app must be open for screenshots to render at correct resolution.
    Without the GUI, the render server produces warped/degenerate framebuffers.
    """
    try:
        result = subprocess.run(
            ["xcrun", "simctl", "boot", udid],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info("Booted simulator %s (%s)", name, udid)
        elif "already booted" in result.stderr.lower():
            logger.info("Simulator %s (%s) already booted", name, udid)
        else:
            logger.error("Failed to boot %s (%s): %s", name, udid, result.stderr.strip())
            return False

        # Open Simulator.app — required for correct screenshot rendering.
        # Without the GUI window, simctl screenshots produce warped images.
        subprocess.run(["open", "-a", "Simulator"], capture_output=True, timeout=10)
        import time
        time.sleep(2)  # Wait for window to establish rendering context
        logger.info("Opened Simulator.app for %s", name)

        # NOTE: Do NOT set OptimizeRenderingForWindowScale=NO — it corrupts
        # the framebuffer on Xcode 16+, producing green/cyan garbage screenshots.
        # The default (YES) works correctly when Simulator.app is open.

        return True
    except Exception:
        logger.error("Exception booting simulator %s", name, exc_info=True)
        return False


def _shutdown_simulator(udid: str, name: str) -> None:
    """Shutdown a simulator by UDID."""
    try:
        subprocess.run(
            ["xcrun", "simctl", "shutdown", udid],
            capture_output=True, text=True, timeout=15,
        )
        logger.info("Shutdown simulator %s (%s)", name, udid)
    except Exception:
        logger.warning("Failed to shutdown simulator %s", name, exc_info=True)


def _workspace_needs_simulator(workspace_path: Path | None) -> bool:
    """Check if a workspace contains iOS project files."""
    if not workspace_path or not workspace_path.exists():
        return False
    # Check for Xcode project
    if list(workspace_path.rglob("*.xcodeproj")):
        return True
    # Check for Capacitor (ionic/web → native)
    if (workspace_path / "capacitor.config.ts").exists():
        return True
    if (workspace_path / "capacitor.config.json").exists():
        return True
    # Check for ios/ directory with Xcode project
    ios_dir = workspace_path / "ios"
    if ios_dir.is_dir() and list(ios_dir.rglob("*.xcodeproj")):
        return True
    return False


@dataclass
class _SimSlot:
    """A simulator slot in the pool."""
    name: str
    udid: str | None = None  # resolved at acquire time if "auto"
    booted_by_us: bool = False  # True if we booted it (so we shut it down on release)


@dataclass
class AcquiredResources:
    """Resources acquired for a single agent run."""

    holder_id: str  # issue_id
    items: dict[str, str] = field(default_factory=dict)  # env_var -> value

    def as_env(self) -> dict[str, str]:
        """Return as environment variables for the agent."""
        return dict(self.items)


class ResourcePool:
    """Async resource pool with acquire/release.

    Resources are configured in workflow.yaml and managed as async queues.
    Agents acquire before dispatch, release on completion/failure.
    """

    def __init__(self, config: ResourcesConfig) -> None:
        self._pools: dict[str, asyncio.Queue[str]] = {}
        self._pool_totals: dict[str, int] = {}  # total size per pool (immutable after init)
        self._held: dict[str, list[tuple[str, str]]] = {}  # holder_id -> [(pool_name, item)]
        self._sim_slots: dict[str, _SimSlot] = {}  # sim_name -> slot
        self._booted_sims: dict[str, _SimSlot] = {}  # holder_id -> slot (for shutdown on release)
        self._config = config

        # Initialize iOS Simulator pool
        if config.ios_simulator:
            q: asyncio.Queue[str] = asyncio.Queue()
            for sim in config.ios_simulator:
                q.put_nowait(sim.name)
                self._sim_slots[sim.name] = _SimSlot(
                    name=sim.name,
                    udid=None if sim.udid == "auto" else sim.udid,
                )
            self._pools["ios_simulator"] = q
            self._pool_totals["ios_simulator"] = len(config.ios_simulator)
            logger.info("iOS Simulator pool: %d sims", len(config.ios_simulator))

        # Initialize port pool
        if config.dev_port_range:
            q = asyncio.Queue()
            for port in config.dev_port_range:
                q.put_nowait(str(port))
            self._pools["dev_port"] = q
            self._pool_totals["dev_port"] = len(config.dev_port_range)
            logger.info("Port pool: %s", config.dev_port_range)

    async def acquire(self, pool_name: str, holder_id: str, timeout_s: float = 300) -> str | None:
        """Acquire a resource from a pool. Returns None if pool doesn't exist."""
        pool = self._pools.get(pool_name)
        if not pool:
            return None

        try:
            item = await asyncio.wait_for(pool.get(), timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.error(
                "Timeout acquiring %s for %s after %.0fs",
                pool_name, holder_id, timeout_s,
            )
            return None

        # Track what this holder has
        if holder_id not in self._held:
            self._held[holder_id] = []
        self._held[holder_id].append((pool_name, item))

        logger.info("Acquired %s=%s for %s", pool_name, item, holder_id)
        return item

    def release(self, pool_name: str, item: str, holder_id: str) -> None:
        """Release a single resource back to its pool."""
        pool = self._pools.get(pool_name)
        if pool:
            pool.put_nowait(item)
            logger.info("Released %s=%s from %s", pool_name, item, holder_id)

        # Remove from held tracking
        if holder_id in self._held:
            self._held[holder_id] = [
                (pn, it) for pn, it in self._held[holder_id]
                if not (pn == pool_name and it == item)
            ]
            if not self._held[holder_id]:
                del self._held[holder_id]

    def release_all(self, holder_id: str, *, project_slug: str | None = None) -> None:
        """Release all resources held by an issue. Call in finally blocks.

        Args:
            holder_id: The issue ID (or pre-namespaced key).
            project_slug: If provided, the actual key becomes ``{project_slug}:{holder_id}``.
        """
        key = f"{project_slug}:{holder_id}" if project_slug else holder_id
        held = self._held.pop(key, [])
        for pool_name, item in held:
            pool = self._pools.get(pool_name)
            if pool:
                pool.put_nowait(item)
                logger.info("Released %s=%s from %s (cleanup)", pool_name, item, key)

        # Shutdown any simulator we booted for this issue
        slot = self._booted_sims.pop(key, None)
        if slot and slot.booted_by_us and slot.udid:
            _shutdown_simulator(slot.udid, slot.name)

    async def acquire_for_issue(
        self,
        issue_id: str,
        workflow_state: str,
        labels: list[str] | None = None,
        workspace_path: Path | None = None,
        *,
        project_slug: str | None = None,
    ) -> AcquiredResources:
        """Acquire all resources needed for an issue based on state and context.

        Args:
            issue_id: The Linear issue ID (e.g. ``imp-100``).
            workflow_state: Current workflow state name.
            labels: Issue labels for resource hints.
            workspace_path: Path to the agent workspace.
            project_slug: If provided, the holder key becomes
                ``{project_slug}:{issue_id}`` to avoid collisions in
                multi-project setups.

        Simulator auto-detection:
        - verify state + workspace has .xcodeproj or capacitor.config → auto-acquire sim
        - Or explicit label: resource:ios-sim, ios, simulator
        - Simulator is booted before returning

        Returns AcquiredResources with env vars for the agent.
        """
        holder_id = f"{project_slug}:{issue_id}" if project_slug else issue_id
        resources = AcquiredResources(holder_id=holder_id)
        labels = labels or []
        label_set = {l.lower() for l in labels}

        has_sim_pool = "ios_simulator" in self._pools

        logger.info(
            "[resource-pool] acquire_for_issue: issue=%s state=%s labels=%s workspace=%s has_sim_pool=%s",
            issue_id, workflow_state, labels, workspace_path, has_sim_pool,
        )

        # Auto-detect: workspace has iOS files?
        ws_needs = _workspace_needs_simulator(workspace_path) if workspace_path else False
        auto_needs_sim = (
            workflow_state == "verify"
            and has_sim_pool
            and ws_needs
        )
        logger.info(
            "[resource-pool] ws_needs_sim=%s auto_needs=%s label_needs=%s",
            ws_needs, auto_needs_sim,
            workflow_state == "verify" and has_sim_pool and any(k in label_set for k in ("resource:ios-sim", "ios", "simulator")),
        )

        # Explicit label override
        label_needs_sim = (
            workflow_state == "verify"
            and has_sim_pool
            and any(k in label_set for k in ("resource:ios-sim", "ios", "simulator"))
        )

        needs_sim = auto_needs_sim or label_needs_sim
        needs_port = "resource:dev-port" in label_set and "dev_port" in self._pools

        if needs_sim:
            sim_name = await self.acquire("ios_simulator", holder_id)
            if sim_name:
                resources.items["AUTOSYMPH_SIM_NAME"] = sim_name

                # Resolve UDID
                slot = self._sim_slots.get(sim_name)
                if slot:
                    if not slot.udid:
                        slot.udid = _resolve_udid(sim_name)
                    if slot.udid:
                        resources.items["AUTOSYMPH_SIM_UDID"] = slot.udid

                        # Boot the simulator
                        if _boot_simulator(slot.udid, sim_name):
                            slot.booted_by_us = True
                            self._booted_sims[holder_id] = slot
                        else:
                            logger.error("Could not boot %s — verify may fail", sim_name)
                    else:
                        logger.error("Could not resolve UDID for %s — verify may fail", sim_name)
            else:
                logger.warning("Failed to acquire iOS simulator for %s — verify may fail", holder_id)

        if needs_port:
            port = await self.acquire("dev_port", holder_id)
            if port:
                resources.items["AUTOSYMPH_DEV_PORT"] = port
            else:
                logger.warning("Failed to acquire dev port for %s", holder_id)

        # Railway preview URL (no resource needed — just construct from config)
        if self._config.railway and workflow_state == "verify":
            resources.items["AUTOSYMPH_RAILWAY_URL_PATTERN"] = self._config.railway.preview_url_pattern

        # Per-worktree DerivedData
        if needs_sim:
            resources.items["_NEEDS_DERIVED_DATA"] = "true"

        return resources

    def pool_status(self) -> dict[str, dict[str, int]]:
        """Return current pool sizes for status display."""
        return {
            name: {
                "available": pool.qsize(),
                "total": self._pool_totals.get(name, 0),
            }
            for name, pool in self._pools.items()
        }

    @staticmethod
    def merge_resources(configs: list[ResourcesConfig]) -> ResourcesConfig:
        """Merge multiple ResourcesConfig into one, deduping simulators by name and unioning ports."""
        from autosymph.config import ResourcesConfig, SimulatorConfig, RailwayConfig

        seen_sims: dict[str, SimulatorConfig] = {}  # name -> config
        all_ports: set[int] = set()
        railway: RailwayConfig | None = None

        for cfg in configs:
            for sim in cfg.ios_simulator:
                if sim.name not in seen_sims:
                    seen_sims[sim.name] = sim
            all_ports.update(cfg.dev_port_range)
            if cfg.railway and not railway:
                railway = cfg.railway

        return ResourcesConfig(
            ios_simulator=list(seen_sims.values()),
            dev_port_range=sorted(all_ports),
            railway=railway,
        )
