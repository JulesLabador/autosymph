"""Unit tests for individual wizard step functions.

Each step is exercised in isolation with a ScriptedPrompter and a stub
LinearClient where applicable. End-to-end orchestration is in
``test_wizard_e2e.py``.
"""
from __future__ import annotations

import os
import socket
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from autosymph.linear_client import LinearClient
from autosymph.wizard import steps
from autosymph.wizard.errors import (
    WizardAborted,
    WizardAlreadyConfigured,
    WizardLinearMutationFailed,
    WizardPromptsRootMissing,
)
from autosymph.wizard.prompter import ScriptedPrompter
from autosymph.wizard.state import (
    PlannedMutation,
    StateDiffEntry,
    WizardState,
)


@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    """Redirect AUTOSYMPH_CONFIG_DIR to a tmp dir so steps don't touch ~."""
    cfg = tmp_path / "config"
    monkeypatch.setenv("AUTOSYMPH_CONFIG_DIR", str(cfg))
    return cfg


def _stub_client():
    """LinearClient instance with a patched _query method ready for AsyncMock side effects."""
    client = LinearClient(api_key="dummy", project_name="probe")
    return client


# --- step_acquire_lock ------------------------------------------------------


class TestAcquireLock:
    def test_acquire_and_release(self, tmp_config_dir):
        state = WizardState()
        with steps.step_acquire_lock(state) as lock_path:
            assert lock_path.exists()
            assert lock_path.read_text().strip() == str(os.getpid())
        assert not lock_path.exists()

    def test_concurrent_acquire_raises(self, tmp_config_dir):
        state = WizardState()
        with steps.step_acquire_lock(state):
            with pytest.raises(WizardAlreadyConfigured) as exc_info:
                with steps.step_acquire_lock(state):
                    pass
            assert "lockfile" in exc_info.value.reason


# --- step_detect_existing_config -------------------------------------------


class TestDetectExistingConfig:
    def _hostname_yaml(self, cfg_dir):
        return cfg_dir / "devices" / f"{socket.gethostname().split('.')[0].lower()}.yaml"

    def test_clean_state_passes(self, tmp_config_dir):
        # Empty cfg dir → no triggers.
        steps.step_detect_existing_config(WizardState())  # should not raise

    def test_existing_device_yaml_triggers(self, tmp_config_dir):
        path = self._hostname_yaml(tmp_config_dir)
        path.parent.mkdir(parents=True)
        path.write_text("machine_name: x\n")
        with pytest.raises(WizardAlreadyConfigured) as exc_info:
            steps.step_detect_existing_config(WizardState())
        assert exc_info.value.trigger_path == str(path)

    def test_existing_project_yaml_triggers(self, tmp_config_dir):
        proj = tmp_config_dir / "projects" / "anything.yaml"
        proj.parent.mkdir(parents=True)
        proj.write_text("tracker: {}\n")
        with pytest.raises(WizardAlreadyConfigured):
            steps.step_detect_existing_config(WizardState())

    def test_nonempty_local_env_triggers(self, tmp_config_dir):
        env = tmp_config_dir / "local.env"
        env.parent.mkdir(parents=True)
        env.write_text("LINEAR_API_KEY=lin_api_xxx\n")
        with pytest.raises(WizardAlreadyConfigured):
            steps.step_detect_existing_config(WizardState())

    def test_empty_local_env_does_not_trigger(self, tmp_config_dir):
        env = tmp_config_dir / "local.env"
        env.parent.mkdir(parents=True)
        env.write_text("   \n")  # whitespace-only
        steps.step_detect_existing_config(WizardState())  # should not raise

    def test_stale_lockfile_triggers(self, tmp_config_dir):
        lock = tmp_config_dir / ".wizard.lock"
        lock.parent.mkdir(parents=True)
        # Use a PID we know is dead. PID 1 is alive (init); pick a high one
        # that's almost certainly not in use.
        lock.write_text("999999\n")
        with pytest.raises(WizardAlreadyConfigured) as exc_info:
            steps.step_detect_existing_config(WizardState())
        assert "stale" in exc_info.value.reason


# --- step_compute_state_diff -----------------------------------------------


