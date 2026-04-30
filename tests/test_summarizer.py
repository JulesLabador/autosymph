"""Tests for ActivitySummarizer (IMP-313)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from autosymph.config import (
    StateCommentsConfig,
    StateConfig,
    StateTransitions,
    TrackerConfig,
    WorkflowCommentsConfig,
    WorkflowConfig,
    WorkspaceConfig,
    resolve_activity_summary,
)
from autosymph.logging.summarizer import ActivitySummarizer
from autosymph.logging.timeline import TimelineExtractor
from autosymph.runners.base import AgentEvent, EventType


# -- Helpers for building synthetic events --


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _assistant_turn(
    tool_uses: list[dict[str, Any]] | None = None,
    texts: list[str] | None = None,
) -> AgentEvent:
    return AgentEvent(
        type=EventType.ASSISTANT_TURN,
        timestamp=_now(),
        data={
            "tool_uses": tool_uses or [],
            "texts": texts or [],
            "thinking": [],
        },
    )


def _tool_result(tool_use_id: str, content: Any, is_error: bool = False) -> AgentEvent:
    return AgentEvent(
        type=EventType.TOOL_RESULT,
        timestamp=_now(),
        data={
            "tool_results": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": content,
                    "is_error": is_error,
                }
            ]
        },
    )


def _error(message: str) -> AgentEvent:
    return AgentEvent(
        type=EventType.ERROR,
        timestamp=_now(),
        data={"error": message},
    )


def _tool_use(name: str, tu_input: dict[str, Any], tu_id: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": tu_id, "name": name, "input": tu_input}


# -- Bullet generation tests --


def test_clean_implement_run() -> None:
    """Skill, edits, commit, gh pr create with PR URL — all bullets in order."""
    s = ActivitySummarizer()
    s.ingest(_assistant_turn([_tool_use("Skill", {"skill": "codebase-explore"}, "t1")]))
    s.ingest(
        _assistant_turn(
            [
                _tool_use("Edit", {"file_path": "src/hello.py"}, "t2"),
                _tool_use("Edit", {"file_path": "tests/test_hello.py"}, "t3"),
            ]
        )
    )
    s.ingest(
        _assistant_turn(
            [_tool_use("Bash", {"command": 'git commit -m "feat(test): hello"'}, "t4")]
        )
    )
    s.ingest(
        _assistant_turn(
            [_tool_use("Bash", {"command": "gh pr create --title 'feat: hello'"}, "t5")]
        )
    )
    s.ingest(_tool_result("t5", "https://github.com/user/repo/pull/44 created"))

    out = s.format_summary()
    assert "/codebase-explore" in out
    assert "Edited" in out
    assert "Committed: feat(test): hello" in out
    assert "Opened PR (https://github.com/user/repo/pull/44)" in out
    # Numbered list, 1-indexed.
    assert out.startswith("1.")


def test_pr_url_extraction() -> None:
    s = ActivitySummarizer()
    s.ingest(_assistant_turn([_tool_use("Bash", {"command": "gh pr create"}, "t1")]))
    s.ingest(_tool_result("t1", "https://github.com/foo/bar/pull/123\nLooks good"))
    out = s.format_summary()
    assert "https://github.com/foo/bar/pull/123" in out


def test_generic_url_fallback() -> None:
    """Any tool result containing a URL surfaces it on the bullet."""
    s = ActivitySummarizer()
    s.ingest(
        _assistant_turn(
            [_tool_use("mcp__linear__save_issue", {"id": "IMP-1"}, "t1")]
        )
    )
    s.ingest(_tool_result("t1", [{"type": "text", "text": "saved at https://linear.app/x/IMP-1"}]))
    out = s.format_summary()
    assert "Used linear.save_issue" in out
    assert "https://linear.app/x/IMP-1" in out


def test_bash_non_pr_no_url_extraction() -> None:
    """Generic Bash commands must NOT inline URLs from their stdout (often grepped noise)."""
    s = ActivitySummarizer()
    s.ingest(
        _assistant_turn(
            [_tool_use("Bash", {"command": "cat Package.swift"}, "t1")]
        )
    )
    s.ingest(
        _tool_result(
            "t1",
            "dependencies: [.package(url: \"https://github.com/supabase/supabase-swift\")]",
        )
    )
    out = s.format_summary()
    assert "https://github.com/supabase" not in out


def test_mcp_list_methods_skip_url_extraction() -> None:
    """Aggregating MCP methods (list_*, search_*) have no canonical action URL."""
    s = ActivitySummarizer()
    s.ingest(
        _assistant_turn(
            [_tool_use("mcp__linear__list_comments", {}, "t1")]
        )
    )
    s.ingest(_tool_result("t1", "Linked issue: https://linear.app/x/IMP-1"))
    out = s.format_summary()
    assert "https://linear.app" not in out


def test_url_regex_breaks_on_backslash_escape() -> None:
    """JSON-stringified content with literal '\\n' must not be glued onto the URL."""
    s = ActivitySummarizer()
    s.ingest(
        _assistant_turn(
            [_tool_use("mcp__linear__get_issue", {}, "t1")]
        )
    )
    s.ingest(
        _tool_result(
            "t1",
            "Issue at https://linear.app/x/IMP-1\\n\\n### header that follows",
        )
    )
    out = s.format_summary()
    assert "IMP-1)" in out
    assert "\\n" not in out


def test_mcp_skips_noisy_signed_urls() -> None:
    """Linear's signed CDN URLs (uploads.linear.app, JWT-bearing) should NOT inline."""
    s = ActivitySummarizer()
    s.ingest(
        _assistant_turn(
            [_tool_use("mcp__linear__get_issue", {"id": "IMP-372"}, "t1")]
        )
    )
    s.ingest(
        _tool_result(
            "t1",
            "https://uploads.linear.app/abc/def/signature=eyJ.JWT.payload\n"
            "Linked issue: https://linear.app/example/issue/ISSUE-123",
        )
    )
    out = s.format_summary()
    assert "uploads.linear.app" not in out
    assert "https://linear.app/example/issue/ISSUE-123" in out


