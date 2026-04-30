#!/usr/bin/env bash
# demo-compare.sh — Side-by-side before/after comparison video
#
# Usage:
#   demo-compare.sh --before <video_or_img> --after <video_or_img> \
#                    --before-label "BEFORE FIX (broken)" \
#                    --after-label "AFTER FIX" \
#                    [--before-sub "subtitle text"] \
#                    [--after-sub "subtitle text"] \
#                    [-o output.mp4] [--hd]
#
# Supports: .mov, .mp4, .png, .jpg inputs (mixed ok — img loops for 5s)
# Produces: side-by-side comparison with labeled header bar and timestamp

set -euo pipefail

BEFORE=""
AFTER=""
BEFORE_LABEL="BEFORE"
AFTER_LABEL="AFTER"
BEFORE_SUB=""
AFTER_SUB=""
OUTPUT="comparison.mp4"
HD=false
PANEL_W=600

while [[ $# -gt 0 ]]; do
  case "$1" in
    --before)       BEFORE="$2"; shift 2 ;;
    --after)        AFTER="$2"; shift 2 ;;
    --before-label) BEFORE_LABEL="$2"; shift 2 ;;
    --after-label)  AFTER_LABEL="$2"; shift 2 ;;
    --before-sub)   BEFORE_SUB="$2"; shift 2 ;;
    --after-sub)    AFTER_SUB="$2"; shift 2 ;;
    -o)             OUTPUT="$2"; shift 2 ;;
    --hd)           HD=true; shift ;;
    *)              echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

[ -n "$BEFORE" ] || { echo "ERROR: --before required" >&2; exit 1; }
[ -n "$AFTER" ]  || { echo "ERROR: --after required" >&2; exit 1; }
[ -f "$BEFORE" ] || { echo "ERROR: not found: $BEFORE" >&2; exit 1; }
[ -f "$AFTER" ]  || { echo "ERROR: not found: $AFTER" >&2; exit 1; }
command -v ffmpeg >/dev/null 2>&1 || { echo "ERROR: ffmpeg not found" >&2; exit 1; }

HAS_DRAWTEXT=false
set +o pipefail
if ffmpeg -filters 2>&1 | grep -q drawtext; then HAS_DRAWTEXT=true; fi
set -o pipefail

if [ "$HD" = true ]; then
  CRF="-crf 18 -preset slow"
else
  CRF="-crf 23 -preset medium"
fi

echo "=== demo-compare.sh ==="
echo "  Before:    $BEFORE"
echo "  After:     $AFTER"
echo "  Drawtext:  $HAS_DRAWTEXT"

# ── Detect image inputs (need -loop 1) ───────────────────────────────────
is_image() { case "${1##*.}" in png|jpg|jpeg|bmp|tiff) return 0;; *) return 1;; esac; }

BEFORE_INPUT=(-i "$BEFORE")
AFTER_INPUT=(-i "$AFTER")
if is_image "$BEFORE"; then BEFORE_INPUT=(-loop 1 -t 5 -i "$BEFORE"); fi
if is_image "$AFTER";  then AFTER_INPUT=(-loop 1 -t 5 -i "$AFTER"); fi

# ── Build the single-pass filter_complex (Tabby pattern) ─────────────────
# Scale both → hstack → pad top for header → drawtext labels
FILTER="[0:v]scale=${PANEL_W}:-2,setpts=PTS-STARTPTS[a];"
FILTER+="[1:v]scale=${PANEL_W}:-2,setpts=PTS-STARTPTS[b];"

if [ "$HAS_DRAWTEXT" = true ]; then
  FILTER+="[a][b]hstack=inputs=2,pad=iw:ih+240:0:140:color=0x1a1a1a,"
  FILTER+="drawtext=text='${BEFORE_LABEL}':fontsize=36:fontcolor=0xff6b6b:x=(w/4)-(text_w/2):y=55,"
  FILTER+="drawtext=text='${AFTER_LABEL}':fontsize=36:fontcolor=0x00ff88:x=(3*w/4)-(text_w/2):y=55"
  if [ -n "$BEFORE_SUB" ]; then
    FILTER+=",drawtext=text='${BEFORE_SUB}':fontsize=20:fontcolor=0xcccccc:x=(w/4)-(text_w/2):y=105"
  fi
  if [ -n "$AFTER_SUB" ]; then
    FILTER+=",drawtext=text='${AFTER_SUB}':fontsize=20:fontcolor=0xcccccc:x=(3*w/4)-(text_w/2):y=105"
  fi
  FILTER+=",drawtext=text='%{pts\:hms}':fontsize=22:fontcolor=white:box=1:boxcolor=black@0.7:boxborderw=8:x=20:y=h-50"
else
  FILTER+="[a][b]hstack=inputs=2"
fi
FILTER+="[v]"

# ── Encode ───────────────────────────────────────────────────────────────
ffmpeg -hide_banner -loglevel warning -y \
  "${BEFORE_INPUT[@]}" "${AFTER_INPUT[@]}" \
  -filter_complex "$FILTER" \
  -map '[v]' \
  -c:v libx264 $CRF -pix_fmt yuv420p -movflags +faststart -an \
  "$OUTPUT"

# ── Report ───────────────────────────────────────────────────────────────
if [ -f "$OUTPUT" ]; then
  SIZE=$(stat -f%z "$OUTPUT" 2>/dev/null || stat -c%s "$OUTPUT")
  SIZE_KB=$(( SIZE / 1024 ))
  RES=$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "$OUTPUT")
  DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUTPUT")
  echo ""
  echo "=== DONE ==="
  echo "  Output:     $OUTPUT"
  echo "  Resolution: $RES"
  echo "  Duration:   ${DUR}s"
  echo "  Size:       ${SIZE_KB}KB"
else
  echo "ERROR: output not created" >&2
  exit 1
fi
