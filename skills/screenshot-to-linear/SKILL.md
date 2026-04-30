---
name: screenshot-to-linear
description: Upload screenshots to Linear from iOS Simulator or Playwright. Handles resize, base64 encode, upload, and embed URL generation. Use when capturing visual evidence for Linear issues during verification or implementation.
---

# Screenshot to Linear

Upload screenshots from iOS Simulator MCP or Playwright MCP to Linear as inline
attachments. Handles the full pipeline: capture → resize → encode → upload → embed.

## When to Use

- During `/ralph` verification (iOS or web)
- When the verify agent needs to upload evidence to Linear
- When the implement agent embeds artifacts in Linear comments
- Any time you have a local screenshot that needs to be in a Linear comment

## The Iron Rule

**Capture and upload are ONE atomic operation. NEVER capture a screenshot without
uploading it to Linear in the same test item.** If you defer uploads to "later,"
you will run out of turns and ALL evidence will be lost. This happened on ISSUE-123
(9 screenshots taken across 3 runs, 0 uploaded — all rejected).

The rhythm is: screenshot → resize → encode → upload → next item. Not: all screenshots → all uploads.

## Why This Exists

Two failure modes that corrupt screenshot uploads:

1. **Retina size:** iOS Simulator screenshots are retina PNGs (1260x2730+, ~140KB+).
   Base64-encoding raw files produces ~190K chars that exceed context limits.
   Agents panic-resize to illegible thumbnails.

2. **Cross-turn truncation:** Even resized base64 (~18K chars) gets truncated by
   context compaction when the agent generates base64 in one API turn and calls
   `create_attachment` in the next. ISSUE-123 lost half its base64 this way (18,984 → 8,498 chars).

This skill uses a **file-based pipeline** — base64 is written to disk, never held
in context across turns. The agent reads the file content in the same tool call
that uploads it.

---

## iOS Simulator Screenshots

### Step 1: Capture

```
mcp__ios-simulator__screenshot(output_path: "verify-{item}.png")
```

### Step 2: Resize + encode to file (SINGLE Bash call)

**CRITICAL: Do this in ONE Bash command. Never let base64 sit in context across turns.**

```bash
sips -Z 800 -s format jpeg verify-{item}.png --out verify-{item}.jpg 2>/dev/null && base64 -i verify-{item}.jpg > verify-{item}.b64 && wc -c < verify-{item}.b64
```

This does three things atomically:
1. Resizes to 800px JPEG (suppresses sips stdout)
2. Writes base64 to a `.b64` file on disk
3. Prints the file size so you can verify it's reasonable (~27K-67K chars)

**If the `.b64` file is >80K chars**, the resize wasn't enough. Drop to 600px:

```bash
sips -Z 600 -s format jpeg verify-{item}.png --out verify-{item}.jpg 2>/dev/null && base64 -i verify-{item}.jpg > verify-{item}.b64 && wc -c < verify-{item}.b64
```

### Step 3: Upload to Linear (ONE Bash call — model never touches base64)

**CRITICAL: Use the upload script. Do NOT cat the .b64 file and paste into
create_attachment — the model cannot faithfully copy 20K+ chars of base64.
ISSUE-123 proved this: 21444 chars in, 9767 chars out, grey image.**

```bash
${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/screenshot-to-linear/upload-to-linear.sh ISSUE-XXX verify-{item}.jpg "Verify: {description}"
```

This script:
1. Requests a presigned upload URL from Linear's API
2. PUTs the raw file directly (no base64 through the model)
3. Creates the attachment on the issue
4. Returns the embeddable URL on stdout

Save the returned URL.

**Fallback** (if the script fails or LINEAR_API_KEY is not in env):
Use `mcp__linear__create_attachment` with base64, but ONLY for files < 5KB
(where the model can copy faithfully). For larger files, fix the script.

### Step 5: Embed in comment

Use the URL in markdown:

```markdown
![{description}]({returned_url})
```

---

## Playwright (Web) Screenshots

### Step 1: Capture

```
mcp__plugin_playwright_playwright__browser_take_screenshot(raw: true)
```

This returns a base64-encoded PNG inline. Playwright screenshots are typically
viewport-sized (1280x720 or similar) and already manageable — no resize needed.

