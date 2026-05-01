"""Supervisor — manages multiple Orchestrator instances with shared resources.

Owns the process lifecycle: signal handling, orchestrator creation, crash
recovery with backoff, and unified status reporting for the TUI.

Each project gets its own Orchestrator, LinearClient, StateMachine, and
WorkspaceManager, but they all share a single ResourcePool and
ConcurrencyManager to prevent resource collisions and enforce global
concurrency limits.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path
from typing import Any

from autosymph.concurrency import ConcurrencyManager
from autosymph.config import WorkflowConfig
from autosymph.linear_client import LinearClient
from autosymph.orchestrator import Orchestrator
from autosymph.resources import ResourcePool
from autosymph.state_machine import StateMachine
from autosymph.workspace import WorkspaceManager

logger = logging.getLogger(__name__)

_BACKOFF_SCHEDULE = [1, 3, 10]  # seconds between restart attempts
_MAX_RESTARTS = 3


class Supervisor:
    """Manages N orchestrators with shared resources and crash recovery.

    Lifecycle:
    1. Constructor: merge resources, build ConcurrencyManager, store configs
    2. run(): install signal handlers, create orchestrators, launch watchers
    3. _watch_orchestrator(): restart-with-backoff loop per orchestrator
    4. shutdown(): set event → all orchestrators drain and exit
    """

    def __init__(
        self,
        configs: list[tuple[Path, WorkflowConfig]],
        warnings: list[str] | None = None,
        max_agents: int | None = None,
    ) -> None:
        self._configs = configs
        self._warnings = warnings or []

        # -- Shared resource pool (merged from all projects) --
        merged_resources = ResourcePool.merge_resources(
            [cfg.resources for _, cfg in configs]
        )
        self._resource_pool = ResourcePool(merged_resources)

        # -- Shared concurrency manager --
        if max_agents is not None:
            global_max = max_agents
        else:
            global_max = sum(cfg.agent.max_concurrent_agents for _, cfg in configs)

        self._concurrency_mgr = ConcurrencyManager(global_max)
        for _, cfg in configs:
            self._concurrency_mgr.register_project(
                cfg.project_slug, cfg.agent.max_concurrent_agents
            )

        # -- Shutdown event (shared with all orchestrators) --
        self._shutdown_event = asyncio.Event()

        # -- Per-orchestrator tracking (populated in run()) --
        self._orchestrators: list[Orchestrator] = []
        self._orch_status: dict[str, dict[str, Any]] = {}
        for _, cfg in configs:
            self._orch_status[cfg.project_slug] = {
                "state": "pending",
                "restart_count": 0,
                "last_error": None,
            }

    # -- Properties --

    @property
    def orchestrators(self) -> list[Orchestrator]:
        """Currently live orchestrator instances (for TUI status_summary calls)."""
        return list(self._orchestrators)

    @property
    def resource_pool(self) -> ResourcePool:
        return self._resource_pool

    @property
    def concurrency_mgr(self) -> ConcurrencyManager:
        return self._concurrency_mgr

    # -- Orchestrator factory --

    def _create_orchestrator(self, index: int) -> Orchestrator:
        """Create an Orchestrator for the config at the given index.

        Each orchestrator gets its own LinearClient, StateMachine, and
        WorkspaceManager, but shares the ResourcePool, ConcurrencyManager,
        and shutdown event.
        """
        config_path, cfg = self._configs[index]

        linear = LinearClient(
            api_key=cfg.tracker.api_key,
            project_name=cfg.tracker.project,
            assignee_filter=cfg.tracker.assignee_filter,
        )
        state_machine = StateMachine(cfg)
        workspace_mgr = WorkspaceManager(
            cfg.workspace, cfg.hooks, project_slug=cfg.project_slug
        )

        return Orchestrator(
            config=cfg,
            config_path=config_path,
            linear=linear,
            state_machine=state_machine,
            workspace_mgr=workspace_mgr,
            shutdown_event=self._shutdown_event,
            resource_pool=self._resource_pool,
            concurrency_mgr=self._concurrency_mgr,
        )

    # -- Main run loop --

    async def run(self) -> None:
        """Start all orchestrators and wait for shutdown.

        Signal handlers are installed here (not in __init__) because they
        require a running event loop.
        """
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._handle_signal)

        # Create orchestrators
        self._orchestrators = [
            self._create_orchestrator(i) for i in range(len(self._configs))
        ]

        # Validate Linear workspace state per-orchestrator if autoplan configured.
        # Runs once at startup; raises ConfigError to abort the supervisor on any project.
        from autosymph.diagnostics import check_linear_states, ConfigError as DiagError
        for orch in self._orchestrators:
            if orch.config.linear_states.autoplan and orch.linear.is_configured:
                try:
                    await check_linear_states(orch.config, orch.linear)
                except DiagError as e:
                    logger.error(
                        "Configuration error for project %s: %s",
                        orch.config.project_slug, e,
                    )
                    raise

        # Start status API server (graceful degradation on failure)
        from autosymph.server import StatusServer

        server_port = self._configs[0][1].server.port if self._configs else 4200
        self._status_server = StatusServer(self, port=server_port)
        await self._status_server.start()

        # Launch watcher tasks
        tasks = [
            asyncio.create_task(
                self._watch_orchestrator(i),
                name=f"watch-{self._configs[i][1].project_slug}",
            )
            for i in range(len(self._configs))
        ]

        logger.info(
            "Supervisor started — managing %d project(s): %s",
            len(self._configs),
            ", ".join(cfg.project_slug for _, cfg in self._configs),
        )

        # Block until shutdown
        await self._shutdown_event.wait()

        # Stop status API server
        await self._status_server.stop()

        # Cancel all watcher tasks
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        logger.info("Supervisor shut down cleanly")

    # -- Watcher (restart-with-backoff) --

    async def _watch_orchestrator(self, index: int) -> None:
        """Run an orchestrator with crash recovery and backoff.

        On failure: log, increment restart count, wait (1s, 3s, 10s).
        After _MAX_RESTARTS consecutive failures, mark as failed and stop.
        """
        slug = self._configs[index][1].project_slug
        self._orch_status[slug]["state"] = "running"

        restart_count = 0
        while not self._shutdown_event.is_set() and restart_count < _MAX_RESTARTS:
            try:
                orch = self._orchestrators[index]
                await orch.run()
                # Clean exit (shutdown event was set) — don't restart
                break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                restart_count += 1
                self._orch_status[slug]["restart_count"] = restart_count
                self._orch_status[slug]["last_error"] = str(exc)

                if restart_count >= _MAX_RESTARTS:
                    self._orch_status[slug]["state"] = "failed"
                    logger.error(
                        "Orchestrator %s failed after %d restarts — giving up: %s",
                        slug, restart_count, exc,
                    )
                    break

                self._orch_status[slug]["state"] = "restarting"
                backoff = _BACKOFF_SCHEDULE[min(restart_count - 1, len(_BACKOFF_SCHEDULE) - 1)]
                logger.warning(
                    "Orchestrator %s crashed (attempt %d/%d), restarting in %ds: %s",
                    slug, restart_count, _MAX_RESTARTS, backoff, exc,
                )
                await asyncio.sleep(backoff)

                # Recreate the orchestrator for the retry
                self._orchestrators[index] = self._create_orchestrator(index)

    # -- Signal handling --

    def _handle_signal(self) -> None:
        """Handle SIGTERM/SIGINT by setting the shutdown event."""
        logger.info("Signal received — initiating shutdown")
        self._shutdown_event.set()

    # -- Public API --

    def shutdown(self) -> None:
        """Trigger graceful shutdown (propagates to all orchestrators)."""
        self._shutdown_event.set()

    def status(self) -> dict[str, Any]:
        """Return per-project health and discovery warnings.

        Returns:
            {
                "projects": {
                    "my-project": {
                        "state": "running" | "failed" | "restarting",
                        "restart_count": 0,
                        "last_error": None,
                    },
                    ...
                },
                "warnings": ["..."],
            }
        """
        return {
            "projects": dict(self._orch_status),
            "warnings": list(self._warnings),
        }
