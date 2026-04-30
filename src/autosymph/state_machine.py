"""State machine — states, transitions, signals, claim lifecycle.

Maps the implement → verify → review → merge lifecycle with risk-based routing.
Tracks internal claim states per issue: Unclaimed → Claimed → Running → Released.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autosymph.config import WorkflowConfig

logger = logging.getLogger(__name__)


class Signal(str, Enum):
    """Signals that trigger state transitions."""

    COMPLETE = "complete"
    COMPLETE_LOW_RISK = "complete_low_risk"
    APPROVE = "approve"
    FAIL = "fail"
    TIMEOUT = "timeout"
    ESCALATE = "escalate"
    WORKAROUND = "workaround"
    NOT_AUTOSYMPH = "not_autosymph"
    BLOCKED = "blocked"  # infrastructure issue — sim won't boot, build tool missing, auth wall
    NEEDS_CONTEXT = "needs_context"  # can't proceed without info not in the issue/PR
    STRUCTIFY = "structify"  # verify_review reject_structify — route to investigating, not retry


class ClaimState(str, Enum):
    """Internal claim lifecycle for an issue within the orchestrator."""

    UNCLAIMED = "unclaimed"
    CLAIMED = "claimed"
    RUNNING = "running"
    RELEASED = "released"


@dataclass
class IssueState:
    """Tracked state for a single issue."""

    issue_id: str
    identifier: str
    workflow_state: str  # state machine state name (implement, verify, etc.)
    claim: ClaimState = ClaimState.UNCLAIMED
    rework_count: int = 0
    verify_retry_count: int = 0  # tracks verify ↔ verify_review cycles
    infra_crash_count: int = 0   # consecutive workspace/setup crashes (not agent failures)
    last_infra_error: str | None = None  # last infra crash message for TUI display
    last_comment_at: str | None = None


class StateMachine:
    """Evaluates state transitions and manages issue claim lifecycle.

    Responsibilities:
    - Resolve the next state given current state + signal
    - Track rework cycle counts per issue
    - Enforce rework exhaustion policy (N cycles → back to Todo)
    - Manage claim states for concurrency control
    - Map Linear status names to workflow state names
    """

    def __init__(self, config: WorkflowConfig) -> None:
        self.config = config
        self._issues: dict[str, IssueState] = {}

    def reload(self, config: WorkflowConfig) -> None:
        """Hot-reload with new config. Preserves issue tracking state."""
        self.config = config

    # -- Linear ↔ workflow state mapping --

    def actionable_linear_statuses(self) -> list[str]:
        """Return Linear status names the orchestrator should poll for."""
        ls = self.config.linear_states
        statuses = [ls.todo, ls.autoplan, ls.active, ls.verifying, ls.investigating, ls.review, ls.gate_approved, ls.rework, ls.blocked]
        return [s for s in statuses if s]

    # -- Issue tracking --

    def track_issue(self, issue_id: str, identifier: str, workflow_state: str) -> IssueState:
        """Start tracking an issue or update its workflow state."""
        if issue_id in self._issues:
            existing = self._issues[issue_id]
            existing.workflow_state = workflow_state
            return existing

        state = IssueState(
            issue_id=issue_id,
            identifier=identifier,
            workflow_state=workflow_state,
        )
        self._issues[issue_id] = state
        logger.info("Tracking %s in state '%s'", identifier, workflow_state)
        return state

    def get_issue(self, issue_id: str) -> IssueState | None:
        return self._issues.get(issue_id)

    def release_issue(self, issue_id: str) -> None:
        """Mark an issue as released (agent done, ready for next state)."""
        if issue_id in self._issues:
            self._issues[issue_id].claim = ClaimState.RELEASED

    def untrack_issue(self, issue_id: str) -> None:
        """Stop tracking an issue (terminal state reached)."""
        self._issues.pop(issue_id, None)

    @property
    def tracked_issues(self) -> dict[str, IssueState]:
        return dict(self._issues)

    # -- Claim lifecycle --

    def claim(self, issue_id: str) -> bool:
        """Attempt to claim an issue for dispatch. Returns True if successful."""
        issue = self._issues.get(issue_id)
        if not issue or issue.claim != ClaimState.UNCLAIMED:
            return False
        issue.claim = ClaimState.CLAIMED
        return True

    def mark_running(self, issue_id: str) -> None:
        """Mark a claimed issue as running (agent spawned)."""
        issue = self._issues.get(issue_id)
        if issue and issue.claim == ClaimState.CLAIMED:
            issue.claim = ClaimState.RUNNING

    def release(self, issue_id: str) -> None:
        """Release an issue back to unclaimed (agent finished or failed)."""
        issue = self._issues.get(issue_id)
        if issue:
            issue.claim = ClaimState.UNCLAIMED

    # -- Transitions --

    def next_state(self, current_state: str, signal: Signal, issue_id: str) -> str | None:
        """Return the target state for a transition, or None if invalid."""
        state_cfg = self.config.states.get(current_state)
        if not state_cfg or not state_cfg.transitions:
            return None

        extras = state_cfg.transitions.__pydantic_extra__ or {}
        target = extras.get(signal.value)
        if not target:
            return None

        # Track rework cycles — only when routing to the gate's rework_to target
        issue = self._issues.get(issue_id)
        if issue and state_cfg.rework_to and target == state_cfg.rework_to:
            issue.rework_count += 1
            if state_cfg.max_rework and issue.rework_count > state_cfg.max_rework:
                logger.warning(
                    "%s exceeded rework cap (%d/%d) — returning to %s",
                    issue.identifier, issue.rework_count, state_cfg.max_rework,
                    state_cfg.rework_exhausted,
                )
                return state_cfg.rework_exhausted or "todo"

        return target

    def record_rework(self, issue_id: str) -> str | None:
        """Record a rework cycle and check exhaustion.

        Called by the orchestrator when an issue re-enters a rework state
        (detected via Linear status change, not via next_state transitions).

        Returns the rework_exhausted target if cap exceeded, else None.
        """
        issue = self._issues.get(issue_id)
        if not issue:
            return None

        issue.rework_count += 1

        # Find the gate state that has rework_to configured
        for state_cfg in self.config.states.values():
            if state_cfg.rework_to == "rework" and state_cfg.max_rework:
                if issue.rework_count > state_cfg.max_rework:
                    logger.warning(
                        "%s exceeded rework cap (%d/%d) — returning to %s",
                        issue.identifier, issue.rework_count, state_cfg.max_rework,
                        state_cfg.rework_exhausted,
                    )
                    return state_cfg.rework_exhausted or "todo"
        return None

    def reset_rework_count(self, issue_id: str) -> None:
        """Reset rework counter for an issue (e.g., on completion)."""
        issue = self._issues.get(issue_id)
        if issue:
            issue.rework_count = 0

    # -- Concurrency queries --

    def running_count(self) -> int:
        """Number of issues currently claimed or running."""
        return sum(
            1 for i in self._issues.values()
            if i.claim in (ClaimState.CLAIMED, ClaimState.RUNNING)
        )

    def running_count_by_state(self, workflow_state: str) -> int:
        """Number of issues running in a specific workflow state."""
        return sum(
            1 for i in self._issues.values()
            if i.workflow_state == workflow_state
            and i.claim in (ClaimState.CLAIMED, ClaimState.RUNNING)
        )
