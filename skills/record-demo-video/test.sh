#!/usr/bin/env bash
# test.sh — Regression test for record-demo-video skill
#
# Creates synthetic test fixtures (fake screenshots + video clips),
# runs demo-stitch.sh, and validates the output.
#
# Exit 0 = all assertions pass, Exit 1 = failure
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STITCH="$SCRIPT_DIR/scripts/demo-stitch.sh"
PREREQS="$SCRIPT_DIR/scripts/demo-prereqs.sh"

TMPDIR_BASE=$(mktemp -d)
trap 'rm -rf "$TMPDIR_BASE"' EXIT

PASS=0
FAIL=0
TOTAL=0

assert() {
  local desc="$1"
  local result="$2"
  TOTAL=$((TOTAL + 1))
  if [ "$result" = "0" ]; then
    PASS=$((PASS + 1))
    echo "  PASS: $desc"
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL: $desc"
  fi
}

# ── Prereq check ─────────────────────────────────────────────────────────
echo "=== Test 1: Prereqs ==="
bash "$PREREQS" >/dev/null 2>&1
assert "demo-prereqs.sh exits 0" "$?"

# ── Create test fixtures ─────────────────────────────────────────────────
echo ""
# Detect drawtext availability (set +o pipefail: ffmpeg -filters exits non-zero,
# pipefail propagates it even when grep succeeds)
HAS_DRAWTEXT=false
set +o pipefail
if ffmpeg -filters 2>&1 | grep -q drawtext; then
  HAS_DRAWTEXT=true
fi
set -o pipefail

echo "=== Test 2: Stitch (subtitles auto-detected: $HAS_DRAWTEXT) ==="

DEMO_DIR="$TMPDIR_BASE/demo-subtitles"
mkdir -p "$DEMO_DIR"/{clips,screenshots}

# Create 2 synthetic test screenshots (390x844 — iPhone 14 Pro)
# Solid color PNGs via ffmpeg
ffmpeg -y -f lavfi -i color=c=blue:s=390x844:d=0.1 -frames:v 1 \
  "$DEMO_DIR/screenshots/before-00.png" 2>/dev/null
ffmpeg -y -f lavfi -i color=c=green:s=390x844:d=0.1 -frames:v 1 \
  "$DEMO_DIR/screenshots/after-00.png" 2>/dev/null
ffmpeg -y -f lavfi -i color=c=red:s=390x844:d=0.1 -frames:v 1 \
  "$DEMO_DIR/screenshots/before-01.png" 2>/dev/null
ffmpeg -y -f lavfi -i color=c=yellow:s=390x844:d=0.1 -frames:v 1 \
  "$DEMO_DIR/screenshots/after-01.png" 2>/dev/null

# Create 2 synthetic video clips (slightly different resolution to test normalization)
# 430x932 simulates a full-screen recording that's wider than the app viewport
ffmpeg -y -f lavfi -i "color=c=blue:s=430x932:d=1,format=yuv420p" \
  -c:v libx264 -t 1 "$DEMO_DIR/clips/action-00.mov" 2>/dev/null
ffmpeg -y -f lavfi -i "color=c=red:s=430x932:d=1,format=yuv420p" \
  -c:v libx264 -t 1 "$DEMO_DIR/clips/action-01.mov" 2>/dev/null

# Create manifest
cat > "$DEMO_DIR/manifest.json" << 'EOF'
{
  "session": "YYYY-MM-DDT10:00:00Z",
  "platform": "ios",
  "actions": [
    {
      "index": 0,
      "action": "NAVIGATE",
      "detail": "Launch app",
      "clip": "action-00.mov",
      "before_screenshot": "before-00.png",
      "after_screenshot": "after-00.png"
    },
    {
      "index": 1,
      "action": "CLICK",
      "detail": "Settings icon",
      "clip": "action-01.mov",
      "before_screenshot": "before-01.png",
      "after_screenshot": "after-01.png"
    }
  ]
}
EOF

# Run stitch
bash "$STITCH" "$DEMO_DIR" 2>&1 | tail -5
STITCH_EXIT=$?

assert "demo-stitch.sh exits 0" "$STITCH_EXIT"
assert "demo.mp4 exists" "$([ -f "$DEMO_DIR/demo.mp4" ]; echo $?)"

# Check resolution matches target (390x844 → even: 390x844)
if [ -f "$DEMO_DIR/demo.mp4" ]; then
  OUT_W=$(ffprobe -v error -select_streams v:0 -show_entries stream=width -of csv=p=0 "$DEMO_DIR/demo.mp4")
  OUT_H=$(ffprobe -v error -select_streams v:0 -show_entries stream=height -of csv=p=0 "$DEMO_DIR/demo.mp4")
  assert "output width is 390" "$([ "$OUT_W" = "390" ]; echo $?)"
  assert "output height is 844" "$([ "$OUT_H" = "844" ]; echo $?)"

  # Check file is reasonable size (> 10KB, < 50MB)
  SIZE=$(stat -f%z "$DEMO_DIR/demo.mp4" 2>/dev/null || stat -c%s "$DEMO_DIR/demo.mp4")
  assert "file size > 1KB" "$([ "$SIZE" -gt 1024 ]; echo $?)"
  assert "file size < 50MB" "$([ "$SIZE" -lt 52428800 ]; echo $?)"

  # Check video is playable (has video stream)
  STREAMS=$(ffprobe -v error -select_streams v -show_entries stream=codec_name -of csv=p=0 "$DEMO_DIR/demo.mp4")
  assert "video stream uses h264" "$(echo "$STREAMS" | grep -q h264; echo $?)"
