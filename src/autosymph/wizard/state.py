"""WizardState: the dataclass step functions populate incrementally.

The runner threads a single ``WizardState`` through the pipeline. Steps mutate
in place (simpler than rebuilding a frozen dataclass on every step). Every
field starts ``None`` / empty so ``WizardState()`` is a valid "fresh" state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from autosymph.wizard.state_defaults import StateDefault


# How a single autosymph-required state name maps to the user's Linear workspace.
# - "exact" : Linear has a state with this name (case-insensitive).
# - "near"  : Linear has a state listed in KNOWN_ALIASES that maps to this slot.
# - "missing" : neither — wizard plans to create it.
DiffCategory = Literal["exact", "near", "missing"]


@dataclass
class StateDiffEntry:
    """One entry in the workspace state diff.

    ``canonical_name`` is the autosymph slot (e.g. "Implementing"). ``linear_name``
    is what Linear actually has (None for missing). ``category`` decides what
    happens at preview time: exact → reuse, near → user-confirm-alias, missing
    → create.
    """

    canonical_name: str
    linear_name: str | None
    category: DiffCategory


@dataclass
class PlannedMutation:
    """One planned ``workflowStateCreate`` call awaiting user confirmation."""

    canonical_name: str
    color: str
    position: float
    state_type: str


TemplateChoice = Literal["ios", "web"]


@dataclass
class WizardState:
    """All inputs the wizard collects before commit.

    Step functions populate these fields in order. ``planned_mutations`` and
    ``planned_writes`` are computed by step_build_plan from the earlier fields;
    nothing else needs to fill them in.
    """

    # --- Linear ---
    linear_api_key: str | None = None
    viewer_user_id: str | None = None
    project_id: str | None = None
    project_slug: str | None = None
    project_name: str | None = None
    team_id: str | None = None
    existing_states: list[str] = field(default_factory=list)
    existing_labels: list[str] = field(default_factory=list)
    state_diff: list[StateDiffEntry] = field(default_factory=list)

    # --- Local repo ---
    repo_path: Path | None = None
    needs_simulator: bool = False
    selected_simulators: list[str] = field(default_factory=list)
    template_choice: TemplateChoice | None = None
    prompts_root_abs: Path | None = None

    # --- Optional ---
    braintrust_api_key: str | None = None

    # --- Plan (built by step_build_plan) ---
    planned_mutations: list[PlannedMutation] = field(default_factory=list)
    planned_writes: dict[Path, str] = field(default_factory=dict)

    # --- Reference data (for tests / verification) ---
    state_defaults_used: list[StateDefault] = field(default_factory=list)
