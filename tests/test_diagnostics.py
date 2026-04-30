"""Tests for autosymph.diagnostics.check_linear_states (IMP-356).

Covers the three failure modes from AC10:
  (a) configured Autoplan state missing from workspace
  (b) needs-review label missing
  (c) autoplan enabled but max_concurrent_agents_by_state.autoplan unset

Plus happy path. The Linear client is mocked — no HTTP.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from autosymph.config import (
    AgentConfig,
    LinearStatesConfig,
    StateConfig,
    TrackerConfig,
    WorkflowConfig,
    WorkspaceConfig,
)
from autosymph.diagnostics import ConfigError, check_linear_states


def _make_config(
    autoplan: str | None = "Autoplan",
    autoplan_concurrency: int | None = 1,
) -> WorkflowConfig:
    """Minimal config with optional autoplan + concurrency entry."""
    by_state: dict[str, int] = {}
    if autoplan_concurrency is not None:
        by_state["autoplan"] = autoplan_concurrency
    return WorkflowConfig(
        tracker=TrackerConfig(project="test", api_key="k"),
        workspace=WorkspaceConfig(root="/tmp/t", repo="/tmp/r"),
        agent=AgentConfig(
            max_concurrent_agents=3,
            max_concurrent_agents_by_state=by_state,
        ),
        linear_states=LinearStatesConfig(autoplan=autoplan),
        states={
            "autoplan": StateConfig(type="agent", prompt="p.md", linear_state="autoplan"),
            "done": StateConfig(type="terminal"),
        },
    )


def _make_client(states: list[str], labels: list[str]):
    """Mock LinearClient exposing the new R12 helpers."""
    client = MagicMock()
    client.resolve_team_for_project = AsyncMock(return_value="team-id-abc")
    client.fetch_workspace_states = AsyncMock(return_value=states)
    client.fetch_workspace_labels = AsyncMock(return_value=labels)
    return client


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_passes_when_state_label_and_concurrency_present(self):
        client = _make_client(
            states=["Triage", "Todo", "Autoplan", "Ready", "Done"],
            labels=["needs-review", "risk:low", "risk:high"],
        )
        # Should not raise.
        await check_linear_states(_make_config(), client)

    @pytest.mark.asyncio
    async def test_skip_when_autoplan_disabled(self):
        """When linear_states.autoplan is None, validator does not check workspace."""
        client = _make_client(states=[], labels=[])  # nothing configured
        # autoplan is None — validator should be a no-op; client may not even be called.
        await check_linear_states(_make_config(autoplan=None, autoplan_concurrency=None), client)


class TestFailureModes:
    @pytest.mark.asyncio
    async def test_a_state_missing(self):
        """AC10(a): configured Autoplan state name not in workspace."""
        client = _make_client(
            states=["Triage", "Todo", "Ready", "Done"],  # no Autoplan
            labels=["needs-review"],
        )
        with pytest.raises(ConfigError) as exc_info:
            await check_linear_states(_make_config(autoplan="Autoplan"), client)
        msg = str(exc_info.value)
        assert "Autoplan" in msg
        assert "team-id-abc" in msg or "team" in msg.lower()

    @pytest.mark.asyncio
    async def test_a_state_missing_custom_name(self):
        """Failure message names the actual configured state, not a hardcoded 'Autoplan'."""
        client = _make_client(states=["Ready", "Done"], labels=["needs-review"])
        with pytest.raises(ConfigError) as exc_info:
            await check_linear_states(_make_config(autoplan="PlanQueue"), client)
        assert "PlanQueue" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_b_label_missing(self):
        """AC10(b): needs-review label missing."""
        client = _make_client(
            states=["Autoplan", "Ready"],
            labels=["risk:low", "risk:high"],  # no needs-review
        )
        with pytest.raises(ConfigError) as exc_info:
            await check_linear_states(_make_config(), client)
        assert "needs-review" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_c_concurrency_key_missing(self):
        """AC10(c): autoplan enabled but max_concurrent_agents_by_state.autoplan unset."""
        client = _make_client(
            states=["Autoplan", "Ready"],
            labels=["needs-review"],
        )
        with pytest.raises(ConfigError) as exc_info:
            await check_linear_states(
                _make_config(autoplan="Autoplan", autoplan_concurrency=None),
                client,
            )
        msg = str(exc_info.value)
        assert "max_concurrent_agents_by_state" in msg
        assert "autoplan" in msg
