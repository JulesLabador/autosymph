"""Canonical autosymph workflow state names + known aliases.

This module is the single source of truth for which Linear workflow states
the onboarding wizard expects. ``REQUIRED_V1`` lists the canonical names with
their (color, position, type) so the wizard can create missing states
deterministically. ``KNOWN_ALIASES`` is a CLOSED dict of
``linear_existing_name → autosymph_canonical_name`` — the wizard never does
substring or fuzzy matching, so adding a new alias requires a code change
(deliberate code-review checkpoint to prevent silent miswiring).

``Autoplan`` and ``Reviewing Evidence`` are intentionally absent: those states
belong to the autoplan / verify-review features, which are out of v1 scope per
PRD R12. A follow-up issue (10.1) will add autoplan onboarding.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StateDefault:
    """Canonical autosymph state with the metadata needed to create it in Linear."""

    name: str
    color: str  # hex, with leading #
    position: float  # Linear orders states by ascending position
    state_type: str  # Linear's enum: triage|backlog|unstarted|started|completed|canceled


# Required workflow states for v1. Order is the wizard's preferred display order.
# Positions are spaced by 1.0 so users can re-order freely later.
REQUIRED_V1: list[StateDefault] = [
    StateDefault(name="Ready", color="#bec2c8", position=1.0, state_type="unstarted"),
    StateDefault(name="Implementing", color="#5e6ad2", position=2.0, state_type="started"),
    StateDefault(name="Verifying", color="#f2c94c", position=3.0, state_type="started"),
    StateDefault(name="Investigating", color="#f2994a", position=4.0, state_type="started"),
    StateDefault(name="In Review", color="#3b82f6", position=5.0, state_type="started"),
    StateDefault(name="Rework", color="#eb5757", position=6.0, state_type="started"),
    StateDefault(name="Merging", color="#0bc4ad", position=7.0, state_type="started"),
    StateDefault(name="Blocked", color="#ff4444", position=8.0, state_type="started"),
    StateDefault(name="Done", color="#5e6ad2", position=9.0, state_type="completed"),
    StateDefault(name="Canceled", color="#bec2c8", position=10.0, state_type="canceled"),
    StateDefault(name="Duplicate", color="#bec2c8", position=11.0, state_type="canceled"),
]


# Closed alias map. Keys are existing Linear state names users may already
# have; values are the autosymph canonical slot they map to. Comparison is
# case-insensitive (handled by the wizard's diff step). Substring/fuzzy
# matching is explicitly NOT used — adding a new alias is a code change.
KNOWN_ALIASES: dict[str, str] = {
    "In Progress": "Implementing",
    # Add more here as users report mismatches; each addition is a deliberate
    # decision that should ship in a PR with a test.
}


def required_canonical_names() -> list[str]:
    """Return just the canonical names from REQUIRED_V1, in display order."""
    return [s.name for s in REQUIRED_V1]


def find_default(name: str) -> StateDefault | None:
    """Look up a StateDefault by canonical name. Case-sensitive (matches REQUIRED_V1)."""
    for entry in REQUIRED_V1:
        if entry.name == name:
            return entry
    return None
