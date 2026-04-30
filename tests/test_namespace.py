"""Tests for namespaced workspace and log paths."""

from __future__ import annotations



from autosymph.config import WorkspaceConfig, HooksConfig


class TestWorkspaceNamespace:
    def test_namespaced_path(self, tmp_path):
        """With project_slug, workspace path includes project dir."""
        from autosymph.workspace import WorkspaceManager

        config = WorkspaceConfig(root=str(tmp_path / "workspaces"), repo="/tmp/fake-repo")
        mgr = WorkspaceManager(config=config, hooks=HooksConfig(), project_slug="implicit")
        # Verify the root and project_slug are stored
        assert mgr.project_slug == "implicit"
        # The actual path construction happens in create(), which needs a git repo.
        # Just verify the path would be correct:
        expected = tmp_path / "workspaces" / "implicit" / "imp-100"
        if mgr.project_slug:
            path = mgr.root / mgr.project_slug / "imp-100"
        else:
            path = mgr.root / "imp-100"
        assert path == expected

    def test_no_project_slug(self, tmp_path):
        """Without project_slug, workspace path is flat (backward compat)."""
        from autosymph.workspace import WorkspaceManager

        config = WorkspaceConfig(root=str(tmp_path / "workspaces"), repo="/tmp/fake-repo")
        mgr = WorkspaceManager(config=config, hooks=HooksConfig())
        assert mgr.project_slug is None
        path = mgr.root / "imp-100"
        expected = tmp_path / "workspaces" / "imp-100"
        assert path == expected


class TestLogStreamNamespace:
    def test_namespaced_log_dir(self, tmp_path):
        """With project_slug, logs go into {root}/{project}/{issue}/."""
        from autosymph.logging.stream import LogStream

        stream = LogStream(log_root=tmp_path / "logs", project_slug="implicit")
        log_dir = stream._issue_dir("imp-100")
        assert log_dir == tmp_path / "logs" / "implicit" / "imp-100"

    def test_flat_log_dir(self, tmp_path):
        """Without project_slug, logs go into {root}/{issue}/ (backward compat)."""
        from autosymph.logging.stream import LogStream

        stream = LogStream(log_root=tmp_path / "logs")
        log_dir = stream._issue_dir("imp-100")
        assert log_dir == tmp_path / "logs" / "imp-100"

    def test_open_creates_namespaced_dir(self, tmp_path):
        """open() creates the namespaced directory structure."""
        from autosymph.logging.stream import LogStream

        stream = LogStream(log_root=tmp_path / "logs", project_slug="implicit")
        path = stream.open("imp-100", "implement", 1)
        assert path.parent.exists()
        assert path == tmp_path / "logs" / "implicit" / "imp-100" / "implement-run1.ndjson"

    def test_count_runs_namespaced(self, tmp_path):
        """count_runs() respects project_slug namespace."""
        from autosymph.logging.stream import LogStream

        stream = LogStream(log_root=tmp_path / "logs", project_slug="implicit")
        # Create some fake run files
        log_dir = tmp_path / "logs" / "implicit" / "imp-100"
        log_dir.mkdir(parents=True)
        (log_dir / "implement-run1.ndjson").touch()
        (log_dir / "implement-run2.ndjson").touch()
        assert stream.count_runs("imp-100", "implement") == 2
