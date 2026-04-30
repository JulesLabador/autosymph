# plan-claude-codex: Required Permissions

Add these to `.claude/settings.local.json` (project-level) or `~/.claude/settings.json` (global) to run the planning workflow with minimal manual approval.

## Setup

Merge the following into your `permissions.allow` array in the target settings file:

```json
{
  "permissions": {
    "allow": [
      "Bash(codex exec:*)",
      "Bash(codex review:*)",
      "Bash(codex --version)",
      "Bash(cat:*)",
      "Bash(which:*)",
      "Write(docs/plans/**)",
      "Write(PRD.md)",
      "Write(task-list.md)",
      "Edit(docs/plans/**)",
      "Edit(PRD.md)",
      "Edit(task-list.md)",
      "mcp__linear__get_issue",
      "mcp__linear__save_issue",
      "mcp__linear__save_comment",
      "mcp__linear__create_document",
      "mcp__linear__update_document",
      "mcp__linear__get_document",
      "mcp__linear__list_comments",
      "mcp__linear__list_documents",
      "mcp__linear__create_attachment"
    ]
  }
}
```

## What Each Permission Does

### Codex CLI
| Permission | Purpose |
|---|---|
| `Bash(codex exec:*)` | Pipe PRD to Codex for review with `-m gpt-5.4` (the core review loop) |
| `Bash(codex review:*)` | Codex review mode (future use) |
| `Bash(codex --version)` | Pre-flight check that Codex is installed |
| `Bash(cat:*)` | Read PRD/task-list files into shell variables for piping |
| `Bash(which:*)` | Check Codex CLI availability |

### File Writes
| Permission | Purpose |
|---|---|
| `Write/Edit(docs/plans/**)` | PRD and task-list drafts during review loop |
| `Write/Edit(PRD.md)` | Root-level PRD working copy |
| `Write/Edit(task-list.md)` | Root-level task-list working copy |

### Linear MCP
| Permission | Purpose |
|---|---|
| `mcp__linear__get_issue` | Fetch issue context (Step 1) |
| `mcp__linear__save_issue` | Create issue when only a description is provided (Step 1) |
| `mcp__linear__save_comment` | Post review round comments (Step 6.5) |
| `mcp__linear__create_document` | Publish PRD and task-list as Linear documents (Step 7) |
| `mcp__linear__update_document` | Update existing documents on re-plan (Step 7) |
| `mcp__linear__get_document` | Read existing documents for dedup check (Step 7) |
| `mcp__linear__list_comments` | Check existing review comments (Step 6) |
| `mcp__linear__list_documents` | Check if PRD/task-list docs already exist (Step 7) |
| `mcp__linear__create_attachment` | Link documents to the issue (Step 7) |

## Prerequisites

- **Codex CLI** installed and authenticated (`codex login`)
- **Linear MCP** configured in `.claude/mcp.json` or Codex `config.toml`
- **Linear API key** set via `LINEAR_API_KEY` env var or Codex auth

## Notes

- These permissions are additive — merge with existing `allow` array, don't replace it.
- The `Agent` tool (for subagents) does not require an explicit permission rule.
- If running from a new project for the first time, copy these into that project's `.claude/settings.local.json`.
