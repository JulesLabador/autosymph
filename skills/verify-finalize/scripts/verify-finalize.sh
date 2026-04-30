#!/bin/bash
# ============================================================================
# verify-finalize — script-driven verify completion (Structify E).
#
# Takes a manifest JSON and atomically:
#   1. Uploads every item.evidence_path to Linear (fileUpload mutation +
#      PUT to presigned URL).
#   2. Builds a markdown verification summary table with the returned asset
#      URLs embedded.
#   3. Posts the summary as ONE save_comment via commentCreate mutation.
#
# This replaces the verify agent's manual "for each item: upload, then post
# summary" workflow that has failed mechanically across runs 10/12/13. The
# agent's only end-action becomes calling this script — there's no separate
# upload step to skip.
#
# Usage:
#   verify-finalize.sh <path-to-manifest.json>
#   verify-finalize.sh --dry-run <path-to-manifest.json>  # builds + prints, no API calls
#
# Exit codes:
#   0 — all uploads succeeded, comment posted
#   2 — partial failure (some uploads failed, comment posted with [UPLOAD FAILED] markers)
#   1 — script/env error (missing LINEAR_API_KEY, malformed manifest)
#
# Manifest schema:
# {
#   "issue_id": "ISSUE-123",
#   "session_name": "ISSUE-123-verify-run14",
#   "result": "complete" | "fail",
#   "minor_fixes_applied": "none" | "<short description>",
#   "items": [
#     {"n": 1, "item": "swift build", "method": "cli",
#      "status": "PASS", "evidence": "exit code 0"},
#     {"n": 6, "item": "Header in Text mode", "method": "ios-simulator + video",
#      "status": "PASS", "evidence_path": "/abs/path/verify-1.mp4",
#      "evidence_kind": "video"},
#     {"n": 12, "item": "Dynamic Type", "method": "screenshot",
#      "status": "PASS", "evidence_path": "/abs/path/verify-2.png",
#      "evidence_kind": "image"},
#     {"n": 8, "item": "iOS chrome", "method": "ios-simulator + video",
#      "status": "BLOCKED", "evidence": "preflight: idb broken"},
#     {"n": 16, "item": "Aesthetic match", "method": "human",
#      "status": "SKIPPED: human-only"}
#   ]
# }
# ============================================================================

set -u

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
  shift
fi

if [[ $# -ne 1 || "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  sed -n '2,42p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
fi

MANIFEST="$1"

if [[ ! -f "$MANIFEST" ]]; then
  echo "verify-finalize: manifest not found: $MANIFEST" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "verify-finalize: jq is required" >&2
  exit 1
fi

# Validate manifest structure (jq exits non-zero on missing required keys).
ISSUE_ID=$(jq -re '.issue_id' "$MANIFEST" 2>/dev/null) || {
  echo "verify-finalize: manifest missing required field 'issue_id'" >&2; exit 1; }
SESSION_NAME=$(jq -re '.session_name' "$MANIFEST" 2>/dev/null) || {
  echo "verify-finalize: manifest missing required field 'session_name'" >&2; exit 1; }
RESULT=$(jq -re '.result' "$MANIFEST" 2>/dev/null) || {
  echo "verify-finalize: manifest missing required field 'result'" >&2; exit 1; }
MINOR_FIXES=$(jq -r '.minor_fixes_applied // "none"' "$MANIFEST")
N_ITEMS=$(jq '.items | length' "$MANIFEST")

if [[ "$N_ITEMS" -eq 0 ]]; then
  echo "verify-finalize: manifest has zero items — nothing to report" >&2
  exit 1
fi

# LINEAR_API_KEY is required for live mode.
LINEAR_API_KEY="${LINEAR_API_KEY:-}"
if [[ $DRY_RUN -eq 0 && -z "$LINEAR_API_KEY" ]]; then
  echo "verify-finalize: LINEAR_API_KEY env var not set (set, or use --dry-run)" >&2
  exit 1
fi

# Allow tests to override the API endpoint via LINEAR_API_URL.
LINEAR_API_URL="${LINEAR_API_URL:-https://api.linear.app/graphql}"

# ---- HTTP helpers --------------------------------------------------------

# JSON-escape a string for inclusion in a JSON literal.
json_escape() {
  printf '%s' "$1" | jq -Rsa .
}

# Call a GraphQL mutation. Args: <query> <variables-json>
# Outputs the data field on success, errors to stderr.
gql() {
  local query="$1"
  local vars="$2"
  local body
  body=$(jq -n --arg q "$query" --argjson v "$vars" '{query:$q, variables:$v}')
  curl -s -X POST "$LINEAR_API_URL" \
    -H "Authorization: $LINEAR_API_KEY" \
    -H "Content-Type: application/json" \
    --data-binary "$body"
}

# Determine MIME content type for a file by extension.
content_type_for() {
  case "$1" in
    *.png|*.PNG)   echo "image/png" ;;
    *.jpg|*.jpeg|*.JPG|*.JPEG) echo "image/jpeg" ;;
    *.gif|*.GIF)   echo "image/gif" ;;
    *.mp4|*.MP4)   echo "video/mp4" ;;
    *.mov|*.MOV)   echo "video/quicktime" ;;
    *.webm|*.WEBM) echo "video/webm" ;;
    *)             echo "application/octet-stream" ;;
  esac
}

