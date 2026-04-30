---
name: context-management
description: Handle large intermediate data safely in long-running agent sessions. Prevents context compaction from silently corrupting base64, JSON, test output, and other data passed between API turns. Use when capturing screenshots, recording videos, uploading artifacts, or running long test suites.
---

# Context Management for Long-Running Agents

Prevent context compaction from silently corrupting your data. This skill teaches
three layers of defense: file-based handoff, size awareness, and context threshold
management.

## When to Use

- During verify runs that capture multiple screenshots or videos
- When base64-encoding files for Linear upload
- When test output exceeds a few hundred lines
- When a session is running long (35+ turns)
- When tool results start showing "persisted to disk" messages
- Any time you hold large data in context across API turns

---

## Layer 1: File-Based Handoff

**The core rule:** Never hold large data in context across API turns. Write it to
disk, then read it back in the SAME turn you need it.

Context compaction triggers at ~80% window fill. Any data in older turns is fair
game for truncation — silently. You won't get a warning. Your 18,984-char base64
becomes 8,498 chars and the upload produces a corrupt file.

### Pattern: Write → Read-in-Same-Turn → Consume

```bash
# Step 1: Generate data and write to disk (ONE Bash call)
sips -Z 800 -s format jpeg input.png --out output.jpg 2>/dev/null && \
  base64 -i output.jpg > output.b64 && \
  wc -c < output.b64

# Step 2: Read file back (in the SAME response as Step 3)
cat output.b64

# Step 3: Consume immediately (SAME API turn as Step 2)
mcp__linear__create_attachment(
  issue: "ISSUE-XXX",
  base64Content: <content from cat>,
  filename: "output.jpg",
  contentType: "image/jpeg",
  title: "Description"
)
```

Steps 2 and 3 MUST be in the SAME response. If they're in different API turns,
context compaction may truncate the base64 between them.

### When to Use Files vs Inline

| Data Type | Size | Action |
|-----------|------|--------|
| Base64 (any) | >10K chars | Write to `.b64` file |
| Base64 (any) | <10K chars | OK inline if consumed same turn |
| JSON payload | >50 lines | Write to `.json` file |
| Test output | >200 lines | Pipe to file: `swift test 2>&1 \| tee test-output.txt` |
| Screenshot PNG | Any | Always resize first (see Size Thresholds) |
| Video | Any | Always file-based (too large for context) |
| Git diff | >100 lines | Write to file, read relevant sections |

---

## Layer 2: Size Awareness

Large data fills context fast. Resize/compress BEFORE encoding.

### Size Thresholds by Data Type

| Data Type | Raw Size | After Processing | Base64 Size | Action |
|-----------|----------|-----------------|-------------|--------|
| iOS screenshot (retina) | 140-500KB PNG | 20-50KB JPEG (800px) | 27-67K chars | Always resize to 800px JPEG |
| iOS screenshot (if still large) | >50KB JPEG | 15-30KB JPEG (600px) | 20-40K chars | Drop to 600px, never below |
| Playwright screenshot | 50-200KB PNG | Usually OK | 67-267K chars | Resize if >100KB |
| Video (demo) | 1-50MB MP4 | N/A | Too large | If <10MB: base64 upload. If >10MB: git commit + SHA reference |
| Test output | 5-500 lines | N/A | N/A | If >200 lines: pipe to file, read summary |

### Resize Commands

**iOS screenshots:**
```bash
sips -Z 800 -s format jpeg input.png --out output.jpg 2>/dev/null
```

**CRITICAL:** The `2>/dev/null` suppresses sips stdout. Without it, sips prints
the output path which contaminates piped base64 streams, causing "Invalid base64
content provided" errors.

**Check if resize was enough:**
```bash
wc -c < output.b64
```
If >80K chars, drop to 600px. Never resize below 600px (images become illegible).

---

## Layer 3: Context Threshold and Graceful Handoff

