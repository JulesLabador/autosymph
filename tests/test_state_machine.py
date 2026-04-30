"""Tests for state_machine.actionable_linear_statuses (IMP-356).

These cover the autoplan addition to the polling list. The Autoplan state
is opt-in: only included when `linear_states.autoplan` is configured.
"""

from __future__ import annotations

from autosymph.config import (
    AgentConfig,
    LinearStatesConfig,
    StateConfig,
    TrackerConfig,
    WorkflowConfig,
    WorkspaceConfig,
)
from autosymph.state_machine import StateMachine


def _make_config(autoplan: str | None = None) -> WorkflowConfig:
    """Minimal config with optional autoplan state."""
    return WorkflowConfig(
        tracker=TrackerConfig(project="test", api_key="k"),
        workspace=WorkspaceConfig(root="/tmp/test", repo="/tmp/repo"),
        agent=AgentConfig(max_concurrent_agents=1),
        linear_states=LinearStatesConfig(autoplan=autoplan),
        states={"done": StateConfig(type="terminal")},
    )


class TestActionableLinearStatusesAutoplan:
    """Verify autoplan inclusion follows the opt-in pattern."""

    def test_autoplan_omitted_when_none(self):
        """When linear_states.autoplan is None, polling list excludes it."""
        sm = StateMachine(_make_config(autoplan=None))
        statuses = sm.actionable_linear_statuses()
        # Default-named states present
        assert "Ready" in statuses
        assert "In Progress" in statuses
        # Autoplan absent
        assert "Autoplan" not in statuses

    def test_autoplan_included_when_set(self):
        """When linear_states.autoplan is a string, polling list includes it."""
        sm = StateMachine(_make_config(autoplan="Autoplan"))
        statuses = sm.actionable_linear_statuses()
        assert "Autoplan" in statuses
        # Existing statuses still present
        assert "Ready" in statuses
        assert "In Progress" in statuses

    def test_autoplan_custom_name(self):
        """Polling list reflects whatever name is configured (not hardcoded)."""
        sm = StateMachine(_make_config(autoplan="PlanQueue"))
        statuses = sm.actionable_linear_statuses()
        assert "PlanQueue" in statuses
        assert "Autoplan" not in statuses