def test_url_trailing_punctuation_stripped() -> None:
    """Sentence-ending punctuation must not be glued onto extracted URLs."""
    s = ActivitySummarizer()
    s.ingest(
        _assistant_turn(
            [_tool_use("mcp__linear__save_issue", {"id": "X"}, "t1")]
        )
    )
    s.ingest(_tool_result("t1", "Saved at https://linear.app/x/IMP-1."))
    out = s.format_summary()
    assert "https://linear.app/x/IMP-1)" in out
    assert "IMP-1.)" not in out


def test_error_bullet() -> None:
    s = ActivitySummarizer()
    s.ingest(_assistant_turn([_tool_use("Skill", {"skill": "x"}, "t1")]))
    s.ingest(_error("Test runner exited with code 1"))
    out = s.format_summary()
    assert "⚠ Test runner exited with code 1" in out


def test_thirty_consecutive_edits_condense() -> None:
    s = ActivitySummarizer()
    tool_uses = [_tool_use("Edit", {"file_path": f"f{i}.py"}, f"t{i}") for i in range(30)]
    s.ingest(_assistant_turn(tool_uses))
    out = s.format_summary()
    bullets = out.splitlines()
    assert len(bullets) <= 15
    assert any("Edited 30 files" in b for b in bullets)
    # Shows first 3 paths plus "and N more"
    assert "f0.py" in out
    assert "and 27 more" in out


def test_narrative_only_turn() -> None:
    """Turn with only text and no tool_uses produces a snippet bullet."""
    s = ActivitySummarizer()
    long_text = (
        "I am going to investigate the failure mode now. "
        "First step is reading the test output."
    )
    s.ingest(_assistant_turn(texts=[long_text]))
    out = s.format_summary()
    assert "investigate the failure mode" in out
    # Sentence split at period, ≤120 chars.
    first_line = out.splitlines()[0]
    body = first_line.split(". ", 1)[1] if ". " in first_line else first_line
    assert len(body) <= 130  # margin for ellipsis


def test_empty_when_no_activities() -> None:
    s = ActivitySummarizer()
    assert s.format_summary() == ""


def test_read_distinct_from_edit() -> None:
    s = ActivitySummarizer()
    s.ingest(_assistant_turn([_tool_use("Read", {"file_path": "src/foo.py"}, "t1")]))
    s.ingest(_assistant_turn([_tool_use("Edit", {"file_path": "src/bar.py"}, "t2")]))
    out = s.format_summary()
    assert "Read src/foo.py" in out
    assert "Edited src/bar.py" in out


def test_15_bullet_cap() -> None:
    """More than 15 distinct (non-condensable) entries truncate with tail."""
    s = ActivitySummarizer()
    # 20 distinct skills — not condensable, since `skill` isn't in CONDENSABLE.
    for i in range(20):
        s.ingest(_assistant_turn([_tool_use("Skill", {"skill": f"s{i}"}, f"t{i}")]))
    out = s.format_summary()
    bullets = out.splitlines()
    assert len(bullets) == 15
    assert "and 6 more steps (see log)" in bullets[-1]


