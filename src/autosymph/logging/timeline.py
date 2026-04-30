"""Timeline extractor — parses AgentEvents into structured timeline (Tier 1 logging).

Produces entries like:
    0:45  Edit  src/components/VoiceButton.tsx
    1:12  Bash  pnpm test
    2:30  ✓    Agent completed
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from autosymph.runners.base import AgentEvent, EventType


@dataclass
class TimelineEntry:
    """A single entry in the simplified timeline."""

    timestamp: datetime
    elapsed_seconds: float
    kind: str  # "tool_call", "milestone", "error"
    summary: str


class TimelineExtractor:
    """Converts raw AgentEvents into a simplified timeline.

    Filters out noise, extracts tool call names and key milestones.
    Used by the TUI for real-time display and by the log viewer.
    """

    def __init__(self) -> None:
        self.entries: list[TimelineEntry] = []
        self._start_time: datetime | None = None

    def ingest(self, event: AgentEvent) -> TimelineEntry | None:
        """Process an event and optionally produce a timeline entry."""
        if not self._start_time:
            self._start_time = event.timestamp

        elapsed = (event.timestamp - self._start_time).total_seconds()

        if event.type == EventType.TOOL_CALL:
            name = event.data.get("tool_name", "unknown")
            entry = TimelineEntry(
                timestamp=event.timestamp,
                elapsed_seconds=elapsed,
                kind="tool_call",
                summary=name,
            )
            self.entries.append(entry)
            return entry

        if event.type == EventType.ERROR:
            error = event.data.get("result") or event.data.get("error", "Unknown error")
            entry = TimelineEntry(
                timestamp=event.timestamp,
                elapsed_seconds=elapsed,
                kind="error",
                summary=str(error)[:100],
            )
            self.entries.append(entry)
            return entry

        if event.type == EventType.COMPLETION:
            entry = TimelineEntry(
                timestamp=event.timestamp,
                elapsed_seconds=elapsed,
                kind="milestone",
                summary="Agent completed",
            )
            self.entries.append(entry)
            return entry

        return None

    def format_timeline(self) -> str:
        """Format all entries as a human-readable string."""
        lines = []
        for e in self.entries:
            mins = int(e.elapsed_seconds) // 60
            secs = int(e.elapsed_seconds) % 60
            lines.append(f"  {mins}:{secs:02d}  {e.kind:<12s} {e.summary}")
        return "\n".join(lines)
