#!/bin/bash
# ============================================================================
# verify-preflight — fast-fail probes for verify agent infrastructure.
#
# Run this BEFORE classifying test items. If it reports blocked categories,
# emit a verification table marking every affected item BLOCKED with the
# preflight reason and exit verify with `fail`.
#
# Args (any combination, repeatable):
#   --ios            test plan has iOS items (probe idb + sim availability)
#   --web            test plan has web items (probe Playwright)
#   --auth-gated     feature under test requires auth (probe auth.md)
#   --workspace P    workspace root (default: cwd) — auth.md lives here
#
# Output:
#   stdout: single JSON object on first line, then markdown table for paste
#   exit 0: all probes passed → safe to proceed
#   exit 2: at least one blocker → fail-fast, do not enter Phase 1+
#   exit 1: script error (bad args, missing dependencies)
#
# Probes are bounded: each has a timeout. Total runtime <10s on cold cache.
# ============================================================================

set -u

WORKSPACE="$(pwd)"
NEED_IOS=0
NEED_WEB=0
NEED_AUTH=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ios)         NEED_IOS=1; shift ;;
    --web)         NEED_WEB=1; shift ;;
    --auth-gated)  NEED_AUTH=1; shift ;;
    --workspace)   WORKSPACE="$2"; shift 2 ;;
    --help|-h)
      sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "preflight: unknown arg: $1" >&2
      exit 1
      ;;
  esac
done

if [[ $NEED_IOS -eq 0 && $NEED_WEB -eq 0 && $NEED_AUTH -eq 0 ]]; then
  echo "preflight: at least one of --ios --web --auth-gated required" >&2
  exit 1
fi

# macOS lacks `timeout` by default; gtimeout (coreutils) or none.
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_CMD="timeout"
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_CMD="gtimeout"
else
  TIMEOUT_CMD=""  # no-op; probe runs without bound
fi
_t() { if [[ -n "$TIMEOUT_CMD" ]]; then "$TIMEOUT_CMD" "$@"; else shift; "$@"; fi; }

# Each probe sets PROBE_<NAME>_RESULT and PROBE_<NAME>_DETAIL.
# Result is one of: ok | missing | broken | skipped.

probe_auth_md() {
  local f="$WORKSPACE/.autosymph/verify/auth.md"
  if [[ ! -f "$f" || ! -s "$f" ]]; then
    PROBE_AUTH_RESULT="missing"
    PROBE_AUTH_DETAIL="$f does not exist or is empty — feature is auth-gated and cannot be reached"
  else
    PROBE_AUTH_RESULT="ok"
    PROBE_AUTH_DETAIL="found at $f"
  fi
}

probe_idb() {
  # Test idb directly — not the shell's python3. idb may bundle its own
  # interpreter (e.g. via uv tool install, pipx) where pyexpat works even
  # though the shell python3 is broken. The original "python3 -c 'import
  # pyexpat'" proxy gave false negatives in that case.
  if ! command -v idb >/dev/null 2>&1; then
    PROBE_IDB_RESULT="broken"
    PROBE_IDB_DETAIL="idb binary not on PATH"
    return
  fi

  local idb_path
  idb_path=$(command -v idb)
  local out
  out=$(_t 5 idb --help 2>&1)
  local rc=$?
  if [[ $rc -ne 0 ]]; then
    PROBE_IDB_RESULT="broken"
    PROBE_IDB_DETAIL="idb --help failed (binary at $idb_path): $(echo "$out" | head -6 | tr '\n' ' ')"
    return
  fi

  # Also probe a real subcommand that exercises pyexpat internally (xml parsing).
  # `idb list-targets` is read-only and fast; if pyexpat is broken inside idb's
  # bundled interpreter, this surfaces here instead of waiting for an actual
  # sim interaction.
  out=$(_t 5 idb list-targets 2>&1)
  rc=$?
  if [[ $rc -ne 0 ]]; then
    # list-targets can fail for non-fatal reasons (no companion); only
    # treat ImportError or pyexpat-related failures as broken.
    if echo "$out" | grep -qE "ImportError|pyexpat|libexpat|Symbol not found"; then
      PROBE_IDB_RESULT="broken"
      PROBE_IDB_DETAIL="idb list-targets pyexpat error: $(echo "$out" | grep -m1 -E 'Import|pyexpat|libexpat|Symbol')"
      return
    fi
  fi

  PROBE_IDB_RESULT="ok"
  PROBE_IDB_DETAIL="idb available at $idb_path"
}

probe_simulator() {
  if ! command -v xcrun >/dev/null 2>&1; then
    PROBE_SIM_RESULT="broken"
    PROBE_SIM_DETAIL="xcrun not on PATH (Xcode CLT missing)"
    return
  fi
  local out
  out=$(_t 5 xcrun simctl list devices booted 2>&1)
  if [[ $? -ne 0 ]]; then
    PROBE_SIM_RESULT="broken"
    PROBE_SIM_DETAIL="simctl failed: $(echo "$out" | head -1)"
    return
  fi
  PROBE_SIM_RESULT="ok"
  PROBE_SIM_DETAIL="simctl available"
}