class TestComputeStateDiff:
    def test_all_exact(self):
        s = WizardState()
        s.existing_states = [
            "Ready", "Implementing", "Verifying", "Investigating",
            "In Review", "Rework", "Merging", "Blocked",
            "Done", "Canceled", "Duplicate",
        ]
        steps.step_compute_state_diff(s)
        assert all(e.category == "exact" for e in s.state_diff)

    def test_in_progress_alias_categorized_as_near(self):
        s = WizardState()
        s.existing_states = ["In Progress"]  # only the alias, nothing else
        steps.step_compute_state_diff(s)
        # Find the Implementing entry
        impl = next(e for e in s.state_diff if e.canonical_name == "Implementing")
        assert impl.category == "near"
        assert impl.linear_name == "In Progress"
        # Other slots are missing
        ready = next(e for e in s.state_diff if e.canonical_name == "Ready")
        assert ready.category == "missing"

    def test_no_substring_matching(self):
        """A workspace state named 'Implementing soon' must NOT match 'Implementing'."""
        s = WizardState()
        s.existing_states = ["Implementing soon"]
        steps.step_compute_state_diff(s)
        impl = next(e for e in s.state_diff if e.canonical_name == "Implementing")
        assert impl.category == "missing"

    def test_case_insensitive_exact(self):
        s = WizardState()
        s.existing_states = ["IMPLEMENTING"]
        steps.step_compute_state_diff(s)
        impl = next(e for e in s.state_diff if e.canonical_name == "Implementing")
        assert impl.category == "exact"
        assert impl.linear_name == "IMPLEMENTING"  # preserves Linear's casing


# --- step_resolve_near_matches ---------------------------------------------


class TestResolveNearMatches:
    def _state_with_one_near(self) -> WizardState:
        s = WizardState()
        s.state_diff = [
            StateDiffEntry(canonical_name="Ready", linear_name="Ready", category="exact"),
            StateDiffEntry(
                canonical_name="Implementing",
                linear_name="In Progress",
                category="near",
            ),
            StateDiffEntry(canonical_name="Verifying", linear_name=None, category="missing"),
        ]
        return s

    def test_accept_keeps_alias(self):
        s = self._state_with_one_near()
        prompter = ScriptedPrompter([True])  # accept the one near match
        steps.step_resolve_near_matches(s, prompter)
        impl = next(e for e in s.state_diff if e.canonical_name == "Implementing")
        assert impl.category == "near"
        assert impl.linear_name == "In Progress"

    def test_reject_demotes_to_missing(self):
        s = self._state_with_one_near()
        prompter = ScriptedPrompter([False])
        steps.step_resolve_near_matches(s, prompter)
        impl = next(e for e in s.state_diff if e.canonical_name == "Implementing")
        assert impl.category == "missing"
        assert impl.linear_name is None

    def test_skips_non_near_entries(self):
        """Exact and missing entries don't trigger prompts (queue should not be drained)."""
        s = self._state_with_one_near()
        prompter = ScriptedPrompter([True])  # exactly one answer
        steps.step_resolve_near_matches(s, prompter)
        # Only the one "near" should have consumed an answer.
        assert prompter.remaining == 0


# --- step_resolve_prompts_root ---------------------------------------------


class TestResolvePromptsRoot:
    def test_happy_path(self, tmp_path, monkeypatch):
        # Build a fake autosymph package layout: <repo>/src/autosymph/__init__.py
        # and <repo>/prompts/{implement,verify,merge,global}.md
        repo = tmp_path / "fake_repo"
        pkg = repo / "src" / "autosymph"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        prompts = repo / "prompts"
        prompts.mkdir()
        for name in ("implement.md", "verify.md", "merge.md", "global.md"):
            (prompts / name).write_text(f"# {name}\n")

        monkeypatch.setattr("autosymph.__file__", str(pkg / "__init__.py"))
        s = WizardState()
        steps.step_resolve_prompts_root(s)
        assert s.prompts_root_abs == prompts.resolve()

    def test_missing_files_raises(self, tmp_path, monkeypatch):
        repo = tmp_path / "fake_repo"
        pkg = repo / "src" / "autosymph"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        # Create only some prompt files.
        prompts = repo / "prompts"
        prompts.mkdir()
        (prompts / "implement.md").write_text("")
        # verify.md, merge.md, global.md are missing.

        monkeypatch.setattr("autosymph.__file__", str(pkg / "__init__.py"))
        with pytest.raises(WizardPromptsRootMissing) as exc_info:
            steps.step_resolve_prompts_root(WizardState())
        assert "verify.md" in exc_info.value.missing_files
        assert "merge.md" in exc_info.value.missing_files


# --- step_choose_template --------------------------------------------------


