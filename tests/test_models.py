"""Tests for the model registry — single source of truth for current Claude model ids."""

from __future__ import annotations

from autosymph.config import (
    ClaudeConfig,
    StateConfig,
    TrackerConfig,
    WorkflowConfig,
)
from autosymph.models import (
    ALIASES,
    KNOWN_STALE,
    LATEST,
    StaleModelRef,
    collect_stale_models,
    compute_registry_update,
    family_of,
    is_alias,
    is_stale,
    pick_latest_per_family,
    suggested_replacement,
    write_models_py,
)


class TestFamilyOf:
    def test_opus(self):
        assert family_of("claude-opus-4-7") == "opus"

    def test_sonnet(self):
        assert family_of("claude-sonnet-4-6") == "sonnet"

    def test_haiku_with_date_suffix(self):
        assert family_of("claude-haiku-4-5-20251001") == "haiku"

    def test_unknown_returns_none(self):
        assert family_of("gpt-4") is None

    def test_empty_string_returns_none(self):
        assert family_of("") is None

    def test_alias_resolves_to_itself(self):
        # "opus" is both an alias AND a family name. family_of must recognize
        # bare aliases so the inventory phase of the skill can bucket them.
        assert family_of("opus") == "opus"
        assert family_of("sonnet") == "sonnet"
        assert family_of("haiku") == "haiku"


class TestIsAlias:
    def test_known_aliases(self):
        assert is_alias("opus") is True
        assert is_alias("sonnet") is True
        assert is_alias("haiku") is True

    def test_pinned_id_is_not_alias(self):
        assert is_alias("claude-opus-4-7") is False
        assert is_alias("claude-sonnet-4-6") is False

    def test_empty_string_is_not_alias(self):
        assert is_alias("") is False

    def test_aliases_constant_matches(self):
        # ALIASES set should equal the family names so callers can iterate it
        assert ALIASES == {"opus", "sonnet", "haiku"}

    def test_aliases_are_never_stale(self):
        # The whole point of recommending aliases: they auto-float. They must
        # never trip `models check` and never appear in collect_stale_models.
        for alias in ALIASES:
            assert is_stale(alias) is False, f"alias '{alias}' should not be stale"
            assert suggested_replacement(alias) is None


class TestIsStale:
    def test_known_stale_returns_true(self):
        assert is_stale("claude-opus-4-6") is True

    def test_current_returns_false(self):
        assert is_stale(LATEST["opus"]) is False

    def test_unknown_returns_false(self):
        # Unknown models aren't stale — they're unknown. The check command treats
        # them separately so a typo doesn't get silently rewritten.
        assert is_stale("claude-opus-9-9") is False


class TestSuggestedReplacement:
    def test_returns_replacement_for_stale(self):
        assert suggested_replacement("claude-opus-4-6") == LATEST["opus"]

    def test_returns_none_for_current(self):
        assert suggested_replacement(LATEST["sonnet"]) is None

    def test_returns_none_for_unknown(self):
        assert suggested_replacement("claude-opus-9-9") is None


class TestRegistryIntegrity:
    """Sanity checks that prevent the registry from going internally inconsistent."""

    def test_latest_has_all_three_families(self):
        assert set(LATEST.keys()) == {"opus", "sonnet", "haiku"}

    def test_every_stale_replacement_is_a_current_model(self):
        # If a KNOWN_STALE entry suggests an id that's not in LATEST.values(),
        # we'd silently rewrite to another stale id. Refresh must keep these in sync.
        current = set(LATEST.values())
        for stale, replacement in KNOWN_STALE.items():
            assert replacement in current, (
                f"KNOWN_STALE['{stale}'] = '{replacement}' is not in LATEST.values()"
            )

    def test_no_current_id_appears_as_stale(self):
        # Defensive: if a current id is also marked stale, refresh has a bug
        # and configs would oscillate.
        for current_id in LATEST.values():
            assert current_id not in KNOWN_STALE, (
                f"'{current_id}' is in LATEST and also in KNOWN_STALE — refresh logic is broken"
            )

    def test_every_stale_belongs_to_a_known_family(self):
        for stale_id in KNOWN_STALE:
            assert family_of(stale_id) is not None, (
                f"KNOWN_STALE id '{stale_id}' has no recognizable family"
            )


def _cfg_with(claude_model: str, state_models: dict[str, str | None]) -> WorkflowConfig:
    """Build a minimal WorkflowConfig with the given model overrides."""
    states = {"done": StateConfig(type="terminal")}
    for name, model in state_models.items():
        states[name] = StateConfig(type="agent", prompt="p.md", model=model)
    return WorkflowConfig(
        tracker=TrackerConfig(project="t", api_key="k"),
        claude=ClaudeConfig(model=claude_model),
        states=states,
    )


