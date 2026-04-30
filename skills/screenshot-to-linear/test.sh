#!/bin/bash
# ============================================================================
# screenshot-to-linear pipeline test
#
# Validates the full resize → encode → size-check pipeline deterministically.
# Creates a retina-resolution test image, runs the skill's exact commands,
# and asserts the output is suitable for Linear upload.
#
# Usage:  ./test.sh           (run all tests)
#         ./test.sh --upload   (also test Linear upload to ISSUE-123 scratch issue)
#
# Inspired by gstack-browser-use's integration test pattern:
# create real artifacts, exercise real tools, assert real outputs.
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

echo "screenshot-to-linear pipeline test"
echo "==================================="
echo "Test dir: $TEST_DIR"
echo ""

# ── Prereqs ──────────────────────────────────────────────────────

echo "## Prerequisites"

if command -v sips &>/dev/null; then
  pass "sips available"
else
  fail "sips not found" "macOS only"
  echo "ABORT: sips is required (macOS)"
  exit 1
fi

if command -v base64 &>/dev/null; then
  pass "base64 available"
else
  fail "base64 not found" "should be in PATH"
  exit 1
fi

echo ""

# ── Phase 1: Create retina-resolution test image ────────────────
# Simulates what mcp__ios-simulator__screenshot produces.
# Creates a 1260x2730 PNG (iPhone Air retina) using sips.

echo "## Phase 1: Create test fixture (retina PNG)"

# Create a small seed image, then sips-resize UP to retina resolution.
# This is fast: generate 126x273, resize to 1260x2730.
python3 -c "
import struct, zlib

def create_png(w, h, path):
    def chunk(ct, d):
        c = ct + d
        return struct.pack('>I', len(d)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)

    # Gradient rows (simulates real UI content, not solid color)
    rows = []
    for y in range(h):
        row = b'\\x00'
        b = int(255 * y / h)
        row += bytes([int(255 * x / w) for x in range(w) for _ in (0,)] + [40] * w + [b] * w)
        rows.append(row)
    # Interleave RGB properly
    raw = b''
    for y in range(h):
        raw += b'\\x00'
        b = int(255 * y / h)
        for x in range(w):
            raw += bytes([int(255 * x / w), 40, b])

    hdr = b'\\x89PNG\\r\\n\\x1a\\n'
    ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
    idat = chunk(b'IDAT', zlib.compress(raw, 1))
    iend = chunk(b'IEND', b'')
    with open(path, 'wb') as f:
        f.write(hdr + ihdr + idat + iend)

create_png(126, 273, '$TEST_DIR/seed.png')
" 2>/dev/null

# Scale up to retina resolution (1260x2730) using sips
sips -z 2730 1260 "$TEST_DIR/seed.png" --out "$TEST_DIR/retina.png" 2>/dev/null

if [ -f "$TEST_DIR/retina.png" ]; then
  RETINA_SIZE=$(wc -c < "$TEST_DIR/retina.png")
  RETINA_DIMS=$(sips -g pixelWidth -g pixelHeight "$TEST_DIR/retina.png" 2>/dev/null | grep pixel | awk '{print $2}' | tr '\n' 'x' | sed 's/x$//')
  echo "  Created: ${RETINA_DIMS} PNG, ${RETINA_SIZE} bytes"
  pass "retina test image created (${RETINA_DIMS})"
else
  fail "could not create test image" "python3 failed"
  exit 1
fi

echo ""

# ── Phase 2: Resize to 800px JPEG ───────────────────────────────
# This is the EXACT command from the skill.

echo "## Phase 2: Resize to 800px JPEG (skill pipeline step 2)"

sips -Z 800 -s format jpeg "$TEST_DIR/retina.png" --out "$TEST_DIR/resized.jpg" 2>/dev/null

