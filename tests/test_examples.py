from __future__ import annotations

from pathlib import Path

import yaml

from autosymph.config import load_config, load_device_config, validate_config


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def test_project_config_examples_are_valid() -> None:
    for path in sorted((EXAMPLES / "config" / "projects").glob("*.yaml.example")):
        cfg = load_config(path)
        validate_config(cfg)


def test_device_config_example_is_valid() -> None:
    cfg = load_device_config(EXAMPLES / "config" / "devices" / "hostname.yaml.example")

    assert "ios-app" in cfg.projects
    assert "web-app" in cfg.projects


def test_project_examples_point_at_packaged_prompts() -> None:
    for path in sorted((EXAMPLES / "config" / "projects").glob("*.yaml.example")):
        data = yaml.safe_load(path.read_text())
        prompt_root = (path.parent / data["prompts"]["root"]).resolve()

        assert prompt_root == (ROOT / "prompts").resolve()
        assert data["prompts"]["global_prompt"] == "global.md"
        assert data["states"]["verify"]["prompt"] == "verify.md"


def test_verify_template_examples_are_packaged() -> None:
    verify_dir = EXAMPLES / "project" / ".autosymph" / "verify"

    for name in ("auth.md", "ios.md", "web.md", "fixtures.md"):
        path = verify_dir / name
        assert path.exists(), name
        assert path.read_text().strip(), name

