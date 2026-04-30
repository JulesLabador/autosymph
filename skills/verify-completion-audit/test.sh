#!/bin/bash
# ============================================================================
# verify-completion-audit test
#
# Validates audit.sh against synthetic ndjson fixtures.
# ============================================================================

set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/scripts/audit.sh"
SKILL_MD="$(cd "$(dirname "$0")" && pwd)/SKILL.md"

PASS=0
FAIL=0
TEST_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR"' EXIT

pass() { PASS=$((PASS + 1)); echo "  PASS: $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL: $1 — $2"; }

echo "verify-completion-audit test"
echo "============================="
echo "Script: $SCRIPT"
echo "Test dir: $TEST_DIR"
echo

# ---- Helpers --------------------------------------------------------------

# Build a fake ndjson line for one tool_use call.
mk_call() {
  local tool_name="$1"
  printf '{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"x","name":"%s","input":{}}]}}\n' "$tool_name"
}

# Run audit and capture exit + stdout.
run_audit() {
  RUN_OUT=$("$SCRIPT" "$1" 2>&1)
  RUN_RC=$?
}

# JSON field extractor (find best python first).
PY=python3
command -v python3.12 >/dev/null && PY=python3.12

json_field() {
  "$PY" -c "
import json, sys
d = json.loads(sys.argv[1].splitlines()[0])
for k in sys.argv[2].split('.'):
    d = d[int(k)] if k.isdigit() else d.get(k, d)
print(d if not isinstance(d, (list, dict)) else json.dumps(d))
" "$1" "$2" 2>/dev/null
}

# ---- 1. Usage error → exit 1 ---------------------------------------------

run_audit "/nonexistent.ndjson"
[[ $RUN_RC -eq 1 ]] && pass "missing file → exit 1" || fail "missing file → exit 1" "got $RUN_RC"

# ---- 2. Clean run (1 save_comment, screenshots all uploaded) → exit 0 ----

CLEAN="$TEST_DIR/clean.ndjson"
{
  mk_call "Bash"
  mk_call "mcp__ios-simulator__screenshot"
  mk_call "mcp__linear__create_attachment"
  mk_call "mcp__ios-simulator__screenshot"
  mk_call "mcp__linear__create_attachment"
  mk_call "mcp__linear__save_comment"
} >"$CLEAN"

run_audit "$CLEAN"
[[ $RUN_RC -eq 0 ]] && pass "clean run → exit 0" || fail "clean run → exit 0" "got $RUN_RC; out=${RUN_OUT:0:120}"
status=$(json_field "$RUN_OUT" status)
[[ "$status" == "ok" ]] && pass "clean run → status=ok" || fail "clean run → status=ok" "got $status"

# ---- 3. Missing summary (screenshots uploaded but no save_comment) ------

NO_SUMMARY="$TEST_DIR/no-summary.ndjson"
{
  mk_call "mcp__ios-simulator__screenshot"
  mk_call "mcp__linear__create_attachment"
} >"$NO_SUMMARY"

run_audit "$NO_SUMMARY"
[[ $RUN_RC -eq 2 ]] && pass "missing summary → exit 2" || fail "missing summary → exit 2" "got $RUN_RC"
[[ "$RUN_OUT" == *"missing_summary_comment"* ]] && pass "missing_summary_comment violation reported" || fail "missing_summary_comment violation reported" "no violation in output"

# ---- 4. Uploads mismatch (screenshots > attachments, but summary posted) -

UPLOAD_MISMATCH="$TEST_DIR/upload-mismatch.ndjson"
{
  mk_call "mcp__ios-simulator__screenshot"
  mk_call "mcp__ios-simulator__screenshot"
  mk_call "mcp__ios-simulator__screenshot"
  mk_call "mcp__linear__create_attachment"  # only 1 of 3 uploaded
  mk_call "mcp__linear__save_comment"
} >"$UPLOAD_MISMATCH"

