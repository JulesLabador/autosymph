"""Unit tests for wizard.state and wizard.state_defaults."""
from __future__ import annotations


import pytest

from autosymph.wizard.state import (
    PlannedMutation,
    StateDiffEntry,
    WizardState,
)
from autosymph.wizard.state_defaults import (
    KNOWN_ALIASES,
    REQUIRED_V1,
    StateDefault,
    find_default,
    required_canonical_names,
)


class TestStateDefaults:
    def test_required_v1_has_expected_canonical_set(self):
        names = required_canonical_names()
        # The v1 wizard must own these slots; missing any is a regression.
        for required in (
            "Ready",
            "Implementing",
            "Verifying",
            "Investigating",
            "In Review",
            "Rework",
            "Merging",
            "Blocked",
            "Done",
            "Canceled",
            "Duplicate",
        ):
            assert required in names, f"REQUIRED_V1 missing {required!r}"

    def test_required_v1_excludes_autoplan_and_verify_review(self):
        """Autoplan and Reviewing Evidence are out of v1 scope (PRD R12)."""
        names = required_canonical_names()
        assert "Autoplan" not in names
        assert "Reviewing Evidence" not in names

    def test_known_aliases_only_contains_v1_canonical_targets(self):
        """Every alias value must map to a REQUIRED_V1 canonical name."""
        canonicals = set(required_canonical_names())
        for linear_name, canonical in KNOWN_ALIASES.items():
            assert canonical in canonicals, (
                f"alias {linear_name!r} -> {canonical!r} "
                f"but {canonical!r} is not in REQUIRED_V1"
            )

    def test_known_aliases_includes_in_progress(self):
        """The most common Linear default must be aliased so users aren't forced to rename."""
        assert KNOWN_ALIASES.get("In Progress") == "Implementing"

    def test_state_default_immutable(self):
        """StateDefault is frozen — accidental mutation should raise."""
        sd = REQUIRED_V1[0]
        with pytest.raises(Exception):
            sd.name = "mutated"  # type: ignore[misc]

    def test_state_defaults_have_unique_positions(self):
        """Linear orders states by ascending position; collisions cause UI weirdness."""
        positions = [s.position for s in REQUIRED_V1]
        assert len(positions) == len(set(positions))

    def test_terminals_use_correct_state_type(self):
        """Done/Canceled/Duplicate must use Linear's completed/canceled state types."""
        type_for = {s.name: s.state_type for s in REQUIRED_V1}
        assert type_for["Done"] == "completed"
        assert type_for["Canceled"] == "canceled"
        assert type_for["Duplicate"] == "canceled"

    def test_find_default_hits_and_misses(self):
        assert isinstance(find_default("Implementing"), StateDefault)
        assert find_default("nonexistent") is None


class TestWizardState:
    def test_default_construction(self):
        """A fresh WizardState() should be valid with no fields populated."""
        s = WizardState()
        assert s.linear_api_key is None
        assert s.repo_path is None
        assert s.template_choice is None
        assert s.state_diff == []
        assert s.planned_mutations == []
        assert s.planned_writes == {}
        assert s.selected_simulators == []

    def test_state_diff_entry_can_hold_each_category(self):
        """Sanity: each DiffCategory value is constructable."""
        for cat in ("exact", "near", "missing"):
            entry = StateDiffEntry(canonical_name="Implementing", linear_name="x", category=cat)  # type: ignore[arg-type]
            assert entry.category == cat

    def test_planned_mutation_round_trip(self):
        m = PlannedMutation(
            canonical_name="Verifying",
            color="#abcdef",
            position=3.0,
            state_type="started",
        )
        assert m.canonical_name == "Verifying"
        assert m.position == 3.0
