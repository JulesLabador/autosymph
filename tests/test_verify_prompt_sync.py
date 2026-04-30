from __future__ import annotations

from pathlib import Path

import pytest
import yaml


def _active_project_configs() -> list[Path]:
    config_root = Path(__file__).resolve().parents[1] / "examples" / "config" / "projects"
    project_configs = [
        config_root / "ios-project.yaml.example",
        config_root / "web-project.yaml.example",
    ]
    existing = [path for path in project_configs if path.exists()]
    if not existing:
        pytest.skip("example project configs are not included in the standalone repo")
    return existing


def test_active_project_configs_use_canonical_repo_prompt_root():
    for path in _active_project_configs():
        data = yaml.safe_load(path.read_text())
        prompt_root = (path.parent / data["prompts"]["root"]).resolve()
        assert prompt_root == (Path(__file__).resolve().parents[1] / "prompts").resolve()
        assert data["prompts"]["global_prompt"] == "global.md"
        assert data["states"]["verify"]["prompt"] == "verify.md"


def test_verify_support_skills_are_packaged():
    root = Path(__file__).resolve().parents[1]

    assert (root / "skills" / "verify-preflight" / "scripts" / "preflight.sh").exists()
    assert (root / "skills" / "verify-finalize" / "scripts" / "verify-finalize.sh").exists()
    assert (root / "skills" / "verify-completion-audit" / "scripts" / "audit.sh").exists()
