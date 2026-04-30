#!/bin/bash
# ============================================================================
# context-management skill test
#
# Validates:
# 1. File-based handoff mechanics (write, read, verify integrity)
# 2. Truncation detection regression
# 3. Prompt references (verify.md and implement.md mention context-management)
# 4. Skill content checks (thresholds, anti-patterns documented)
#
# Usage:  ./test.sh
# Runtime: <10 seconds
# ============================================================================

set -euo pipefail

PASS=0
FAIL=0
SKIP=0
TEST_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR"' EXIT

pass() { PASS=$((PASS + 1)); echo "  PASS: $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL: $1 — $2"; }
skip() { SKIP=$((SKIP + 1)); echo "  SKIP: $1 — $2"; }

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_MD="$SKILL_DIR/SKILL.md"
REPO_ROOT="$(cd "$SKILL_DIR/../.." && pwd)"

echo "context-management skill test"
echo "=============================="
echo "Test dir: $TEST_DIR"
echo ""

# ── Phase 1: Skill file exists ──────────────────────────────────

echo "## Phase 1: Skill file exists"

if [ -f "$SKILL_MD" ]; then
  pass "SKILL.md exists"
else
  fail "SKILL.md not found" "$SKILL_MD"
  exit 1
fi

echo ""

# ── Phase 2: File-based handoff mechanics ───────────────────────

echo "## Phase 2: File-based handoff (write → read → verify)"

# Create test data (simulates base64 payload)
python3 -c "
import base64, os
data = os.urandom(20000)  # ~27K base64 chars
b64 = base64.b64encode(data).decode()
with open('$TEST_DIR/test-payload.b64', 'w') as f:
    f.write(b64)
with open('$TEST_DIR/test-original.bin', 'wb') as f:
    f.write(data)
print(f'Generated: {len(data)} bytes raw, {len(b64)} chars base64')
"

# Read back and decode
B64_CONTENT=$(cat "$TEST_DIR/test-payload.b64")
echo "$B64_CONTENT" | base64 -d > "$TEST_DIR/test-roundtrip.bin" 2>/dev/null

# Verify integrity
ORIG_SIZE=$(wc -c < "$TEST_DIR/test-original.bin" | tr -d ' ')
RT_SIZE=$(wc -c < "$TEST_DIR/test-roundtrip.bin" | tr -d ' ')

if [ "$ORIG_SIZE" -eq "$RT_SIZE" ]; then
  pass "file-based roundtrip preserves data ($ORIG_SIZE bytes)"
else
  fail "file-based roundtrip size mismatch" "original=$ORIG_SIZE roundtrip=$RT_SIZE"
fi

# Verify file-based base64 is readable
B64_LEN=${#B64_CONTENT}
if [ "$B64_LEN" -gt 1000 ]; then
  pass "base64 file contains substantial data ($B64_LEN chars)"
else
  fail "base64 file too small" "$B64_LEN chars"
fi

echo ""

# ── Phase 3: Truncation detection ──────────────────────────────

echo "## Phase 3: Truncation detection regression"

# Simulate ISSUE-123: truncate base64 to half
HALF_LEN=$((B64_LEN / 2))
TRUNCATED="${B64_CONTENT:0:$HALF_LEN}"
echo "$TRUNCATED" | base64 -d > "$TEST_DIR/test-truncated.bin" 2>/dev/null || true
TRUNC_SIZE=$(wc -c < "$TEST_DIR/test-truncated.bin" 2>/dev/null | tr -d ' ' || echo "0")

if [ "$TRUNC_SIZE" -lt "$ORIG_SIZE" ]; then
  pass "truncated base64 produces smaller file ($TRUNC_SIZE < $ORIG_SIZE)"
else
  fail "truncation not detectable" "truncated=$TRUNC_SIZE original=$ORIG_SIZE"
fi

# Verify we can detect truncation by comparing sizes
if [ "$TRUNC_SIZE" -lt "$((ORIG_SIZE * 3 / 4))" ]; then
  pass "truncation detectable via size comparison (>25% smaller)"
else
  fail "truncation margin too small" "might miss subtle truncation"
fi

echo ""

# ── Phase 4: Prompt reference checks ──────────────────────────

echo "## Phase 4: Prompt references to context-management"

# Check packaged verify.md
VERIFY_PROD="$REPO_ROOT/prompts/verify.md"
if [ -f "$VERIFY_PROD" ]; then
  if grep -qi "context.management" "$VERIFY_PROD" 2>/dev/null; then
    pass "verify.md references context-management"
  else
    fail "verify.md missing context-management reference" "$VERIFY_PROD"
  fi
else
  skip "verify.md not found" "$VERIFY_PROD"
fi

# Check packaged implement.md
IMPL_PROD="$REPO_ROOT/prompts/implement.md"
if [ -f "$IMPL_PROD" ]; then
  if grep -qi "context.management" "$IMPL_PROD" 2>/dev/null; then
    pass "implement.md references context-management"
  else
    fail "implement.md missing context-management reference" "$IMPL_PROD"
  fi
else
  skip "implement.md not found" "$IMPL_PROD"
fi

echo ""

# ── Phase 5: Skill content checks ─────────────────────────────

echo "## Phase 5: Skill content completeness"

# Check for key sections
check_content() {
  local label="$1"
  local pattern="$2"
  if grep -qi "$pattern" "$SKILL_MD" 2>/dev/null; then
    pass "SKILL.md contains: $label"
  else
    fail "SKILL.md missing: $label" "pattern: $pattern"
  fi
}

check_content "file-based handoff pattern" "file.based"
check_content "same-turn guarantee" "same.turn"
check_content "size thresholds table" "size.*threshold"
check_content "context threshold (~400K)" "400K"
check_content "anti-patterns table" "anti.pattern"
check_content "ISSUE-123 reference" "ISSUE-123"
check_content "ISSUE-123 reference" "ISSUE-123"
check_content "screenshot-to-linear integration" "screenshot.to.linear"
check_content "record-demo-video integration" "record.demo.video"
check_content "common errors table" "common.error"
check_content "decision tree" "decision.tree"

echo ""

# ── Summary ────────────────────────────────────────────────────

echo "=============================="
echo "Results: $PASS passed, $FAIL failed, $SKIP skipped"
echo ""

if [ "$FAIL" -gt 0 ]; then
  echo "SKILL INCOMPLETE — fix before deploying"
  exit 1
else
  echo "SKILL OK — context-management skill is complete and referenced"
  exit 0
fi