Claude Code uses a 1M token context window. Compaction triggers at ~80% (~800K
tokens). You have meaningful headroom, but you need to plan ahead.

### The ~400K Token Checkpoint

At approximately 400K tokens (rough proxies below), you're at the halfway point.
Don't panic — you have 600K tokens of headroom. But start planning:

**Proxy signals that you're approaching the threshold:**
- You've used **35+ turns** in this session
- Tool results start showing **"persisted to disk"** (output exceeded inline limit)
- You're seeing **truncated tool results** (content you wrote earlier is missing)

### What to Do at the Threshold

1. **Finish your current step.** Don't abandon mid-task.
2. **Upload any captured evidence NOW.** Screenshots, videos — upload immediately.
   Partial evidence > no evidence.
3. **Summarize earlier work.** Write a brief summary of what you've done so far,
   what passed, what failed, what URLs you've collected.
4. **Preserve critical data.** Keep these RAW (don't summarize):
   - Linear attachment URLs (needed for the summary comment)
   - File paths of artifacts on disk
   - Test results (pass/fail per item)
   - Error messages from failed steps
5. **Let go of exploration.** You can drop:
   - Codebase exploration output from early in the session
   - Full file contents you read but no longer need
   - Intermediate reasoning about approach decisions

### What NOT to Do

- Don't panic-exit at 400K. You have headroom.
- Don't stop mid-upload. Finish the current capture-upload cycle.
- Don't try to "compact manually" by re-reading files. Just proceed carefully.

---

## Integration with Existing Skills

### screenshot-to-linear

The `screenshot-to-linear` skill implements Layer 1 for screenshots specifically.
Use it for all screenshot uploads. It handles resize, file-based base64, and
atomic upload. See that skill for exact commands.

### record-demo-video

The `record-demo-video` skill uses structured temp directories with `manifest.json`
to track video artifacts. All data lives on disk, not in context. Upload the
final stitched video immediately after `demo-stitch.sh` produces it.

---

## Anti-Patterns (Real Failures)

| Anti-Pattern | What Happened | Issue | Fix |
|-------------|---------------|-------|-----|
| Base64 across turns | 18,984 chars truncated to 8,498 between API turns | ISSUE-123 | Write to `.b64` file, read in same turn as upload |
| Deferred uploads | 9 screenshots captured, 0 uploaded across 3 runs | ISSUE-123 | Atomic capture-upload per test item, no separate "upload phase" |
| Raw retina base64 | 190K char base64 exceeded inline limit, persisted to disk | ISSUE-123 | Resize to 800px JPEG before encoding |
| sips stdout contamination | sips output path mixed into base64 stream | ISSUE-123 | Use `2>/dev/null` on all sips commands |
| Panic resize | Agent resized to 300px (illegible thumbnails) | ISSUE-123 | Never resize below 600px |

---

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| "Invalid base64 content provided" | Base64 truncated by compaction | Use file-based pipeline |
| "Invalid base64 content provided" | sips stdout contaminating stream | Add `2>/dev/null` |
| Tool result "persisted to disk" | Output too large for inline | Resize input, or read persisted file |
| Corrupt JPEG on Linear | Truncated base64 uploaded successfully (no error) | Verify file size after base64 roundtrip |
| Screenshots not in summary | Agent ran out of turns before uploading | Upload atomically with each capture |

---

## Quick Decision Tree

```
Got large data to pass between steps?
├── Is it base64? → Write to .b64 file, read in same turn as upload
├── Is it test output? → Pipe to file, read summary
├── Is it JSON? → Write to .json file
├── Is it a screenshot? → Resize first (800px JPEG), then file-based base64
└── Is it a video? → Always file-based, upload or git-commit

Approaching 35+ turns?
├── Have uncaptured evidence? → Upload NOW
├── Have captured but not uploaded? → Upload NOW (atomic)
├── Need to continue? → Summarize earlier work, preserve URLs/paths/results
└── Can finish in 5 more turns? → Finish, then post summary
```
