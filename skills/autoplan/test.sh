#!/bin/bash
# test.sh - Regression tests for autoplan harness configuration.

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_FILE="$SKILL_DIR/SKILL.md"
PASS=0
FAIL=0

assert() {
  local desc="$1"
  local result="$2"
  if [ "$result" = "0" ]; then
    echo "  PASS: $desc"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $desc"
    FAIL=$((FAIL + 1))
  fi
}

echo "=== autoplan harness configuration tests ==="
echo ""

test -f "$SKILL_FILE"
assert "SKILL.md exists" $?

grep -q 'codex-claude' "$SKILL_FILE"
assert "Contains codex-claude flipped harness preset" $?

grep -q '`codex` | harness preset' "$SKILL_FILE"
assert "Contains short codex preset alias" $?

grep -q 'PLANNER_HARNESS=codex' "$SKILL_FILE"
assert "Documents Codex as planner harness" $?

grep -q 'QUESTION_HARNESS=claude' "$SKILL_FILE"
assert "Documents Claude as flipped question harness" $?

grep -q 'REVIEW_HARNESS=claude' "$SKILL_FILE"
assert "Documents Claude as flipped review harness" $?

grep -q -- '--planner-harness=claude|codex' "$SKILL_FILE"
assert "Contains planner harness override flag" $?

grep -q -- '--question-harness=codex|claude' "$SKILL_FILE"
assert "Contains question harness override flag" $?

grep -q -- '--review-harness=codex|claude' "$SKILL_FILE"
assert "Contains review harness override flag" $?

grep -q 'codex exec -m "$CODEX_MODEL" -' "$SKILL_FILE"
assert "Uses configurable Codex model command shape" $?

grep -q 'codex --auto --codex-review' "$SKILL_FILE"
assert "Documents /autoplan codex autonomous flipped mode" $?

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