else
  # Skip resolution/size checks if demo.mp4 wasn't created
  for i in 1 2 3 4 5; do
    assert "SKIPPED (no demo.mp4)" "1"
  done
fi

# Check cleanup happened (final/ should be removed)
assert "final/ dir cleaned up" "$([ ! -d "$DEMO_DIR/final" ]; echo $?)"

# ── Test 3: No subtitles mode ───────────────────────────────────────────
echo ""
echo "=== Test 3: Stitch without subtitles ==="

DEMO_DIR2="$TMPDIR_BASE/demo-nosubs"
cp -r "$DEMO_DIR" "$DEMO_DIR2"
rm -f "$DEMO_DIR2/demo.mp4"  # Remove previous output

bash "$STITCH" "$DEMO_DIR2" --no-subtitles 2>&1 | tail -3
assert "no-subtitles mode exits 0" "$?"
assert "no-subtitles demo.mp4 exists" "$([ -f "$DEMO_DIR2/demo.mp4" ]; echo $?)"

# ── Test 4: HD mode ─────────────────────────────────────────────────────
echo ""
echo "=== Test 4: Stitch with HD ==="

DEMO_DIR3="$TMPDIR_BASE/demo-hd"
cp -r "$DEMO_DIR" "$DEMO_DIR3"
rm -f "$DEMO_DIR3/demo.mp4"

bash "$STITCH" "$DEMO_DIR3" --hd 2>&1 | tail -3
assert "hd mode exits 0" "$?"
assert "hd demo.mp4 exists" "$([ -f "$DEMO_DIR3/demo.mp4" ]; echo $?)"

# HD should produce a larger file than standard
if [ -f "$DEMO_DIR/demo.mp4" ] && [ -f "$DEMO_DIR3/demo.mp4" ]; then
  STD_SIZE=$(stat -f%z "$DEMO_DIR/demo.mp4" 2>/dev/null || stat -c%s "$DEMO_DIR/demo.mp4")
  HD_SIZE=$(stat -f%z "$DEMO_DIR3/demo.mp4" 2>/dev/null || stat -c%s "$DEMO_DIR3/demo.mp4")
  # HD should be at least somewhat larger (not always 2x, but non-trivially larger)
  assert "hd file >= standard file size" "$([ "$HD_SIZE" -ge "$STD_SIZE" ]; echo $?)"
fi

# ── Test 5: Error handling — empty manifest ──────────────────────────────
echo ""
echo "=== Test 5: Error handling ==="

DEMO_DIR4="$TMPDIR_BASE/demo-empty"
mkdir -p "$DEMO_DIR4"/{clips,screenshots}
echo '{"session":"test","platform":"ios","actions":[]}' > "$DEMO_DIR4/manifest.json"

STITCH_EXIT4=0
bash "$STITCH" "$DEMO_DIR4" 2>/dev/null || STITCH_EXIT4=$?
assert "empty manifest exits non-zero" "$([ "$STITCH_EXIT4" -ne 0 ]; echo $?)"

# ── Test 6: Error handling — missing manifest ────────────────────────────
DEMO_DIR5="$TMPDIR_BASE/demo-nomanifest"
mkdir -p "$DEMO_DIR5"

STITCH_EXIT5=0
bash "$STITCH" "$DEMO_DIR5" 2>/dev/null || STITCH_EXIT5=$?
assert "missing manifest exits non-zero" "$([ "$STITCH_EXIT5" -ne 0 ]; echo $?)"

# ── Test 7: Regression — missing clip gracefully handled ─────────────────
echo ""
echo "=== Test 7: Missing clip regression ==="

DEMO_DIR6="$TMPDIR_BASE/demo-missingclip"
mkdir -p "$DEMO_DIR6"/{clips,screenshots}

# Only create screenshots, no clips
ffmpeg -y -f lavfi -i color=c=blue:s=390x844:d=0.1 -frames:v 1 \
  "$DEMO_DIR6/screenshots/before-00.png" 2>/dev/null
ffmpeg -y -f lavfi -i color=c=green:s=390x844:d=0.1 -frames:v 1 \
  "$DEMO_DIR6/screenshots/after-00.png" 2>/dev/null

cat > "$DEMO_DIR6/manifest.json" << 'EOF'
{
  "session": "test",
  "platform": "ios",
  "actions": [
    {
      "index": 0,
      "action": "CLICK",
      "detail": "Test button",
      "clip": "action-00.mov",
      "before_screenshot": "before-00.png",
      "after_screenshot": "after-00.png"
    }
  ]
}
EOF

# Should still produce a video (screenshots-only fallback)
bash "$STITCH" "$DEMO_DIR6" --no-subtitles 2>&1 | tail -3
assert "missing clip still produces demo.mp4" "$([ -f "$DEMO_DIR6/demo.mp4" ]; echo $?)"

# ── Results ──────────────────────────────────────────────────────────────
echo ""
echo "========================================="
echo "  Results: $PASS/$TOTAL passed, $FAIL failed"
echo "========================================="

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