class TestChooseTemplate:
    def test_default_ios_when_simulator_needed(self):
        s = WizardState()
        s.needs_simulator = True
        prompter = ScriptedPrompter([0])  # pick first (which is "ios" with [default] note)
        steps.step_choose_template(s, prompter)
        assert s.template_choice == "ios"

    def test_default_web_when_no_simulator(self):
        s = WizardState()
        s.needs_simulator = False
        prompter = ScriptedPrompter([1])  # pick the web option
        steps.step_choose_template(s, prompter)
        assert s.template_choice == "web"

    def test_user_can_override_to_web(self):
        s = WizardState()
        s.needs_simulator = True  # default would be ios
        prompter = ScriptedPrompter([1])  # pick web
        steps.step_choose_template(s, prompter)
        assert s.template_choice == "web"


# --- step_collect_repo_path ------------------------------------------------


class TestCollectRepoPath:
    def test_valid_git_repo_accepted(self, tmp_path):
        # Make a real git repo
        import subprocess as sp
        sp.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
        s = WizardState()
        prompter = ScriptedPrompter([str(tmp_path)])
        steps.step_collect_repo_path(s, prompter)
        # repo_path is normalized to git toplevel
        assert s.repo_path == tmp_path.resolve()

    def test_nonexistent_path_loops_then_aborts(self, tmp_path):
        s = WizardState()
        prompter = ScriptedPrompter([
            str(tmp_path / "does-not-exist"),  # path doesn't exist
            False,                              # don't try again
        ])
        with pytest.raises(WizardAborted):
            steps.step_collect_repo_path(s, prompter)


# --- step_build_plan -------------------------------------------------------


class TestBuildPlan:
    def _populated_state(self, tmp_path):
        s = WizardState()
        s.linear_api_key = "lin_api_test"
        s.project_id = "p1"
        s.project_name = "Demo App"
        s.project_slug = "demo-app"
        s.team_id = "t1"
        s.repo_path = tmp_path
        s.template_choice = "web"
        s.prompts_root_abs = Path("/abs/path/to/prompts")
        s.needs_simulator = False
        s.state_diff = [
            StateDiffEntry(canonical_name="Ready", linear_name="Ready", category="exact"),
            StateDiffEntry(
                canonical_name="Implementing",
                linear_name="In Progress",
                category="near",
            ),
            StateDiffEntry(canonical_name="Verifying", linear_name=None, category="missing"),
            StateDiffEntry(canonical_name="Investigating", linear_name=None, category="missing"),
            StateDiffEntry(canonical_name="In Review", linear_name="In Review", category="exact"),
            StateDiffEntry(canonical_name="Rework", linear_name=None, category="missing"),
            StateDiffEntry(canonical_name="Merging", linear_name=None, category="missing"),
            StateDiffEntry(canonical_name="Blocked", linear_name=None, category="missing"),
            StateDiffEntry(canonical_name="Done", linear_name="Done", category="exact"),
            StateDiffEntry(canonical_name="Canceled", linear_name="Canceled", category="exact"),
            StateDiffEntry(canonical_name="Duplicate", linear_name="Duplicate", category="exact"),
        ]
        return s

    def test_planned_mutations_match_missing_count(self, tmp_path, tmp_config_dir):
        s = self._populated_state(tmp_path)
        steps.step_build_plan(s)
        # 5 missing entries → 5 mutations
        missing_canonicals = [e.canonical_name for e in s.state_diff if e.category == "missing"]
        assert len(s.planned_mutations) == 5
        assert {m.canonical_name for m in s.planned_mutations} == set(missing_canonicals)

    def test_planned_writes_paths(self, tmp_path, tmp_config_dir):
        s = self._populated_state(tmp_path)
        steps.step_build_plan(s)
        paths = list(s.planned_writes.keys())
        assert any("devices" in str(p) for p in paths)
        assert any("projects" in str(p) and "demo-app.yaml" in str(p) for p in paths)
        assert any(p.name == "local.env" for p in paths)

    def test_project_yaml_uses_alias_name_for_near(self, tmp_path, tmp_config_dir):
        s = self._populated_state(tmp_path)
        steps.step_build_plan(s)
        proj_path = next(p for p in s.planned_writes if "projects" in str(p))
        content = s.planned_writes[proj_path]
        # near match: Linear has "In Progress", autosymph slot is "active".
        assert 'active: "In Progress"' in content
        # missing slot: canonical name will be created.
        assert 'verifying: "Verifying"' in content
        # terminals: list-form
        assert 'terminal: ["Done", "Canceled", "Duplicate"]' in content

    def test_local_env_includes_braintrust_when_set(self, tmp_path, tmp_config_dir):
        s = self._populated_state(tmp_path)
        s.braintrust_api_key = "bt_xxx"
        steps.step_build_plan(s)
        env_path = next(p for p in s.planned_writes if p.name == "local.env")
        content = s.planned_writes[env_path]
        assert "LINEAR_API_KEY=lin_api_test" in content
        assert "BRAINTRUST_API_KEY=bt_xxx" in content

    def test_local_env_excludes_braintrust_when_unset(self, tmp_path, tmp_config_dir):
        s = self._populated_state(tmp_path)
        # braintrust_api_key stays None
        steps.step_build_plan(s)
        env_path = next(p for p in s.planned_writes if p.name == "local.env")
        content = s.planned_writes[env_path]
        assert "BRAINTRUST_API_KEY" not in content

    def test_device_yaml_includes_simulator_block_when_selected(self, tmp_path, tmp_config_dir):
        s = self._populated_state(tmp_path)
        s.needs_simulator = True
        s.selected_simulators = ["iPhone 17 Pro"]
        steps.step_build_plan(s)
        dev_path = next(p for p in s.planned_writes if "devices" in str(p))
        content = s.planned_writes[dev_path]
        assert "ios_simulator:" in content
        assert "iPhone 17 Pro" in content

    def test_device_yaml_omits_simulator_block_when_no_sims(self, tmp_path, tmp_config_dir):
        s = self._populated_state(tmp_path)
        s.needs_simulator = False
        steps.step_build_plan(s)
        dev_path = next(p for p in s.planned_writes if "devices" in str(p))
        content = s.planned_writes[dev_path]
        assert "ios_simulator" not in content


