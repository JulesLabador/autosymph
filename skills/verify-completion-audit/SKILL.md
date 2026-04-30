---
name: verify-completion-audit
description: Use during verify_review (or after any verify run) to mechanically check whether the verify agent honored its completion contract — posted a summary, uploaded screenshots, no orphan local files. Returns a JSON+markdown audit and exit 0 (clean) or 2 (violations to cite as reject_structify reasons).
---

# verify-completion-audit

Counts critical tool calls in a verify-run ndjson and asserts the completion
contract. Designed to replace the verify_review agent's manual eyeballing of
the log with a deterministic check.

## When to use

**verify_review agent**: as the FIRST thing you do after fetching the verify
run's ndjson. The audit either confirms the contract is intact (continue with
your normal evidence audit) or returns the rejection reasons pre-packaged.

**Autosymph monitor / debugging**: any time you want to know "did this verify run
actually finish, or did it just stop?"

## The contract being checked

Verify is considered complete IFF all of these hold:

| Check | Why |
|-------|-----|
| `mcp__linear__save_comment` count ≥ 1 | The verification table must reach Linear; without it nothing exists for a human to evaluate |
| `create_attachment` count ≥ `screenshot` + `record_video` count | Every local visual file must be uploaded; orphan local files are wasted work |
| If any visual evidence was captured, `create_attachment` ≥ 1 | Catches the "took 24 screenshots, uploaded 0" case directly |

## The script

```bash
${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/verify-completion-audit/scripts/audit.sh \
  <path-to-verify-runN.ndjson>
```

Output: JSON line first (machine readable), then markdown (paste into Linear).

Exit codes:
- `0` — contract honored (all visual evidence uploaded; summary posted)
- `2` — at least one violation; the JSON `violations` array is the
  recommended `reject_structify` `reasons` list
- `1` — usage / file-not-found

## How verify_review uses it

```bash
# Step 1 of verify_review: download the run's ndjson, audit it.
NDJSON=$(autosymph fetch-ndjson ISSUE-123 verify run10)  # or read from logs dir
${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/verify-completion-audit/scripts/audit.sh "$NDJSON"
case $? in
  0) echo "completion contract intact — proceed with evidence audit" ;;
  2) echo "completion violations — emit reject_structify with the audit's reasons array" ;;
esac
```

If exit 2, the verify_review agent should:
1. Paste the audit's markdown table into the Linear comment.
2. Use the violation `kind` strings as the `reasons` in the verdict block.
3. Emit `decision: "reject_structify"`.

This lets verify_review trust mechanical evidence rather than improvising.

## Why a script, not just a prompt

Same rationale as `verify-preflight`: prompts that ask the agent to "count
calls" produce inconsistent counts depending on how much of the log it
actually read. A script counts deterministically every time. The skill design
rule is: heavy lifting in scripts, prompt only
tells the model when to call them.

## Sample output

```json
{
  "status": "violations",
  "ndjson": "<workspace>/verify-run12.ndjson",
  "counts": {
    "save_comment": 0,
    "create_attachment": 0,
    "screenshot_calls": 3,
    "record_video_calls": 0,
    "visual_evidence_taken": 3
  },
  "violations": [
    {"kind": "missing_summary_comment", "detail": "...", "fix": "..."},
    {"kind": "incomplete_uploads", "detail": "...", "fix": "..."},
    {"kind": "create_attachment_never_called", "detail": "...", "fix": "..."}
  ]
}
```

## Anti-patterns

- **Do NOT eyeball the log.** The whole point is mechanical detection. If
  you find yourself counting tool calls by hand, run the script.
- **Do NOT skip if the verify run "looks like it did a lot of work".** The
  The triggering run had many screenshot calls and launch_app calls — looked
  productive — but uploaded 0. Activity ≠ completion.
- **Do NOT fix violations downstream.** The fix is in the verify prompt
  (Phase 7 + Hard Rule #8) so the agent never produces orphan local files.
  This skill detects; the verify prompt prevents.

## Test

```bash
${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/verify-completion-audit/test.sh
```

Asserts: clean run → exit 0; missing-summary → exit 2 with that violation;
uploads-mismatch → exit 2 with that violation; combined → exit 2 with all
violations; synthetic fixtures cover all 3.
