"""Tests for the Supervisor multi-orchestrator manager."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autosymph.supervisor import Supervisor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_configs(factory, n: int = 2) -> list[tuple[Path, object]]:
    """Build N (path, WorkflowConfig) tuples with distinct project names."""
    results = []
    for i in range(n):
        cfg = factory(project=f"project-{chr(ord('a') + i)}", max_agents=2)
        results.append((Path(f"/tmp/config-{i}.yaml"), cfg))
    return results


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSupervisorInit:
    """Constructor-level tests — no run() needed."""

    def test_creates_orchestrators_on_run(self, mock_config_factory):
        """After brief run(), orchestrators list should match config count."""
        configs = _make_configs(mock_config_factory, n=2)
        sup = Supervisor(configs)

        # Before run: no orchestrators yet
        assert len(sup.orchestrators) == 0

        # Simulate what run() does: create orchestrators (skip signals/tasks)
        with patch("autosymph.supervisor.LinearClient"), \
             patch("autosymph.supervisor.StateMachine"), \
             patch("autosymph.supervisor.WorkspaceManager"):
            sup._orchestrators = [
                sup._create_orchestrator(i) for i in range(len(configs))
            ]

        assert len(sup.orchestrators) == 2

    def test_shared_resource_pool(self, mock_config_factory):
        """All orchestrators must share the same ResourcePool instance."""
        configs = _make_configs(mock_config_factory, n=3)
        sup = Supervisor(configs)

        with patch("autosymph.supervisor.LinearClient"), \
             patch("autosymph.supervisor.StateMachine"), \
             patch("autosymph.supervisor.WorkspaceManager"):
            sup._orchestrators = [
                sup._create_orchestrator(i) for i in range(len(configs))
            ]

        pool = sup.resource_pool
        for orch in sup.orchestrators:
            assert orch.resource_pool is pool

    def test_shared_concurrency_manager(self, mock_config_factory):
        """All orchestrators must share the same ConcurrencyManager instance."""
        configs = _make_configs(mock_config_factory, n=2)
        sup = Supervisor(configs)

        with patch("autosymph.supervisor.LinearClient"), \
             patch("autosymph.supervisor.StateMachine"), \
             patch("autosymph.supervisor.WorkspaceManager"):
            sup._orchestrators = [
                sup._create_orchestrator(i) for i in range(len(configs))
            ]

        cm = sup.concurrency_mgr
        for orch in sup.orchestrators:
            assert orch._concurrency_mgr is cm

    def test_concurrency_global_max_default(self, mock_config_factory):
        """Without max_agents override, global_max = sum of per-project limits."""
        configs = _make_configs(mock_config_factory, n=2)  # each has max_agents=2
        sup = Supervisor(configs)
        assert sup.concurrency_mgr.global_max == 4

    def test_concurrency_global_max_override(self, mock_config_factory):
        """With explicit max_agents, that value is used as global_max."""
        configs = _make_configs(mock_config_factory, n=2)  # each has max_agents=2
        sup = Supervisor(configs, max_agents=5)
        assert sup.concurrency_mgr.global_max == 5


class TestSupervisorStatus:
    """status() shape tests."""

    def test_status_returns_per_project_health(self, mock_config_factory):
        """status() must have an entry for every project."""
        configs = _make_configs(mock_config_factory, n=2)
        sup = Supervisor(configs)

        s = sup.status()
        assert "projects" in s
        assert "project-a" in s["projects"]
        assert "project-b" in s["projects"]

        for slug, info in s["projects"].items():
            assert info["state"] == "pending"
            assert info["restart_count"] == 0
            assert info["last_error"] is None

    def test_status_includes_warnings(self, mock_config_factory):
        """Discovery warnings must be surfaced in status()."""
        configs = _make_configs(mock_config_factory, n=1)
        warnings = ["config X skipped: missing tracker", "config Y has no states"]
        sup = Supervisor(configs, warnings=warnings)

        s = sup.status()
        assert s["warnings"] == warnings

    def test_status_no_warnings_by_default(self, mock_config_factory):
        """Without warnings, status().warnings is an empty list."""
        configs = _make_configs(mock_config_factory, n=1)
        sup = Supervisor(configs)

        s = sup.status()
        assert s["warnings"] == []


class TestSupervisorShutdown:
    """Shutdown propagation tests."""

    def test_shutdown_sets_event(self, mock_config_factory):
        """shutdown() must set the internal event."""
        configs = _make_configs(mock_config_factory, n=1)
        sup = Supervisor(configs)

        assert not sup._shutdown_event.is_set()
        sup.shutdown()
        assert sup._shutdown_event.is_set()


class TestWatchOrchestrator:
    """Crash recovery and backoff logic."""

    @pytest.mark.asyncio
    async def test_watch_marks_failed_after_max_restarts(self, mock_config_factory):
        """After _MAX_RESTARTS crashes, orchestrator should be marked failed."""
        configs = _make_configs(mock_config_factory, n=1)
        sup = Supervisor(configs)

        # Create a mock orchestrator whose run() always raises
        mock_orch = MagicMock()
        mock_orch.run = AsyncMock(side_effect=RuntimeError("boom"))
        sup._orchestrators = [mock_orch]

        # Patch _create_orchestrator to return fresh mocks on restart
        with patch.object(sup, "_create_orchestrator", return_value=mock_orch):
            # Patch sleep to not actually wait
            with patch("autosymph.supervisor.asyncio.sleep", new_callable=AsyncMock):
                await sup._watch_orchestrator(0)

        slug = configs[0][1].project_slug
        assert sup._orch_status[slug]["state"] == "failed"
        assert sup._orch_status[slug]["restart_count"] == 3
        assert "boom" in sup._orch_status[slug]["last_error"]

    @pytest.mark.asyncio
    async def test_watch_clean_exit_does_not_restart(self, mock_config_factory):
        """If orchestrator.run() returns cleanly, no restart happens."""
        configs = _make_configs(mock_config_factory, n=1)
        sup = Supervisor(configs)

        mock_orch = MagicMock()
        mock_orch.run = AsyncMock(return_value=None)  # clean exit
        sup._orchestrators = [mock_orch]

        await sup._watch_orchestrator(0)

        slug = configs[0][1].project_slug
        assert sup._orch_status[slug]["state"] == "running"
        assert sup._orch_status[slug]["restart_count"] == 0
