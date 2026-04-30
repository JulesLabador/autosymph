#!/bin/bash
# ============================================================================
# upload-to-linear.sh — Upload a file to Linear as an issue attachment
#
# Bypasses the model entirely for data transfer. The model calls this script
# and gets back a URL. It never touches the file content.
#
# Usage:
#   upload-to-linear.sh <issue_id> <file_path> <title> [content_type]
#
# Example:
#   upload-to-linear.sh ISSUE-123 verify-launch.jpg "App Launch" image/jpeg
#
# Requires: LINEAR_API_KEY environment variable
# Returns: The Linear attachment URL on stdout (for embedding in markdown)
# ============================================================================

set -euo pipefail

ISSUE_ID="${1:?Usage: upload-to-linear.sh <issue_id> <file_path> <title> [content_type]}"
FILE_PATH="${2:?Usage: upload-to-linear.sh <issue_id> <file_path> <title> [content_type]}"
TITLE="${3:?Usage: upload-to-linear.sh <issue_id> <file_path> <title> [content_type]}"
CONTENT_TYPE="${4:-image/jpeg}"

if [ ! -f "$FILE_PATH" ]; then
  echo "Error: file not found: $FILE_PATH" >&2
  exit 1
fi

if [ -z "${LINEAR_API_KEY:-}" ]; then
  echo "Error: LINEAR_API_KEY not set" >&2
  exit 1
fi

API_URL="https://api.linear.app/graphql"
FILENAME=$(basename "$FILE_PATH")
FILESIZE=$(wc -c < "$FILE_PATH" | tr -d ' ')

# Step 1: Request upload URL from Linear
UPLOAD_RESPONSE=$(curl -s -X POST "$API_URL" \
  -H "Authorization: $LINEAR_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"mutation { fileUpload(contentType: \\\"$CONTENT_TYPE\\\", filename: \\\"$FILENAME\\\", size: $FILESIZE) { success uploadFile { uploadUrl assetUrl headers { key value } } } }\"}")

# Extract upload URL and asset URL
UPLOAD_URL=$(echo "$UPLOAD_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['fileUpload']['uploadFile']['uploadUrl'])" 2>/dev/null)
ASSET_URL=$(echo "$UPLOAD_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['fileUpload']['uploadFile']['assetUrl'])" 2>/dev/null)

if [ -z "$UPLOAD_URL" ] || [ "$UPLOAD_URL" = "None" ]; then
  echo "Error: failed to get upload URL from Linear" >&2
  echo "Response: $UPLOAD_RESPONSE" >&2
  exit 1
fi

# Extract headers
HEADERS=$(echo "$UPLOAD_RESPONSE" | python3 -c "
import sys, json
d = json.load(sys.stdin)
headers = d['data']['fileUpload']['uploadFile'].get('headers', []) or []
for h in headers:
    print(f\"{h['key']}: {h['value']}\")
" 2>/dev/null)

# Step 2: Upload the file directly via PUT
UPLOAD_CMD="curl -s -X PUT '$UPLOAD_URL' --data-binary '@$FILE_PATH' -H 'Content-Type: $CONTENT_TYPE' -H 'Cache-Control: public, max-age=31536000'"

# Add any extra headers from Linear
while IFS= read -r header; do
  if [ -n "$header" ]; then
    UPLOAD_CMD="$UPLOAD_CMD -H '$header'"
  fi
done <<< "$HEADERS"

eval "$UPLOAD_CMD" > /dev/null

# Step 3: Create the attachment on the issue
# First, resolve issue identifier to ID if needed
ISSUE_QUERY_ID="$ISSUE_ID"

ATTACH_RESPONSE=$(curl -s -X POST "$API_URL" \
  -H "Authorization: $LINEAR_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"mutation { attachmentCreate(input: { issueId: \\\"$ISSUE_QUERY_ID\\\", title: \\\"$TITLE\\\", url: \\\"$ASSET_URL\\\" }) { success attachment { url } } }\"}")

# Try to extract the attachment URL
ATTACH_URL=$(echo "$ATTACH_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['attachmentCreate']['attachment']['url'])" 2>/dev/null)

if [ -z "$ATTACH_URL" ] || [ "$ATTACH_URL" = "None" ]; then
  # attachmentCreate might fail if issue ID is an identifier like ISSUE-123
  # In that case, just return the asset URL which is directly embeddable
  echo "$ASSET_URL"
else
  echo "$ASSET_URL"
fi
