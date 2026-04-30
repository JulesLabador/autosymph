#!/bin/bash
# ============================================================================
# verify-preflight test
#
# Validates preflight.sh against the failure modes that triggered structify
# from repeated verify stalls:
#   - missing auth.md
#   - broken idb (pyexpat ImportError)
#   - broken Playwright (no install)
#   - all-healthy happy path
#   - JSON output is parseable
#   - exit codes match status
#
# Strategy: stubs on PATH for python3/idb/xcrun/npx so we can simulate
# broken/healthy infra deterministically. Each scenario gets its own PATH.
#
# Usage:  ./test.sh
# ============================================================================

set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/scripts/preflight.sh"
SKILL_MD="$(cd "$(dirname "$0")" && pwd)/SKILL.md"

PASS=0
FAIL=0
TEST_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR"' EXIT

pass() { PASS=$((PASS + 1)); echo "  PASS: $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL: $1 — $2"; }

echo "verify-preflight test"
echo "====================="
echo "Script: $SCRIPT"
echo "Test dir: $TEST_DIR"
echo

# ---- Setup: stub binaries ------------------------------------------------
#
# Critical: the preflight script itself uses `python3` for JSON escaping.
# We must NOT replace python3 with a non-functional stub. Instead, the
# python3 stub passes through to the real interpreter EXCEPT when invoked
# with `import pyexpat` — that's the call we want to fail.

REAL_PY3="$(command -v python3)"
[[ -z "$REAL_PY3" ]] && { echo "test requires python3 on PATH"; exit 1; }

mk_stub() {
  # mk_stub <stub_dir> <name> <exit_code> [output]
  local dir="$1" name="$2" code="$3" out="${4:-}"
  mkdir -p "$dir"
  cat >"$dir/$name" <<EOF
#!/bin/bash
echo "$out"
exit $code
EOF
  chmod +x "$dir/$name"
}

mk_python3_pyexpat_ok() {
  # python3 that pretends `import pyexpat` succeeds (host may actually have
  # the bug — we don't want test outcome to depend on host state).
  # All other invocations pass through to the real interpreter so the
  # script's own json_escape calls still work.
  local dir="$1"
  mkdir -p "$dir"
  cat >"$dir/python3" <<EOF
#!/bin/bash
for a in "\$@"; do
  if [[ "\$a" == *"import pyexpat"* ]]; then
    exit 0
  fi
done
exec "$REAL_PY3" "\$@"
EOF
  chmod +x "$dir/python3"
}