If you saved to a file instead:

```
mcp__plugin_playwright_playwright__browser_take_screenshot(filename: "verify-{item}.png")
```

### Step 2: Resize + encode to file

Playwright screenshots are usually <100KB. Check and encode in one call:

```bash
SIZE=$(wc -c < verify-{item}.png | tr -d ' ') && if [ "$SIZE" -gt 100000 ]; then sips -Z 800 -s format jpeg verify-{item}.png --out verify-{item}.jpg 2>/dev/null && base64 -i verify-{item}.jpg > verify-{item}.b64; else base64 -i verify-{item}.png > verify-{item}.b64; fi && wc -c < verify-{item}.b64
```

### Step 3-4: Read file + upload + embed

Same as iOS above — `cat verify-{item}.b64`, then `create_attachment` in the
same turn. Embed with `![description](url)`.

---

## Quick Reference

| Platform | Capture Tool | Typical Size | Resize? | Format |
|----------|-------------|-------------|---------|--------|
| iOS Simulator | `mcp__ios-simulator__screenshot` | 140-500KB PNG | **Always** → 800px JPEG | image/jpeg |
| Playwright (raw) | `browser_take_screenshot(raw: true)` | <100KB base64 inline | Rarely | image/png |
| Playwright (file) | `browser_take_screenshot(filename: ...)` | 50-200KB PNG | If >100KB | image/jpeg |

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| "Invalid base64 content provided" | Base64 truncated by context compaction between turns | Use file-based pipeline: write `.b64` to disk, read back in same turn as upload |
| "Invalid base64 content provided" | `sips` stdout mixed into base64 stream | Use `2>/dev/null` on sips command |
| Base64 output "persisted to disk" | File too large (>25K tokens inline) | Resize to 600px JPEG and retry |
| `sips` permission denied | Not in agent's allowed_tools | Add `Bash(sips:*)` to config |
| Image appears grey/truncated | Uploaded a tiny over-compressed thumbnail | Resize to 800px (not 300px) |

## Testing

Run the pipeline test to verify the skill works on this machine:

```bash
${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/screenshot-to-linear/test.sh
```

The test creates a retina-resolution PNG, exercises every pipeline step, and
asserts outputs are valid and uploadable. Includes a regression test for the
ISSUE-123 sips contamination bug. 13 assertions, ~5s runtime.

Follows gstack-browser-use's pattern: real artifacts, real tools, real assertions.

## Anti-Patterns

- **DO NOT** base64-encode raw retina PNGs. They will exceed context limits.
- **DO NOT** resize below 600px. Images become illegible in Linear.
- **DO NOT** chain `sips ... && base64 ...` without redirecting sips output.
- **DO NOT** batch all uploads at the end. Upload IMMEDIATELY after each capture.
  (Hard Rule 0 in the verify prompt.)
- **DO NOT** pass base64 through context across turns. Write to `.b64` file instead.
  Context compaction WILL truncate long base64 strings between API turns.
- **DO NOT** cat a .b64 file and paste the content into `create_attachment`.
  The model cannot faithfully copy 20K+ chars of opaque data. It drops characters
  starting around char 1125, producing a valid-looking but corrupted JPEG.
  ISSUE-123: 21444 chars in file → 9767 chars in tool call → grey image.
  **Use `upload-to-linear.sh` instead** — it bypasses the model entirely.

## Upload Immediately Pattern (script-based)

The verify prompt requires uploading evidence immediately after capture. Follow
this rhythm for each test item:

```
1. mcp__ios-simulator__screenshot(output_path: "verify-{N}.png")
2. Bash: sips -Z 800 -s format jpeg verify-{N}.png --out verify-{N}.jpg 2>/dev/null
3. Bash: ${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/screenshot-to-linear/upload-to-linear.sh ISSUE-XXX verify-{N}.jpg "Verify: {description}"
   → Returns the embeddable URL on stdout
4. Save URL → use in final summary
```

The upload script bypasses the model entirely — raw file goes via HTTP PUT to
Linear's upload API. The model never sees or copies base64 data.

Repeat for each item. Do not wait until all items are verified.
