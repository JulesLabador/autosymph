"""Tests for `autosymph models` subcommands — check and refresh."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from autosymph.cli import main


def _write_workflow(path: Path, *, claude_model: str, state_model: str | None) -> None:
    """Write a minimal valid workflow YAML at path."""
    state_extra = ""
    if state_model is not None:
        state_extra = f"    model: {state_model}\n"
    path.write_text(
        f"""tracker:
  project: test
  api_key: k
claude:
  model: {claude_model}
states:
  verify:
    type: agent
    prompt: p.md
{state_extra}    transitions:
      complete: done
  done:
    type: terminal
"""
    )


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Empty config dir wired in via AUTOSYMPH_CONFIG_DIR.

    Layered discovery (devices/ + projects/) is skipped because subdirs are
    absent — we go straight to the legacy flat fallback that picks up *.yaml.
    """
    cfg_dir = tmp_path / "configs"
    cfg_dir.mkdir()
    monkeypatch.setenv("AUTOSYMPH_CONFIG_DIR", str(cfg_dir))
    return cfg_dir


class TestModelsCheck:
    def test_clean_configs_exit_zero(
        self, runner: CliRunner, isolated_config_dir: Path
    ) -> None:
        _write_workflow(
            isolated_config_dir / "clean.yaml",
            claude_model="claude-sonnet-4-6",
            state_model="claude-opus-4-7",
        )

        result = runner.invoke(main, ["models", "check"])

        assert result.exit_code == 0, result.output
        assert "OK" in result.output

    def test_stale_state_model_exits_one_and_lists_offender(
        self, runner: CliRunner, isolated_config_dir: Path
    ) -> None:
        _write_workflow(
            isolated_config_dir / "stale.yaml",
            claude_model="claude-sonnet-4-6",
            state_model="claude-opus-4-6",
        )

        result = runner.invoke(main, ["models", "check"])

        assert result.exit_code == 1, result.output
        assert "stale.yaml" in result.output
        assert "claude-opus-4-6" in result.output
        assert "claude-opus-4-7" in result.output  # the suggested replacement
        assert "states.verify.model" in result.output

    def test_stale_default_model_exits_one(
        self, runner: CliRunner, isolated_config_dir: Path
    ) -> None:
        _write_workflow(
            isolated_config_dir / "stale-default.yaml",
            claude_model="claude-opus-4-6",
            state_model=None,
        )

        result = runner.invoke(main, ["models", "check"])

        assert result.exit_code == 1, result.output
        assert "claude.model" in result.output

    def test_multiple_configs_one_stale_exits_one(
        self, runner: CliRunner, isolated_config_dir: Path
    ) -> None:
        _write_workflow(
            isolated_config_dir / "clean.yaml",
            claude_model="claude-sonnet-4-6",
            state_model="claude-opus-4-7",
        )
        _write_workflow(
            isolated_config_dir / "stale.yaml",
            claude_model="claude-sonnet-4-6",
            state_model="claude-opus-4-6",
        )

        result = runner.invoke(main, ["models", "check"])

        assert result.exit_code == 1, result.output
        assert "stale.yaml" in result.output
        # Clean config should not be flagged
        clean_lines = [
            line for line in result.output.splitlines() if "clean.yaml" in line
        ]
        # The path may appear once as a banner/header but should not have a stale ref under it
        for line in clean_lines:
            assert "claude-opus-4-6" not in line


# -- models refresh --


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch):
    """Replace the Anthropic API call with a fake. Returns the holder dict to mutate."""
    holder: dict[str, dict] = {"response": {"data": []}}

    def _fake(api_key: str) -> dict:
        return holder["response"]

    monkeypatch.setattr("autosymph.cli._fetch_models_response", _fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return holder


class TestModelsRefresh:
    def test_no_change_exits_zero_and_writes_nothing(
        self, runner: CliRunner, fake_api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Mirror the live registry exactly so update.changed is False
        from autosymph.models import LATEST

        fake_api["response"] = {
            "data": [
                {"id": LATEST["opus"], "created_at": "2030-01-01T00:00:00Z"},
                {"id": LATEST["sonnet"], "created_at": "2030-01-01T00:00:00Z"},
                {"id": LATEST["haiku"], "created_at": "2030-01-01T00:00:00Z"},
            ]
        }

        wrote: list[str] = []
        monkeypatch.setattr(
            "autosymph.cli.write_models_py",
            lambda target, **kw: wrote.append(str(target)),
        )

        result = runner.invoke(main, ["models", "refresh"])

        assert result.exit_code == 0, result.output
        assert "current" in result.output.lower() or "no change" in result.output.lower()
        assert wrote == []

    def test_dry_run_with_bump_prints_diff_but_does_not_write(
        self, runner: CliRunner, fake_api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_api["response"] = {
            "data": [
                {"id": "claude-opus-4-9", "created_at": "2030-01-01T00:00:00Z"},
                {"id": "claude-sonnet-4-9", "created_at": "2030-01-01T00:00:00Z"},
                {"id": "claude-haiku-4-9", "created_at": "2030-01-01T00:00:00Z"},
            ]
        }

        wrote: list[str] = []
        monkeypatch.setattr(
            "autosymph.cli.write_models_py",
            lambda target, **kw: wrote.append(str(target)),
        )

        result = runner.invoke(main, ["models", "refresh"])

        assert result.exit_code == 0, result.output
        assert "claude-opus-4-9" in result.output
        assert "opus" in result.output  # family label in the diff
        assert wrote == []  # dry-run must not write

    def test_apply_writes_models_py_with_new_latest(
        self, runner: CliRunner, fake_api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_api["response"] = {
            "data": [
                {"id": "claude-opus-4-9", "created_at": "2030-01-01T00:00:00Z"},
                {"id": "claude-sonnet-4-9", "created_at": "2030-01-01T00:00:00Z"},
                {"id": "claude-haiku-4-9", "created_at": "2030-01-01T00:00:00Z"},
            ]
        }

        captured: dict = {}

        def _capture(target, *, latest, known_stale):
            captured["target"] = target
            captured["latest"] = latest
            captured["known_stale"] = known_stale

        monkeypatch.setattr("autosymph.cli.write_models_py", _capture)

        result = runner.invoke(main, ["models", "refresh", "--apply"])

        assert result.exit_code == 0, result.output
        assert captured["latest"] == {
            "opus": "claude-opus-4-9",
            "sonnet": "claude-sonnet-4-9",
            "haiku": "claude-haiku-4-9",
        }
        # Old latests must have been demoted into stale
        assert "claude-opus-4-7" in captured["known_stale"]
        assert captured["known_stale"]["claude-opus-4-7"] == "claude-opus-4-9"

    def test_missing_api_key_exits_one(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        result = runner.invoke(main, ["models", "refresh"])

        assert result.exit_code == 1
        assert "ANTHROPIC_API_KEY" in result.output
