"""Unit tests for ScriptedPrompter (test impl) and ClickPrompter (production)."""
from __future__ import annotations

import pytest
from click.testing import CliRunner

from autosymph.wizard.prompter import ClickPrompter, ScriptedPrompter


class TestScriptedPrompter:
    def test_ask_returns_queued_string(self):
        p = ScriptedPrompter(["hello"])
        assert p.ask("what?") == "hello"
        assert p.remaining == 0

    def test_confirm_returns_queued_bool(self):
        p = ScriptedPrompter([True, False])
        assert p.confirm("yes?") is True
        assert p.confirm("no?") is False

    def test_select_returns_queued_index(self):
        p = ScriptedPrompter([1])
        assert p.select("pick", ["a", "b", "c"]) == 1

    def test_multi_select_returns_queued_list(self):
        p = ScriptedPrompter([[0, 2]])
        assert p.multi_select("pick", ["a", "b", "c"]) == [0, 2]

    def test_multi_select_empty_list(self):
        p = ScriptedPrompter([[]])
        assert p.multi_select("pick", ["a", "b"]) == []

    def test_exhausted_queue_raises_with_prompt_in_message(self):
        p = ScriptedPrompter([])
        with pytest.raises(IndexError, match="exhausted"):
            p.ask("any prompt")

    def test_ask_rejects_non_string_answer(self):
        p = ScriptedPrompter([42])
        with pytest.raises(TypeError):
            p.ask("expect string")

    def test_confirm_rejects_non_bool(self):
        p = ScriptedPrompter(["yes"])
        with pytest.raises(TypeError):
            p.confirm("expect bool")

    def test_select_rejects_out_of_range_index(self):
        p = ScriptedPrompter([5])
        with pytest.raises(ValueError, match="out of range"):
            p.select("pick", ["a", "b"])

    def test_select_rejects_bool_disguised_as_int(self):
        """bool is a subclass of int in Python — guard against accidental True/False."""
        p = ScriptedPrompter([True])
        with pytest.raises(TypeError):
            p.select("pick", ["a", "b"])

    def test_multi_select_rejects_non_list(self):
        p = ScriptedPrompter(["not-a-list"])
        with pytest.raises(TypeError):
            p.multi_select("pick", ["a"])

    def test_multi_select_rejects_out_of_range(self):
        p = ScriptedPrompter([[0, 99]])
        with pytest.raises(ValueError):
            p.multi_select("pick", ["a", "b"])


class TestClickPrompter:
    """Smoke tests using Click's CliRunner so we exercise the real input path."""

    def _run_with_input(self, fn, stdin: str):
        runner = CliRunner()
        captured: dict[str, object] = {}

        import click

        @click.command()
        def cmd() -> None:
            captured["result"] = fn()

        result = runner.invoke(cmd, input=stdin, catch_exceptions=False)
        if result.exit_code != 0:
            raise AssertionError(f"command failed: {result.output}")
        return captured["result"]

    def test_ask_reads_stdin(self):
        out = self._run_with_input(lambda: ClickPrompter().ask("name"), "Alice\n")
        assert out == "Alice"

    def test_confirm_yes(self):
        out = self._run_with_input(
            lambda: ClickPrompter().confirm("ok?", default=False), "y\n"
        )
        assert out is True

    def test_confirm_no(self):
        out = self._run_with_input(
            lambda: ClickPrompter().confirm("ok?", default=True), "n\n"
        )
        assert out is False

    def test_select_picks_index(self):
        out = self._run_with_input(
            lambda: ClickPrompter().select("pick", ["alpha", "beta", "gamma"]),
            "2\n",
        )
        assert out == 1  # 0-based

    def test_select_retries_on_invalid_input(self):
        out = self._run_with_input(
            lambda: ClickPrompter().select("pick", ["alpha", "beta"]),
            "garbage\n9\n1\n",
        )
        assert out == 0

    def test_multi_select_parses_comma_list(self):
        out = self._run_with_input(
            lambda: ClickPrompter().multi_select("pick", ["a", "b", "c", "d"]),
            "1,3\n",
        )
        assert out == [0, 2]

    def test_multi_select_blank_returns_empty(self):
        out = self._run_with_input(
            lambda: ClickPrompter().multi_select("pick", ["a", "b"]),
            "\n",
        )
        assert out == []

    def test_multi_select_dedupes(self):
        out = self._run_with_input(
            lambda: ClickPrompter().multi_select("pick", ["a", "b", "c"]),
            "1,1,2\n",
        )
        assert out == [0, 1]
