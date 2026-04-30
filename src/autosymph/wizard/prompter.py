"""Prompter abstraction so wizard steps are testable without a real terminal.

``ClickPrompter`` is the production impl: thin wrapper over Click's prompt /
confirm. Reads from ``click.get_text_stream('stdin')`` so non-TTY scripted
invocation works (``autosymph init < answers.txt``).

``ScriptedPrompter`` is the test impl: consumes a queue of pre-scripted
answers. Raises a clear ``IndexError`` if the queue is exhausted (instead of
hanging or returning silently-wrong data).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import click


@runtime_checkable
class Prompter(Protocol):
    """Protocol every wizard step depends on for user interaction.

    Step functions take a ``Prompter`` and never call ``input()`` /
    ``click.prompt`` directly. Tests pass ``ScriptedPrompter``; production
    passes ``ClickPrompter``.
    """

    def ask(self, prompt: str, default: str | None = None) -> str:
        """Free-text prompt. Returns the user's response (or default on empty)."""

    def confirm(self, prompt: str, default: bool = False) -> bool:
        """Yes/no prompt. Returns True for yes."""

    def select(self, prompt: str, choices: list[str]) -> int:
        """Single-select numbered list. Returns the chosen index (0-based)."""

    def multi_select(self, prompt: str, choices: list[str]) -> list[int]:
        """Multi-select via comma-separated indices. Returns chosen indices (0-based, may be empty)."""


class ClickPrompter:
    """Production Prompter using Click's prompt machinery.

    All input goes through ``click.get_text_stream('stdin')`` so stdin
    redirection from non-TTY contexts (subprocess, pipe) works.
    """

    def ask(self, prompt: str, default: str | None = None) -> str:
        # Click's prompt handles default=None as "required" and a string default
        # as "press enter to accept default".
        return click.prompt(prompt, default=default if default is not None else None)

    def confirm(self, prompt: str, default: bool = False) -> bool:
        return click.confirm(prompt, default=default)

    def select(self, prompt: str, choices: list[str]) -> int:
        if not choices:
            raise ValueError("select() requires at least one choice")
        click.echo(prompt)
        for i, choice in enumerate(choices, start=1):
            click.echo(f"  {i}. {choice}")
        while True:
            raw = click.prompt(f"Enter 1-{len(choices)}", type=str)
            try:
                idx = int(raw.strip()) - 1
            except ValueError:
                click.echo(f"  Not a number: {raw!r}")
                continue
            if 0 <= idx < len(choices):
                return idx
            click.echo(f"  Out of range: {raw!r}")

    def multi_select(self, prompt: str, choices: list[str]) -> list[int]:
        if not choices:
            return []
        click.echo(prompt)
        for i, choice in enumerate(choices, start=1):
            click.echo(f"  {i}. {choice}")
        click.echo("  Enter comma-separated numbers (or blank for none).")
        while True:
            raw = click.prompt("Selections", type=str, default="", show_default=False)
            if not raw.strip():
                return []
            try:
                parts = [int(p.strip()) - 1 for p in raw.split(",") if p.strip()]
            except ValueError:
                click.echo(f"  Could not parse {raw!r}")
                continue
            if any(p < 0 or p >= len(choices) for p in parts):
                click.echo(f"  Out of range in {raw!r}")
                continue
            # Dedupe while preserving order.
            seen: set[int] = set()
            unique: list[int] = []
            for p in parts:
                if p not in seen:
                    unique.append(p)
                    seen.add(p)
            return unique


class ScriptedPrompter:
    """Test Prompter that consumes a queue of pre-scripted answers.

    Each ``ask`` / ``confirm`` / ``select`` / ``multi_select`` pops one entry.
    For ``select`` and ``multi_select`` the entry is the integer index (or
    list of indices); other prompts get strings or bools.

    Exhausting the queue raises ``IndexError`` with the prompt that was being
    asked, so test failures pinpoint which prompt didn't get a scripted
    answer.
    """

    def __init__(self, answers: list[str | bool | int | list[int]]) -> None:
        self._answers = list(answers)

    def _pop(self, prompt: str) -> object:
        if not self._answers:
            raise IndexError(
                f"ScriptedPrompter exhausted: no answer left for prompt {prompt!r}"
            )
        return self._answers.pop(0)

    def ask(self, prompt: str, default: str | None = None) -> str:
        ans = self._pop(prompt)
        if isinstance(ans, str):
            return ans
        raise TypeError(
            f"ScriptedPrompter.ask({prompt!r}) expected str answer, got {type(ans).__name__}"
        )

    def confirm(self, prompt: str, default: bool = False) -> bool:
        ans = self._pop(prompt)
        if isinstance(ans, bool):
            return ans
        raise TypeError(
            f"ScriptedPrompter.confirm({prompt!r}) expected bool answer, got {type(ans).__name__}"
        )

    def select(self, prompt: str, choices: list[str]) -> int:
        ans = self._pop(prompt)
        # Reject bool first since bool is a subclass of int in Python.
        if isinstance(ans, bool) or not isinstance(ans, int):
            raise TypeError(
                f"ScriptedPrompter.select({prompt!r}) expected int answer, got {type(ans).__name__}"
            )
        if not (0 <= ans < len(choices)):
            raise ValueError(
                f"ScriptedPrompter.select({prompt!r}) index {ans} out of range "
                f"for {len(choices)} choices"
            )
        return ans

    def multi_select(self, prompt: str, choices: list[str]) -> list[int]:
        ans = self._pop(prompt)
        if not isinstance(ans, list):
            raise TypeError(
                f"ScriptedPrompter.multi_select({prompt!r}) expected list answer, got {type(ans).__name__}"
            )
        for v in ans:
            if not isinstance(v, int) or not (0 <= v < len(choices)):
                raise ValueError(
                    f"ScriptedPrompter.multi_select({prompt!r}) bad index {v!r}"
                )
        return list(ans)

    @property
    def remaining(self) -> int:
        return len(self._answers)
