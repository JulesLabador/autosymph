"""Agent runner ABC — common interface for all AI agent backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable


class EventType(str, Enum):
    ASSISTANT_MESSAGE = "assistant_message"
    ASSISTANT_TURN = "assistant_turn"  # Full assistant message with tool_use blocks
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"  # Tool execution results from user events
    ERROR = "error"
    TOKEN_USAGE = "token_usage"
    COMPLETION = "completion"
    SYSTEM = "system"


@dataclass
class AgentEvent:
    """A single event from an agent's NDJSON stream."""

    type: EventType
    timestamp: datetime
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunResult:
    """Outcome of an agent run."""

    success: bool
    exit_code: int
    session_id: str | None = None
    events: list[AgentEvent] = field(default_factory=list)
    duration_seconds: float = 0.0
    token_usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None


class AgentRunner(ABC):
    """Abstract base for agent runners.

    Each runner knows how to:
    - Spawn its agent as a subprocess
    - Parse the agent's NDJSON output into AgentEvents
    - Detect completion or failure
    - Kill a stalled agent
    """

    @abstractmethod
    async def run(
        self,
        prompt: str,
        workspace_path: str,
        config: dict[str, Any],
        session_id: str | None = None,
        on_event: Callable[[AgentEvent], None] | None = None,
        on_raw_line: Callable[[str], None] | None = None,
    ) -> RunResult:
        """Execute the agent and return the result."""

    @abstractmethod
    def parse_event(self, line: str) -> AgentEvent | None:
        """Parse a single NDJSON line into an AgentEvent, or None if unparseable."""

    @abstractmethod
    async def kill(self, pid: int) -> None:
        """Terminate a running agent process."""
