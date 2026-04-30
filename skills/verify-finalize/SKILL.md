---
name: verify-finalize
description: MANDATORY end-action for the verify agent — script-driven completion that uploads all visual evidence to Linear AND posts the verification summary as ONE atomic operation. Replaces hand-rolled save_comment + create_attachment loops that can fail mechanically across long verify runs. Triggers on "verify finalize", invoked at the end of every verify run.
---

# verify-finalize

The verify agent's ONLY end-action. Build a manifest, call this script. Done.

## Why this skill exists

`verify-completion-audit` gives deterministic detection of "the agent took
screenshots and uploaded none." Detection alone is not enough: prompt-only
enforcement can still be skipped during a long verify run.

This skill replaces the discipline rule with a **mechanical can't-skip-it**:
the agent's only end-action is calling `verify-finalize.sh`. The script
handles upload + post atomically. The agent CAN'T forget — there's no
separate sub-step to forget.

## When to use

**Always**, as the LAST tool call of every verify run. Replaces:
- All direct `mcp__linear__create_attachment` calls
- The single `mcp__linear__save_comment` call with the summary table

If preflight (Phase 0) blocked verify before iOS work, you still call
verify-finalize with the BLOCKED-marked items. There's never a verify run
that should NOT end with verify-finalize.

## Manifest schema

JSON file. Required fields: `issue_id`, `session_name`, `result`, `items`.
Optional: `minor_fixes_applied`.

```json
{
  "issue_id": "ISSUE-123",
  "session_name": "ISSUE-123-verify-run14",
  "result": "complete",
  "minor_fixes_applied": "none",
  "items": [
    {
      "n": 1,
      "item": "swift build passes",
      "method": "cli",
      "status": "PASS",
      "evidence": "exit code 0"
    },
    {
      "n": 6,
      "item": "Header in Text mode",
      "method": "ios-simulator + video",
      "status": "PASS",
      "evidence_path": "<workspace>/verify-1.mp4",
      "evidence_kind": "video"
    },
    {
      "n": 12,
      "item": "Dynamic Type largest size",
      "method": "screenshot",
      "status": "PASS",
      "evidence_path": "<workspace>/verify-2.png",
      "evidence_kind": "image"
    },
    {
      "n": 8,
      "item": "iOS chrome",
      "method": "ios-simulator + video",
      "status": "BLOCKED",
      "evidence": "preflight: idb broken (pyexpat ImportError)"
    },
    {
      "n": 16,
      "item": "Aesthetic match vs design ref",
      "method": "human",
      "status": "SKIPPED: human-only"
    }
  ]
}
```

### Field semantics

| Field | Required | Notes |
|-------|----------|-------|
| `n` | yes | row number in the testing plan |
| `item` | yes | short description from the testing plan |
| `method` | yes | `cli` / `ios-simulator + video` / `screenshot` / `human` / `web` etc. |
| `status` | yes | `PASS` / `FAIL` / `BLOCKED` / `SKIPPED: human-only` |
| `evidence` | conditional | text evidence (CLI exit codes, BLOCKED reasons, etc.) |
| `evidence_path` | conditional | absolute path to local file to upload |
| `evidence_kind` | conditional | `image` / `video` — controls embed vs link rendering |

For each item, supply EITHER `evidence` (text) OR `evidence_path` (file).
Items without either render as `—`.

## How to call

```bash
${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/verify-finalize/scripts/verify-finalize.sh /tmp/manifest.json
```

For testing without hitting the Linear API:

```bash
${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/verify-finalize/scripts/verify-finalize.sh --dry-run /tmp/manifest.json
```

`LINEAR_API_KEY` must be in the environment (autosymph already exports
this; for local testing source it from `~/.envrc` or similar).

## Exit codes

| Code | Meaning | Agent action |
|------|---------|--------------|
| 0 | All uploads succeeded, comment posted | Signal `complete` to autosymph |
| 2 | At least one upload failed; comment posted with `[UPLOAD FAILED: ...]` markers | Signal `complete` (the failed uploads are documented in the comment for human review) |
| 1 | Script error (missing manifest, missing LINEAR_API_KEY, jq not installed) | Signal `fail` — the script itself broke; do NOT retry without inspecting |

## Anti-patterns

**Do NOT call `mcp__linear__create_attachment` yourself.** That's what
verify-finalize does. The whole point is removing that step from the
agent's responsibility.

**Do NOT call `mcp__linear__save_comment` for the verification summary.**
Same reason. (Other comments — e.g. progress notes during debugging — are
fine; the `verify-completion-audit` only counts whether at least one
save_comment exists, so an extra one is harmless.)

**Do NOT skip verify-finalize "because everything passed and the table
would be empty."** A run that produced no test items is itself a failure
mode worth reporting. Build the manifest with whatever you have.

**Do NOT manually format the markdown table.** verify-finalize builds it
from the manifest. The format is stable; downstream verify_review parses
it.

## Test

```bash
${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/verify-finalize/test.sh
```

Tests scenarios: empty items, CLI-only, all media uploaded, partial upload
failure, malformed manifest, missing fields. All exercise the script's
parsing + table-building without hitting real Linear (curl is PATH-stubbed
to return canned responses).