class TestCollectStaleModels:
    def test_no_stale_returns_empty(self):
        cfg = _cfg_with(LATEST["sonnet"], {"verify": LATEST["opus"]})
        assert collect_stale_models(cfg) == []

    def test_stale_claude_default_is_reported(self):
        cfg = _cfg_with("claude-opus-4-6", {})
        refs = collect_stale_models(cfg)

        assert len(refs) == 1
        assert refs[0].state_name == "<default>"
        assert refs[0].model_id == "claude-opus-4-6"
        assert refs[0].suggested == LATEST["opus"]
        assert refs[0].field == "claude.model"

    def test_stale_state_model_is_reported(self):
        cfg = _cfg_with(LATEST["sonnet"], {"verify": "claude-opus-4-6"})
        refs = collect_stale_models(cfg)

        assert len(refs) == 1
        assert refs[0].state_name == "verify"
        assert refs[0].model_id == "claude-opus-4-6"
        assert refs[0].suggested == LATEST["opus"]
        assert refs[0].field == "states.verify.model"

    def test_multiple_stale_entries_collected(self):
        cfg = _cfg_with(
            "claude-sonnet-4-5",
            {
                "verify": "claude-opus-4-6",
                "investigating": "claude-opus-4-6",
                "implement": LATEST["sonnet"],  # current — not reported
            },
        )
        refs = collect_stale_models(cfg)
        names = sorted(r.state_name for r in refs)
        assert names == ["<default>", "investigating", "verify"]

    def test_state_with_none_model_inherits_default_and_is_not_double_reported(self):
        # When a state has model=None it inherits from claude.model. We should
        # report the default once, not once per inheriting state.
        cfg = _cfg_with(
            "claude-opus-4-6",
            {"implement": None, "rework": None},
        )
        refs = collect_stale_models(cfg)
        assert len(refs) == 1
        assert refs[0].state_name == "<default>"

    def test_unknown_model_id_is_not_stale_and_not_reported(self):
        # Unknown ids are not in KNOWN_STALE — they must NOT be silently rewritten.
        # The check command surfaces them separately as "unknown", not as stale.
        cfg = _cfg_with(LATEST["sonnet"], {"verify": "claude-opus-9-9"})
        assert collect_stale_models(cfg) == []

    def test_alias_only_config_returns_empty(self):
        # When everything in the config is an alias, models check must pass —
        # aliases auto-float at dispatch time so drift is structurally impossible.
        cfg = _cfg_with("sonnet", {"verify": "opus", "implement": "haiku"})
        assert collect_stale_models(cfg) == []

    def test_mixed_alias_and_pinned_only_flags_pinned(self):
        # Real-world: most states use aliases, one pins for reproducibility
        # and that pin happens to be stale. Flag only the pinned one.
        cfg = _cfg_with("sonnet", {"verify": "opus", "release": "claude-opus-4-6"})
        refs = collect_stale_models(cfg)
        assert len(refs) == 1
        assert refs[0].state_name == "release"
        assert refs[0].model_id == "claude-opus-4-6"

    def test_stale_model_ref_is_a_dataclass(self):
        ref = StaleModelRef(
            state_name="verify",
            model_id="claude-opus-4-6",
            suggested="claude-opus-4-7",
            field="states.verify.model",
        )
        assert ref.state_name == "verify"


# -- /v1/models response → LATEST mapping --


def _model(id_: str, created_at: str) -> dict:
    return {"type": "model", "id": id_, "display_name": id_, "created_at": created_at}


class TestPickLatestPerFamily:
    def test_picks_newest_by_created_at_per_family(self):
        response = {
            "data": [
                _model("claude-opus-4-6", "2025-09-01T00:00:00Z"),
                _model("claude-opus-4-7", "2026-01-01T00:00:00Z"),
                _model("claude-sonnet-4-5", "2025-08-01T00:00:00Z"),
                _model("claude-sonnet-4-6", "2025-12-01T00:00:00Z"),
                _model("claude-haiku-4-5-20251001", "2025-10-01T00:00:00Z"),
            ]
        }
        result = pick_latest_per_family(response)
        assert result == {
            "opus": "claude-opus-4-7",
            "sonnet": "claude-sonnet-4-6",
            "haiku": "claude-haiku-4-5-20251001",
        }

    def test_id_lexical_order_is_not_used(self):
        # If we sorted by id we'd pick "claude-opus-4-9" (alpha-released) over the
        # actually newer "claude-opus-4-7" (stable). Date wins.
        response = {
            "data": [
                _model("claude-opus-4-7", "2026-02-01T00:00:00Z"),
                _model("claude-opus-4-9", "2025-06-01T00:00:00Z"),
            ]
        }
        result = pick_latest_per_family(response)
        assert result["opus"] == "claude-opus-4-7"

    def test_unknown_families_are_ignored(self):
        response = {
            "data": [
                _model("claude-opus-4-7", "2026-01-01T00:00:00Z"),
                _model("gpt-5", "2026-01-01T00:00:00Z"),
                _model("claude-magic-99", "2026-01-01T00:00:00Z"),
            ]
        }
        result = pick_latest_per_family(response)
        assert result == {"opus": "claude-opus-4-7"}

    def test_missing_family_is_omitted(self):
        response = {"data": [_model("claude-opus-4-7", "2026-01-01T00:00:00Z")]}
        result = pick_latest_per_family(response)
        assert result == {"opus": "claude-opus-4-7"}

    def test_empty_response_returns_empty(self):
        assert pick_latest_per_family({"data": []}) == {}