if [ -f "$TEST_DIR/resized.jpg" ]; then
  RESIZED_SIZE=$(wc -c < "$TEST_DIR/resized.jpg")
  RESIZED_W=$(sips -g pixelWidth "$TEST_DIR/resized.jpg" 2>/dev/null | grep pixelWidth | awk '{print $2}')
  RESIZED_H=$(sips -g pixelHeight "$TEST_DIR/resized.jpg" 2>/dev/null | grep pixelHeight | awk '{print $2}')
  echo "  Output: ${RESIZED_W}x${RESIZED_H} JPEG, ${RESIZED_SIZE} bytes"

  # Assert max dimension is 800
  MAX_DIM=$((RESIZED_W > RESIZED_H ? RESIZED_W : RESIZED_H))
  if [ "$MAX_DIM" -le 800 ]; then
    pass "max dimension <= 800px ($MAX_DIM)"
  else
    fail "max dimension > 800px" "got $MAX_DIM"
  fi

  # Assert file size < 100KB (should be ~20-50KB for most screenshots)
  if [ "$RESIZED_SIZE" -lt 102400 ]; then
    pass "file size < 100KB ($RESIZED_SIZE bytes)"
  else
    fail "file size >= 100KB" "$RESIZED_SIZE bytes"
  fi

  # Assert file size > 1KB (not a degenerate image)
  if [ "$RESIZED_SIZE" -gt 1024 ]; then
    pass "file size > 1KB (not degenerate)"
  else
    fail "file size <= 1KB" "$RESIZED_SIZE bytes — may be corrupt"
  fi
else
  fail "sips resize failed" "no output file"
fi

echo ""

# ── Phase 3: File-based base64 encode (ISSUE-123 fix) ───────────
# The EXACT command from the updated skill: encode to .b64 file, not inline.
# This prevents cross-turn context compaction from truncating base64.

echo "## Phase 3: File-based base64 encode (skill pipeline step 2+3 combined)"

# Exact skill command: resize + encode + write to file in ONE Bash call
sips -Z 800 -s format jpeg "$TEST_DIR/retina.png" --out "$TEST_DIR/verify-test.jpg" 2>/dev/null && \
  base64 -i "$TEST_DIR/verify-test.jpg" > "$TEST_DIR/verify-test.b64" && \
  B64_FILE_SIZE=$(wc -c < "$TEST_DIR/verify-test.b64")

if [ -f "$TEST_DIR/verify-test.b64" ]; then
  pass ".b64 file created by combined command"
else
  fail ".b64 file not created" "combined sips+base64 command failed"
fi

echo "  .b64 file size: $B64_FILE_SIZE chars"

# Assert file-based base64 < 100K chars
if [ "$B64_FILE_SIZE" -lt 100000 ]; then
  pass "file-based base64 < 100K chars ($B64_FILE_SIZE)"
else
  fail "file-based base64 >= 100K chars" "$B64_FILE_SIZE"
fi

# Assert file-based base64 > 100 chars (not empty)
if [ "$B64_FILE_SIZE" -gt 100 ]; then
  pass "file-based base64 > 100 chars (not empty)"
else
  fail "file-based base64 <= 100 chars" "$B64_FILE_SIZE — file likely corrupt"
fi

# Validate file-based base64 decodes cleanly
cat "$TEST_DIR/verify-test.b64" | base64 -d > "$TEST_DIR/roundtrip.jpg" 2>/dev/null
if [ -f "$TEST_DIR/roundtrip.jpg" ]; then
  RT_SIZE=$(wc -c < "$TEST_DIR/roundtrip.jpg")
  ORIG_SIZE=$(wc -c < "$TEST_DIR/verify-test.jpg")
  if [ "$RT_SIZE" -eq "$ORIG_SIZE" ]; then
    pass "file-based base64 roundtrip matches ($RT_SIZE bytes)"
  else
    fail "file-based base64 roundtrip mismatch" "original=$ORIG_SIZE roundtrip=$RT_SIZE"
  fi
else
  fail "file-based base64 decode failed" "invalid base64 content"
fi

