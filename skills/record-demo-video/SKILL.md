---
name: record-demo-video
description: Record demo videos using real screen recording + screenshot stitching hybrid approach. Captures smooth video clips per action, overlays color-coded subtitle badges, and concatenates into a polished demo. Works with iOS Simulator MCP (record_video) and macOS screencapture (web). Triggers on "record a video", "screen recording", "demo video", "proof that it works", "capture video", "record demo", "record smooth demo", "demo with animations".
---

# Record Demo Video

## Prereq Guard (RUN FIRST)

```bash
${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/record-demo-video/scripts/demo-prereqs.sh
```

If it exits non-zero, STOP and tell the user what's missing. Do NOT improvise around missing tools.

---

## Decision Tree

```
What are you demoing?
|
+-- Bug fix / behavior change (has a "before" state to compare against)
|   --> COMPARISON MODE: side-by-side before/after (demo-compare.sh)
|
+-- Animated / interactive / multi-step flow
|   --> SEQUENTIAL MODE: capture loop + stitch (demo-stitch.sh)
|
+-- Static layout / single state / styling
    --> Use screenshot-stitch skill instead (screenshots only, lighter)
```

**How to decide comparison vs sequential:**
- Can you reproduce the broken behavior on main/before the fix? --> comparison
- Is the value in showing a multi-step flow or journey? --> sequential
- Both? Record the flow on main first, then on the branch, then compare

---

## Comparison Mode (before/after side-by-side)

For bug fixes and behavior changes where showing the diff is more powerful than
showing the flow. Produces a side-by-side video like Tabby's visual-proof pattern.

```bash
${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/record-demo-video/scripts/demo-compare.sh \
  --before before.mov --after after.mov \
  --before-label "BEFORE FIX (broken)" \
  --after-label "AFTER FIX" \
  --before-sub "MRU order NOT preserved" \
  --after-sub "MRU order preserved via URL fingerprint" \
  -o comparison.mp4
```

Accepts videos (.mov, .mp4) or screenshots (.png, .jpg) — mix and match.
Handles duration matching (pads shorter clip with freeze frame).
Adds labeled header bar + timestamp badge if drawtext is available.

**Workflow for comparison:**
1. Checkout `main`, record the broken behavior (screenshot or video)
2. Checkout the fix branch, record the fixed behavior
3. Run `demo-compare.sh` with both artifacts

---

## Sequential Mode (multi-step flow)

For new features and multi-step interactions where the value is in showing
the journey. Produces a timeline video with action boundaries.

```
1. SETUP    -->  Create DEMO_DIR, init manifest
2. CAPTURE  -->  Per-action loop (screenshot -> record -> action -> stop -> screenshot)
3. STITCH   -->  demo-stitch.sh $DEMO_DIR [--hd] [--no-subtitles]
4. UPLOAD   -->  screenshot-to-linear skill or base64 + create_attachment
```

---

### Step 1: Setup

```bash
export DEMO_DIR="./demos/$(date +%Y-%m-%d-%H%M%S)"
mkdir -p "$DEMO_DIR"/{clips,screenshots}
echo '{"session":"'"$(date -u +%FT%TZ)"'","platform":"PLATFORM","actions":[]}' > "$DEMO_DIR/manifest.json"
```

Replace `PLATFORM` with `ios` or `web`.

---

### Step 2: Capture Loop

For EVERY action in the demo, repeat this exact sequence:

#### iOS (Simulator MCP)

```
1. mcp__ios-simulator__screenshot          --> save to screenshots/before-NN.png
2. mcp__ios-simulator__record_video        --> starts recording
3. PERFORM ACTION (ui_tap, ui_swipe, launch_app, etc.)
4. WAIT for animation to settle (300-1000ms)
5. mcp__ios-simulator__stop_recording      --> move output to clips/action-NN.mov
6. mcp__ios-simulator__screenshot          --> save to screenshots/after-NN.png
7. LOG to manifest (see below)
```

#### Web (screencapture + Playwright MCP)

```
1. browser_take_screenshot                 --> save to screenshots/before-NN.png
2. screencapture -v "$DEMO_DIR/clips/action-NN.mov" &
   RECORD_PID=$!
3. PERFORM ACTION (browser_click, browser_fill_form, etc.)
4. WAIT for animation to settle (300-1000ms)
5. kill -INT $RECORD_PID && wait $RECORD_PID 2>/dev/null
6. browser_take_screenshot                 --> save to screenshots/after-NN.png
7. LOG to manifest (see below)
```