# Upload a single file. Args: <file_path>
# Echoes the asset URL on success, or "UPLOAD_FAILED:<reason>" on failure.
upload_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "UPLOAD_FAILED:file_not_found:$path"
    return
  fi
  local size filename ctype
  size=$(stat -f%z "$path" 2>/dev/null || stat -c%s "$path" 2>/dev/null)
  filename=$(basename "$path")
  ctype=$(content_type_for "$path")

  if [[ $DRY_RUN -eq 1 ]]; then
    # Synthesize a deterministic fake URL so the table is renderable.
    echo "https://uploads.linear.app/dryrun/$(echo -n "$path" | shasum -a 1 | cut -d' ' -f1)/$filename"
    return
  fi

  local q='mutation($size: Int!, $filename: String!, $contentType: String!) {
    fileUpload(size: $size, filename: $filename, contentType: $contentType) {
      uploadFile { uploadUrl assetUrl headers { key value } }
    }
  }'
  local vars
  vars=$(jq -n --arg fn "$filename" --arg ct "$ctype" --argjson sz "$size" \
    '{size:$sz, filename:$fn, contentType:$ct}')

  local resp
  resp=$(gql "$q" "$vars")
  local upload_url asset_url
  upload_url=$(echo "$resp" | jq -r '.data.fileUpload.uploadFile.uploadUrl // empty')
  asset_url=$(echo "$resp" | jq -r '.data.fileUpload.uploadFile.assetUrl // empty')

  if [[ -z "$upload_url" || -z "$asset_url" ]]; then
    local err
    err=$(echo "$resp" | jq -r '.errors[0].message // "unknown error"')
    echo "UPLOAD_FAILED:fileUpload_mutation:$err"
    return
  fi

  # Build curl headers from the GraphQL response's headers array.
  local header_args=()
  while IFS=$'\t' read -r k v; do
    header_args+=(-H "$k: $v")
  done < <(echo "$resp" | jq -r '.data.fileUpload.uploadFile.headers[] | [.key, .value] | @tsv')
  header_args+=(-H "Content-Type: $ctype")

  local http_code
  http_code=$(curl -s -o /dev/null -w "%{http_code}" -X PUT \
    "${header_args[@]}" \
    --data-binary "@$path" \
    "$upload_url")
  if [[ "$http_code" != "200" && "$http_code" != "204" ]]; then
    echo "UPLOAD_FAILED:put_status_$http_code"
    return
  fi
  echo "$asset_url"
}

# ---- Build the verification summary table --------------------------------

UPLOAD_FAILURES=0
OUTPUT_TABLE_FILE=$(mktemp)
trap 'rm -f "$OUTPUT_TABLE_FILE"' EXIT