# ISSUE-123 regression: simulate truncation and verify detection
# If base64 is truncated (as happened in ISSUE-123), roundtrip should fail
FULL_B64=$(cat "$TEST_DIR/verify-test.b64")
HALF_LEN=$((${#FULL_B64} / 2))
TRUNCATED="${FULL_B64:0:$HALF_LEN}"
echo "$TRUNCATED" | base64 -d > "$TEST_DIR/truncated.jpg" 2>/dev/null
TRUNC_SIZE=$(wc -c < "$TEST_DIR/truncated.jpg" 2>/dev/null || echo "0")
if [ "$TRUNC_SIZE" -lt "$ORIG_SIZE" ]; then
  pass "truncated base64 produces smaller file (${TRUNC_SIZE} < ${ORIG_SIZE}) — detectable corruption"
else
  fail "truncation not detectable" "truncated=$TRUNC_SIZE original=$ORIG_SIZE"
fi

echo ""

# ── Phase 4: sips stdout contamination test ─────────────────────
# Reproduces the EXACT bug from ISSUE-123: sips stdout leaking into base64.

echo "## Phase 4: sips stdout contamination regression test"

# BAD pattern (what ISSUE-123 did — DO NOT use in production)
BAD_B64=$(sips -Z 600 -s format jpeg "$TEST_DIR/retina.png" --out "$TEST_DIR/bad.jpg" && base64 -i "$TEST_DIR/bad.jpg")
# GOOD pattern (skill's recommended approach)
sips -Z 600 -s format jpeg "$TEST_DIR/retina.png" --out "$TEST_DIR/good.jpg" 2>/dev/null
GOOD_B64=$(base64 -i "$TEST_DIR/good.jpg")

# The bad pattern includes sips output path in the string
if echo "$BAD_B64" | head -1 | grep -q "/"; then
  pass "detected contamination in BAD pattern (sips path in output)"
else
  # sips might not print to stdout on all versions — skip if clean
  skip "no contamination in BAD pattern" "sips version may not print path"
fi

# The good pattern should be clean base64 only
if echo "$GOOD_B64" | base64 -d > /dev/null 2>&1; then
  pass "GOOD pattern (2>/dev/null) produces clean base64"
else
  fail "GOOD pattern produces invalid base64" "unexpected"
fi

echo ""

# ── Phase 5: Minimum quality gate ───────────────────────────────
# Prevents the ISSUE-123 failure: images resized below 600px.

echo "## Phase 5: Minimum quality gate (prevents ISSUE-123 regression)"

sips -Z 300 -s format jpeg "$TEST_DIR/retina.png" --out "$TEST_DIR/tiny.jpg" 2>/dev/null
TINY_W=$(sips -g pixelWidth "$TEST_DIR/tiny.jpg" 2>/dev/null | grep pixelWidth | awk '{print $2}')
TINY_H=$(sips -g pixelHeight "$TEST_DIR/tiny.jpg" 2>/dev/null | grep pixelHeight | awk '{print $2}')

if [ "$TINY_W" -lt 400 ] && [ "$TINY_H" -lt 400 ]; then
  pass "300px resize produces sub-400px image (${TINY_W}x${TINY_H}) — this is what we're preventing"
else
  skip "300px resize still above 400px" "${TINY_W}x${TINY_H}"
fi

# Verify 800px resize stays above minimum
if [ "$RESIZED_W" -ge 400 ] || [ "$RESIZED_H" -ge 400 ]; then
  pass "800px resize stays above 400px quality gate (${RESIZED_W}x${RESIZED_H})"
else
  fail "800px resize below 400px" "${RESIZED_W}x${RESIZED_H} — quality too low"
fi

echo ""

# ── Phase 6 (optional): Linear upload test ──────────────────────

if [ "${1:-}" = "--upload" ]; then
  echo "## Phase 6: Linear upload smoke test"
  echo "  (requires Linear MCP — run from Claude Code session)"
  echo "  Base64 payload ready: $B64_LEN chars"
  echo "  To test: paste base64 into mcp__linear__create_attachment"
  skip "Linear upload" "manual — run from within Claude Code session"
  echo ""
fi

# ── Summary ──────────────────────────────────────────────────────

echo "==================================="
echo "Results: $PASS passed, $FAIL failed, $SKIP skipped"
echo ""

if [ "$FAIL" -gt 0 ]; then
  echo "PIPELINE BROKEN — fix before deploying skill"
  exit 1
else
  echo "PIPELINE OK — skill commands produce valid, uploadable images"
  exit 0
fi
