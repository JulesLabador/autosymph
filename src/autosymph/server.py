"""HTTP status API for autosymph monitoring.

Serves status_summary() as JSON on localhost:{port}/status.
Provides a kill endpoint to terminate stuck agents.

Binds to 127.0.0.1 only (no external access). If the port is in use,
logs a warning and degrades gracefully — autosymph continues without the API.
"""

from __future__ import annotations

import asyncio
import json
import logging
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autosymph.supervisor import Supervisor

logger = logging.getLogger(__name__)


class StatusServer:
    """Minimal async HTTP server exposing orchestrator status as JSON.

    Endpoints:
        GET  /status  → status_summary() for all orchestrators
        POST /kill/{issue_id} → kill a running agent (by issue identifier)
    """

    def __init__(self, supervisor: Supervisor, host: str = "127.0.0.1", port: int = 4200) -> None:
        self._supervisor = supervisor
        self._host = host
        self._port = port
        self._server: asyncio.Server | None = None

    async def start(self) -> bool:
        """Start the HTTP server. Returns True on success, False on failure."""
        try:
            self._server = await asyncio.start_server(
                self._handle_connection, self._host, self._port
            )
            logger.info("Status API listening on http://%s:%d/status", self._host, self._port)
            return True
        except OSError as exc:
            logger.warning(
                "Status API failed to start on port %d: %s. "
                "Autosymph will continue without the status API.",
                self._port,
                exc,
            )
            return False

    async def stop(self) -> None:
        """Stop the HTTP server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info("Status API stopped")

    def _build_status(self) -> dict[str, Any]:
        """Aggregate status from all orchestrators."""
        projects: dict[str, Any] = {}
        total_completed: list[str] = []
        total_failed: list[str] = []

        for orch in self._supervisor.orchestrators:
            summary = orch.status_summary()
            slug = summary.get("project_slug", "unknown")
            projects[slug] = summary
            total_completed.extend(summary.get("completed_today", []))
            total_failed.extend(summary.get("failed_today", []))

        return {
            "projects": projects,
            "total_completed_today": len(total_completed),
            "total_failed_today": len(total_failed),
            "completed_issues": total_completed,
            "failed_issues": total_failed,
            "orchestrator_count": len(self._supervisor.orchestrators),
        }

    def _find_runner_issue(self, issue_id: str) -> tuple[Any, str] | None:
        """Find which orchestrator owns a running issue."""
        issue_id_lower = issue_id.lower()
        for orch in self._supervisor.orchestrators:
            summary = orch.status_summary()
            for identifier in summary.get("runners", {}):
                if identifier.lower() == issue_id_lower:
                    return orch, identifier
        return None

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a single HTTP connection."""
        try:
            # Read the request line
            request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not request_line:
                writer.close()
                return

            request_str = request_line.decode("utf-8", errors="replace").strip()
            parts = request_str.split()
            if len(parts) < 2:
                await self._send_response(writer, HTTPStatus.BAD_REQUEST, {"error": "malformed request"})
                return

            method, path = parts[0], parts[1]

            # Consume headers (we don't need them, but must read them)
            while True:
                header_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if header_line in (b"\r\n", b"\n", b""):
                    break

            # Route
            if method == "GET" and path == "/status":
                await self._handle_status(writer)
            elif method == "POST" and path.startswith("/kill/"):
                issue_id = path[len("/kill/"):]
                await self._handle_kill(writer, issue_id)
            elif method == "GET" and path == "/health":
                await self._send_response(writer, HTTPStatus.OK, {"status": "ok"})
            else:
                await self._send_response(writer, HTTPStatus.NOT_FOUND, {"error": "not found"})

        except (asyncio.TimeoutError, ConnectionError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_status(self, writer: asyncio.StreamWriter) -> None:
        """GET /status — return aggregated status for all orchestrators."""
        status = self._build_status()
        await self._send_response(writer, HTTPStatus.OK, status)

    async def _handle_kill(self, writer: asyncio.StreamWriter, issue_id: str) -> None:
        """POST /kill/{issue_id} — kill a running agent."""
        if not issue_id:
            await self._send_response(
                writer, HTTPStatus.BAD_REQUEST, {"error": "issue_id required"}
            )
            return

        result = self._find_runner_issue(issue_id)
        if result is None:
            await self._send_response(
                writer,
                HTTPStatus.NOT_FOUND,
                {"error": f"no active runner for {issue_id}"},
            )
            return

        orch, identifier = result
        # Cancel the agent's task
        running = getattr(orch, "_running", {})
        agent = running.get(identifier)
        if agent and hasattr(agent, "task") and agent.task:
            agent.task.cancel()
            await self._send_response(
                writer,
                HTTPStatus.OK,
                {"killed": identifier, "state": agent.workflow_state},
            )
            logger.info("Monitor killed agent for %s (state=%s)", identifier, agent.workflow_state)
        else:
            await self._send_response(
                writer,
                HTTPStatus.CONFLICT,
                {"error": f"{identifier} found but task not cancellable"},
            )

    async def _send_response(
        self,
        writer: asyncio.StreamWriter,
        status: HTTPStatus,
        body: dict[str, Any],
    ) -> None:
        """Send an HTTP/1.1 JSON response."""
        body_bytes = json.dumps(body, default=str).encode("utf-8")
        response = (
            f"HTTP/1.1 {status.value} {status.phrase}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body_bytes)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("utf-8") + body_bytes
        writer.write(response)
        await writer.drain()
