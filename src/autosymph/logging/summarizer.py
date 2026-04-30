"""Activity summarizer — turns the live AgentEvent stream into a numbered Markdown bullet list for Linear stage comments.

Consumes the same `AgentEvent` stream as `TimelineExtractor` but parses richer
fields: `ASSISTANT_TURN.tool_uses` for tool inputs, `TOOL_RESULT.tool_results`
for outputs (correlated by `tool_use_id`).

Either method may raise on unexpected event shape; the orchestrator wraps both
in `try/except` and falls back to the legacy timeline block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from autosymph.runners.base import AgentEvent, EventType

# -- Configuration constants --

CONDENSE_THRESHOLD = 3  # collapse 3+ consecutive same-category entries
CONDENSE_LIST_LIMIT = 3  # show at most 3 paths/commands before "... and N more"
BULLET_CAP = 15  # hard cap on rendered bullets
SUMMARY_BULLET_CAP = 8  # cap for high-level Linear activity sections
SUMMARY_SNIPPET_LIMIT = 3  # max examples per high-level bullet
COMMAND_TRUNCATION = 60  # max chars from a Bash command
COMMIT_SUBJECT_TRUNCATION = 80  # max chars from a commit -m subject
ERROR_TRUNCATION = 100  # max chars from an error string
NARRATIVE_TRUNCATION = 120  # max chars of a narrative-only sentence

# Categories produced by `_classify_tool` and the narrative/error paths.
# Same-category consecutive runs are eligible for condensation.
_CONDENSABLE_CATEGORIES = {"read", "file_edit", "bash_other", "mcp", "tool_other"}


# -- Public types --


@dataclass
class Activity:
    """One item in the summary — produced by `ingest`, rendered by `format_summary`."""

    category: str
    text: str  # pre-rendered bullet body (URL appended later if any)
    tool_name: str | None = None
    tool_use_id: str | None = None
    command: str | None = None  # raw Bash command (used for PR-URL extractor re-check)
    paths: list[str] = field(default_factory=list)  # for read / file_edit categories
    url: str | None = None  # filled in by `_extract_url` when a tool_result yields one


# -- The summarizer --


class ActivitySummarizer:
    """Builds a numbered activity summary from a stream of `AgentEvent`s.

    Usage:
        summarizer = ActivitySummarizer()
        for event in stream:
            summarizer.ingest(event)
        markdown = summarizer.format_summary()

    The orchestrator wraps `ingest` in a try/except that sets `errored = True`
    on any exception, signalling the caller to fall back to the legacy comment.
    """

    def __init__(self) -> None:
        self.activities: list[Activity] = []
        self._by_id: dict[str, Activity] = {}
        self.errored: bool = False

    def ingest(self, event: AgentEvent) -> None:
        """Process one AgentEvent and append derived activities.

        Only `ASSISTANT_TURN`, `TOOL_RESULT`, and `ERROR` are consumed. All other
        event types are ignored — `TOOL_CALL` is redundant with `ASSISTANT_TURN`,
        `ASSISTANT_MESSAGE` is text deltas (use `ASSISTANT_TURN.texts` instead),
        and `TOKEN_USAGE` / `COMPLETION` / `SYSTEM` carry no narrative content.
        """
        if event.type == EventType.ASSISTANT_TURN:
            self._ingest_assistant_turn(event)
        elif event.type == EventType.TOOL_RESULT:
            self._ingest_tool_result(event)
        elif event.type == EventType.ERROR:
            self._ingest_error(event)

    # -- ingest helpers --

    def _ingest_assistant_turn(self, event: AgentEvent) -> None:
        tool_uses = event.data.get("tool_uses") or []
        texts = event.data.get("texts") or []

        for tu in tool_uses:
            # `_classify_tool` returns the rendered Activity; raises if `tu` is
            # not a dict (e.g. malformed event with a string in tool_uses).
            activity = self._classify_tool(tu)
            self.activities.append(activity)
            if activity.tool_use_id:
                self._by_id[activity.tool_use_id] = activity

        # Narrative fallback: turn has only text and no tool_uses.
        if not tool_uses and texts:
            joined = " ".join(t for t in texts if t).strip()
            if joined:
                snippet = _first_sentence(joined, NARRATIVE_TRUNCATION)
                if snippet:
                    self.activities.append(Activity(category="narrative", text=snippet))

    def _ingest_tool_result(self, event: AgentEvent) -> None:
        for result in event.data.get("tool_results") or []:
            tool_use_id = result.get("tool_use_id")
            if not tool_use_id:
                continue
            activity = self._by_id.get(tool_use_id)
            if activity is None:
                continue
            if result.get("is_error"):
                detail = _first_sentence(
                    _stringify_content(result.get("content")),
                    ERROR_TRUNCATION,
                )
                message = detail or "Tool result was marked as an error"
                self.activities.append(
                    Activity(
                        category="failure",
                        text=f"{_failure_label(activity)} failed: {message}",
                        tool_name=activity.tool_name,
                        tool_use_id=tool_use_id,
                    )
                )
                continue
            url = _extract_url(activity.tool_name or "", activity.command, result.get("content"))
            if url:
                activity.url = url

    def _ingest_error(self, event: AgentEvent) -> None:
        msg = event.data.get("error") or event.data.get("result") or "Unknown error"
        text = f"⚠ {str(msg)[:ERROR_TRUNCATION]}"
        self.activities.append(Activity(category="error", text=text))

    # -- tool classification --

    def _classify_tool(self, tu: dict[str, Any]) -> Activity:
        """Build an Activity from a tool_use dict.

        Raises if `tu` is not a dict — this is the documented ingest-failure
        signal. Per-tool extraction is keyed off the tool name; unknown tools
        get a generic `Used <name>` bullet so they aren't silently dropped.
        """
        tool_name = tu["name"]  # KeyError or TypeError if shape is wrong
        tool_id = tu.get("id")
        tu_input = tu.get("input") or {}

        if tool_name == "Skill":
            skill = tu_input.get("skill", "")
            return Activity(
                category="skill",
                text=f"/{skill}" if skill else "/Skill",
                tool_name=tool_name,
                tool_use_id=tool_id,
            )

        if tool_name == "Read":
            path = tu_input.get("file_path", "")
            return Activity(
                category="read",
                text=f"Read {path}" if path else "Read",
                tool_name=tool_name,
                tool_use_id=tool_id,
                paths=[path] if path else [],
            )

        if tool_name in ("Edit", "Write"):
            path = tu_input.get("file_path", "")
            return Activity(
                category="file_edit",
                text=f"Edited {path}" if path else "Edited",
                tool_name=tool_name,
                tool_use_id=tool_id,
                paths=[path] if path else [],
            )

        if tool_name == "Bash":
            command = tu_input.get("command", "") or ""
            return _classify_bash(command, tool_id)

        if tool_name.startswith("mcp__"):
            # mcp__<server>__<method> — parse server/method, opaque otherwise.
            parts = tool_name.split("__", 2)
            label = f"{parts[1]}.{parts[2]}" if len(parts) == 3 else tool_name
            return Activity(
                category="mcp",
                text=f"Used {label}",
                tool_name=tool_name,
                tool_use_id=tool_id,
            )

        return Activity(
            category="tool_other",
            text=f"Used {tool_name}",
            tool_name=tool_name,
            tool_use_id=tool_id,
        )

    # -- formatting --

    def format_summary(self) -> str:
        """Render activities as a numbered Markdown list, or `""` if empty.

        Applies condensation (3+ consecutive same-category → one bullet) and a
        15-bullet hard cap with truncation tail.
        """
        rendered = [self._render_with_url(a) for a in self._condense(self.activities)]
        rendered = [r for r in rendered if r]
        if not rendered:
            return ""

        if len(rendered) > BULLET_CAP:
            head = rendered[: BULLET_CAP - 1]
            extra = len(rendered) - (BULLET_CAP - 1)
            head.append(f"... and {extra} more steps (see log)")
            rendered = head

        return "\n".join(f"{i + 1}. {body}" for i, body in enumerate(rendered))

    def format_digest(self, extra_failure_modes: list[str] | None = None) -> str:
        """Render a concise Linear comment body with summary + failure modes.

        This is intentionally higher-level than `format_summary`: it groups the
        NDJSON event stream into human-readable work categories so Linear gets a
        useful state summary without replaying the whole tool timeline.
        """
        summary = self._summary_bullets()
        failures = self.failure_mode_bullets()
        if extra_failure_modes:
            failures = [*extra_failure_modes, *failures]

        if not summary and not failures:
            return ""

        lines: list[str] = []
        if summary:
            lines.extend(["### Activity summary", ""])
            lines.extend(f"- {bullet}" for bullet in summary[:SUMMARY_BULLET_CAP])
            if len(summary) > SUMMARY_BULLET_CAP:
                extra = len(summary) - SUMMARY_BULLET_CAP
                lines.append(f"- ... and {extra} more summary items.")

        lines.extend(["", "### Failure modes", ""])
        if failures:
            lines.extend(f"- {failure}" for failure in _dedupe_preserve_order(failures))
        else:
            lines.append("- No errors or failed tool results captured in the event stream.")

        return "\n".join(lines).strip()

    def failure_mode_bullets(self) -> list[str]:
        """Return failures captured from agent errors and failed tool results."""
        failures: list[str] = []
        if self.errored:
            failures.append(
                "Activity summarizer ingest failed; the NDJSON log is the source of truth."
            )
        for activity in self.activities:
            if activity.category == "error":
                failures.append(activity.text.removeprefix("⚠ ").strip())
            elif activity.category == "failure":
                failures.append(activity.text)
            elif activity.category == "narrative" and _looks_like_failure_note(activity.text):
                failures.append(activity.text)
        return failures

    def _summary_bullets(self) -> list[str]:
        skills: list[str] = []
        reads: list[str] = []
        edits: list[str] = []
        commits: list[str] = []
        prs: list[str] = []
        verification_commands: list[str] = []
        other_commands: list[str] = []
        mcp_tools: list[str] = []
        other_tools: list[str] = []
        narratives: list[str] = []

        for activity in self.activities:
            if activity.category == "skill":
                skills.append(activity.text)
            elif activity.category == "read":
                reads.extend(activity.paths or [activity.text.removeprefix("Read ").strip()])
            elif activity.category == "file_edit":
                edits.extend(activity.paths or [activity.text.removeprefix("Edited ").strip()])
            elif activity.category == "commit":
                commits.append(activity.text.removeprefix("Committed: ").strip())
            elif activity.category == "pr":
                prs.append(activity.url or "PR created")
            elif activity.category == "bash_other":
                command = activity.command or activity.text.removeprefix("Ran: ").strip()
                if _looks_like_verification_command(command):
                    verification_commands.append(command)
                else:
                    other_commands.append(command)
            elif activity.category == "mcp":
                mcp_tools.append(activity.text.removeprefix("Used ").strip())
            elif activity.category == "tool_other":
                other_tools.append(activity.text.removeprefix("Used ").strip())
            elif activity.category == "narrative":
                narratives.append(activity.text)

        bullets: list[str] = []
        if skills:
            bullets.append(f"Used skills: {_format_examples(skills)}.")
        if reads:
            bullets.append(f"Read {len(reads)} files, including {_format_examples(reads)}.")
        if edits:
            bullets.append(f"Edited {len(edits)} files, including {_format_examples(edits)}.")
        if verification_commands:
            bullets.append(f"Ran verification: {_format_examples(verification_commands)}.")
        if other_commands:
            bullets.append(
                f"Ran {len(other_commands)} other commands, "
                f"including {_format_examples(other_commands)}."
            )
        if commits:
            bullets.append(f"Committed changes: {_format_examples(commits)}.")
        if prs:
            bullets.append(f"Opened PR: {_format_examples(prs)}.")
        if mcp_tools:
            bullets.append(f"Used MCP tools: {_format_examples(mcp_tools)}.")
        if other_tools:
            bullets.append(f"Used other tools: {_format_examples(other_tools)}.")
        if narratives:
            bullets.append(f"Captured agent notes: {_format_examples(narratives)}.")

        return bullets

    def _render_with_url(self, activity: Activity) -> str:
        body = activity.text
        if activity.url:
            body = f"{body} ({activity.url})"
        return body

    def _condense(self, activities: list[Activity]) -> list[Activity]:
        """Collapse 3+ consecutive same-category entries into one summary bullet."""
        out: list[Activity] = []
        i = 0
        while i < len(activities):
            current = activities[i]
            if current.category not in _CONDENSABLE_CATEGORIES:
                out.append(current)
                i += 1
                continue

            run_end = i + 1
            while (
                run_end < len(activities) and activities[run_end].category == current.category
            ):
                run_end += 1
            run = activities[i:run_end]

            if len(run) < CONDENSE_THRESHOLD:
                out.extend(run)
            else:
                out.append(_summarize_run(run))
            i = run_end
        return out


# -- module-level helpers --


def _classify_bash(command: str, tool_id: str | None) -> Activity:
    stripped = command.strip()
    if stripped.startswith("git commit"):
        subject = _parse_commit_subject(stripped)[:COMMIT_SUBJECT_TRUNCATION]
        return Activity(
            category="commit",
            text=f"Committed: {subject}" if subject else "Committed",
            tool_name="Bash",
            tool_use_id=tool_id,
            command=command,
        )

    if stripped.startswith("gh pr create"):
        return Activity(
            category="pr",
            text="Opened PR",
            tool_name="Bash",
            tool_use_id=tool_id,
            command=command,
        )

    truncated = stripped[:COMMAND_TRUNCATION]
    return Activity(
        category="bash_other",
        text=f"Ran: {truncated}",
        tool_name="Bash",
        tool_use_id=tool_id,
        command=command,
    )


def _parse_commit_subject(command: str) -> str:
    """Extract the -m argument from a `git commit -m "<subject>"` invocation."""
    # Try double-quoted first, then single-quoted.
    for pattern in (r'-m\s+"([^"]+)"', r"-m\s+'([^']+)'", r"-m\s+(\S+)"):
        m = re.search(pattern, command)
        if m:
            return m.group(1).splitlines()[0]
    return ""


def _summarize_run(run: list[Activity]) -> Activity:
    """Collapse a same-category run of activities into one summary Activity."""
    sample = run[0]
    snippets = _collect_snippets(run)
    head = snippets[:CONDENSE_LIST_LIMIT]
    body = ", ".join(head)
    if len(snippets) > CONDENSE_LIST_LIMIT:
        body += f", ... and {len(snippets) - CONDENSE_LIST_LIMIT} more"

    label = _category_label(sample.category, len(run))
    text = f"{label} ({body})" if body else label
    return Activity(category=sample.category, text=text)


def _collect_snippets(run: list[Activity]) -> list[str]:
    """Pull the salient string from each Activity for condensed display."""
    out: list[str] = []
    for a in run:
        if a.paths:
            out.append(a.paths[0])
        elif a.command:
            out.append(a.command.strip()[:COMMAND_TRUNCATION])
        else:
            # Strip the leading verb (e.g. "Used ") so the condensed list is
            # readable.
            out.append(a.text.removeprefix("Used ").strip())
    return out


def _category_label(category: str, count: int) -> str:
    if category == "read":
        return f"Read {count} files"
    if category == "file_edit":
        return f"Edited {count} files"
    if category == "bash_other":
        return f"Ran {count} commands"
    if category == "mcp":
        return f"Used {count} MCP tools"
    if category == "tool_other":
        return f"Used {count} tools"
    return f"{count} {category} actions"


def _first_sentence(text: str, max_chars: int) -> str:
    """Return the first sentence (split on '. ') truncated to `max_chars`."""
    candidate = text.split(". ", 1)[0]
    candidate = candidate.strip()
    if len(candidate) > max_chars:
        return candidate[:max_chars].rstrip() + "…"
    return candidate


def _failure_label(activity: Activity) -> str:
    if activity.tool_name == "Bash" and activity.command:
        return f"Command `{activity.command.strip()[:COMMAND_TRUNCATION]}`"
    if activity.text:
        return activity.text
    return activity.tool_name or "Tool"


def _looks_like_verification_command(command: str) -> bool:
    lowered = command.lower()
    needles = (
        "pytest",
        "ruff",
        "mypy",
        "verify-completion-audit",
        "verify-preflight",
        "verify-finalize",
        "audit.sh",
        "swift test",
        "xcodebuild",
        "npm test",
        "npm run test",
        "pnpm test",
        "pnpm run test",
        "yarn test",
        "cargo test",
        "go test",
    )
    return any(needle in lowered for needle in needles)


def _looks_like_failure_note(text: str) -> bool:
    lowered = text.lower()
    needles = (
        "blocked",
        "contract violation",
        "denied",
        "doesn't exist",
        "does not exist",
        "error",
        "exit code 2",
        "fail",
        "not found",
        "permission",
        "unavailable",
        "violation",
        "0 tests",
    )
    return any(needle in lowered for needle in needles)


def _format_examples(items: list[str]) -> str:
    cleaned = [item.strip() for item in _dedupe_preserve_order(items) if item and item.strip()]
    head = cleaned[:SUMMARY_SNIPPET_LIMIT]
    rendered = ", ".join(f"`{item}`" for item in head)
    if len(cleaned) > SUMMARY_SNIPPET_LIMIT:
        rendered += f", and {len(cleaned) - SUMMARY_SNIPPET_LIMIT} more"
    return rendered


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _extract_url(tool_name: str, command: str | None, content: Any) -> str | None:
    """Best-effort URL extraction from a tool_result content payload.

    v1 rules — conservative to avoid noisy bullets:
    - `Bash` + `gh pr create` command → first GitHub PR URL in the result
    - `Bash` other → no URL extraction (commands too often grep URLs from stdout)
    - MCP tools → first `https?://...` URL, but skip known-noisy hosts
    - all other tools → no URL extraction
    """
    text = _stringify_content(content)
    if not text:
        return None

    if tool_name == "Bash":
        if command and command.strip().startswith("gh pr create"):
            m = re.search(r"https://github\.com/[^\s\)\"<>\\]+/pull/\d+", text)
            if m:
                return _strip_trailing_punct(m.group(0))
        return None

    if tool_name.startswith("mcp__") and not _is_aggregating_mcp_method(tool_name):
        for match in re.finditer(r"https?://[^\s\)\"<>\\]+", text):
            url = _strip_trailing_punct(match.group(0))
            if not _is_noisy_url(url):
                return url
        return None

    return None


def _strip_trailing_punct(url: str) -> str:
    """Drop sentence-ending punctuation that the URL regex over-matches."""
    return url.rstrip(".,;:!?")


# Hosts that produce URLs which are never useful as inline bullet links.
# Currently: Linear's signed CDN (JWT-bearing asset URLs).
_NOISY_URL_HOSTS = ("uploads.linear.app",)


def _is_noisy_url(url: str) -> bool:
    """Filter out signed asset URLs and other host patterns that add noise without value."""
    return any(host in url for host in _NOISY_URL_HOSTS)


# MCP methods that aggregate over many items — no single canonical URL on the
# action. Skip URL extraction; otherwise the bullet would inline some random
# nested URL from one of the listed items.
_AGGREGATING_MCP_PREFIXES = ("list_", "search_")


def _is_aggregating_mcp_method(tool_name: str) -> bool:
    parts = tool_name.split("__", 2)
    method = parts[2] if len(parts) == 3 else ""
    return method.startswith(_AGGREGATING_MCP_PREFIXES)


def _stringify_content(content: Any) -> str:
    """Flatten the tool_result content (string, list of blocks, etc.) into one string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text" and "text" in block:
                    parts.append(str(block["text"]))
                elif "text" in block:
                    parts.append(str(block["text"]))
        return "\n".join(parts)
    return str(content)
