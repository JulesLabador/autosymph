"""TUI — real-time terminal status board.

Uses simple ANSI escape codes to render in-place. No Rich Live (it fights
with raw terminal mode and async).

Supports both single-orchestrator and multi-instance (Supervisor) modes.
In multi-instance mode, renders grouped sections per project with config
metadata headers and collapses idle projects to a single line.
"""

from __future__ import annotations

import asyncio
import os
import sys
import termios
import time
import tty
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autosymph.orchestrator import Orchestrator
    from autosymph.supervisor import Supervisor

VERSION = "0.1.0"


def _format_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s"


STATE_ICONS = {
    "implement": "\033[36m",   # cyan
    "verify": "\033[33m",      # yellow
    "verify_review": "\033[33m",  # yellow (same as verify)
    "review": "\033[35m",      # magenta
    "rework": "\033[31m",      # red
    "finalize": "\033[32m",    # green
}
# Display order for tracked issues (lower = higher priority)
STATE_ORDER = {
    "implement": 0,
    "rework": 1,
    "verify": 2,
    "verify_review": 3,
    "finalize": 3,
    "review": 4,
}
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"


def render_status(orch: Orchestrator) -> str:
    """Build a plain-text status display for a single orchestrator."""
    status = orch.status_summary()
    lines = []

    # Header
    next_poll = int(status["next_poll_s"])
    interval = int(status["polling_interval_s"])
    lines.append(f"{BOLD}autosymph{RESET} {DIM}v{VERSION} | polling: {interval}s | next: {next_poll}s{RESET}")
    lines.append(f"{DIM}{'─' * 55}{RESET}")

    # Runners
    active = status["runners_active"]
    max_r = status["runners_max"]
    color = "\033[36m" if active > 0 else DIM
    lines.append(f" {BOLD}RUNNERS{RESET}  {color}{active}/{max_r} active{RESET}")
    lines.append("")

    runners = status.get("runners", {})
    if runners:
        for ident, info in runners.items():
            state = info["state"]
            dur = _format_duration(info["duration_s"])
            turns = info.get("turns", 0)
            tokens = info.get("tokens", 0)
            last_tool = info.get("last_tool", "")
            runner = info.get("runner", "claude")
            c = STATE_ICONS.get(state, "")
            token_str = f"{tokens:,}" if tokens else "0"
            tool_str = f"  {DIM}→ {last_tool}{RESET}" if last_tool else ""
            lines.append(
                f" {BOLD}{ident:<10s}{RESET} {c}{state:<14s}{RESET} "
                f"{DIM}{dur:>8s}{RESET}  "
                f"{runner:<6s} turns={turns:<3d} tok={token_str}"
                f"{tool_str}"
            )
    else:
        lines.append(f" {DIM}No active runners{RESET}")

    lines.append("")

    # Tracked issues (sorted by state priority)
    tracked = status.get("tracked_issues", {})
    if tracked:
        sorted_issues = sorted(
            tracked.items(),
            key=lambda x: (STATE_ORDER.get(x[1]["state"], 99), x[0]),
        )
        lines.append(f" {BOLD}ISSUES{RESET}   {DIM}{len(tracked)} tracked{RESET}")
        lines.append("")
        for ident, info in sorted_issues:
            state = info["state"]
            c = STATE_ICONS.get(state, DIM)
            lines.append(f"   {BOLD}{ident:<10s}{RESET} {c}{state}{RESET}")
        lines.append("")

    # Completed / failed
    completed = status["completed_today"]
    failed = status["failed_today"]
    c_color = "\033[32m" if completed else DIM
    lines.append(f" {BOLD}COMPLETED{RESET}  {c_color}{len(completed)}{RESET}")

    if failed:
        lines.append(f" {BOLD}FAILED{RESET}     \033[31m{len(failed)}{RESET} {DIM}({', '.join(failed[-3:])}){RESET}")

    # Warnings
    warnings = status.get("warnings", [])
    if warnings:
        lines.append(f" {BOLD}WARNINGS{RESET}")
        for w in warnings:
            lines.append(f"   \033[33m⚠ {w}{RESET}")
        lines.append("")

    lines.append(f" {DIM}[q] quit  [r] refresh{RESET}")

    return "\n".join(lines)


# -- Multi-instance rendering --

# Section header colors (cycle through for visual distinction)
_SECTION_COLORS = ["\033[36m", "\033[33m", "\033[35m", "\033[32m", "\033[34m"]


