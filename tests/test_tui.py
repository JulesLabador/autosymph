"""Tests for TUI multi-instance rendering."""

from __future__ import annotations

from unittest.mock import MagicMock

from autosymph.tui import render_multi_status, _render_project_section


def _make_mock_orch(
    project_slug: str = "test-project",
    config_path: str = "/configs/test.yaml",
    repo_path: str = "/repos/test",
    runners_active: int = 0,
    runners_max: int = 3,
    runners: dict | None = None,
    tracked_issues: dict | None = None,
    completed: list | None = None,
    failed: list | None = None,
) -> MagicMock:
    """Create a mock orchestrator with a controllable status_summary()."""
    orch = MagicMock()
    orch.status_summary.return_value = {
        "tick": 1,
        "polling_interval_s": 30,
        "next_poll_s": 15,
        "runners": runners or {},
        "runners_active": runners_active,
        "runners_max": runners_max,
        "completed_today": completed or [],
        "failed_today": failed or [],
        "tracked_issues": tracked_issues or {},
        "warnings": [],
        "project_slug": project_slug,
        "config_path": config_path,
        "repo_path": repo_path,
    }
    return orch


class TestRenderMultiStatus:
    def test_grouped_sections(self):
        """Two projects render as distinct grouped sections."""
        orch_a = _make_mock_orch(
            project_slug="demo",
            config_path="/configs/demo.yaml",
            repo_path="/repos/demo-app",
            runners_active=1,
            runners={"ISSUE-100": {
                "state": "implement", "duration_s": 120, "turns": 5,
                "tokens": 1000, "last_tool": "edit",
            }},
            tracked_issues={"ISSUE-100": {"state": "implement", "claim": "running"}},
        )
        orch_b = _make_mock_orch(
            project_slug="stokowski",
            config_path="/configs/stokowski.yaml",
            repo_path="/repos/stokowski",
            runners_active=0,
        )
        output = render_multi_status([orch_a, orch_b])
        assert "demo" in output
        assert "stokowski" in output
        assert "2 projects" in output
        assert "ISSUE-100" in output
        assert "demo.yaml" in output
        assert "stokowski.yaml" in output

    def test_idle_project_collapsed(self):
        """An idle project (0 runners, 0 tracked) renders as a single line."""
        orch = _make_mock_orch(
            project_slug="idle-project",
            config_path="/configs/idle.yaml",
            runners_active=0,
        )
        output = render_multi_status([orch])
        # Idle project should have "idle" in the line
        lines = output.split("\n")
        idle_lines = [line for line in lines if "idle-project" in line]
        assert len(idle_lines) == 1  # Collapsed to single line
        assert "idle" in idle_lines[0]

    def test_active_project_expanded(self):
        """An active project shows runners and tracked issues."""
        orch = _make_mock_orch(
            project_slug="active",
            config_path="/configs/active.yaml",
            runners_active=2,
            runners={
                "ISSUE-100": {
                    "state": "implement", "duration_s": 60,
                    "turns": 3, "tokens": 500, "last_tool": "",
                },
                "ISSUE-101": {
                    "state": "verify", "duration_s": 30,
                    "turns": 1, "tokens": 200, "last_tool": "",
                },
            },
            tracked_issues={
                "ISSUE-100": {"state": "implement", "claim": "running"},
                "ISSUE-101": {"state": "verify", "claim": "running"},
            },
        )
        output = render_multi_status([orch])
        assert "ISSUE-100" in output
        assert "ISSUE-101" in output
        assert "2/3 agents" in output

    def test_failed_project_section(self):
        """A failed project renders with error message."""
        orch = _make_mock_orch(project_slug="broken")
        sup_status = {
            "projects": {
                "broken": {"state": "failed", "last_error": "API key invalid"},
            },
            "warnings": [],
        }
        output = render_multi_status([orch], supervisor_status=sup_status)
        assert "FAILED" in output
        assert "API key invalid" in output

    def test_discovery_warnings(self):
        """Supervisor warnings appear in output."""
        orch = _make_mock_orch(project_slug="good")
        sup_status = {
            "projects": {},
            "warnings": ["Skipped bad.yaml: missing tracker field"],
        }
        output = render_multi_status([orch], supervisor_status=sup_status)
        assert "bad.yaml" in output
        assert "missing tracker" in output

    def test_global_stats(self):
        """Global header shows total projects and agents."""
        orchs = [
            _make_mock_orch(project_slug="a", runners_active=2),
            _make_mock_orch(project_slug="b", runners_active=1),
            _make_mock_orch(project_slug="c", runners_active=0),
        ]
        output = render_multi_status(orchs)
        assert "3 projects" in output
        assert "3" in output  # 3 total agents


class TestRenderProjectSection:
    def test_idle_is_single_line(self):
        """Idle project produces exactly 1 line."""
        status = {
            "project_slug": "idle",
            "config_path": "idle.yaml",
            "repo_path": "/repos/idle",
            "polling_interval_s": 30,
            "runners_active": 0,
            "runners_max": 3,
            "runners": {},
            "tracked_issues": {},
        }
        lines = _render_project_section(status, "\033[36m")
        assert len(lines) == 1
        assert "idle" in lines[0]

    def test_failed_section(self):
        """Failed project shows error."""
        status = {"project_slug": "fail", "config_path": "", "repo_path": ""}
        lines = _render_project_section(
            status, "\033[31m", failed=True, error="crash"
        )
        assert any("FAILED" in line for line in lines)
        assert any("crash" in line for line in lines)