probe_playwright() {
  local out
  if ! command -v npx >/dev/null 2>&1; then
    PROBE_PW_RESULT="broken"
    PROBE_PW_DETAIL="npx not on PATH"
    return
  fi
  out=$(_t 8 npx --no-install playwright --version 2>&1)
  if [[ $? -ne 0 ]]; then
    PROBE_PW_RESULT="broken"
    PROBE_PW_DETAIL="playwright not installed (npx --no-install playwright failed)"
    return
  fi
  PROBE_PW_RESULT="ok"
  PROBE_PW_DETAIL="playwright $(echo "$out" | tr -d '\n' | head -c 40)"
}

PROBE_AUTH_RESULT="skipped"; PROBE_AUTH_DETAIL=""
PROBE_IDB_RESULT="skipped";  PROBE_IDB_DETAIL=""
PROBE_SIM_RESULT="skipped";  PROBE_SIM_DETAIL=""
PROBE_PW_RESULT="skipped";   PROBE_PW_DETAIL=""

[[ $NEED_AUTH -eq 1 ]]                     && probe_auth_md
[[ $NEED_IOS  -eq 1 ]]                     && { probe_idb; probe_simulator; }
[[ $NEED_WEB  -eq 1 ]]                     && probe_playwright

BLOCKED_RAW=""
if [[ "$PROBE_AUTH_RESULT" == "missing" ]]; then
  # auth-gated alone is itself a blocker; if iOS/web flagged, block those too
  BLOCKED_RAW+=$'auth-gated\n'
  [[ $NEED_IOS -eq 1 ]] && BLOCKED_RAW+=$'ios\n'
  [[ $NEED_WEB -eq 1 ]] && BLOCKED_RAW+=$'web\n'
fi
if [[ "$PROBE_IDB_RESULT" == "broken" || "$PROBE_SIM_RESULT" == "broken" ]]; then
  BLOCKED_RAW+=$'ios\n'
fi
if [[ "$PROBE_PW_RESULT" == "broken" ]]; then
  BLOCKED_RAW+=$'web\n'
fi

UNIQUE_BLOCKED=$(printf "%s" "$BLOCKED_RAW" | sort -u | tr '\n' ',' | sed 's/,$//')

if [[ -n "$UNIQUE_BLOCKED" ]]; then
  STATUS="blocked"
  EXIT_CODE=2
else
  STATUS="ok"
  EXIT_CODE=0
fi

# Emit JSON line first (machine readable).
json_escape() { python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$1"; }

printf '{"status":"%s","blocked_categories":[%s],"probes":{' "$STATUS" \
  "$(echo "$UNIQUE_BLOCKED" | awk -F, '{for(i=1;i<=NF;i++){printf (i>1?",":"")"\""$i"\""}}')"

FIRST=1
for name in AUTH IDB SIM PW; do
  rvar="PROBE_${name}_RESULT"
  dvar="PROBE_${name}_DETAIL"
  result="${!rvar}"
  detail="${!dvar}"
  [[ "$result" == "skipped" ]] && continue
  [[ $FIRST -eq 0 ]] && printf ','
  printf '"%s":{"result":"%s","detail":%s}' \
    "$(echo $name | tr '[:upper:]' '[:lower:]')" "$result" "$(json_escape "$detail")"
  FIRST=0
done
printf '}}\n'

# Then human-readable markdown table for paste into Linear.
echo
echo "## Verify Preflight"
echo
echo "| Probe | Result | Detail |"
echo "|-------|--------|--------|"
for name in AUTH IDB SIM PW; do
  rvar="PROBE_${name}_RESULT"
  dvar="PROBE_${name}_DETAIL"
  result="${!rvar}"
  detail="${!dvar}"
  [[ "$result" == "skipped" ]] && continue
  case "$result" in
    ok)      icon="PASS" ;;
    missing) icon="BLOCK" ;;
    broken)  icon="BLOCK" ;;
    *)       icon="?" ;;
  esac
  printf "| %s | %s | %s |\n" "$(echo $name | tr '[:upper:]' '[:lower:]')" "$icon ($result)" "$detail"
done
echo
if [[ "$STATUS" == "blocked" ]]; then
  echo "**Blocked categories:** $UNIQUE_BLOCKED"
  echo
  echo "Mark every test item in the blocked categories as BLOCKED with the matching probe detail. Signal verify result \`fail\` with reason \`preflight: $UNIQUE_BLOCKED\`. Do not enter Phase 1+."
else
  echo "**All probes passed.** Proceed to Phase 1."
fi

exit $EXIT_CODE
