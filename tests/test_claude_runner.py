"""Tests for ClaudeRunner — IMP-356 R11 session-start log line + argv extraction.

The runner is where the `claude session start:` audit log lives so that the
recorded `model=...` reflects the literal --model argv (not the input dict).
This protects AC3/AC6/AC7/AC8 from a future refactor that diverges
runner_config["model"] from the rendered cmd.
"""

from __future__ import annotations

import logging

import pytest

from autosymph.runners.claude import ClaudeRunner


class TestExtractArg:
    def test_returns_value_after_flag(self):
        cmd = ["claude", "--model", "claude-opus-4-7", "--verbose"]
        assert ClaudeRunner._extract_arg(cmd, "--model") == "claude-opus-4-7"

    def test_returns_none_when_flag_absent(self):
        cmd = ["claude", "--verbose"]
        assert ClaudeRunner._extract_arg(cmd, "--model") is None

    def test_returns_none_when_flag_is_last_token(self):
        """Truncated argv shouldn't IndexError."""
        cmd = ["claude", "--model"]
        assert ClaudeRunner._extract_arg(cmd, "--model") is None


class TestSessionStartLogLine:
    """The ``claude session start:`` line must be emitted by ClaudeRunner.run
    using the rendered cmd's --model argv, plus identifier/state/prompt
    metadata supplied by the orchestrator via the config dict."""

    def test_extract_picks_up_model_from_built_command(self):
        runner = ClaudeRunner()
        cmd = runner._build_command(
            {"model": "claude-sonnet-4-6"},
            session_id=None,
        )
        assert ClaudeRunner._extract_arg(cmd, "--model") == "claude-sonnet-4-6"

    def test_default_model_when_unset(self):
        """If the orchestrator forgets to set model, the runner default applies
        and the log line still names the actual argv."""
        runner = ClaudeRunner()
        cmd = runner._build_command({}, session_id=None)
        # _build_command falls back to claude-sonnet-4-6
        assert ClaudeRunner._extract_arg(cmd, "--model") == "claude-sonnet-4-6"

    def test_log_line_emitted_at_spawn(self, caplog, monkeypatch):
        """The log line is emitted before subprocess spawn. We patch
        ``asyncio.create_subprocess_exec`` to raise FileNotFoundError early so
        the test stays fully synchronous below the log call."""
        import asyncio

        async def _fake_exec(*args, **kwargs):
            raise FileNotFoundError("simulated — claude CLI not present in test env")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

        runner = ClaudeRunner()
        with caplog.at_level(logging.INFO, logger="autosymph.runners.claude"):
            asyncio.run(runner.run(
                prompt="hi",
                workspace_path="/tmp",
                config={
                    "model": "claude-haiku-4-5-20251001",
                    "identifier": "IMP-356",
                    "workflow_state": "autoplan",
                    "prompt_path": "prompts/autoplan.md",
                },
            ))

        lines = [r.getMessage() for r in caplog.records]
        line = next((l for l in lines if "claude session start:" in l), None)
        assert line is not None, f"session-start log line missing — got {lines!r}"
        assert "identifier=IMP-356" in line
        assert "state=autoplan" in line
        assert "model=claude-haiku-4-5-20251001" in line
        assert "prompt=prompts/autoplan.md" in line
