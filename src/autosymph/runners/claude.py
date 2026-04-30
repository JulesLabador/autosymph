"""Claude Code runner — spawns claude CLI in headless mode, parses NDJSON.

Spawn command:
    claude -p "{prompt}" --bare --output-format stream-json \
           --model {model} --permission-mode {mode} \
           --allowedTools "{tools}" --settings '{"maxTurns": N}'

Session resume:
    claude --resume {session_id} -p "{prompt}" ...

NDJSON format (each line):
    {"type": "stream_event", "event": {...}, "session_id": "...", ...}

Event types inside event.type:
    content_block_start   — tool_use start (name, id)
    content_block_delta   — text_delta or input_json_delta
    content_block_stop    — block complete
    message_delta         — stop_reason
    message_stop          — message complete
    system                — api_retry, errors
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from datetime import datetime, timezone
from typing import Any, Callable

from autosymph.runners.base import AgentEvent, AgentRunner, EventType, RunResult

logger = logging.getLogger(__name__)


class ClaudeRunner(AgentRunner):
    """Runner for Claude Code CLI."""

    async def run(
        self,
        prompt: str,
        workspace_path: str,
        config: dict[str, Any],
        session_id: str | None = None,
        on_event: Callable[[AgentEvent], None] | None = None,
        on_raw_line: Callable[[str], None] | None = None,
    ) -> RunResult:
        """Spawn claude CLI and stream NDJSON events. Prompt piped via stdin.

        ``config`` may include optional metadata used only for logging:
        - ``identifier`` (str): Linear issue identifier (e.g. IMP-356)
        - ``workflow_state`` (str): autosymph workflow state name
        - ``prompt_path`` (str): path of the agent prompt file
        These are not required for execution; they are emitted in the
        ``claude session start:`` audit line so downstream graps can correlate
        the issue, state, model, and prompt with the actual ``--model`` argv.
        """
        cmd = self._build_command(config, session_id)
        # IMP-356 R11: structured session-start log line emitted at the actual
        # subprocess spawn site, naming the literal --model argv so AC3/AC6/AC7/AC8
        # are verifiable from logs even if a future refactor diverges
        # config["model"] from the rendered cmd.
        rendered_model = self._extract_arg(cmd, "--model")
        logger.info(
            "claude session start: identifier=%s state=%s model=%s prompt=%s",
            config.get("identifier", "-"),
            config.get("workflow_state", "-"),
            rendered_model or "-",
            config.get("prompt_path", "-"),
        )
        logger.info("Spawning claude in %s (prompt: %d chars)", workspace_path, len(prompt))

        start = time.monotonic()
        events: list[AgentEvent] = []
        captured_session_id: str | None = None
        token_usage: dict[str, int] = {}

        # Merge resource env vars into subprocess environment
        env = None
        extra_env = config.get("env_vars")
        if extra_env:
            env = {**os.environ, **extra_env}

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace_path,
                env=env,
                limit=10 * 1024 * 1024,  # 10MB — Claude NDJSON lines can exceed default 64KB
            )
        except FileNotFoundError:
            return RunResult(
                success=False,
                exit_code=-1,
                error="'claude' CLI not found — is Claude Code installed?",
            )

        self._pid = proc.pid

        # Feed prompt via stdin
        if proc.stdin:
            proc.stdin.write(prompt.encode())
            proc.stdin.close()

        try:
            async for line in self._read_lines(proc.stdout):
                # Tier 2: tee raw line to disk
                if on_raw_line:
                    on_raw_line(line)

                event = self.parse_event(line)
                if not event:
                    continue

                events.append(event)

                # Capture session_id from first event
                if not captured_session_id:
                    captured_session_id = event.data.get("session_id")

                # Capture token usage from result event (final totals)
                if event.type == EventType.COMPLETION:
                    usage = event.data.get("usage", {})
                    for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                        val = usage.get(key)
                        if val:
                            token_usage[key] = val  # result has cumulative totals

                if on_event:
                    on_event(event)

        except asyncio.CancelledError:
            await self.kill(proc.pid)
            raise

        # Wait for process exit
        await proc.wait()
        stderr_out = await proc.stderr.read() if proc.stderr else b""
        duration = time.monotonic() - start

        exit_code = proc.returncode or 0

        # Check for errors in the event stream (auth failures, max turns, etc.)
        error_events = [e for e in events if e.type == EventType.ERROR]
        stream_error = None
        if error_events:
            last_err = error_events[-1].data
            stream_error = last_err.get("_parsed_error") or last_err.get("result") or last_err.get("error", "unknown error")

        success = exit_code == 0 and not stream_error

        if not success and stderr_out:
            logger.warning("Claude stderr: %s", stderr_out.decode(errors="replace")[:500])

        logger.info(
            "Claude exited code=%d duration=%.1fs tokens=%s session=%s",
            exit_code, duration, token_usage, captured_session_id,
        )

        return RunResult(
            success=success,
            exit_code=exit_code,
            session_id=captured_session_id,
            events=events,
            duration_seconds=duration,
            token_usage=token_usage,
            error=stream_error or (stderr_out.decode(errors="replace")[:500] if not success and stderr_out else None),
        )

    @staticmethod
    def _extract_arg(cmd: list[str], flag: str) -> str | None:
        """Return the value following ``flag`` in a command argv, or None.

        Used by ``run`` so the session-start log captures the literal --model
        argv rather than re-reading the input dict (which could diverge from
        argv if a future refactor adds a transform in ``_build_command``).
        """
        try:
            idx = cmd.index(flag)
        except ValueError:
            return None
        return cmd[idx + 1] if idx + 1 < len(cmd) else None

    def _build_command(
        self,
        config: dict[str, Any],
        session_id: str | None,
    ) -> list[str]:
        """Build the claude CLI command. Prompt is piped via stdin."""
        cmd = ["claude"]

        # Session resume
        if session_id:
            cmd.extend(["--resume", session_id])

        # Read prompt from stdin
        cmd.extend(["-p", "-"])

        # Core flags
        cmd.extend(["--output-format", "stream-json", "--verbose"])

        # Model
        model = config.get("model", "claude-sonnet-4-6")
        cmd.extend(["--model", model])

        # Permission mode
        perm = config.get("permission_mode", "acceptEdits")
        cmd.extend(["--permission-mode", perm])

        # Allowed tools
        tools = config.get("allowed_tools")
        if tools:
            cmd.extend(["--allowedTools", tools])

        # MCP config file (JSON path or inline JSON string)
        mcp_config = config.get("mcp_config")
        if mcp_config:
            cmd.extend(["--mcp-config", mcp_config])

        # Runtime settings — maxTurns, etc. passed via --settings JSON
        settings: dict[str, Any] = {}
        max_turns = config.get("max_turns")
        if max_turns:
            settings["maxTurns"] = max_turns
        if settings:
            import json as _json
            cmd.extend(["--settings", _json.dumps(settings)])

        return cmd

    def parse_event(self, line: str) -> AgentEvent | None:
        """Parse a Claude Code NDJSON line into an AgentEvent."""
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None

        now = datetime.now(timezone.utc)
        top_type = data.get("type", "")

        # System events (init, api_retry, errors)
        if top_type == "system":
            subtype = data.get("subtype", "")
            if subtype in ("api_retry", "error"):
                return AgentEvent(type=EventType.SYSTEM, timestamp=now, data=data)
            return None

        # Result event — final outcome (may contain errors like auth failures)
        if top_type == "result":
            is_error = data.get("is_error", False)
            if is_error:
                subtype = data.get("subtype", "")
                errors_list = data.get("errors", [])
                num_turns = data.get("num_turns")
                # Build a human-readable error message
                if subtype == "error_max_turns":
                    error_msg = f"max turns reached ({num_turns} turns used)"
                    if errors_list:
                        error_msg += f" — {errors_list[0]}"
                elif errors_list:
                    error_msg = "; ".join(errors_list)
                else:
                    error_msg = data.get("result") or subtype or "unknown error"
                logger.error("Claude result error: %s", error_msg)
                data["_parsed_error"] = error_msg
                return AgentEvent(type=EventType.ERROR, timestamp=now, data=data)
            return AgentEvent(type=EventType.COMPLETION, timestamp=now, data=data)

        # Assistant message — full turn with tool_use blocks and/or text
        if top_type == "assistant":
            error = data.get("error")
            if error:
                msg = data.get("message", {})
                text = ""
                for block in msg.get("content", []):
                    if block.get("type") == "text":
                        text = block.get("text", "")
                logger.error("Claude error (%s): %s", error, text)
                return AgentEvent(type=EventType.ERROR, timestamp=now, data=data)
            # Extract token usage from assistant message (Claude CLI format)
            msg = data.get("message", {})
            usage = msg.get("usage", {})
            if usage:
                # Emit TOKEN_USAGE event so TUI can track tokens live
                self._pending_usage = usage
            # Emit ASSISTANT_TURN with parsed content blocks
            content = msg.get("content", [])
            tool_uses = [b for b in content if b.get("type") == "tool_use"]
            thinking = [b.get("thinking", "") for b in content if b.get("type") == "thinking"]
            texts = [b.get("text", "") for b in content if b.get("type") == "text"]
            if tool_uses or thinking or texts:
                return AgentEvent(
                    type=EventType.ASSISTANT_TURN,
                    timestamp=now,
                    data={
                        **data,
                        "tool_uses": tool_uses,
                        "thinking": thinking,
                        "texts": texts,
                        "usage": usage,
                    },
                )
            # Even if no content blocks, emit usage if present
            if usage:
                return AgentEvent(type=EventType.TOKEN_USAGE, timestamp=now, data={"usage": usage})
            return None

        # User message — tool execution results
        if top_type == "user":
            msg = data.get("message", {})
            content = msg.get("content", [])
            tool_results = [b for b in content if b.get("type") == "tool_result"]
            if tool_results:
                return AgentEvent(
                    type=EventType.TOOL_RESULT,
                    timestamp=now,
                    data={**data, "tool_results": tool_results},
                )
            return None

        # Stream events wrap the actual Claude API events
        if top_type != "stream_event":
            return None

        event = data.get("event", {})
        event_type = event.get("type", "")

        # Tool call start
        if event_type == "content_block_start":
            block = event.get("content_block", {})
            if block.get("type") == "tool_use":
                return AgentEvent(
                    type=EventType.TOOL_CALL,
                    timestamp=now,
                    data={
                        **data,
                        "tool_name": block.get("name"),
                        "tool_id": block.get("id"),
                    },
                )

        # Text content
        if event_type == "content_block_delta":
            delta = event.get("delta", {})
            delta_type = delta.get("type", "")
            if delta_type == "text_delta":
                return AgentEvent(
                    type=EventType.ASSISTANT_MESSAGE,
                    timestamp=now,
                    data={**data, "text": delta.get("text", "")},
                )

        # Message complete
        if event_type == "message_stop":
            return AgentEvent(type=EventType.COMPLETION, timestamp=now, data=data)

        # Message delta with stop reason
        if event_type == "message_delta":
            delta = event.get("delta", {})
            usage = event.get("usage", {})
            if usage:
                return AgentEvent(type=EventType.TOKEN_USAGE, timestamp=now, data=data)
            if delta.get("stop_reason"):
                return AgentEvent(
                    type=EventType.COMPLETION,
                    timestamp=now,
                    data={**data, "stop_reason": delta["stop_reason"]},
                )

        return None

    async def _read_lines(self, stream: asyncio.StreamReader | None):
        """Async generator that yields lines from the subprocess stdout."""
        if not stream:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            yield line.decode(errors="replace").rstrip("\n")

    async def kill(self, pid: int) -> None:
        """Send SIGTERM to a claude process."""
        try:
            os.kill(pid, signal.SIGTERM)
            logger.info("Sent SIGTERM to claude pid=%d", pid)
        except ProcessLookupError:
            pass