run_audit "$UPLOAD_MISMATCH"
[[ $RUN_RC -eq 2 ]] && pass "upload mismatch → exit 2" || fail "upload mismatch → exit 2" "got $RUN_RC"
[[ "$RUN_OUT" == *"incomplete_uploads"* ]] && pass "incomplete_uploads violation reported" || fail "incomplete_uploads violation reported" "no violation in output"

# ---- 5. Both violations (no summary, no uploads, but visuals taken) ------

BOTH_BROKEN="$TEST_DIR/both-broken.ndjson"
{
  mk_call "Bash"
  mk_call "mcp__ios-simulator__launch_app"
  mk_call "mcp__ios-simulator__screenshot"
  mk_call "mcp__ios-simulator__screenshot"
  mk_call "mcp__ios-simulator__record_video"
} >"$BOTH_BROKEN"

run_audit "$BOTH_BROKEN"
[[ $RUN_RC -eq 2 ]] && pass "both violations → exit 2" || fail "both violations → exit 2" "got $RUN_RC"
violation_count=$(json_field "$RUN_OUT" "violations")
# violations is a list — JSON-encoded; count the kind: occurrences
kinds=$(echo "$RUN_OUT" | head -1 | "$PY" -c "import json,sys;print(len(json.loads(sys.stdin.read())['violations']))" 2>/dev/null)
[[ "$kinds" == "3" ]] && pass "both-broken reports 3 violations (summary, uploads, no-attach)" || fail "both-broken reports 3 violations" "got $kinds"

# ---- 6. CLI-only run (no visuals, summary posted) → exit 0 ---------------

CLI_ONLY="$TEST_DIR/cli-only.ndjson"
{
  mk_call "Bash"
  mk_call "Bash"
  mk_call "Bash"
  mk_call "mcp__linear__save_comment"
} >"$CLI_ONLY"

run_audit "$CLI_ONLY"
[[ $RUN_RC -eq 0 ]] && pass "cli-only run → exit 0" || fail "cli-only run → exit 0" "got $RUN_RC"

# ---- 7. Web verify (Playwright screenshot, properly uploaded) → exit 0 ---

WEB_OK="$TEST_DIR/web-ok.ndjson"
{
  mk_call "mcp__plugin_playwright_playwright__browser_navigate"
  mk_call "mcp__plugin_playwright_playwright__browser_take_screenshot"
  mk_call "mcp__linear__create_attachment"
  mk_call "mcp__plugin_playwright_playwright__browser_take_screenshot"
  mk_call "mcp__linear__create_attachment"
  mk_call "mcp__linear__save_comment"
} >"$WEB_OK"

run_audit "$WEB_OK"
[[ $RUN_RC -eq 0 ]] && pass "web-ok run → exit 0 (playwright counts as visual)" || fail "web-ok run → exit 0" "got $RUN_RC"

# ---- 8. JSON output is single-line and valid ----------------------------

run_audit "$CLEAN"
first_line=$(echo "$RUN_OUT" | head -1)
echo "$first_line" | "$PY" -c "import json,sys; json.loads(sys.stdin.read())" 2>/dev/null \
  && pass "first line is valid JSON" \
  || fail "first line is valid JSON" "got: ${first_line:0:80}"

# ---- 9. Markdown table follows JSON --------------------------------------

[[ "$RUN_OUT" == *"## Verify Completion Audit"* ]] && pass "markdown header present" || fail "markdown header present" ""

# ---- 10. SKILL.md cross-references ---------------------------------------

[[ -f "$SKILL_MD" ]] && pass "SKILL.md exists" || fail "SKILL.md exists" ""
grep -q "audit.sh" "$SKILL_MD" && pass "SKILL.md references audit.sh" || fail "SKILL.md references audit.sh" ""
grep -q "test.sh" "$SKILL_MD" && pass "SKILL.md references test.sh" || fail "SKILL.md references test.sh" ""

# ---- Summary -------------------------------------------------------------

echo
echo "======================================="
echo "Results: $PASS passed, $FAIL failed"
echo "======================================="

[[ $FAIL -eq 0 ]] && exit 0 || exit 1