class TestComputeRegistryUpdate:
    def test_no_change_when_api_matches_current(self):
        current = dict(LATEST)
        update = compute_registry_update(current_latest=current, current_stale={}, api_latest=current)
        assert update.changed is False
        assert update.new_latest == current

    def test_bump_moves_old_latest_into_stale(self):
        current_latest = {"opus": "claude-opus-4-6", "sonnet": "claude-sonnet-4-5"}
        current_stale = {"claude-opus-4-5": "claude-opus-4-6"}
        api_latest = {"opus": "claude-opus-4-7", "sonnet": "claude-sonnet-4-6"}

        update = compute_registry_update(
            current_latest=current_latest,
            current_stale=current_stale,
            api_latest=api_latest,
        )

        assert update.changed is True
        assert update.new_latest == api_latest
        # Previous latests are now stale, pointing at new latest
        assert update.new_stale["claude-opus-4-6"] == "claude-opus-4-7"
        assert update.new_stale["claude-sonnet-4-5"] == "claude-sonnet-4-6"
        # Pre-existing stale entries are preserved AND retargeted to new latest
        assert update.new_stale["claude-opus-4-5"] == "claude-opus-4-7"

    def test_existing_stale_entries_get_retargeted_on_bump(self):
        # When opus bumps from 4-6 to 4-7, an entry like 4-5→4-6 must become 4-5→4-7,
        # else `models check` would suggest a (now-stale) replacement.
        current_latest = {"opus": "claude-opus-4-6"}
        current_stale = {"claude-opus-4-5": "claude-opus-4-6", "claude-opus-4-1": "claude-opus-4-6"}
        api_latest = {"opus": "claude-opus-4-7"}

        update = compute_registry_update(
            current_latest=current_latest,
            current_stale=current_stale,
            api_latest=api_latest,
        )

        assert update.new_stale["claude-opus-4-5"] == "claude-opus-4-7"
        assert update.new_stale["claude-opus-4-1"] == "claude-opus-4-7"

    def test_new_latest_id_is_never_in_new_stale(self):
        # Defensive: invariant must hold or `models check` oscillates.
        current_latest = {"opus": "claude-opus-4-6"}
        current_stale = {}
        api_latest = {"opus": "claude-opus-4-7"}

        update = compute_registry_update(
            current_latest=current_latest,
            current_stale=current_stale,
            api_latest=api_latest,
        )

        for new_id in update.new_latest.values():
            assert new_id not in update.new_stale


class TestWriteModelsPy:
    def test_written_file_is_valid_python_with_expected_constants(self, tmp_path):
        target = tmp_path / "models.py"
        write_models_py(
            target,
            latest={"opus": "claude-opus-4-9", "sonnet": "claude-sonnet-4-7", "haiku": "claude-haiku-4-5"},
            known_stale={"claude-opus-4-7": "claude-opus-4-9"},
        )

        # File round-trips through Python: importable + introspectable.
        # Register in sys.modules so @dataclass annotation resolution works
        # (matches how a real `import autosymph.models` would behave).
        import importlib.util
        import sys

        spec = importlib.util.spec_from_file_location("rewritten_models", target)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["rewritten_models"] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop("rewritten_models", None)

        assert module.LATEST == {
            "opus": "claude-opus-4-9",
            "sonnet": "claude-sonnet-4-7",
            "haiku": "claude-haiku-4-5",
        }
        assert module.KNOWN_STALE == {"claude-opus-4-7": "claude-opus-4-9"}
        # Helpers must still be present
        assert callable(module.is_stale)
        assert callable(module.collect_stale_models)
        assert callable(module.pick_latest_per_family)

    def test_written_file_passes_registry_invariants(self, tmp_path):
        # Same invariants test_models.py asserts on the live registry must hold
        # for the rewritten file, else --apply would land us in a broken state.
        target = tmp_path / "models.py"
        latest = {"opus": "claude-opus-4-7", "sonnet": "claude-sonnet-4-6", "haiku": "claude-haiku-4-5"}
        write_models_py(
            target,
            latest=latest,
            known_stale={"claude-opus-4-6": "claude-opus-4-7"},
        )

        import importlib.util
        import sys

        spec = importlib.util.spec_from_file_location("rw2", target)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["rw2"] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop("rw2", None)

        # No current id appears as stale
        for current_id in module.LATEST.values():
            assert current_id not in module.KNOWN_STALE
        # Every stale replacement is current
        for replacement in module.KNOWN_STALE.values():
            assert replacement in module.LATEST.values()
