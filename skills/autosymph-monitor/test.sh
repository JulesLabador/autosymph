#!/bin/bash
# ============================================================================
# autosymph-monitor skill test
#
# Validates SKILL.md content completeness and correctness.
# Does NOT test the actual monitor loop (requires running autosymph).
#
# Usage:  ./test.sh
# Runtime: <5 seconds
# ============================================================================

set -euo pipefail

PASS=0
FAIL=0
SKIP=0

pass() { PASS=$((PASS + 1)); echo "  PASS: $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL: $1 — $2"; }
skip() { SKIP=$((SKIP + 1)); echo "  SKIP: $1 — $2"; }

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_MD="$SKILL_DIR/SKILL.md"
STATUS_URL_SH="$SKILL_DIR/scripts/status-url.sh"
TICK_SH="$SKILL_DIR/scripts/tick.sh"
REPO_ROOT="$(cd "$SKILL_DIR/../.." && pwd)"

echo "autosymph-monitor skill test"
echo "============================"
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

# ── Phase 2: Alert rules documented ─────────────────────────────

echo "## Phase 2: Alert detection rules"

check() {
  local label="$1"
  local pattern="$2"
  if grep -qi "$pattern" "$SKILL_MD" 2>/dev/null; then
    pass "$label"
  else
    fail "$label" "pattern not found: $pattern"
  fi
}

check "stall detection rule" "idle_s.*300"
check "stall gate (turns == 0)" "turns.*==.*0"
check "stall gate (active runner)" "active"
check "verify churn rule (3+)" "3.*verify"
check "verify churn time window (24h)" "24.*hour"
check "infra crash rule" "infra_crash_count.*3"
check "token burn rule (100K)" "100000"
check "process down rule" "process.*down"
check "status API discovery" "status-url.sh"
check "delta-based comparison" "delta"
check "mandatory activity summary from logs" "MANDATORY"
check "ndjson log reading for activity" "ndjson"
check "one-line activity context" "one-line\|one.liner\|activity summary"

echo ""

# ── Phase 3: Self-healing documented ─────────────────────────────

echo "## Phase 3: Self-healing patterns"

check "structify integration" "structify"
check "kill endpoint" "kill"
check "re-queue to Ready" "Ready"
check "move to Blocked" "Blocked"
check "Linear comment before action" "comment.*before\|audit trail"
check "permission denial diagnosis" "permission.*denial\|allowed_tools"

echo ""

# ── Phase 4: Config awareness ───────────────────────────────────

echo "## Phase 3b: Connectivity self-check"

check "restart guidance when API unavailable" "restart\|Restart"
check "curl permission self-check" "curl.*permission\|Bash.*curl\|permission.*curl"

echo ""

echo "## Phase 4: Config awareness"

check "reads project configs" "projects/.*yaml"
check "parses state definitions" "state.*definition\|states.*type"
check "parses allowed_tools" "allowed_tools"
check "parses stall timeout" "stall_timeout"
check "parses max concurrent" "max.*concurrent\|max_agents"

echo ""

# ── Phase 5: Linear mutations ───────────────────────────────────

echo "## Phase 5: Linear mutations"

check "save_issue (state changes)" "save_issue"
check "save_comment (audit trail)" "save_comment"
check "interactive move command" "move.*Ready\|move.*ISSUE"

echo ""

# ── Phase 6: Interactive commands ────────────────────────────────

echo "## Phase 6: Interactive commands"

check "what happened query" "what happened"
check "completion stats query" "last.*issues\|completed.*today"
check "kill command" "kill.*ISSUE\|kill.*issue"

echo ""

# ── Phase 7: Anti-patterns ──────────────────────────────────────

echo "## Phase 7: Anti-patterns"

check "don't structify code bugs" "DO NOT.*structify.*code\|code bug"
check "don't fight autosymph" "DO NOT.*fight\|Kill.*first"
check "don't escalate first failure" "DO NOT.*escalate.*first\|3.*occurrence"

echo ""

# ── Phase 7b: Discovery scripts ─────────────────────────────────

echo "## Phase 7b: Discovery scripts"

if [ -x "$STATUS_URL_SH" ]; then
  pass "status-url.sh exists and is executable"
else
  fail "status-url.sh missing or not executable" "$STATUS_URL_SH"
fi

if [ -x "$TICK_SH" ]; then
  pass "tick.sh exists and is executable"
else
  fail "tick.sh missing or not executable" "$TICK_SH"
fi

if grep -q "AUTOSYMPH_STATUS_URL" "$STATUS_URL_SH" && grep -q "status-api.json" "$STATUS_URL_SH"; then
  pass "status-url.sh supports env and discovery file"
else
  fail "status-url.sh missing discovery sources" "$STATUS_URL_SH"
fi

if grep -q "status-url.sh" "$TICK_SH"; then
  pass "tick.sh uses status-url discovery"
else
  fail "tick.sh does not use status-url discovery" "$TICK_SH"
fi

echo ""

# ── Phase 8: Server integration ─────────────────────────────────

echo "## Phase 8: Server module exists"

SERVER_PY="$REPO_ROOT/src/autosymph/server.py"
if [ -f "$SERVER_PY" ]; then
  pass "server.py exists"
  if grep -q "status_summary\|/status" "$SERVER_PY"; then
    pass "server.py serves status_summary"
  else
    fail "server.py missing status_summary" "$SERVER_PY"
  fi
  if grep -q "/kill/" "$SERVER_PY"; then
    pass "server.py has kill endpoint"
  else
    fail "server.py missing kill endpoint" "$SERVER_PY"
  fi
  if grep -q "127.0.0.1" "$SERVER_PY"; then
    pass "server.py binds to localhost only"
  else
    fail "server.py not bound to localhost" "security risk"
  fi
  if grep -q "status-api.json" "$SERVER_PY"; then
    pass "server.py writes status discovery file"
  else
    fail "server.py missing status discovery file" "$SERVER_PY"
  fi
else
  fail "server.py not found" "$SERVER_PY"
fi

echo ""

# ── Summary ──────────────────────────────────────────────────────

echo "==================="
echo "Results: $PASS passed, $FAIL failed, $SKIP skipped"
echo ""

if [ "$FAIL" -gt 0 ]; then
  echo "SKILL INCOMPLETE — fix before deploying"
  exit 1
else
  echo "SKILL OK — autosymph monitor is complete"
  exit 0
fi
