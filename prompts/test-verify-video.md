# Test Prompt: Verify Agent Video Recording Pipeline

**Purpose:** Exercise the full verify → record-demo-video → stitch → upload pipeline
using the iOS Simulator. This is a dry-run test prompt — not a real issue.

---

## Simulated Context

Pretend you are the verify agent for a ticket called "TEST-001: Settings screen
shows user name and avatar". The test plan has 3 items:

```
## Test Plan
1. [iOS/screenshot] App launches to home screen
2. [iOS/video] Tap Settings → screen transitions smoothly → shows user profile
3. [iOS/screenshot] Back button returns to home screen
```

There is no real PR or Linear issue. Use `TEST-001` as the issue ID placeholder.
Do NOT actually upload to Linear — just prove the pipeline runs end-to-end locally.

---

## What to Execute

### Step 1: Prereq Check

```bash
SKILLS_DIR="${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}"
"$SKILLS_DIR/record-demo-video/scripts/demo-prereqs.sh"
```

Report what's available and what's degraded (e.g., no drawtext → no subtitles).

### Step 2: Setup

```bash
export DEMO_DIR="./demos/test-verify-$(date +%Y-%m-%d-%H%M%S)"
mkdir -p "$DEMO_DIR"/{clips,screenshots}
echo '{"session":"'"$(date -u +%FT%TZ)"'","platform":"ios","actions":[]}' > "$DEMO_DIR/manifest.json"
```

### Step 3: Check Simulator

1. `mcp__ios-simulator__get_booted_sim_id` — is a simulator running?
2. If yes: `mcp__ios-simulator__screenshot` — can you capture the current screen?
3. If no simulator: create synthetic test fixtures instead (see Fallback below)

### Step 4: Capture (Real Simulator Path)

If a simulator is booted with any app visible:

**Item 1 — Static screenshot:**
```
mcp__ios-simulator__screenshot → save to $DEMO_DIR/screenshots/before-00.png
```

**Item 2 — Video flow (the key test):**
Follow the record-demo-video capture loop exactly:
```
1. mcp__ios-simulator__screenshot → screenshots/before-01.png
2. mcp__ios-simulator__record_video
3. mcp__ios-simulator__ui_tap x=195 y=400  (or any safe tap target)
4. sleep 0.8
5. mcp__ios-simulator__stop_recording → clips/action-01.mov
6. mcp__ios-simulator__screenshot → screenshots/after-01.png
7. Log action to manifest
```

**Item 3 — Static screenshot:**
```
mcp__ios-simulator__screenshot → save to $DEMO_DIR/screenshots/after-02.png
```

### Step 4 (Fallback): Synthetic Fixtures

If no simulator is available, create synthetic test data:

```bash
# Fake screenshots (solid colors, iPhone 14 Pro size)
ffmpeg -y -f lavfi -i color=c=blue:s=390x844:d=0.1 -frames:v 1 "$DEMO_DIR/screenshots/before-00.png" 2>/dev/null
ffmpeg -y -f lavfi -i color=c=green:s=390x844:d=0.1 -frames:v 1 "$DEMO_DIR/screenshots/after-00.png" 2>/dev/null
ffmpeg -y -f lavfi -i color=c=blue:s=390x844:d=0.1 -frames:v 1 "$DEMO_DIR/screenshots/before-01.png" 2>/dev/null
ffmpeg -y -f lavfi -i color=c=red:s=390x844:d=0.1 -frames:v 1 "$DEMO_DIR/screenshots/after-01.png" 2>/dev/null
ffmpeg -y -f lavfi -i color=c=red:s=390x844:d=0.1 -frames:v 1 "$DEMO_DIR/screenshots/after-02.png" 2>/dev/null

# Fake video clip
ffmpeg -y -f lavfi -i "color=c=purple:s=430x932:d=1.5,format=yuv420p" \
  -c:v libx264 -t 1.5 "$DEMO_DIR/clips/action-01.mov" 2>/dev/null

# Manifest
cat > "$DEMO_DIR/manifest.json" << 'EOF'
{
  "session": "test-verify",
  "platform": "ios",
  "actions": [
    {
      "index": 0,
      "action": "NAVIGATE",
      "detail": "App launch (home screen)",
      "clip": "action-01.mov",
      "before_screenshot": "before-01.png",
      "after_screenshot": "after-01.png"
    }
  ]
}
EOF
```

### Step 5: Stitch

```bash
SKILLS_DIR="${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}"
"$SKILLS_DIR/record-demo-video/scripts/demo-stitch.sh" "$DEMO_DIR" --no-subtitles
```

### Step 6: Validate Output

Check:
1. `$DEMO_DIR/demo.mp4` exists
2. Resolution matches screenshots (390x844 or similar)
3. File is playable (has h264 video stream)
4. File size is reasonable

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,codec_name -of csv=p=0 "$DEMO_DIR/demo.mp4"
stat -f%z "$DEMO_DIR/demo.mp4"
```

### Step 7: Simulate Upload (DRY RUN)

Do NOT actually call Linear APIs. Instead, report what you WOULD do:

```
WOULD UPLOAD:
  1. Screenshot: $DEMO_DIR/screenshots/before-00.png → TEST-001-home-screen.png
  2. Video: $DEMO_DIR/demo.mp4 → demo-TEST-001.mp4
  3. Screenshot: $DEMO_DIR/screenshots/after-02.png → TEST-001-back-to-home.png

WOULD POST COMMENT:
  **Verification Summary** (test-verify)

  | # | Item | Platform | Evidence | Status |
  |---|------|----------|----------|--------|
  | 1 | App launches to home | iOS | ![screenshot](url) | PASS |
  | 2 | Settings transition | iOS | [Watch demo](url) | PASS |
  | 3 | Back to home | iOS | ![screenshot](url) | PASS |

  **Result:** complete
```

---

## Success Criteria

This test passes if:
- [ ] Prereq check runs and reports status
- [ ] DEMO_DIR is created with correct structure
- [ ] Capture loop runs (real simulator) or synthetic fixtures are created
- [ ] `demo-stitch.sh` produces a valid demo.mp4
- [ ] Output resolution matches input screenshots
- [ ] Simulated upload plan is generated
- [ ] No ffmpeg commands were improvised (all came from the script)
- [ ] Total evidence pipeline took < 60 seconds