def test_format_digest_groups_high_level_activity() -> None:
    """Digest comments should summarize categories instead of replaying every step."""
    s = ActivitySummarizer()
    s.ingest(_assistant_turn([_tool_use("Skill", {"skill": "codebase-explore"}, "t1")]))
    s.ingest(_assistant_turn([_tool_use("Read", {"file_path": "src/foo.py"}, "t2")]))
    s.ingest(_assistant_turn([_tool_use("Edit", {"file_path": "src/bar.py"}, "t3")]))
    s.ingest(
        _assistant_turn(
            [_tool_use("Bash", {"command": "uv run pytest tests/test_foo.py -v"}, "t4")]
        )
    )
    s.ingest(_assistant_turn([_tool_use("Bash", {"command": "gh pr create"}, "t5")]))
    s.ingest(_tool_result("t5", "https://github.com/user/repo/pull/44 created"))

    out = s.format_digest()

    assert "### Activity summary" in out
    assert "Used skills: `/codebase-explore`." in out
    assert "Read 1 files, including `src/foo.py`." in out
    assert "Edited 1 files, including `src/bar.py`." in out
    assert "Ran verification: `uv run pytest tests/test_foo.py -v`." in out
    assert "Opened PR: `https://github.com/user/repo/pull/44`." in out
    assert "### Failure modes" in out
    assert "No errors or failed tool results captured" in out


def test_format_digest_reports_failed_tool_results() -> None:
    s = ActivitySummarizer()
    s.ingest(_assistant_turn([_tool_use("Bash", {"command": "uv run pytest"}, "t1")]))
    s.ingest(_tool_result("t1", "tests failed with exit code 1", is_error=True))

    out = s.format_digest()

    assert "### Failure modes" in out
    assert "Command `uv run pytest` failed: tests failed with exit code 1" in out


def test_format_digest_promotes_audit_failure_notes() -> None:
    """verify_review/investigating often report failures as narrative findings."""
    s = ActivitySummarizer()
    s.ingest(
        _assistant_turn(
            [
                _tool_use(
                    "Bash",
                    {
                        "command": (
                            "$HOME/.autosymph/skills/verify-completion-audit/scripts/audit.sh "
                            "~/.autosymph/logs/ios-app/issue-123/verify-run2.ndjson"
                        )
                    },
                    "t1",
                )
            ]
        )
    )
    s.ingest(
        _assistant_turn(
            texts=[
                "The completion audit returned exit code 2 — completion violations found."
            ]
        )
    )
    s.ingest(_assistant_turn(texts=["The xcodeproj doesn't exist at the current directory."]))

    out = s.format_digest()

    assert "Ran verification:" in out
    assert "completion audit returned exit code 2" in out
    assert "xcodeproj doesn't exist" in out


# -- Failure-mode tests --


def test_ingest_raises_on_non_dict_tool_use(caplog: pytest.LogCaptureFixture) -> None:
    """Synthetic AgentEvent with a non-dict tool_uses[0] triggers failure."""
    s = ActivitySummarizer()
    bad = AgentEvent(
        type=EventType.ASSISTANT_TURN,
        timestamp=_now(),
        data={"tool_uses": ["not-a-dict"], "texts": [], "thinking": []},
    )

    # Simulate the orchestrator's wrapped ingest path.
    try:
        s.ingest(bad)
    except Exception:
        s.errored = True

    assert s.errored is True


def test_format_summary_can_be_monkeypatched_to_raise() -> None:
    s = ActivitySummarizer()
    s.ingest(_assistant_turn([_tool_use("Skill", {"skill": "x"}, "t1")]))

    def boom() -> str:
        raise RuntimeError("kaboom")

    s.format_summary = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="kaboom"):
        s.format_summary()


# -- Config resolution tests --


def _make_workflow(activity_summary_default: bool = True) -> WorkflowConfig:
    return WorkflowConfig(
        tracker=TrackerConfig(project="test", api_key="k"),
        workspace=WorkspaceConfig(root="/tmp/x", repo="."),
        comments=WorkflowCommentsConfig(activity_summary=activity_summary_default),
        states={
            "implement": StateConfig(
                type="agent",
                prompt="p.md",
                transitions=StateTransitions(**{"complete": "done"}),
            ),
            "done": StateConfig(type="terminal"),
        },
    )


def test_resolve_default_true() -> None:
    workflow = _make_workflow(activity_summary_default=True)
    state = workflow.states["implement"]
    assert resolve_activity_summary(workflow, state) is True


def test_resolve_workflow_default_false() -> None:
    workflow = _make_workflow(activity_summary_default=False)
    state = workflow.states["implement"]
    assert resolve_activity_summary(workflow, state) is False


def test_resolve_state_override_wins() -> None:
    workflow = _make_workflow(activity_summary_default=True)
    workflow.states["implement"].comments = StateCommentsConfig(activity_summary=False)
    assert resolve_activity_summary(workflow, workflow.states["implement"]) is False


