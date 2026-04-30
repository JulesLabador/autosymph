#!/bin/bash
# ============================================================================
# verify-finalize test
#
# Validates verify-finalize.sh end-to-end with a curl stub that simulates
# Linear's GraphQL + presigned-URL upload flow. No network calls.
# ============================================================================

set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/scripts/verify-finalize.sh"
SKILL_MD="$(cd "$(dirname "$0")" && pwd)/SKILL.md"

PASS=0
FAIL=0
TEST_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR"' EXIT

pass() { PASS=$((PASS + 1)); echo "  PASS: $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL: $1 — $2"; }

echo "verify-finalize test"
echo "===================="
echo "Script: $SCRIPT"
echo "Test dir: $TEST_DIR"
echo

# ---- 1. Usage / help -----------------------------------------------------

"$SCRIPT" --help >/dev/null 2>&1
[[ $? -eq 1 ]] && pass "--help → exit 1 (usage)" || fail "--help → exit 1" "got $?"

"$SCRIPT" /nonexistent.json >/dev/null 2>&1
[[ $? -eq 1 ]] && pass "missing manifest → exit 1" || fail "missing manifest" "got $?"

# ---- 2. Malformed manifest (missing required fields) → exit 1 -----------

BAD="$TEST_DIR/bad.json"
echo '{"foo":"bar"}' >"$BAD"
"$SCRIPT" --dry-run "$BAD" >/dev/null 2>&1
[[ $? -eq 1 ]] && pass "missing required fields → exit 1" || fail "missing required fields" "got $?"

# ---- 3. Dry-run, CLI-only manifest → exit 0, table renders --------------

CLI_MAN="$TEST_DIR/cli-only.json"
cat >"$CLI_MAN" <<EOF
{
  "issue_id": "ISSUE-123",
  "session_name": "ISSUE-123-verify-run1",
  "result": "complete",
  "minor_fixes_applied": "none",
  "items": [
    {"n":1,"item":"swift build","method":"cli","status":"PASS","evidence":"exit code 0"},
    {"n":2,"item":"swift test","method":"cli","status":"PASS","evidence":"all 116 passed"}
  ]
}
EOF
OUT=$("$SCRIPT" --dry-run "$CLI_MAN" 2>&1)
RC=$?
[[ $RC -eq 0 ]] && pass "cli-only dry-run → exit 0" || fail "cli-only dry-run → exit 0" "got $RC"
[[ "$OUT" == *"swift build"* ]] && pass "cli-only renders item names" || fail "cli-only renders" "no item in output"
[[ "$OUT" == *"exit code 0"* ]] && pass "cli-only renders evidence text" || fail "cli-only evidence" "no evidence"

# ---- 4. Dry-run with screenshots/videos → URLs synthesized + correct embeds

cat <(printf '\x89PNG\r\n\x1a\n_fake_') >"$TEST_DIR/shot.png"
echo "fake-mp4-bytes" >"$TEST_DIR/clip.mp4"
MEDIA_MAN="$TEST_DIR/media.json"
cat >"$MEDIA_MAN" <<EOF
{
  "issue_id": "ISSUE-123",
  "session_name": "ISSUE-123-verify-run1",
  "result": "complete",
  "items": [
    {"n":1,"item":"Header look","method":"screenshot","status":"PASS","evidence_path":"$TEST_DIR/shot.png","evidence_kind":"image"},
    {"n":2,"item":"Mode swap","method":"ios-simulator + video","status":"PASS","evidence_path":"$TEST_DIR/clip.mp4","evidence_kind":"video"}
  ]
}
EOF
OUT=$("$SCRIPT" --dry-run "$MEDIA_MAN" 2>&1)
RC=$?
[[ $RC -eq 0 ]] && pass "media dry-run → exit 0" || fail "media dry-run → exit 0" "got $RC"
[[ "$OUT" == *"![Header look]("* ]] && pass "image renders as embedded ![]()" || fail "image embed" "no ![]() pattern"
[[ "$OUT" == *"[Watch]("* ]] && pass "video renders as [Watch](url) link" || fail "video link" "no [Watch] pattern"
[[ "$OUT" == *"https://uploads.linear.app/dryrun/"* ]] && pass "dry-run synthesizes deterministic URLs" || fail "dry-run URLs" "no dryrun URL"

# ---- 5. Live mode requires LINEAR_API_KEY (when not dry-run) ------------

(unset LINEAR_API_KEY; "$SCRIPT" "$CLI_MAN" >/dev/null 2>&1)
[[ $? -eq 1 ]] && pass "live mode without LINEAR_API_KEY → exit 1" || fail "missing API key check" "got $?"

# ---- 6. Live mode with curl stub: success path → exit 0, comment posted -

# Stub curl: respond appropriately to GraphQL POST and PUT.
STUB_DIR="$TEST_DIR/stub"
mkdir -p "$STUB_DIR"
CURL_LOG="$TEST_DIR/curl-calls.log"