# --- step_execute_linear_mutations ----------------------------------------


class TestExecuteMutations:
    def test_serialized_creates_log_succeeded(self, tmp_config_dir):
        s = WizardState()
        s.team_id = "t1"
        s.planned_mutations = [
            PlannedMutation(canonical_name="Implementing", color="#1", position=1.0, state_type="started"),
            PlannedMutation(canonical_name="Verifying", color="#2", position=2.0, state_type="started"),
        ]
        client = _stub_client()
        responses = [
            {"workflowStateCreate": {"success": True, "workflowState": {"id": "id-1"}}},
            {"workflowStateCreate": {"success": True, "workflowState": {"id": "id-2"}}},
        ]
        with patch.object(client, "_query", new=AsyncMock(side_effect=responses)):
            steps.step_execute_linear_mutations(s, client)
        log = (tmp_config_dir / "wizard-mutations.log").read_text()
        assert "Implementing" in log
        assert "id-1" in log
        assert "Verifying" in log
        assert "id-2" in log

    def test_partial_failure_records_succeeded(self, tmp_config_dir):
        s = WizardState()
        s.team_id = "t1"
        s.planned_mutations = [
            PlannedMutation(canonical_name="Implementing", color="#1", position=1.0, state_type="started"),
            PlannedMutation(canonical_name="Verifying", color="#2", position=2.0, state_type="started"),
            PlannedMutation(canonical_name="Investigating", color="#3", position=3.0, state_type="started"),
        ]
        client = _stub_client()
        responses = [
            {"workflowStateCreate": {"success": True, "workflowState": {"id": "id-1"}}},
            {"workflowStateCreate": {"success": True, "workflowState": {"id": "id-2"}}},
            RuntimeError("API blew up"),
        ]
        with patch.object(client, "_query", new=AsyncMock(side_effect=responses)):
            with pytest.raises(WizardLinearMutationFailed) as exc_info:
                steps.step_execute_linear_mutations(s, client)
        assert exc_info.value.failed_state == "Investigating"
        assert exc_info.value.succeeded_states == ["Implementing", "Verifying"]
        log = (tmp_config_dir / "wizard-mutations.log").read_text()
        assert "Implementing" in log
        assert "Verifying" in log
        assert "Investigating" not in log


# --- step_atomic_writes ----------------------------------------------------


class TestAtomicWrites:
    def test_all_files_written(self, tmp_path):
        a = tmp_path / "a.yaml"
        b = tmp_path / "sub" / "b.yaml"
        env = tmp_path / "local.env"
        s = WizardState()
        s.planned_writes = {a: "a content\n", b: "b content\n", env: "K=V\n"}
        steps.step_atomic_writes(s)
        assert a.read_text() == "a content\n"
        assert b.read_text() == "b content\n"
        assert env.read_text() == "K=V\n"
        # local.env should have 0600 perms
        assert (env.stat().st_mode & 0o777) == 0o600

    def test_no_tmp_files_left(self, tmp_path):
        target = tmp_path / "out.yaml"
        s = WizardState()
        s.planned_writes = {target: "x"}
        steps.step_atomic_writes(s)
        assert not (tmp_path / "out.yaml.tmp").exists()