{
  echo "**Verification Summary** (\`$SESSION_NAME\`)"
  echo
  echo "| # | Item | Method | Status | Evidence |"
  echo "|---|------|--------|--------|----------|"
} >"$OUTPUT_TABLE_FILE"

for ((i=0; i<N_ITEMS; i++)); do
  n=$(jq -r ".items[$i].n // ($i+1)" "$MANIFEST")
  item=$(jq -r ".items[$i].item // \"(unnamed)\"" "$MANIFEST")
  method=$(jq -r ".items[$i].method // \"?\"" "$MANIFEST")
  status=$(jq -r ".items[$i].status // \"?\"" "$MANIFEST")
  evidence_path=$(jq -r ".items[$i].evidence_path // empty" "$MANIFEST")
  evidence_kind=$(jq -r ".items[$i].evidence_kind // empty" "$MANIFEST")
  evidence_text=$(jq -r ".items[$i].evidence // empty" "$MANIFEST")

  evidence_cell=""
  if [[ -n "$evidence_path" ]]; then
    asset_url=$(upload_file "$evidence_path")
    if [[ "$asset_url" == UPLOAD_FAILED:* ]]; then
      UPLOAD_FAILURES=$((UPLOAD_FAILURES+1))
      evidence_cell="❌ [UPLOAD FAILED: ${asset_url#UPLOAD_FAILED:}]"
    elif [[ "$evidence_kind" == "image" ]]; then
      evidence_cell="![${item}](${asset_url})"
    elif [[ "$evidence_kind" == "video" ]]; then
      evidence_cell="[Watch](${asset_url})"
    else
      evidence_cell="[link](${asset_url})"
    fi
  elif [[ -n "$evidence_text" ]]; then
    evidence_cell="$evidence_text"
  else
    evidence_cell="—"
  fi

  # Escape pipes in cells.
  item=${item//|/\\|}
  evidence_cell=${evidence_cell//|/\\|}
  echo "| $n | $item | $method | $status | $evidence_cell |" >>"$OUTPUT_TABLE_FILE"
done

{
  echo
  echo "**Result:** $RESULT"
  echo "**Minor fixes applied:** $MINOR_FIXES"
  if [[ $UPLOAD_FAILURES -gt 0 ]]; then
    echo
    echo "⚠ **$UPLOAD_FAILURES upload failure(s).** Affected items show \`[UPLOAD FAILED: ...]\` above; the local files still exist on disk under the verify run's workspace."
  fi
} >>"$OUTPUT_TABLE_FILE"

# ---- Post the comment ----------------------------------------------------

BODY=$(cat "$OUTPUT_TABLE_FILE")

if [[ $DRY_RUN -eq 1 ]]; then
  echo "=== DRY RUN — would post to issue $ISSUE_ID ==="
  echo "$BODY"
  echo
  echo "=== summary ==="
  echo "items=$N_ITEMS upload_failures=$UPLOAD_FAILURES result=$RESULT"
  if [[ $UPLOAD_FAILURES -gt 0 ]]; then
    exit 2
  fi
  exit 0
fi

q='mutation($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) { success comment { id } }
}'
vars=$(jq -n --arg iid "$ISSUE_ID" --arg body "$BODY" '{issueId:$iid, body:$body}')

resp=$(gql "$q" "$vars")
success=$(echo "$resp" | jq -r '.data.commentCreate.success // false')
if [[ "$success" != "true" ]]; then
  err=$(echo "$resp" | jq -r '.errors[0].message // "commentCreate did not return success"')
  echo "verify-finalize: failed to post comment: $err" >&2
  echo "verify-finalize: response: $resp" >&2
  exit 2
fi

comment_id=$(echo "$resp" | jq -r '.data.commentCreate.comment.id')
echo "verify-finalize: posted comment $comment_id on $ISSUE_ID"
echo "verify-finalize: items=$N_ITEMS upload_failures=$UPLOAD_FAILURES result=$RESULT"

if [[ $UPLOAD_FAILURES -gt 0 ]]; then
  exit 2
fi
exit 0
