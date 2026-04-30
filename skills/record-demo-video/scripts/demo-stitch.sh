#!/usr/bin/env bash
# demo-stitch.sh — Stitch captured clips + screenshots into a polished demo.mp4
#
# Usage:  demo-stitch.sh DEMO_DIR [--hd] [--no-subtitles]
#
# Expects DEMO_DIR to contain:
#   clips/action-NN.mov       — video clips per action
#   screenshots/before-NN.png — before screenshots per action
#   screenshots/after-NN.png  — after screenshots per action
#   manifest.json             — action log with index, action, detail, clip, before/after
#
# Produces: DEMO_DIR/demo.mp4

set -euo pipefail

# ── Args ─────────────────────────────────────────────────────────────────
DEMO_DIR="${1:?Usage: demo-stitch.sh DEMO_DIR [--hd] [--no-subtitles]}"
shift

SUBTITLES=true
HD=false

for arg in "$@"; do
  case "$arg" in
    --hd)            HD=true ;;
    --no-subtitles)  SUBTITLES=false ;;
    *)               echo "Unknown flag: $arg" >&2; exit 1 ;;
  esac
done

# ── Prereq check ─────────────────────────────────────────────────────────
for cmd in ffmpeg ffprobe jq; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "ERROR: $cmd not found" >&2; exit 1; }
done

# Auto-disable subtitles if drawtext filter not available
if [ "$SUBTITLES" = true ]; then
  set +o pipefail
  if ! ffmpeg -filters 2>&1 | grep -q drawtext; then
    echo "  NOTE: drawtext filter not available — disabling subtitles"
    SUBTITLES=false
  fi
  set -o pipefail
fi

# ── Canonicalize DEMO_DIR (prevent relative path bugs in subshells) ───────
DEMO_DIR="$(cd "$DEMO_DIR" && pwd)"

# ── Validate DEMO_DIR structure ──────────────────────────────────────────
[ -f "$DEMO_DIR/manifest.json" ] || { echo "ERROR: $DEMO_DIR/manifest.json not found" >&2; exit 1; }

ACTION_COUNT=$(jq '.actions | length' "$DEMO_DIR/manifest.json")
if [ "$ACTION_COUNT" -eq 0 ]; then
  echo "ERROR: manifest.json has no actions" >&2
  exit 1
fi

echo "=== demo-stitch.sh ==="
echo "  DEMO_DIR:    $DEMO_DIR"
echo "  Actions:     $ACTION_COUNT"
echo "  Subtitles:   $SUBTITLES"
echo "  HD:          $HD"

# ── Encoding preset ──────────────────────────────────────────────────────
if [ "$HD" = true ]; then
  ENCODE_OPTS="-c:v libx264 -crf 18 -preset slow -pix_fmt yuv420p"
else
  ENCODE_OPTS="-c:v libx264 -crf 23 -preset medium -pix_fmt yuv420p"
fi

# ── Create working directories ───────────────────────────────────────────
mkdir -p "$DEMO_DIR/annotated" "$DEMO_DIR/final"

# ── Lock target resolution from first screenshot ─────────────────────────
FIRST_BEFORE=$(jq -r '.actions[0].before_screenshot' "$DEMO_DIR/manifest.json")
FIRST_SCREENSHOT="$DEMO_DIR/screenshots/$FIRST_BEFORE"

if [ ! -f "$FIRST_SCREENSHOT" ]; then
  echo "ERROR: First screenshot not found: $FIRST_SCREENSHOT" >&2
  exit 1
fi

TARGET_W=$(ffprobe -v error -select_streams v:0 -show_entries stream=width -of csv=p=0 "$FIRST_SCREENSHOT")
TARGET_H=$(ffprobe -v error -select_streams v:0 -show_entries stream=height -of csv=p=0 "$FIRST_SCREENSHOT")

# Ensure even dimensions (required by libx264)
TARGET_W=$(( (TARGET_W / 2) * 2 ))
TARGET_H=$(( (TARGET_H / 2) * 2 ))

echo "  Resolution:  ${TARGET_W}x${TARGET_H}"

# ── Color map ────────────────────────────────────────────────────────────
get_color() {
  case "$1" in
    CLICK)    echo "00CED1" ;;
    SWIPE)    echo "FF00FF" ;;
    TYPE)     echo "32CD32" ;;
    NAVIGATE) echo "4169E1" ;;
    SCROLL)   echo "FFD700" ;;
    EXEC)     echo "FF8C00" ;;
    *)        echo "808080" ;;
  esac
}

# ── Helper: escape text for ffmpeg drawtext filter ───────────────────────
# Colons, semicolons, commas, and backslashes must be escaped or ffmpeg
# misparses the filter chain (exit 234).
escape_drawtext() {
  printf '%s' "$1" | sed "s/\\\\/\\\\\\\\/g; s/:/\\\\:/g; s/'/\\\\'/g; s/;/\\\\;/g; s/,/\\\\,/g"
}

# ── Helper: convert screenshot to short video clip ───────────────────────
screenshot_to_clip() {
  local input="$1" output="$2" duration="$3"
  ffmpeg -y -loop 1 -i "$input" \
    -vf "scale=${TARGET_W}:${TARGET_H}:force_original_aspect_ratio=decrease,pad=${TARGET_W}:${TARGET_H}:(ow-iw)/2:(oh-ih)/2" \
    $ENCODE_OPTS -t "$duration" \
    "$output" 2>/dev/null
}