#### Log each action to manifest

```bash
jq --arg idx "NN" --arg action "CLICK" --arg detail "Button Name" \
   --arg clip "action-NN.mov" --arg before "before-NN.png" --arg after "after-NN.png" \
   '.actions += [{ index: ($idx|tonumber), action: $action, detail: $detail, clip: $clip, before_screenshot: $before, after_screenshot: $after }]' \
   "$DEMO_DIR/manifest.json" > "$DEMO_DIR/manifest.tmp" \
   && mv "$DEMO_DIR/manifest.tmp" "$DEMO_DIR/manifest.json"
```

Action types: `CLICK`, `SWIPE`, `TYPE`, `NAVIGATE`, `SCROLL`, `EXEC`, `WAIT`

---

### Step 3: Stitch (ONE command)

```bash
${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/record-demo-video/scripts/demo-stitch.sh "$DEMO_DIR"
```

Options:
| Flag | Effect |
|------|--------|
| `--hd` | Higher quality: crf 18, preset slow (~2-3x larger) |
| `--no-subtitles` | Skip color-coded action badges |

Output: `$DEMO_DIR/demo.mp4`

The script handles: resolution locking from first screenshot, aspect ratio normalization, subtitle badge overlay, variable hold times, concat, and cleanup. You do NOT need to run any ffmpeg commands manually.

---

### Step 4: Upload

**If a Linear issue is in context** (MANDATORY -- local paths are ephemeral):

Use the `screenshot-to-linear` skill if available. Otherwise:

```bash
# Encode
B64=$(base64 -i "$DEMO_DIR/demo.mp4")

# Upload via MCP
mcp__linear__create_attachment(
  issue: "ISSUE-XXX",
  base64Content: $B64,
  filename: "demo-ISSUE-XXX.mp4",
  contentType: "video/mp4",
  title: "Demo: ISSUE-XXX <description>"
)
# CAPTURE the returned URL --> use it in the embed comment
```

Embed in a Linear comment with actual URLs (NEVER `attachment:filename` placeholders):

```markdown
## Demo Video
[Watch demo](<video_url_from_create_attachment>)

### Key Screenshots
![Before](<screenshot_url>)
![After](<screenshot_url>)
```

**Fallback:** If video > 10MB or upload fails, commit to branch and `git push`. Reference commit SHA in Linear comment.

---

## Platform Detection

| Signal | Platform | Recording | Screenshots |
|--------|----------|-----------|-------------|
| `.xcodeproj` or Simulator booted | iOS | `mcp__ios-simulator__record_video` | `mcp__ios-simulator__screenshot` |
| `package.json` or localhost URL | Web | `screencapture -v` (macOS) | Playwright `browser_take_screenshot` |

---

## Subagent Delegation

When there are 2+ independent video gates:
- Spawn one Agent per gate (parallel for independent screens, sequential for dependent flows)
- Each subagent gets: gate ID, bundle ID, action sequence, pass criteria, DEMO_DIR path
- Main agent collects results and posts summary to Linear

When there's 1 gate or a simple flow: run inline.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Wrong aspect ratio | demo-stitch.sh auto-crops clips to match screenshot resolution |
| Video won't play in QuickTime | demo-stitch.sh uses `-pix_fmt yuv420p` (already handled) |
| iOS Sim recording fails | `xcrun simctl list devices \| grep Booted` -- ensure simulator is up |
| screencapture records wrong area | Resize browser window to fill viewport before recording |
| Video > 10MB for Linear | Commit to branch, reference commit SHA |
| Base64 corrupted by context compaction | Write base64 to temp file, read back in same turn as upload (ISSUE-123) |
| Badge text cut off | Adjust via `--no-subtitles` or edit stitch script fontsize |

---

## Common Viewport Sizes

| Device | Width | Height |
|--------|-------|--------|
| iPhone 14 Pro | 390 | 844 |
| iPhone 16 Pro | 393 | 852 |
| iPhone SE | 375 | 667 |
| iPad Mini | 768 | 1024 |
| Desktop | 1280 | 800 |
