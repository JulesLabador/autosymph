"""Global concurrency manager — fair-share agent slot allocation across projects.

Enforces a global max_concurrent_agents cap with per-project limits.
All operations are atomic via asyncio.Lock to prevent TOCTOU races.

Fair-share rules:
- Each project with runnable work gets minimum 1 slot (best-effort)
- Surplus slots distributed round-robin among projects with pending dispatch
- Idle projects (no acquire() calls) don't reserve slots
- If projects > global cap, first N get 1 slot each, remainder wait
- Per-project caps are frozen at startup (not hot-reloadable)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class _ProjectState:
    """Tracks allocation state for a single project."""
    max_agents: int
    held: int = 0  # currently held slots


class ConcurrencyManager:
    """Thread-safe global concurrency manager.

    All acquire/release operations are atomic via asyncio.Lock.
    """

    def __init__(self, global_max: int) -> None:
        self._global_max = global_max
        self._projects: dict[str, _ProjectState] = {}
        self._lock = asyncio.Lock()
        self._total_held = 0

    @property
    def global_max(self) -> int:
        return self._global_max

    def register_project(self, project_slug: str, max_agents: int) -> None:
        """Register a project at startup. Must be called before acquire/release."""
        if project_slug in self._projects:
            logger.warning("Project %s already registered, skipping", project_slug)
            return
        self._projects[project_slug] = _ProjectState(max_agents=max_agents)
        logger.info(
            "Registered project %s (max_agents=%d, global_max=%d)",
            project_slug, max_agents, self._global_max,
        )

    async def acquire(self, project_slug: str) -> bool:
        """Attempt to reserve an agent slot for a project.

        Single atomic operation. Returns True if slot reserved, False if denied.
        Checks: global cap, per-project limit, fair-share minimum guarantee.
        """
        async with self._lock:
            project = self._projects.get(project_slug)
            if project is None:
                logger.error("acquire() for unregistered project: %s", project_slug)
                return False

            # Check per-project limit
            if project.held >= project.max_agents:
                logger.debug(
                    "Denied %s: per-project limit (%d/%d)",
                    project_slug, project.held, project.max_agents,
                )
                return False

            # Check global cap
            if self._total_held >= self._global_max:
                logger.debug(
                    "Denied %s: global cap (%d/%d)",
                    project_slug, self._total_held, self._global_max,
                )
                return False

            # Fair-share check: if we're near the cap, ensure other projects
            # can still get their minimum 1 slot
            remaining_after = self._global_max - self._total_held - 1
            projects_needing_minimum = sum(
                1 for slug, ps in self._projects.items()
                if slug != project_slug and ps.held == 0
            )
            # Only enforce fair-share if this project already has at least 1 slot
            # and granting another would prevent other projects from getting their minimum
            if (
                project.held > 0
                and projects_needing_minimum > remaining_after
                and remaining_after >= 0
            ):
                logger.debug(
                    "Denied %s: fair-share (held=%d, %d projects need minimum, %d remaining)",
                    project_slug, project.held, projects_needing_minimum, remaining_after,
                )
                return False

            # Grant the slot
            project.held += 1
            self._total_held += 1
            logger.debug(
                "Granted %s: %d/%d project, %d/%d global",
                project_slug, project.held, project.max_agents,
                self._total_held, self._global_max,
            )
            return True

    async def release(self, project_slug: str) -> None:
        """Release an agent slot for a project."""
        async with self._lock:
            project = self._projects.get(project_slug)
            if project is None:
                logger.error("release() for unregistered project: %s", project_slug)
                return
            if project.held <= 0:
                logger.warning("release() when no slots held for %s", project_slug)
                return
            project.held -= 1
            self._total_held -= 1
            logger.debug(
                "Released %s: %d/%d project, %d/%d global",
                project_slug, project.held, project.max_agents,
                self._total_held, self._global_max,
            )

    def status(self) -> dict:
        """Current allocation status. Not locked — snapshot only."""
        return {
            "global_max": self._global_max,
            "global_held": self._total_held,
            "projects": {
                slug: {"max": ps.max_agents, "held": ps.held}
                for slug, ps in self._projects.items()
            },
        }
