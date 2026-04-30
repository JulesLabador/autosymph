from __future__ import annotations

from pathlib import Path

import pytest
import yaml


def _active_project_configs() -> list[Path]:
    config_root = Path(__file__).resolve().parents[2] / "autosymph-config" / "projects"
    project_configs = [
        config_root / "ios-app.yaml",
        config_root / "web-app.yaml",
        config_root / "api-service.yaml",
    ]
    existing = [path for path in project_configs if path.exists()]
    if not existing:
        pytest.skip("active project configs are not included in the standalone repo")
    return existing


def test_active_project_configs_use_canonical_repo_prompt_root():
    for path in _active_project_configs():
        data = yaml.safe_load(path.read_text())
        assert data["prompts"]["root"] == "../../autosymph/prompts"
        assert data["prompts"]["global_prompt"] == "global.md"
        assert data["states"]["verify"]["prompt"] == "verify.md"


def test_active_project_configs_have_verify_finalize_in_allowed_tools():
    """The legacy `tomas-macbook-pro-2.yaml` enforced this for the single-file
    config; now that we're layered, every project config that runs verify must
    keep verify-preflight + verify-finalize + the bash/jq/curl scripts they need
    in `allowed_tools`. Replaces the deleted legacy-flat-config test."""
    for path in _active_project_configs():
        data = yaml.safe_load(path.read_text())
        allowed_tools = data["states"]["verify"]["allowed_tools"]

        assert "verify-preflight/scripts/preflight.sh" in allowed_tools, path
        assert "verify-finalize/scripts/verify-finalize.sh" in allowed_tools, path
        assert "Bash(bash:*)" in allowed_tools, path
        assert "Bash(jq:*)" in allowed_tools, path
        assert "Bash(curl:*)" in allowed_tools, path
