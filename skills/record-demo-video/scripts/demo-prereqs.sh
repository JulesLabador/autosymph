#!/usr/bin/env bash
# demo-prereqs.sh — Check that all required tools exist before recording.
# Exit 0 if ready, exit 1 with a message if anything is missing.
set -euo pipefail

MISSING=()
WARNINGS=()

command -v ffmpeg   >/dev/null 2>&1 || MISSING+=("ffmpeg  (brew install ffmpeg)")
command -v ffprobe  >/dev/null 2>&1 || MISSING+=("ffprobe (comes with ffmpeg)")
command -v jq       >/dev/null 2>&1 || MISSING+=("jq      (brew install jq)")
command -v base64   >/dev/null 2>&1 || MISSING+=("base64  (should be built-in on macOS)")

# Check ffmpeg has drawtext support (needed for subtitle badges)
# This is optional — without it, use --no-subtitles
HAS_DRAWTEXT=false
if command -v ffmpeg >/dev/null 2>&1; then
  set +o pipefail
  if ffmpeg -filters 2>&1 | grep -q drawtext; then
    HAS_DRAWTEXT=true
  else
    WARNINGS+=("ffmpeg drawtext filter not available — subtitles will be disabled")
    WARNINGS+=("  To enable: brew reinstall ffmpeg (needs libfreetype)")
    WARNINGS+=("  Workaround: use --no-subtitles flag with demo-stitch.sh")
  fi
  set -o pipefail
fi

if [ ${#MISSING[@]} -gt 0 ]; then
  echo "PREREQ CHECK FAILED — missing required tools:"
  for tool in "${MISSING[@]}"; do
    echo "  - $tool"
  done
  echo ""
  echo "Install missing tools and re-run. Do NOT proceed without them."
  exit 1
fi

if [ ${#WARNINGS[@]} -gt 0 ]; then
  echo "PREREQ CHECK PASSED (with warnings):"
  for warn in "${WARNINGS[@]}"; do
    echo "  WARNING: $warn"
  done
  echo ""
  echo "HAS_DRAWTEXT=$HAS_DRAWTEXT"
  exit 0
fi

echo "PREREQ CHECK PASSED — ffmpeg, ffprobe, jq, base64, drawtext all available."
echo "HAS_DRAWTEXT=$HAS_DRAWTEXT"
exit 0