def _render_project_section(status: dict, color: str, failed: bool = False, error: str = "") -> list[str]:
    """Render a single project section for the multi-instance TUI."""
    lines = []
    slug = status.get("project_slug", "unknown")
    config_path = status.get("config_path", "")
    repo_path = status.get("repo_path", "")
    interval = int(status.get("polling_interval_s", 0))
    active = status.get("runners_active", 0)
    max_r = status.get("runners_max", 0)

    # Config filename only (not full path)
    config_name = os.path.basename(config_path) if config_path else ""
    # Shorten repo path
    repo_short = repo_path.replace(os.path.expanduser("~"), "~") if repo_path else ""

    # Section header
    if failed:
        lines.append(f" \033[31m{'═' * 50}{RESET}")
        lines.append(f" \033[31m{BOLD}{slug}{RESET} \033[31m— FAILED: {error}{RESET}")
        lines.append(f" \033[31m{'═' * 50}{RESET}")
        return lines

    runners = status.get("runners", {})
    tracked = status.get("tracked_issues", {})

    # Idle project — collapse to single line
    if active == 0 and not tracked:
        lines.append(
            f" {color}▸{RESET} {BOLD}{slug}{RESET}"
            f"  {DIM}{config_name} · {repo_short} · polling: {interval}s · idle{RESET}"
        )
        return lines

    # Active project — full section
    lines.append(f" {color}{'═' * 50}{RESET}")
    lines.append(
        f" {color}{BOLD}{slug}{RESET}"
        f"  {DIM}{config_name} · {repo_short} · polling: {interval}s"
        f" · {active}/{max_r} agents{RESET}"
    )

    # Runners
    if runners:
        for ident, info in runners.items():
            state = info["state"]
            dur = _format_duration(info["duration_s"])
            turns = info.get("turns", 0)
            tokens = info.get("tokens", 0)
            last_tool = info.get("last_tool", "")
            runner = info.get("runner", "claude")
            c = STATE_ICONS.get(state, "")
            token_str = f"{tokens:,}" if tokens else "0"
            tool_str = f"  {DIM}→ {last_tool}{RESET}" if last_tool else ""
            lines.append(
                f"   {BOLD}{ident:<10s}{RESET} {c}{state:<14s}{RESET} "
                f"{DIM}{dur:>8s}{RESET}  "
                f"{runner:<6s} turns={turns:<3d} tok={token_str}"
                f"{tool_str}"
            )

    # Tracked issues
    if tracked:
        sorted_issues = sorted(
            tracked.items(),
            key=lambda x: (STATE_ORDER.get(x[1]["state"], 99), x[0]),
        )
        for ident, info in sorted_issues:
            if ident not in runners:
                state = info["state"]
                c = STATE_ICONS.get(state, DIM)
                lines.append(f"   {DIM}{ident:<10s}{RESET} {c}{state}{RESET}")

    return lines


def render_multi_status(orchestrators: list[Orchestrator], supervisor_status: dict | None = None) -> str:
    """Build a multi-instance status display with grouped project sections."""
    lines = []
    total_active = 0
    total_projects = len(orchestrators)

    # Gather all summaries first
    summaries = []
    for orch in orchestrators:
        s = orch.status_summary()
        summaries.append(s)
        total_active += s.get("runners_active", 0)

    # Global header
    global_cap = ""
    if supervisor_status:
        cm_status = supervisor_status.get("concurrency", {})
        if cm_status:
            global_cap = f"/{cm_status.get('global_max', '?')}"
    lines.append(
        f"{BOLD}autosymph{RESET} {DIM}v{VERSION}"
        f" | {total_projects} projects"
        f" | {total_active}{global_cap} agents{RESET}"
    )
    lines.append(f"{DIM}{'─' * 55}{RESET}")

    # Per-project sections
    for i, summary in enumerate(summaries):
        color = _SECTION_COLORS[i % len(_SECTION_COLORS)]
        slug = summary.get("project_slug", "unknown")

        # Check if this project is failed (from supervisor status)
        failed = False
        error = ""
        if supervisor_status:
            orch_status = supervisor_status.get("projects", {}).get(slug, {})
            if orch_status.get("state") == "failed":
                failed = True
                error = orch_status.get("last_error", "unknown error")

        section_lines = _render_project_section(summary, color, failed=failed, error=error)
        lines.extend(section_lines)

    lines.append("")

    # Global completed/failed
    all_completed = []
    all_failed = []
    for s in summaries:
        all_completed.extend(s.get("completed_today", []))
        all_failed.extend(s.get("failed_today", []))

    c_color = "\033[32m" if all_completed else DIM
    lines.append(f" {BOLD}COMPLETED{RESET}  {c_color}{len(all_completed)}{RESET}")
    if all_failed:
        lines.append(
            f" {BOLD}FAILED{RESET}     \033[31m{len(all_failed)}{RESET}"
            f" {DIM}({', '.join(all_failed[-3:])}){RESET}"
        )

    # Discovery warnings (from supervisor)
    warnings = []
    if supervisor_status:
        warnings = supervisor_status.get("warnings", [])
    if warnings:
        lines.append(f" {BOLD}WARNINGS{RESET}")
        for w in warnings:
            lines.append(f"   \033[33m⚠ {w}{RESET}")

    lines.append("")
    lines.append(f" {DIM}[q] quit  [r] refresh  [j/k] scroll{RESET}")

    return "\n".join(lines)


