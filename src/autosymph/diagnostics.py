"""Startup validators for autosymph configuration (IMP-356).

`check_linear_states` enforces the explicit-config rules required by the
Autoplan Linear-state feature:

  (a) When `linear_states.autoplan` is set, that state name must exist in the
      Linear team's workflow.
  (b) When autoplan is enabled, the `needs-review` issue label must exist in
      the workspace (used by the both-reviewers-fail fallback path).
  (c) When autoplan is enabled, `agent.max_concurrent_agents_by_state.autoplan`
      must be set (R9 explicit-config rule from PRD round 2).

Wired into autosymph startup in cli.py after config load and before the
supervisor loop starts. Raises `ConfigError` to fail loudly with a clear
message naming the missing piece.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from autosymph.config import WorkflowConfig


class ConfigError(RuntimeError):
    """Configuration is invalid for runtime — startup must abort."""


class _StatesAndLabelsClient(Protocol):
    """Subset of LinearClient surface area `check_linear_states` consumes.

    Defined as a Protocol so tests can pass a minimal mock without inheriting
    from LinearClient (avoids real HTTP setup).
    """

    async def resolve_team_for_project(self) -> str: ...
    async def fetch_workspace_states(self, team_id: str) -> list[str]: ...
    async def fetch_workspace_labels(self) -> list[str]: ...


async def check_linear_states(
    config: WorkflowConfig,
    client: _StatesAndLabelsClient,
) -> None:
    """Validate the Linear workspace matches the autoplan configuration.

    No-op when `linear_states.autoplan` is None — preserves backward
    compatibility for projects that don't enable autoplan.

    Raises ConfigError on the three failure modes described in module docstring.
    """
    autoplan_state = config.linear_states.autoplan
    if not autoplan_state:
        # Autoplan not enabled — skip all checks.
        return

    # (c) — Concurrency key must be explicit. Cheap check; do first so we don't
    # hit the Linear API only to fail on a config-only error.
    by_state = config.agent.max_concurrent_agents_by_state or {}
    if "autoplan" not in by_state:
        raise ConfigError(
            "linear_states.autoplan is set but "
            "agent.max_concurrent_agents_by_state.autoplan is unset. "
            "Per IMP-356 R9, this key must be explicit (recommended: 1). "
            "Add to your project YAML under agent.max_concurrent_agents_by_state."
        )

    team_id = await client.resolve_team_for_project()

    # (a) — Configured state must exist in the team's workflow.
    states = await client.fetch_workspace_states(team_id)
    if autoplan_state not in states:
        raise ConfigError(
            f"linear_states.autoplan='{autoplan_state}' is not a state in "
            f"Linear team {team_id}. Available states: {sorted(states)!r}. "
            f"Create the state in Linear (Settings → Workflow) or update the "
            f"config to match an existing state."
        )

    # (b) — needs-review label must exist (used by both-reviewers-fail fallback).
    labels = await client.fetch_workspace_labels()
    if "needs-review" not in labels:
        raise ConfigError(
            "Required 'needs-review' issue label is missing from the Linear "
            "workspace. Per IMP-356 R12, this label is applied by the autoplan "
            "skill when both Codex and Claude review fall back. Create it in "
            "Linear (color recommended: red) and re-run autosymph."
        )
