---
name: verify-preflight
description: Use BEFORE Phase 1 of any verify run. Probes infrastructure (auth.md fixture, idb/simulator, Playwright) in <10s and fast-fails the run if anything is broken instead of stalling for 10 minutes discovering it the hard way. Triggers on "verify preflight", auto-invoked from verify prompt Phase 0.
---

# verify-preflight

Fast-fail probes for verify agent infrastructure. Run this FIRST. If it returns
exit 2, do NOT enter test classification or execution — emit the BLOCKED table
the script printed and signal `fail`.

## When to Use

**Always**, at the very start of any verify run, BEFORE Phase 1 (Load Context).

This skill exists because verify agents waste 5–10 minutes per run discovering
that idb is broken or auth.md is missing by trying ui_tap or launching the sim
and watching it fail repeatedly. A 10-second deterministic probe replaces the
trial-and-error.

## The Script

```bash
${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/verify-preflight/scripts/preflight.sh \
  --workspace "$WORKSPACE" \
  [--ios] [--web] [--auth-gated]
```

Pass the flags that match the test plan. If the test plan has any iOS items,
pass `--ios`. If any web items, `--web`. If the feature under test is
auth-gated (you need to log in to reach it), pass `--auth-gated`.

The script writes a single JSON line to stdout, then a markdown table:

```
{"status":"blocked","blocked_categories":["ios"],"probes":{"idb":{"result":"broken","detail":"python3 cannot import pyexpat: ..."}}}

## Verify Preflight

| Probe | Result | Detail |
|-------|--------|--------|
| idb   | BLOCK (broken) | python3 cannot import pyexpat: ... |
| sim   | PASS (ok)      | simctl available |

**Blocked categories:** ios

Mark every test item in the blocked categories as BLOCKED with the matching probe detail. Signal verify result `fail` with reason `preflight: ios`. Do not enter Phase 1+.
```

## Exit Codes

| Code | Meaning | Agent action |
|------|---------|--------------|
| 0    | All probes passed | Continue to Phase 1 |
| 2    | At least one blocker | Emit BLOCKED table for affected items, signal `fail` |
| 1    | Script error (bad args, missing python3) | Treat as soft failure, continue but log warning |

## What the Probes Check

| Probe | What it verifies | Bounded by |
|-------|-----------------|------------|
| `auth_md` | `.autosymph/verify/auth.md` exists and is non-empty | none (file check) |
| `idb` | `python3 -c "import pyexpat"` succeeds AND `idb --help` works | 5s timeout each |
| `sim` | `xcrun simctl list devices booted` returns 0 | 5s timeout |
| `playwright` | `npx --no-install playwright --version` succeeds | 8s timeout |

Total runtime: <10s on a cold cache. Skipped probes contribute 0s.

## Output Contract

The script ALWAYS writes the JSON line first, then the markdown. Parse JSON for
machine decisions, paste markdown into the Linear comment.

The JSON shape is stable:

```json
{
  "status": "ok" | "blocked",
  "blocked_categories": ["ios", "web"],
  "probes": {
    "auth": {"result": "ok|missing", "detail": "..."},
    "idb":  {"result": "ok|broken",  "detail": "..."},
    "sim":  {"result": "ok|broken",  "detail": "..."},
    "pw":   {"result": "ok|broken",  "detail": "..."}
  }
}
```

Probes that were skipped (flag not set) are absent from the JSON.

## Common Errors

| Symptom | Cause | Fix |
|---------|-------|-----|
| `idb broken: pyexpat ImportError` | Python 3.14 + system libexpat ABI mismatch | Downgrade Python in the device venv to 3.12 OR install matching `libexpat` |
| `auth missing` | `.autosymph/verify/auth.md` does not exist or is empty | Create the file with valid Supabase OTP test-account credentials |
| `playwright broken` | Project lacks Playwright dep | `pnpm add -D @playwright/test && npx playwright install chromium` |
| `sim broken: xcrun not on PATH` | Xcode CLT not installed | `xcode-select --install` |

## Anti-Patterns

**Do NOT improvise probes.** If you're tempted to "just try ui_describe and see
what happens" — that's the bug this skill exists to fix. The agent before you
spent 30 minutes and 25k tokens doing exactly that. Run the script.

**Do NOT skip preflight on retry.** If verify is being retried (run 3, run 4,
etc.), preflight is more important, not less. The orchestrator may have
re-dispatched without realizing infra is still broken.

**Do NOT continue past exit 2.** If preflight reports blocked categories, the
items in those categories CANNOT be verified by any means available to the
agent. Emit the table, signal `fail`, exit. The downstream verify_review will
correctly route to structify or Blocked.

**Do NOT pass all flags by default.** Only pass `--ios` if the test plan has
iOS items, etc. The skipped probes save real time and avoid spurious failures
on CLI-only verifies.

## Test

```bash
${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/verify-preflight/test.sh
```

The test creates fixtures simulating each failure mode and asserts the script
emits the right JSON and exit code. It must pass before changes to
preflight.sh ship.
