"""End-to-end wizard tests with a stub LinearClient and ScriptedPrompter.

These exercise ``run_wizard`` through the full step pipeline. The Linear API
is faked by patching ``LinearClient._query`` with an ``AsyncMock`` that
returns canned GraphQL responses.

The subprocess-based real-CLI test (per AC12) lives in
``test_wizard_cli_stdin.py``.
"""
from __future__ import annotations

import socket
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from autosymph.linear_client import LinearClient
from autosymph.wizard.prompter import ScriptedPrompter
from autosymph.wizard.runner import run_wizard


# --- Helpers ---------------------------------------------------------------


def _hostname() -> str:
    return socket.gethostname().split(".")[0].lower()


def _empty_workspace_responses(team_id: str = "team-1") -> list[dict]:
    """Sequence of canned responses for a happy-path run against an EMPTY workspace.

    Order of _query calls during run_wizard happy path:
      1. resolve_viewer (in step_collect_linear_key, via temporary client)
      2. resolve_viewer (NOT called again — viewer_id was set on state, but
         the live client built in runner.py has its own resolve_viewer cache,
         and we only need viewer for resolve_assignee_filter which we don't hit)
      Actually: looking at the code path, the temporary probe client closes
      its httpx session in `step_collect_linear_key`. Then runner.py builds a
      NEW LinearClient (state.linear_api_key, project_name="__wizard_pending__").
      Then step_select_project calls list_projects (paginated).
      Then step_fetch_workspace calls resolve_team_for_project (one query),
      fetch_workspace_states, fetch_workspace_labels.
      Then step_execute_linear_mutations calls create_workflow_state per
      missing state (8 of them for an empty workspace).

    For an empty workspace (no existing states), the diff yields ALL 11
    canonical states as missing, but terminals (Done, Canceled, Duplicate)
    are still planned for creation since they're in REQUIRED_V1.
    """
    project_response = {
        "projects": {
            "nodes": [
                {
                    "id": "p1",
                    "name": "Demo Project",
                    "slugId": "demo",
                    "teams": {"nodes": [{"id": team_id, "name": "Demo Team"}]},
                },
            ],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }
    }
    team_resolution = {
        "project": {"teams": {"nodes": [{"id": team_id, "name": "Demo Team"}]}}
    }
    states_response: dict = {"team": {"states": {"nodes": []}}}  # empty workspace
    labels_response: dict = {"issueLabels": {"nodes": []}}

    # 11 states in REQUIRED_V1 — all missing → 11 mutations
    mutation_responses = [
        {
            "workflowStateCreate": {
                "success": True,
                "workflowState": {"id": f"state-{i}"},
            }
        }
        for i in range(11)
    ]

    return [
        project_response,        # list_projects
        team_resolution,         # resolve_team_for_project
        states_response,         # fetch_workspace_states
        labels_response,         # fetch_workspace_labels
        *mutation_responses,
    ]


def _make_fake_autosymph_repo(tmp_path: Path) -> Path:
    """Create a fake repo layout with autosymph package + prompts/.

    Returns the path to the prompts/ dir. Caller monkeypatches
    autosymph.__file__ to the fake package's __init__.py.
    """
    repo = tmp_path / "fake_autosymph_repo"
    pkg = repo / "src" / "autosymph"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    prompts = repo / "prompts"
    prompts.mkdir()
    for name in ("implement.md", "verify.md", "merge.md", "global.md"):
        (prompts / name).write_text(f"# {name}\n")
    return prompts


