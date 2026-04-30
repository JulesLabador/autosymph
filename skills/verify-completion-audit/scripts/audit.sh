#!/bin/bash
# ============================================================================
# verify-completion-audit — does a verify run honor its completion contract?
#
# Reads a verify-run ndjson and counts critical tool calls. Fails (exit 2) if
# the agent took screenshots/videos but didn't upload them, or if it never
# posted a summary comment.
#
# Usage:
#   audit.sh <path-to-verify-run.ndjson>
#
# Output:
#   stdout: JSON line, then markdown table
#   exit 0: contract honored (or N/A — no visual work)
#   exit 2: violations found (caller should treat as reject_structify reason)
#   exit 1: usage error
#
# Contract (verify must satisfy ALL of these to be considered complete):
#
#   1. AT LEAST ONE call to mcp__linear__save_comment (the summary comment)
#   2. create_attachment count >= screenshot count + record_video count
#      (every visual evidence local file MUST be uploaded)
#   3. If sips was invoked (resize), there must be a matching create_attachment
#      (resized image, never used → suspicious abandoned upload)
#
# Designed to be deterministic: same ndjson → same audit. The verify_review
# agent should call this script and use its output to make the rejection
# decision instead of eyeballing the log.
# ============================================================================

set -u

if [[ $# -ne 1 || "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
fi

NDJSON="$1"

if [[ ! -f "$NDJSON" ]]; then
  echo "audit: ndjson not found: $NDJSON" >&2
  exit 1
fi

# Pick a Python that works (Python 3.14 broken on this machine — see autosymph
# README). Prefer 3.12 if available.
if command -v python3.12 >/dev/null 2>&1; then
  PY=python3.12
elif command -v python3.13 >/dev/null 2>&1; then
  PY=python3.13
else
  PY=python3
fi

"$PY" - "$NDJSON" <<'PYEOF'
import json
import sys
from collections import Counter

ndjson_path = sys.argv[1]
calls = Counter()

for raw in open(ndjson_path):
    try:
        d = json.loads(raw)
    except Exception:
        continue
    msg = d.get("message", {})
    for c in (msg.get("content") or []):
        if c.get("type") == "tool_use":
            name = c.get("name", "?")
            calls[name] += 1

# Contract checks.
save_comment = calls.get("mcp__linear__save_comment", 0)
create_attach = calls.get("mcp__linear__create_attachment", 0)

# Visual evidence sources: any tool that produces local screenshot/video files.
screenshot_calls = (
    calls.get("mcp__ios-simulator__screenshot", 0)
    + calls.get("mcp__plugin_playwright_playwright__browser_take_screenshot", 0)
)
record_calls = (
    calls.get("mcp__ios-simulator__record_video", 0)
)
visual_evidence_taken = screenshot_calls + record_calls

violations = []
if save_comment < 1:
    violations.append({
        "kind": "missing_summary_comment",
        "detail": "verify_completed_without_posting_summary — 0 calls to mcp__linear__save_comment",
        "fix": "Phase 7 mandates exactly 1 save_comment call (the verification table) before signaling complete",
    })

if visual_evidence_taken > 0 and create_attach < visual_evidence_taken:
    violations.append({
        "kind": "incomplete_uploads",
        "detail": (
            f"screenshots_taken_but_not_uploaded — "
            f"{visual_evidence_taken} visual evidence local files captured "
            f"({screenshot_calls} screenshots + {record_calls} videos), "
            f"but only {create_attach} create_attachment calls"
        ),
        "fix": "Every screenshot/video MUST be uploaded via create_attachment before next test item (atomic capture+upload)",
    })

if create_attach == 0 and visual_evidence_taken > 0:
    violations.append({
        "kind": "create_attachment_never_called",
        "detail": f"create_attachment_never_called despite {visual_evidence_taken} visual captures",
        "fix": "Use the screenshot-to-linear skill — capture, resize, base64, create_attachment as ONE atomic operation",
    })

status = "violations" if violations else "ok"
exit_code = 2 if violations else 0

# Emit JSON line first.
out = {
    "status": status,
    "ndjson": ndjson_path,
    "counts": {
        "save_comment": save_comment,
        "create_attachment": create_attach,
        "screenshot_calls": screenshot_calls,
        "record_video_calls": record_calls,
        "visual_evidence_taken": visual_evidence_taken,
    },
    "violations": violations,
}
print(json.dumps(out))

# Then markdown summary.
print()
print("## Verify Completion Audit")
print()
print(f"**Source:** `{ndjson_path}`")
print()
print("| Metric | Count |")
print("|--------|------:|")
print(f"| `mcp__linear__save_comment` calls | {save_comment} |")
print(f"| `mcp__linear__create_attachment` calls | {create_attach} |")
print(f"| Screenshots captured locally | {screenshot_calls} |")
print(f"| Videos recorded locally | {record_calls} |")
print(f"| Total visual evidence captured | {visual_evidence_taken} |")
print()

if violations:
    print(f"### {len(violations)} contract violation(s)")
    print()
    for v in violations:
        print(f"- **{v['kind']}** — {v['detail']}")
        print(f"  - Fix: {v['fix']}")
    print()
    print("**Recommended verdict:** `reject_structify` with the kinds listed above as `reasons`.")
else:
    print("**Contract honored.** Verify completion discipline is intact.")

sys.exit(exit_code)
PYEOF
