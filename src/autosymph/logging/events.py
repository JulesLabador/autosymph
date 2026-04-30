"""Orchestrator event log (Tier 3).

Structured log of orchestrator-level events:
    DISPATCH, COMPLETE, FAIL, TIMEOUT, RETRY, GATE, STATE_CHANGE

Written to: {log_root}/orchestrator.log (default ~/.autosymph/logs/)

Format:
    {iso_timestamp} {event} {issue_id} state={state} run={N} [details]
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class EventLog:
    """Append-only orchestrator event log."""

    def __init__(self, log_root: Path) -> None:
        self.log_root = log_root.expanduser().resolve()
        self.log_root.mkdir(parents=True, exist_ok=True)
        self._path = self.log_root / "orchestrator.log"

    def _write(self, event: str, issue_id: str, **kwargs: str | int | float | None) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        details = " ".join(f"{k}={v}" for k, v in kwargs.items() if v is not None)
        line = f"{ts} {event:<14s} {issue_id} {details}\n"
        with self._path.open("a") as f:
            f.write(line)

    def dispatch(
        self,
        issue_id: str,
        state: str,
        run: int,
        session_name: str,
        runner: str = "claude",
    ) -> None:
        self._write("DISPATCH", issue_id, state=state, run=run, session=session_name, runner=runner)

    def complete(
        self, issue_id: str, state: str, run: int, duration_s: float, tokens: int | None = None,
    ) -> None:
        token_str = f"{tokens // 1000}k" if tokens else None
        self._write("COMPLETE", issue_id, state=state, run=run, duration=f"{duration_s:.0f}s", tokens=token_str)

    def fail(self, issue_id: str, state: str, run: int, reason: str | None = None) -> None:
        self._write("FAIL", issue_id, state=state, run=run, reason=reason)

    def timeout(self, issue_id: str, state: str, reason: str = "stall") -> None:
        self._write("TIMEOUT", issue_id, state=state, reason=reason)

    def gate(self, issue_id: str, state: str, status: str = "waiting") -> None:
        self._write("GATE", issue_id, state=state, status=status)

    def state_change(self, issue_id: str, from_state: str, to_state: str) -> None:
        self._write("STATE_CHANGE", issue_id, **{"from": from_state, "to": to_state})

    def rework_exhausted(self, issue_id: str, count: int) -> None:
        self._write("REWORK_CAP", issue_id, cycles=count, action="back_to_todo")

    def read_recent(self, issue_id: str | None = None, limit: int = 50) -> list[str]:
        """Read recent log lines, optionally filtered by issue."""
        if not self._path.exists():
            return []
        lines = self._path.read_text().splitlines()
        if issue_id:
            lines = [line for line in lines if issue_id in line]
        return lines[-limit:]