cat >"$STUB_DIR/curl" <<EOF
#!/bin/bash
# Stub curl. Logs invocations, returns canned GraphQL/PUT responses.
echo "CURL ARGS: \$*" >> "$CURL_LOG"
# Read the request body (--data-binary may or may not be present; pull from args).
body=""
for ((i=1; i<=\$#; i++)); do
  if [[ "\${!i}" == "--data-binary" ]]; then
    nxt=\$((i+1))
    body="\${!nxt}"
  fi
done
# Detect URL (last positional arg).
url="\${@: -1}"

# fileUpload mutation → return canned upload + asset URLs.
if [[ "\$body" == *"fileUpload"* ]]; then
  cat <<'JSON'
{"data":{"fileUpload":{"uploadFile":{"uploadUrl":"https://uploads.linear.app/STUB-UPLOAD-URL","assetUrl":"https://uploads.linear.app/STUB-ASSET-URL.png","headers":[{"key":"x-test","value":"1"}]}}}}
JSON
  exit 0
fi

# commentCreate mutation → return success.
if [[ "\$body" == *"commentCreate"* ]]; then
  cat <<'JSON'
{"data":{"commentCreate":{"success":true,"comment":{"id":"comment-stub-id"}}}}
JSON
  exit 0
fi

# PUT to upload URL → return HTTP 200 (status code only, via -w "%{http_code}")
if [[ "\$url" == *"STUB-UPLOAD-URL"* ]]; then
  # Check if -w was requested (script uses -w "%{http_code}").
  for ((i=1; i<=\$#; i++)); do
    if [[ "\${!i}" == "-w" ]]; then
      echo "200"
      exit 0
    fi
  done
  exit 0
fi

# Fallback: empty.
echo "{}"
EOF
chmod +x "$STUB_DIR/curl"

OUT=$(LINEAR_API_KEY=fake-test-key PATH="$STUB_DIR:$PATH" "$SCRIPT" "$MEDIA_MAN" 2>&1)
RC=$?
[[ $RC -eq 0 ]] && pass "stubbed live mode (success) → exit 0" || fail "stubbed live mode → exit 0" "got $RC; out=${OUT:0:120}"
[[ "$OUT" == *"comment-stub-id"* ]] && pass "stubbed live mode reports comment id" || fail "comment id reported" "out: ${OUT:0:120}"

# Verify the script made the right shape of calls.
fileUpload_count=$(grep -c "fileUpload" "$CURL_LOG" 2>/dev/null || echo 0)
commentCreate_count=$(grep -c "commentCreate" "$CURL_LOG" 2>/dev/null || echo 0)
put_count=$(grep -c "STUB-UPLOAD-URL" "$CURL_LOG" 2>/dev/null || echo 0)

[[ "$fileUpload_count" -eq 2 ]] && pass "calls fileUpload twice (one per media item)" || fail "fileUpload count" "got $fileUpload_count, expected 2"
[[ "$commentCreate_count" -eq 1 ]] && pass "calls commentCreate exactly once (the summary)" || fail "commentCreate count" "got $commentCreate_count, expected 1"
[[ "$put_count" -eq 2 ]] && pass "PUTs file content twice (one per media item)" || fail "PUT count" "got $put_count, expected 2"

# ---- 7. Live mode with upload failure: → exit 2 + comment still posted --

# Stub curl that fails the PUT but lets fileUpload + commentCreate succeed.
cat >"$STUB_DIR/curl" <<EOF
#!/bin/bash
echo "CURL ARGS: \$*" >> "$CURL_LOG.fail"
body=""
for ((i=1; i<=\$#; i++)); do
  if [[ "\${!i}" == "--data-binary" ]]; then
    nxt=\$((i+1))
    body="\${!nxt}"
  fi
done
url="\${@: -1}"
if [[ "\$body" == *"fileUpload"* ]]; then
  cat <<'JSON'
{"data":{"fileUpload":{"uploadFile":{"uploadUrl":"https://uploads.linear.app/STUB-UPLOAD-URL","assetUrl":"https://uploads.linear.app/STUB-ASSET-URL.png","headers":[]}}}}
JSON
  exit 0
fi
if [[ "\$body" == *"commentCreate"* ]]; then
  cat <<'JSON'
{"data":{"commentCreate":{"success":true,"comment":{"id":"comment-fail-stub"}}}}
JSON
  exit 0
fi
if [[ "\$url" == *"STUB-UPLOAD-URL"* ]]; then
  # Simulate upload failure: HTTP 500
  for ((i=1; i<=\$#; i++)); do
    if [[ "\${!i}" == "-w" ]]; then
      echo "500"; exit 0
    fi
  done
fi
echo "{}"
EOF
chmod +x "$STUB_DIR/curl"

OUT=$(LINEAR_API_KEY=fake-test-key PATH="$STUB_DIR:$PATH" "$SCRIPT" "$MEDIA_MAN" 2>&1)
RC=$?
[[ $RC -eq 2 ]] && pass "upload failures but comment posted → exit 2" || fail "partial-failure exit 2" "got $RC; out=${OUT:0:120}"
[[ "$OUT" == *"upload_failures=2"* ]] && pass "partial-failure summary line accurate" || fail "summary line" "out: ${OUT:0:120}"

# ---- 8. SKILL.md cross-references ----------------------------------------

[[ -f "$SKILL_MD" ]] && pass "SKILL.md exists" || fail "SKILL.md exists" ""
grep -q "verify-finalize.sh" "$SKILL_MD" && pass "SKILL.md references verify-finalize.sh" || fail "SKILL.md script ref" ""
grep -q "test.sh" "$SKILL_MD" && pass "SKILL.md references test.sh" || fail "SKILL.md test ref" ""
grep -q "manifest" "$SKILL_MD" && pass "SKILL.md documents manifest schema" || fail "SKILL.md manifest" ""

# ---- 9. Empty items array → exit 1 (refuses to post empty table) --------

EMPTY="$TEST_DIR/empty.json"
cat >"$EMPTY" <<EOF
{"issue_id":"ISSUE-X","session_name":"ISSUE-X-verify-run0","result":"fail","items":[]}
EOF
"$SCRIPT" --dry-run "$EMPTY" >/dev/null 2>&1
[[ $? -eq 1 ]] && pass "empty items → exit 1" || fail "empty items" "got $?"

# ---- Summary -------------------------------------------------------------

echo
echo "======================================="
echo "Results: $PASS passed, $FAIL failed"
echo "======================================="

[[ $FAIL -eq 0 ]] && exit 0 || exit 1