def _make_target_repo(tmp_path: Path, *, ios: bool = False) -> Path:
    """Create a target git repo for the wizard's repo-path step."""
    repo = tmp_path / ("target_ios" if ios else "target_web")
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    if ios:
        # Make it look iOS-y so _workspace_needs_simulator returns True.
        (repo / "MyApp.xcodeproj").mkdir()
    return repo


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Tmp config dir + restored env vars; isolates the test from ~/.autosymph/."""
    cfg = tmp_path / "config"
    monkeypatch.setenv("AUTOSYMPH_CONFIG_DIR", str(cfg))
    # The probe client in step_collect_linear_key reads $LINEAR_API_KEY for default.
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    return cfg


# --- Tests -----------------------------------------------------------------


class TestHappyPath:
    def test_empty_workspace_full_run(self, tmp_path, monkeypatch, isolated_env):
        target_repo = _make_target_repo(tmp_path, ios=False)
        prompts_dir = _make_fake_autosymph_repo(tmp_path)
        monkeypatch.setattr(
            "autosymph.__file__",
            str(prompts_dir.parent / "src" / "autosymph" / "__init__.py"),
        )

        # Stub xcrun simctl so detect_simulators can't be flaky on CI.
        # Web target → _workspace_needs_simulator returns False; simctl never called.

        # Scripted answers in the order steps consume them:
        # 1. ask: Linear API key
        # 2. select: project (index 0 = "Demo Project")
        # 3. (no near-match prompts — empty workspace, all "missing")
        # 4. ask: repo path
        # 5. (no simulator prompts — needs_simulator False)
        # 6. select: template (web is index 1; default-annotated)
        # 7. confirm: configure Braintrust (no)
        # 8. confirm: combined preview (yes)
        prompter = ScriptedPrompter([
            "lin_api_test_key",          # Linear API key
            0,                            # select Demo Project
            str(target_repo),             # repo path
            1,                            # template (web is index 1 in ['ios','web'])
            False,                        # skip Braintrust
            True,                         # confirm combined preview
        ])

        # First call inside step_collect_linear_key is the temporary probe
        # client's resolve_viewer. Then a SECOND LinearClient is built in
        # runner.py. We need to patch BOTH. Simplest approach: patch
        # LinearClient._query at the class level so every instance shares the
        # mock.
        viewer_response = {"viewer": {"id": "viewer-1", "name": "Test User"}}
        responses = [viewer_response] + _empty_workspace_responses()

        with patch.object(
            LinearClient, "_query", new=AsyncMock(side_effect=responses)
        ):
            exit_code = run_wizard(prompter)

        assert exit_code == 0

        # Verify config files exist with expected content.
        cfg_dir = isolated_env
        device_yaml = cfg_dir / "devices" / f"{_hostname()}.yaml"
        project_yaml = cfg_dir / "projects" / "demo-project.yaml"
        local_env = cfg_dir / "local.env"
        assert device_yaml.exists()
        assert project_yaml.exists()
        assert local_env.exists()

        device_content = device_yaml.read_text()
        assert f'machine_name: "{_hostname()}"' in device_content
        assert "demo-project:" in device_content
        assert str(target_repo) in device_content
        # Web project → no simulator block
        assert "ios_simulator" not in device_content

        project_content = project_yaml.read_text()
        assert 'project: "Demo Project"' in project_content
        # All states are canonical (empty workspace → all missing → all created)
        assert 'active: "Implementing"' in project_content
        assert 'verifying: "Verifying"' in project_content
        assert 'investigating: "Investigating"' in project_content
        assert 'terminal: ["Done", "Canceled", "Duplicate"]' in project_content
        # prompts.root is an absolute path
        assert f'root: "{prompts_dir.resolve()}"' in project_content

        env_content = local_env.read_text()
        assert "LINEAR_API_KEY=lin_api_test_key" in env_content
        assert "BRAINTRUST_API_KEY" not in env_content

        # Mutation log records all 11 created states
        log = (cfg_dir / "wizard-mutations.log").read_text()
        for canonical in ("Ready", "Implementing", "Verifying", "Done"):
            assert canonical in log


class TestPromptsRootMissing:
    def test_exits_before_any_filesystem_or_linear_changes(
        self, tmp_path, monkeypatch, isolated_env
    ):
        # Build a fake autosymph layout with prompts/ MISSING required files.
        repo = tmp_path / "broken_install"
        pkg = repo / "src" / "autosymph"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        # Don't create prompts/ at all.
        monkeypatch.setattr("autosymph.__file__", str(pkg / "__init__.py"))

        target_repo = _make_target_repo(tmp_path, ios=False)
        prompter = ScriptedPrompter([
            "lin_api_test_key",
            0,                       # select project
            str(target_repo),        # repo path
            1,                       # template web
            False,                   # skip braintrust (won't reach this anyway)
            True,                    # combined preview confirm (won't reach)
        ])
        viewer_response = {"viewer": {"id": "v", "name": "x"}}
        # We won't get past resolve_prompts_root.
        responses = [viewer_response] + _empty_workspace_responses()
        with patch.object(
            LinearClient, "_query", new=AsyncMock(side_effect=responses)
        ):
            exit_code = run_wizard(prompter)
        assert exit_code == 1
        # No config files written
        cfg = isolated_env
        assert not (cfg / "devices").exists() or not list((cfg / "devices").glob("*.yaml"))
        assert not (cfg / "projects").exists() or not list((cfg / "projects").glob("*.yaml"))
        assert not (cfg / "local.env").exists()


class TestUserAbortAtPreview:
    def test_abort_at_combined_preview_writes_nothing(
        self, tmp_path, monkeypatch, isolated_env
    ):
        target_repo = _make_target_repo(tmp_path, ios=False)
        prompts_dir = _make_fake_autosymph_repo(tmp_path)
        monkeypatch.setattr(
            "autosymph.__file__",
            str(prompts_dir.parent / "src" / "autosymph" / "__init__.py"),
        )

        prompter = ScriptedPrompter([
            "lin_api_test_key",
            0,                       # select project
            str(target_repo),        # repo path
            1,                       # template web
            False,                   # skip braintrust
            False,                   # ABORT at combined preview
        ])
        viewer_response = {"viewer": {"id": "v", "name": "x"}}
        # Mutation responses MUST NOT be consumed since we abort before then.
        # Provide them anyway so a bug that proceeds gets exposed via the
        # mutation log assertion below.
        responses = [viewer_response] + _empty_workspace_responses()
        with patch.object(
            LinearClient, "_query", new=AsyncMock(side_effect=responses)
        ) as mock_query:
            exit_code = run_wizard(prompter)
        # Abort returns 0 with "no changes made" message per AC3.
        assert exit_code == 0

        # No mutation calls happened (only reads).
        # First 4 reads: list_projects, resolve_team_for_project, fetch_workspace_states, fetch_workspace_labels
        # Plus the initial resolve_viewer = 5 total.
        assert mock_query.call_count <= 5

        # No user-config files written
        cfg = isolated_env
        assert not (cfg / "local.env").exists()
        assert not list((cfg / "devices").glob("*.yaml")) if (cfg / "devices").exists() else True

        # No mutation log file written
        assert not (cfg / "wizard-mutations.log").exists()


class TestLinearKeyInvalid:
    def test_invalid_key_aborts_before_filesystem_writes(
        self, tmp_path, monkeypatch, isolated_env
    ):
        prompts_dir = _make_fake_autosymph_repo(tmp_path)
        monkeypatch.setattr(
            "autosymph.__file__",
            str(prompts_dir.parent / "src" / "autosymph" / "__init__.py"),
        )

        # First call to _query (in resolve_viewer) raises 401-equivalent.
        # User says "no" to retry → abort.
        prompter = ScriptedPrompter([
            "lin_api_BAD_key",       # API key
            False,                   # don't try again after failure → abort
        ])
        with patch.object(
            LinearClient,
            "_query",
            new=AsyncMock(side_effect=RuntimeError("HTTP 401 Unauthorized")),
        ):
            exit_code = run_wizard(prompter)
        # Aborted by user → exit 0 (per WizardAborted handling).
        assert exit_code == 0
        # No config files
        cfg = isolated_env
        assert not (cfg / "local.env").exists()


class TestNearMatchAcceptedAndRejected:
    @pytest.mark.parametrize("accept", [True, False])
    def test_near_match_branch(self, tmp_path, monkeypatch, isolated_env, accept):
        """
        Linear has 'In Progress'. Wizard prompts: use it for 'Implementing' slot?
          accept=True  → project YAML uses 'In Progress'; no Implementing creation.
          accept=False → demote to missing; Implementing IS created.
        """
        target_repo = _make_target_repo(tmp_path, ios=False)
        prompts_dir = _make_fake_autosymph_repo(tmp_path)
        monkeypatch.setattr(
            "autosymph.__file__",
            str(prompts_dir.parent / "src" / "autosymph" / "__init__.py"),
        )

        # Workspace has only "In Progress" — alias for Implementing.
        viewer_response = {"viewer": {"id": "v", "name": "x"}}
        project_response = {
            "projects": {
                "nodes": [
                    {
                        "id": "p1",
                        "name": "Demo Project",
                        "slugId": "demo",
                        "teams": {"nodes": [{"id": "t1", "name": "Team"}]},
                    },
                ],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }
        team_resolution = {"project": {"teams": {"nodes": [{"id": "t1", "name": "Team"}]}}}
        states_response = {"team": {"states": {"nodes": [{"id": "s1", "name": "In Progress"}]}}}
        labels_response = {"issueLabels": {"nodes": []}}

        # accept=True  → 10 missing (everything except Implementing's alias)
        # accept=False → 11 missing (alias rejected → Implementing also created)
        n_mutations = 10 if accept else 11
        mutation_responses = [
            {"workflowStateCreate": {"success": True, "workflowState": {"id": f"id-{i}"}}}
            for i in range(n_mutations)
        ]
        responses = [
            viewer_response,
            project_response,
            team_resolution,
            states_response,
            labels_response,
            *mutation_responses,
        ]

        prompter = ScriptedPrompter([
            "lin_api_test_key",
            0,                # select project
            accept,           # confirm: use 'In Progress' for 'Implementing'?
            str(target_repo), # repo path
            1,                # template web
            False,            # skip braintrust
            True,             # combined preview confirm
        ])
        with patch.object(LinearClient, "_query", new=AsyncMock(side_effect=responses)):
            exit_code = run_wizard(prompter)
        assert exit_code == 0

        proj_yaml = isolated_env / "projects" / "demo-project.yaml"
        content = proj_yaml.read_text()
        if accept:
            # Project YAML uses Linear's existing name in the active slot.
            assert 'active: "In Progress"' in content
        else:
            assert 'active: "Implementing"' in content


class TestExistingConfigDetected:
    def test_existing_device_yaml_blocks_run(self, tmp_path, monkeypatch, isolated_env):
        # Pre-create a device YAML for this hostname.
        cfg = isolated_env
        (cfg / "devices").mkdir(parents=True)
        (cfg / "devices" / f"{_hostname()}.yaml").write_text("machine_name: x\n")
        # Provide minimal answers — wizard should exit before consuming any.
        prompter = ScriptedPrompter([])
        exit_code = run_wizard(prompter)
        assert exit_code == 1
        # Prompter queue should not have been consumed.
        assert prompter.remaining == 0

    def test_existing_local_env_blocks_run(self, tmp_path, monkeypatch, isolated_env):
        cfg = isolated_env
        cfg.mkdir(parents=True)
        (cfg / "local.env").write_text("LINEAR_API_KEY=existing\n")
        prompter = ScriptedPrompter([])
        assert run_wizard(prompter) == 1
        assert prompter.remaining == 0

    def test_existing_project_yaml_blocks_run(self, tmp_path, monkeypatch, isolated_env):
        cfg = isolated_env
        (cfg / "projects").mkdir(parents=True)
        (cfg / "projects" / "anything.yaml").write_text("tracker: {}\n")
        prompter = ScriptedPrompter([])
        assert run_wizard(prompter) == 1
        assert prompter.remaining == 0


class TestLinearMutationFailure:
    def test_partial_failure_no_files_written(self, tmp_path, monkeypatch, isolated_env):
        target_repo = _make_target_repo(tmp_path, ios=False)
        prompts_dir = _make_fake_autosymph_repo(tmp_path)
        monkeypatch.setattr(
            "autosymph.__file__",
            str(prompts_dir.parent / "src" / "autosymph" / "__init__.py"),
        )

        prompter = ScriptedPrompter([
            "lin_api_test_key",
            0,                   # select project
            str(target_repo),    # repo path
            1,                   # template web
            False,               # skip braintrust
            True,                # combined preview confirm
        ])

        viewer_response = {"viewer": {"id": "v", "name": "x"}}
        # Empty workspace → 11 mutations planned. First 2 succeed, third fails.
        responses = [
            viewer_response,
            *_empty_workspace_responses(),
        ]
        # Replace the 3rd mutation response with an exception.
        # mutation responses start at index 5 (after viewer + 4 reads).
        responses[5 + 2] = RuntimeError("Linear server error 500")

        with patch.object(LinearClient, "_query", new=AsyncMock(side_effect=responses)):
            exit_code = run_wizard(prompter)
        assert exit_code == 1

        cfg = isolated_env
        # No user-config files (R14 guarantee)
        assert not (cfg / "local.env").exists()
        if (cfg / "devices").exists():
            assert not list((cfg / "devices").glob("*.yaml"))
        if (cfg / "projects").exists():
            assert not list((cfg / "projects").glob("*.yaml"))
        # Mutation log captures the 2 successes
        log = (cfg / "wizard-mutations.log").read_text()
        # First 2 canonical names from REQUIRED_V1 are Ready and Implementing.
        assert "Ready" in log
        assert "Implementing" in log