# ── Helper: annotate a frame with a subtitle badge ──────────────────────
annotate_frame() {
  local input="$1" output="$2" label="$3" color="$4"
  local safe_label
  safe_label=$(escape_drawtext "$label")
  ffmpeg -y -i "$input" \
    -vf "drawtext=text='${safe_label}':fontcolor=white:fontsize=24:box=1:boxcolor=0x${color}@0.75:boxborderw=10:x=10:y=h-th-20" \
    "$output" 2>/dev/null
}

# ── Helper: annotate + normalize a video clip ────────────────────────────
process_clip() {
  local input="$1" output="$2" label="$3" color="$4"
  local safe_label
  safe_label=$(escape_drawtext "$label")

  local vf="crop=ih*${TARGET_W}/${TARGET_H}:ih:(iw-ih*${TARGET_W}/${TARGET_H})/2:0,scale=${TARGET_W}:${TARGET_H},fps=30"

  if [ "$SUBTITLES" = true ]; then
    vf="${vf},drawtext=text='${safe_label}':fontcolor=white:fontsize=24:box=1:boxcolor=0x${color}@0.75:boxborderw=10:x=10:y=h-th-20"
  fi

  ffmpeg -y -i "$input" \
    -vf "$vf" \
    $ENCODE_OPTS \
    "$output" 2>/dev/null
}

# ── Build timeline ───────────────────────────────────────────────────────
CONCAT="$DEMO_DIR/final/concat.txt"
> "$CONCAT"

for i in $(seq 0 $((ACTION_COUNT - 1))); do
  idx=$(printf '%02d' "$i")

  action=$(jq -r ".actions[$i].action" "$DEMO_DIR/manifest.json")
  detail=$(jq -r ".actions[$i].detail" "$DEMO_DIR/manifest.json")
  clip_name=$(jq -r ".actions[$i].clip" "$DEMO_DIR/manifest.json")
  before_name=$(jq -r ".actions[$i].before_screenshot" "$DEMO_DIR/manifest.json")
  after_name=$(jq -r ".actions[$i].after_screenshot" "$DEMO_DIR/manifest.json")

  color=$(get_color "$action")
  label="${action}: ${detail}"

  echo "  Processing action $i: $label"

  # ── Before screenshot → held clip ────────────────────────────────────
  before_src="$DEMO_DIR/screenshots/$before_name"
  before_final="$DEMO_DIR/final/before-${idx}.png"

  if [ "$SUBTITLES" = true ]; then
    annotate_frame "$before_src" "$before_final" "$label" "$color"
  else
    cp "$before_src" "$before_final"
  fi

  # First action gets 1.5s hold, others get 0.8s
  if [ "$i" -eq 0 ]; then hold="1.5"; else hold="0.8"; fi
  screenshot_to_clip "$before_final" "$DEMO_DIR/final/before-${idx}.mp4" "$hold"
  echo "file 'before-${idx}.mp4'" >> "$CONCAT"

  # ── Action video clip ────────────────────────────────────────────────
  clip_src="$DEMO_DIR/clips/$clip_name"
  clip_final="$DEMO_DIR/final/action-${idx}.mp4"

  if [ -f "$clip_src" ]; then
    process_clip "$clip_src" "$clip_final" "$label" "$color"
    echo "file 'action-${idx}.mp4'" >> "$CONCAT"
  else
    echo "  WARNING: clip not found: $clip_src (skipping video segment)"
  fi

  # ── After screenshot → held clip ─────────────────────────────────────
  after_src="$DEMO_DIR/screenshots/$after_name"
  after_final="$DEMO_DIR/final/after-${idx}.png"
  after_label="${action}: ${detail} (result)"

  if [ "$SUBTITLES" = true ]; then
    annotate_frame "$after_src" "$after_final" "$after_label" "$color"
  else
    cp "$after_src" "$after_final"
  fi

  # Last action gets 2.0s hold, others get 0.8s
  if [ "$i" -eq $((ACTION_COUNT - 1)) ]; then hold="2.0"; else hold="0.8"; fi
  screenshot_to_clip "$after_final" "$DEMO_DIR/final/after-${idx}.mp4" "$hold"
  echo "file 'after-${idx}.mp4'" >> "$CONCAT"
done

# ── Concatenate ──────────────────────────────────────────────────────────
echo "  Concatenating..."
(cd "$DEMO_DIR/final" && ffmpeg -y -f concat -safe 0 -i concat.txt \
  $ENCODE_OPTS -an \
  "$DEMO_DIR/demo.mp4" 2>/dev/null)

# ── Cleanup intermediate files ───────────────────────────────────────────
rm -rf "$DEMO_DIR/final" "$DEMO_DIR/annotated"

# ── Report ───────────────────────────────────────────────────────────────
if [ -f "$DEMO_DIR/demo.mp4" ]; then
  SIZE=$(stat -f%z "$DEMO_DIR/demo.mp4" 2>/dev/null || stat -c%s "$DEMO_DIR/demo.mp4")
  SIZE_MB=$(echo "scale=1; $SIZE / 1048576" | bc 2>/dev/null || echo "?")
  echo ""
  echo "=== DONE ==="
  echo "  Output:   $DEMO_DIR/demo.mp4 (${SIZE_MB}MB)"
  echo "  Actions:  $ACTION_COUNT"
  echo "  Resolution: ${TARGET_W}x${TARGET_H}"
  exit 0
else
  echo "ERROR: demo.mp4 was not created" >&2
  exit 1
fi