def test_resolve_state_none_inherits() -> None:
    workflow = _make_workflow(activity_summary_default=True)
    workflow.states["implement"].comments = StateCommentsConfig(activity_summary=None)
    assert resolve_activity_summary(workflow, workflow.states["implement"]) is True


# -- Integration: _render_activity_block --


@pytest.fixture
def orchestrator_with_workflow():
    """Build a minimal Orchestrator stub with just the fields _render_activity_block reads."""
    from autosymph.orchestrator import Orchestrator

    workflow = _make_workflow(activity_summary_default=True)
    orch = MagicMock(spec=Orchestrator)
    orch.config = workflow
    # Bind the real method to the mock.
    orch._render_activity_block = Orchestrator._render_activity_block.__get__(orch, Orchestrator)
    return orch, workflow


def test_render_activity_block_summary_path(orchestrator_with_workflow) -> None:
    orch, _ = orchestrator_with_workflow
    s = ActivitySummarizer()
    s.ingest(_assistant_turn([_tool_use("Skill", {"skill": "foo"}, "t1")]))
    timeline = TimelineExtractor()
    out = orch._render_activity_block(
        state="implement", session_name="impl-X", summarizer=s, timeline=timeline,
    )
    assert "/foo" in out
    assert "### Activity summary" in out
    assert "### Failure modes" in out
    assert "```" not in out  # no fenced legacy block


def test_render_activity_block_falls_back_when_disabled(orchestrator_with_workflow) -> None:
    orch, workflow = orchestrator_with_workflow
    workflow.states["implement"].comments = StateCommentsConfig(activity_summary=False)

    s = ActivitySummarizer()
    s.ingest(_assistant_turn([_tool_use("Skill", {"skill": "foo"}, "t1")]))
    timeline = TimelineExtractor()
    timeline.ingest(_assistant_turn([_tool_use("Read", {"file_path": "x.py"}, "t1")]))
    # Trigger a TOOL_CALL so timeline has at least one entry.
    timeline.ingest(
        AgentEvent(
            type=EventType.TOOL_CALL,
            timestamp=_now(),
            data={"tool_name": "Read", "tool_id": "t9"},
        )
    )

    out = orch._render_activity_block(
        state="implement", session_name="impl-X", summarizer=s, timeline=timeline,
    )
    assert "/foo" not in out
    assert "```" in out  # legacy fenced timeline


def test_render_activity_block_falls_back_when_errored(orchestrator_with_workflow) -> None:
    orch, _ = orchestrator_with_workflow
    s = ActivitySummarizer()
    s.errored = True

    timeline = TimelineExtractor()
    timeline.ingest(
        AgentEvent(
            type=EventType.TOOL_CALL,
            timestamp=_now(),
            data={"tool_name": "Read", "tool_id": "t1"},
        )
    )

    out = orch._render_activity_block(
        state="implement", session_name="impl-X", summarizer=s, timeline=timeline,
    )
    assert "```" in out


def test_render_activity_block_falls_back_when_format_raises(
    orchestrator_with_workflow, caplog: pytest.LogCaptureFixture,
) -> None:
    orch, _ = orchestrator_with_workflow
    s = ActivitySummarizer()
    s.ingest(_assistant_turn([_tool_use("Skill", {"skill": "foo"}, "t1")]))

    def boom_digest(extra_failure_modes=None) -> str:
        raise RuntimeError("boom")

    s.format_digest = boom_digest  # type: ignore[method-assign]

    timeline = TimelineExtractor()
    timeline.ingest(
        AgentEvent(
            type=EventType.TOOL_CALL,
            timestamp=_now(),
            data={"tool_name": "Read", "tool_id": "t1"},
        )
    )

    with caplog.at_level(logging.WARNING, logger="autosymph.orchestrator"):
        out = orch._render_activity_block(
            state="implement", session_name="impl-X", summarizer=s, timeline=timeline,
        )

    assert "```" in out
    assert any("activity summary failed" in r.getMessage() for r in caplog.records)


def test_render_activity_block_empty_summary_falls_back(orchestrator_with_workflow) -> None:
    orch, _ = orchestrator_with_workflow
    s = ActivitySummarizer()  # no ingest → empty
    timeline = TimelineExtractor()
    timeline.ingest(
        AgentEvent(
            type=EventType.TOOL_CALL,
            timestamp=_now(),
            data={"tool_name": "Read", "tool_id": "t1"},
        )
    )

    out = orch._render_activity_block(
        state="implement", session_name="impl-X", summarizer=s, timeline=timeline,
    )
    # Empty summary → legacy block (or empty if no timeline entries).
    assert "```" in out
