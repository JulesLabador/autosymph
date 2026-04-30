"""Tests for WorkspaceManager .autosymph/ propagation.

Regression: see autosymph/docs/postmortems/2026-04-25-auth-md-missing-from-worktree.md.

`.autosymph/` is gitignored, so `git worktree add` doesn't carry the directory.
WorkspaceManager.create() must explicitly copy it (mirroring the
settings.local.json copy already in place) — otherwise verify agents see no
auth.md / fixtures.md / ios.md and fail with "auth.md missing".
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from autosymph.config import HooksConfig, WorkspaceConfig
from autosymph.workspace import WorkspaceManager


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.check_call(
        cmd, cwd=str(cwd),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
             "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
    )


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """Make a temporary git repo with `.autosymph/verify/auth.md` (gitignored)
    and one tracked file so worktree add succeeds."""
    repo = tmp_path / "src-repo"
    repo.mkdir()
    _run(["git", "init", "-b", "main"], cwd=repo)

    # Tracked file so the repo has at least one commit
    (repo / "README.md").write_text("hello\n")
    (repo / ".gitignore").write_text(".autosymph/\n.claude/settings.local.json\n")
    _run(["git", "add", "README.md", ".gitignore"], cwd=repo)
    _run(["git", "commit", "-m", "init"], cwd=repo)

    # The fixture autosymph dir — gitignored, must still propagate to worktrees
    autosymph = repo / ".autosymph" / "verify"
    autosymph.mkdir(parents=True)
    (autosymph / "auth.md").write_text(
        "# Auth\nUse e2e-login bypass at /api/auth/e2e-login\n"
    )
    (autosymph / "ios.md").write_text("# iOS\nDefault build instructions.\n")

    # Also a settings.local.json (existing copy logic should still work)
    settings_dir = repo / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.local.json").write_text('{"mcpServers":{}}\n')

    return repo


def _make_manager(repo: Path, root: Path) -> WorkspaceManager:
    cfg = WorkspaceConfig(repo=str(repo), root=str(root))
    hooks = HooksConfig(before_run="", after_create="", after_complete="",
                        on_failure="", timeout_ms=30000)
    return WorkspaceManager(cfg, hooks, project_slug="testproj")


def test_parse_branch_list_line_strips_worktree_marker():
    """Branches checked out in another worktree are prefixed with '+ '."""
    assert (
        WorkspaceManager._parse_branch_list_line("+ feat/imp-388")
        == "feat/imp-388"
    )
    assert (
        WorkspaceManager._parse_branch_list_line("* feat/imp-388")
        == "feat/imp-388"
    )
    assert (
        WorkspaceManager._parse_branch_list_line("  feat/imp-388")
        == "feat/imp-388"
    )
    assert WorkspaceManager._parse_branch_list_line("   ") is None


@pytest.mark.asyncio
async def test_autosymph_dir_copied_into_worktree(tmp_repo: Path, tmp_path: Path):
    """Regression for IMP-374: .autosymph/verify/auth.md must reach the worktree."""
    root = tmp_path / "ws-root"
    mgr = _make_manager(tmp_repo, root)

    ws = await mgr.create("imp-test")

    auth_md = ws.path / ".autosymph" / "verify" / "auth.md"
    ios_md = ws.path / ".autosymph" / "verify" / "ios.md"

    assert auth_md.exists(), (
        f"auth.md not found in worktree at {auth_md}. "
        "If this fails, the IMP-374 'auth.md missing' bug is back: gitignored "
        ".autosymph/ never made it across the worktree boundary."
    )
    assert ios_md.exists(), "ios.md should also be propagated"

    content = auth_md.read_text()
    assert "e2e-login" in content, "auth.md content corrupted in copy"

    # cleanup
    await mgr.cleanup(ws)


@pytest.mark.asyncio
async def test_settings_local_still_copied(tmp_repo: Path, tmp_path: Path):
    """Don't regress the existing settings.local.json copy behavior."""
    root = tmp_path / "ws-root"
    mgr = _make_manager(tmp_repo, root)

    ws = await mgr.create("imp-test2")

    settings = ws.path / ".claude" / "settings.local.json"
    assert settings.exists(), "settings.local.json copy regression"

    await mgr.cleanup(ws)


@pytest.mark.asyncio
async def test_autosymph_absent_does_not_error(tmp_path: Path):
    """If source repo has no .autosymph/ dir, create() must still succeed."""
    repo = tmp_path / "bare-repo"
    repo.mkdir()
    _run(["git", "init", "-b", "main"], cwd=repo)
    (repo / "README.md").write_text("x\n")
    _run(["git", "add", "README.md"], cwd=repo)
    _run(["git", "commit", "-m", "init"], cwd=repo)

    root = tmp_path / "ws-root"
    mgr = _make_manager(repo, root)

    ws = await mgr.create("imp-bare")

    # Worktree exists, no .autosymph/ leaked into it
    assert ws.path.exists()
    assert not (ws.path / ".autosymph").exists()

    await mgr.cleanup(ws)


@pytest.mark.asyncio
async def test_recreate_replaces_stale_autosymph(tmp_repo: Path, tmp_path: Path):
    """If the worktree already had a stale .autosymph/, recreating must use
    the source repo's current contents (not stale)."""
    root = tmp_path / "ws-root"
    mgr = _make_manager(tmp_repo, root)

    # First create
    ws1 = await mgr.create("imp-restale")
    auth1 = (ws1.path / ".autosymph" / "verify" / "auth.md").read_text()
    assert "e2e-login" in auth1

    # Edit source after first create
    (tmp_repo / ".autosymph" / "verify" / "auth.md").write_text(
        "# Auth v2\nNew bypass token.\n"
    )

    # Second create — should pick up new content
    ws2 = await mgr.create("imp-restale")
    auth2 = (ws2.path / ".autosymph" / "verify" / "auth.md").read_text()
    assert "v2" in auth2, "stale auth.md was not refreshed on recreate"

    await mgr.cleanup(ws2)