mk_python3_pyexpat_broken() {
  # python3 that fails ONLY on the exact `-c "import pyexpat"` probe call.
  # Other invocations (incl. the script's json.dumps escape calls that
  # contain "import pyexpat" inside data values) pass through to real python3.
  local dir="$1"
  mkdir -p "$dir"
  cat >"$dir/python3" <<EOF
#!/bin/bash
if [[ \$# -eq 2 && "\$1" == "-c" && "\$2" == "import pyexpat" ]]; then
  echo "Traceback (most recent call last):" >&2
  echo "ImportError: dlopen pyexpat.cpython-314-darwin.so — Symbol not found: _XML_SetAllocTrackerActivationThreshold" >&2
  exit 1
fi
exec "$REAL_PY3" "\$@"
EOF
  chmod +x "$dir/python3"
}

# Healthy stubs: python3 passes through, idb/xcrun/npx return success
HEALTHY_STUBS="$TEST_DIR/stubs-healthy"
mk_python3_pyexpat_ok "$HEALTHY_STUBS"
mk_stub "$HEALTHY_STUBS" "idb"   0 "idb help text"
mk_stub "$HEALTHY_STUBS" "xcrun" 0 "Booted device list"
mk_stub "$HEALTHY_STUBS" "npx"   0 "1.40.0"

# Broken-idb stubs: idb --help fails with pyexpat ImportError (realistic
# failure mode when idb's bundled Python is the broken 3.14). The shell's
# python3 doesn't matter for this probe anymore — we test idb directly.
BROKEN_IDB_STUBS="$TEST_DIR/stubs-broken-idb"
mk_python3_pyexpat_ok "$BROKEN_IDB_STUBS"  # shell python is fine
mkdir -p "$BROKEN_IDB_STUBS"
cat >"$BROKEN_IDB_STUBS/idb" <<EOF
#!/bin/bash
# Simulate: idb's bundled python imports pyexpat at startup → ImportError
echo "Traceback (most recent call last):" >&2
echo "  File '/.../idb/cli/main.py', line 5, in <module>" >&2
echo "    from xml.parsers import expat" >&2
echo "ImportError: dlopen pyexpat.cpython-314-darwin.so — Symbol not found: _XML_SetAllocTrackerActivationThreshold" >&2
exit 1
EOF
chmod +x "$BROKEN_IDB_STUBS/idb"
mk_stub "$BROKEN_IDB_STUBS" "xcrun" 0 ""
mk_stub "$BROKEN_IDB_STUBS" "npx"   0 ""

# Broken-playwright stubs: python3 passes through, npx fails
BROKEN_PW_STUBS="$TEST_DIR/stubs-broken-pw"
mk_python3_pyexpat_ok "$BROKEN_PW_STUBS"
mk_stub "$BROKEN_PW_STUBS" "npx" 1 "playwright not installed"

# Workspaces
WS_NO_AUTH="$TEST_DIR/ws-no-auth"
WS_WITH_AUTH="$TEST_DIR/ws-with-auth"
mkdir -p "$WS_NO_AUTH" "$WS_WITH_AUTH/.autosymph/verify"
echo "supabase-otp-creds" > "$WS_WITH_AUTH/.autosymph/verify/auth.md"

# ---- Helper: run preflight, capture stdout + exit ------------------------

run_pf() {
  # run_pf <stub_dir|none> <args...>  →  writes stdout to $RUN_OUT, exit to $RUN_RC
  local stubs="$1"; shift
  local path
  if [[ "$stubs" == "none" ]]; then
    path="$PATH"
  else
    path="$stubs:$PATH"
  fi
  RUN_OUT=$(env PATH="$path" "$SCRIPT" "$@" 2>&1)
  RUN_RC=$?
}

json_field() {
  # json_field <json> <jq-style key path>
  "$REAL_PY3" -c 'import json,sys,functools; d=json.loads(sys.argv[1].splitlines()[0]); print(functools.reduce(lambda v,k: v[int(k)] if k.isdigit() else v[k], sys.argv[2].split("."), d))' "$1" "$2" 2>/dev/null
}

# ---- 1. No-args → exit 1 (script error) ----------------------------------

run_pf "none"
[[ $RUN_RC -eq 1 ]] && pass "no-args exits 1" || fail "no-args exits 1" "got $RUN_RC"

# ---- 2. --help works -----------------------------------------------------

run_pf "none" --help
[[ "$RUN_OUT" == *"verify-preflight"* ]] && pass "--help mentions skill name" || fail "--help mentions skill name" "out: ${RUN_OUT:0:80}"

# ---- 3. Auth missing → exit 2, blocked=auth-gated ------------------------

run_pf "$HEALTHY_STUBS" --auth-gated --workspace "$WS_NO_AUTH"
[[ $RUN_RC -eq 2 ]] && pass "auth-missing exits 2" || fail "auth-missing exits 2" "got $RUN_RC"
status=$(json_field "$RUN_OUT" status)
[[ "$status" == "blocked" ]] && pass "auth-missing status=blocked" || fail "auth-missing status=blocked" "got '$status'"
cat0=$(json_field "$RUN_OUT" "blocked_categories.0")
[[ "$cat0" == "auth-gated" ]] && pass "auth-missing blocked=auth-gated" || fail "auth-missing blocked=auth-gated" "got '$cat0'"

# ---- 4. Auth present + healthy iOS → exit 0 ------------------------------

run_pf "$HEALTHY_STUBS" --auth-gated --ios --workspace "$WS_WITH_AUTH"
[[ $RUN_RC -eq 0 ]] && pass "all-healthy exits 0" || fail "all-healthy exits 0" "got $RUN_RC; out=${RUN_OUT:0:200}"
status=$(json_field "$RUN_OUT" status)
[[ "$status" == "ok" ]] && pass "all-healthy status=ok" || fail "all-healthy status=ok" "got '$status'"

# ---- 5. iOS with broken idb → exit 2, blocked=ios ------------------------

run_pf "$BROKEN_IDB_STUBS" --ios --workspace "$WS_WITH_AUTH"
[[ $RUN_RC -eq 2 ]] && pass "broken-idb exits 2" || fail "broken-idb exits 2" "got $RUN_RC"
cat0=$(json_field "$RUN_OUT" "blocked_categories.0")
[[ "$cat0" == "ios" ]] && pass "broken-idb blocked=ios" || fail "broken-idb blocked=ios" "got '$cat0'"
detail=$(json_field "$RUN_OUT" "probes.idb.detail")
[[ "$detail" == *"pyexpat"* ]] && pass "broken-idb detail mentions pyexpat" || fail "broken-idb detail mentions pyexpat" "got '$detail'"

# ---- 6. Web with broken playwright → exit 2, blocked=web -----------------

run_pf "$BROKEN_PW_STUBS" --web
[[ $RUN_RC -eq 2 ]] && pass "broken-pw exits 2" || fail "broken-pw exits 2" "got $RUN_RC"
cat0=$(json_field "$RUN_OUT" "blocked_categories.0")
[[ "$cat0" == "web" ]] && pass "broken-pw blocked=web" || fail "broken-pw blocked=web" "got '$cat0'"

# ---- 7. Combined: missing auth + broken idb → blocked=[auth-gated,ios] --

run_pf "$BROKEN_IDB_STUBS" --auth-gated --ios --workspace "$WS_NO_AUTH"
[[ $RUN_RC -eq 2 ]] && pass "combined exits 2" || fail "combined exits 2" "got $RUN_RC"
all_cats=$(echo "$RUN_OUT" | head -1 | python3 -c "import json,sys; print(','.join(sorted(json.loads(sys.stdin.read())['blocked_categories'])))" 2>/dev/null)
[[ "$all_cats" == "auth-gated,ios" ]] && pass "combined blocked=auth-gated,ios" || fail "combined blocked=auth-gated,ios" "got '$all_cats'"

# ---- 8. Output shape: JSON line first, then markdown ---------------------

run_pf "$HEALTHY_STUBS" --auth-gated --workspace "$WS_WITH_AUTH"
first_line=$(echo "$RUN_OUT" | head -1)
echo "$first_line" | python3 -c "import json,sys; json.loads(sys.stdin.read())" 2>/dev/null \
  && pass "first stdout line is valid JSON" \
  || fail "first stdout line is valid JSON" "first: ${first_line:0:80}"
[[ "$RUN_OUT" == *"## Verify Preflight"* ]] && pass "markdown table follows JSON" || fail "markdown table follows JSON" "no header"

# ---- 9. Regression: pyexpat ImportError doesn't get hidden ---------------
#       (the exact failure mode that triggered structify)

run_pf "$BROKEN_IDB_STUBS" --ios --workspace "$WS_WITH_AUTH"
[[ "$RUN_OUT" == *"pyexpat"* ]] && pass "regression: pyexpat surfaced in output" \
  || fail "regression: pyexpat surfaced in output" "out missing pyexpat"

# ---- 10. SKILL.md references the script + test ---------------------------

[[ -f "$SKILL_MD" ]] && pass "SKILL.md exists" || fail "SKILL.md exists" "missing"
grep -q "preflight.sh" "$SKILL_MD" && pass "SKILL.md references preflight.sh" || fail "SKILL.md references preflight.sh" ""
grep -q "test.sh" "$SKILL_MD" && pass "SKILL.md references test.sh" || fail "SKILL.md references test.sh" ""

# ---- 11. Skipped probes don't appear in JSON output ----------------------

run_pf "$HEALTHY_STUBS" --auth-gated --workspace "$WS_WITH_AUTH"
first_line=$(echo "$RUN_OUT" | head -1)
echo "$first_line" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert 'idb' not in d['probes'], 'idb should be absent'; assert 'pw' not in d['probes'], 'pw should be absent'" 2>/dev/null \
  && pass "skipped probes absent from JSON" \
  || fail "skipped probes absent from JSON" "got: $first_line"

# ---- Summary -------------------------------------------------------------

echo
echo "======================================="
echo "Results: $PASS passed, $FAIL failed"
echo "======================================="

[[ $FAIL -eq 0 ]] && exit 0 || exit 1
