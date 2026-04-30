"""@mention comment dispatch — parse Linear comments into agent instructions."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class MentionCommand:
    """A parsed @mention command from a Linear comment."""

    action: str  # "rework", "verify", "dispatch"
    instructions: str  # free-text after the action keyword
    comment_id: str
    issue_id: str


# Matches: @rework fix the padding
#          @verify retake with ?test=demo
MENTION_PATTERN = re.compile(r"@(rework|verify|dispatch)\s+(.*)", re.IGNORECASE)


def parse_mention(comment_body: str, comment_id: str, issue_id: str) -> MentionCommand | None:
    """Extract a MentionCommand from a comment body, or None if not a command."""
    match = MENTION_PATTERN.search(comment_body)
    if not match:
        return None

    return MentionCommand(
        action=match.group(1).lower(),
        instructions=match.group(2).strip(),
        comment_id=comment_id,
        issue_id=issue_id,
    )