class TUI:
    """Terminal status board with keyboard input.

    Supports two modes:
    - Single orchestrator (backward compat): TUI(orchestrator=orch)
    - Multi-instance via Supervisor: TUI(supervisor=sup)
    """

    def __init__(
        self,
        orchestrator: Orchestrator | None = None,
        supervisor: Supervisor | None = None,
    ) -> None:
        self._supervisor = supervisor
        self._single_orch = orchestrator
        self._flash: str | None = None
        self._flash_until: float = 0
        self._scroll_offset: int = 0

        # For backward compat: if only orchestrator passed, wrap it
        if orchestrator and not supervisor:
            self._multi = False
        elif supervisor:
            self._multi = True
        else:
            raise ValueError("TUI requires either orchestrator or supervisor")

    @property
    def orch(self) -> Orchestrator:
        """Backward-compat: single orchestrator reference."""
        if self._single_orch:
            return self._single_orch
        # In multi mode, return first orchestrator (for legacy callers)
        orchs = self._supervisor.orchestrators if self._supervisor else []
        if orchs:
            return orchs[0]
        raise RuntimeError("No orchestrators available")

    async def run(self) -> None:
        """Start TUI alongside the orchestrator(s)."""
        if self._multi and self._supervisor:
            # Multi-instance: Supervisor manages orchestrator lifecycle
            sup_task = asyncio.create_task(self._supervisor.run())
            display_task = asyncio.create_task(self._display_loop())
            kb_task = asyncio.create_task(self._keyboard_loop())

            try:
                done, pending = await asyncio.wait(
                    [sup_task, display_task, kb_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
            except asyncio.CancelledError:
                pass
            finally:
                sys.stdout.write("\033[?25h\n")
                sys.stdout.flush()
        else:
            # Single orchestrator mode (backward compat)
            orch_task = asyncio.create_task(self.orch.run())
            display_task = asyncio.create_task(self._display_loop())
            kb_task = asyncio.create_task(self._keyboard_loop())

            try:
                done, pending = await asyncio.wait(
                    [orch_task, display_task, kb_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
            except asyncio.CancelledError:
                pass
            finally:
                sys.stdout.write("\033[?25h\n")
                sys.stdout.flush()

    async def _display_loop(self) -> None:
        """Redraw the screen every second."""
        sys.stdout.write("\033[?25l\033[2J\033[H")
        sys.stdout.flush()

        while True:
            # Handle flash message
            flash_str = ""
            now = time.monotonic()
            if self._flash:
                self._flash_until = now + 2
                flash_str = f" \033[33m{self._flash}{RESET}"
                self._flash = None
            elif now < self._flash_until:
                flash_str = f" \033[33mpolling...{RESET}"

            # Render based on mode
            if self._multi and self._supervisor:
                sup_status = self._supervisor.status()
                output = render_multi_status(
                    self._supervisor.orchestrators,
                    supervisor_status=sup_status,
                )
            else:
                output = render_status(self.orch)

            if flash_str:
                first_newline = output.index("\n") if "\n" in output else len(output)
                output = output[:first_newline] + flash_str + output[first_newline:]

            # Scrolling: apply offset if content exceeds terminal height
            output_lines = output.split("\n")
            try:
                term_height = os.get_terminal_size().lines - 1
            except OSError:
                term_height = 40
            if len(output_lines) > term_height:
                max_scroll = max(0, len(output_lines) - term_height)
                self._scroll_offset = min(self._scroll_offset, max_scroll)
                visible = output_lines[self._scroll_offset:self._scroll_offset + term_height]
                if self._scroll_offset > 0:
                    visible[0] = f"{DIM}↑ {self._scroll_offset} more{RESET}"
                if self._scroll_offset < max_scroll:
                    visible[-1] = f"{DIM}↓ {max_scroll - self._scroll_offset} more{RESET}"
                output = "\n".join(visible)

            sys.stdout.write("\033[H\033[J")
            sys.stdout.write(output)
            sys.stdout.flush()

            await asyncio.sleep(1)

    async def _keyboard_loop(self) -> None:
        """Listen for keypresses: q=quit, r=refresh, j/k=scroll."""
        loop = asyncio.get_running_loop()
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            while True:
                char = await loop.run_in_executor(None, lambda: sys.stdin.read(1))
                if char in ("q", "Q", "\x03"):
                    if self._multi and self._supervisor:
                        self._supervisor.shutdown()
                    else:
                        await self.orch.shutdown()
                    return
                if char in ("r", "R"):
                    self._flash = "polling..."
                    if self._multi and self._supervisor:
                        for orch in self._supervisor.orchestrators:
                            asyncio.create_task(orch.poll_tick())
                    else:
                        asyncio.create_task(self.orch.poll_tick())
                if char in ("j",):
                    self._scroll_offset += 1
                if char in ("k",):
                    self._scroll_offset = max(0, self._scroll_offset - 1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
