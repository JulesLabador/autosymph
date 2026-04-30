"""Git worktree lifecycle — create, clean up, run hooks.

Creates isolated git worktrees per issue under the configured workspaces root.
Reuses existing feat/{slug} branches on rework. Runs lifecycle hooks with timeout.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from autosymph.config import HooksConfig, WorkspaceConfig

logger = logging.getLogger(__name__)


@dataclass
class Workspace:
    """An isolated git worktree for a single issue."""

    issue_id: str
    path: Path
    branch: str


class WorkspaceManager:
    """Manages git worktrees for agent runs.

    Worktree layout:
        {root}/{project-slug}/{issue-slug}/  — namespaced (multi-instance)
        {root}/{issue-slug}/                 — flat (single-instance, backward compat)

    Branch naming:
        feat/{issue-slug}        — new branch from main
        feat/{issue-slug}-*      — reused variant (e.g. feat/imp-251-hydrate-ids)
    """

    def __init__(
        self,
        config: WorkspaceConfig,
        hooks: HooksConfig,
        project_slug: str | None = None,
    ) -> None:
        self.root = Path(self._resolve(config.root)).expanduser().resolve()
        self.repo = Path(self._resolve(config.repo)).expanduser().resolve()
        self.hooks = hooks
        self.hook_timeout_s = hooks.timeout_ms / 1000
        self.project_slug = project_slug

    async def create(self, issue_slug: str) -> Workspace:
        """Create or reuse a worktree for an issue.

        1. Check for existing feat/{slug} or feat/{slug}-* branch
        2. If found, create worktree from that branch
        3. If not, create new branch from main
        """
        if self.project_slug:
            workspace_path = self.root / self.project_slug / issue_slug
        else:
            workspace_path = self.root / issue_slug
        workspace_path.parent.mkdir(parents=True, exist_ok=True)

        # Safety: only operate inside configured root
        if not str(workspace_path).startswith(str(self.root)):
            raise ValueError(f"Workspace path {workspace_path} is outside root {self.root}")

        # If worktree already exists, remove it first (stale from previous run)
        if workspace_path.exists():
            logger.info("Removing stale worktree at %s", workspace_path)
            try:
                await self._run_git("worktree", "remove", str(workspace_path), "--force")
            except RuntimeError:
                # Not a valid worktree (leftover directory from crash) — just remove it
                import shutil
                shutil.rmtree(workspace_path, ignore_errors=True)
                logger.info("Removed leftover directory %s", workspace_path)
            await self._run_git("worktree", "prune")

        # Look for existing branch
        existing_branch = await self._find_existing_branch(issue_slug)

        if existing_branch:
            logger.info("Reusing existing branch %s for %s", existing_branch, issue_slug)
            await self._run_git("worktree", "add", str(workspace_path), existing_branch)
            branch = existing_branch
        else:
            branch = f"feat/{issue_slug}"
            logger.info("Creating new branch %s for %s", branch, issue_slug)
            await self._run_git(
                "worktree", "add", str(workspace_path), "-b", branch, "main",
            )

        ws = Workspace(issue_id=issue_slug, path=workspace_path, branch=branch)

        # Copy settings.local.json to worktree (gitignored, has MCP server config)
        local_settings = self.repo / ".claude" / "settings.local.json"
        if local_settings.exists():
            target = workspace_path / ".claude" / "settings.local.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(str(local_settings), str(target))
            logger.info("Copied settings.local.json to worktree")

        # Copy .autosymph/ to worktree (gitignored: holds verify fixtures like
        # auth.md, ios.md, fixtures.md). Without this, verify agents see no
        # auth fixtures and report "auth.md missing" — see postmortems
        # 2026-04-25-auth-md-missing-from-worktree.md.
        autosymph_dir = self.repo / ".autosymph"
        if autosymph_dir.exists() and autosymph_dir.is_dir():
            target_autosymph = workspace_path / ".autosymph"
            import shutil
            if target_autosymph.exists():
                shutil.rmtree(str(target_autosymph))
            shutil.copytree(str(autosymph_dir), str(target_autosymph))
            logger.info("Copied .autosymph/ fixtures to worktree (%s)", autosymph_dir)

        # Run after_create hook
        await self.run_hook("after_create", ws)

        return ws

    async def prepare(self, workspace: Workspace) -> None:
        """Run before_run hook — stash dirty tree, fetch + rebase."""
        await self.run_hook("before_run", workspace)

    async def cleanup(self, workspace: Workspace, failed: bool = False) -> None:
        """Remove a worktree and prune. Runs after_complete or on_failure hook."""
        hook = "on_failure" if failed else "after_complete"
        try:
            await self.run_hook(hook, workspace)
        except Exception:
            logger.exception("Hook '%s' failed for %s", hook, workspace.issue_id)

        # Remove worktree
        try:
            await self._run_git("worktree", "remove", str(workspace.path), "--force")
        except RuntimeError:
            logger.warning("Failed to remove worktree %s — may need manual cleanup", workspace.path)

        await self._run_git("worktree", "prune")
        logger.info("Cleaned up worktree for %s", workspace.issue_id)

    async def run_hook(self, hook_name: str, workspace: Workspace) -> None:
        """Execute a lifecycle hook script in the workspace directory."""
        script = getattr(self.hooks, hook_name, None)
        if not script:
            return

        logger.debug("Running hook '%s' for %s", hook_name, workspace.issue_id)

        env = {
            **os.environ,
            "ISSUE_ID": workspace.issue_id,
            "WORKSPACE": str(workspace.path),
            "BRANCH": workspace.branch,
            "REPO": str(self.repo),
        }

        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-c", script,
                cwd=str(workspace.path),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.hook_timeout_s,
            )
        except asyncio.TimeoutError:
            logger.error(
                "Hook '%s' timed out after %.0fs for %s",
                hook_name, self.hook_timeout_s, workspace.issue_id,
            )
            proc.kill()
            raise RuntimeError(f"Hook '{hook_name}' timed out")

        if proc.returncode != 0:
            stderr_text = stderr.decode(errors="replace")[:500]
            logger.error(
                "Hook '%s' failed (exit %d) for %s: %s",
                hook_name, proc.returncode, workspace.issue_id, stderr_text,
            )
            raise RuntimeError(f"Hook '{hook_name}' failed: {stderr_text}")

        stdout_text = stdout.decode(errors="replace").strip()
        if stdout_text:
            logger.debug("Hook '%s' output: %s", hook_name, stdout_text[:200])

    async def _find_existing_branch(self, issue_slug: str) -> str | None:
        """Find an existing feat/{slug} or feat/{slug}-* branch."""
        try:
            result = await self._run_git(
                "branch", "--list", f"feat/{issue_slug}", f"feat/{issue_slug}-*",
            )
        except RuntimeError:
            return None

        branches = [
            branch
            for line in result.splitlines()
            if (branch := self._parse_branch_list_line(line))
        ]
        return branches[0] if branches else None

    @staticmethod
    def _parse_branch_list_line(line: str) -> str | None:
        """Return the branch name from `git branch --list` output."""
        branch = line.strip()
        if not branch:
            return None
        if branch[0] in {"*", "+"}:
            branch = branch[1:].strip()
        return branch or None

    @staticmethod
    def _resolve(value: str) -> str:
        """Resolve $ENV_VAR references in config values."""
        if value.startswith("$"):
            resolved = os.environ.get(value[1:], "")
            if not resolved:
                raise RuntimeError(
                    f"Environment variable {value} is not set. "
                    f"Set it with: export {value[1:]}=/path/to/your/repo"
                )
            return resolved
        return value

    async def _run_git(self, *args: str) -> str:
        """Run a git command against the source repo.

        Uses both ``-C`` and ``cwd`` to avoid 'Unable to read current working
        directory' errors when the process inherits an inaccessible CWD
        (e.g. a deleted directory or macOS TCC-restricted path).
        """
        cmd = ["git", "-C", str(self.repo), *args]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.repo),
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            logger.debug("git %s failed (exit %d): %s", " ".join(args), proc.returncode, err)
            raise RuntimeError(f"git {' '.join(args)} failed: {err}")

        return stdout.decode(errors="replace").strip()
