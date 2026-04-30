---
name: structify
description: Turn a recurring agentic failure into a deterministic skill with a test. Investigates the gap, compares working vs failing runs, writes a skill + test.sh, hardens prompts/config, and writes a postmortem. Use when an agent pipeline fails due to infrastructure, skill, or config gaps — not code bugs. Triggers on "structify", "/structify", "why does this keep failing", "agents keep failing at X", "this pipeline is broken".
---

# Structify

Turn a recurring agentic failure into a deterministic, tested skill.

## When to Use

An agent (verify, implement, any) fails at a mechanical pipeline step — not
because the code is wrong, but because the **infrastructure, skill, or config**
that the agent depends on is missing or broken.

**Signals:**
- Permission denied on a tool the agent needs
- Agent improvises a multi-step pipeline with no skill to guide it
- Same failure across multiple issues (different code, same infra)
- Output quality is garbage (tiny images, corrupted uploads, truncated data)
- Agent retries the same broken step in a loop
- "It worked on that other issue but not this one"

**Not for:**
- Code bugs in the target repo (that's implement/rework)
- Orchestrator crashes (that's standard investigating)
- One-off flaky failures (that's retry)

---

## The 6 Steps

### 1. DETECT — name the failure pattern

State the failure clearly. What operation failed? What did the agent try to do?
What was the actual output vs expected output?

```
FAILURE: Verify agent uploaded 138x300 pixel JPEG thumbnails to Linear.
EXPECTED: Readable screenshots at 800px+ resolution.
PATTERN: Agent couldn't pass retina base64 through context, panic-resized.
```

### 2. COMPARE — find a working reference

Find an instance where the same operation succeeded, even on a different issue
or in a different context. The diff between working and failing reveals the gap.

**Where to look:**
- Linear comments on recent issues (search for the operation type)
- Agent logs (`~/.autosymph/logs/` or equivalent)
- Git history for when this used to work
- Other agents/projects that do the same thing

**What to compare:**
- Tool calls in ndjson logs (what was called, in what order, with what params)
- File sizes and dimensions of artifacts
- Permission denials vs approvals
- Time spent on the operation

If no working reference exists, the comparison is "what a human would do" vs
"what the agent tried."

### 3. ROOT CAUSE — identify the specific gap

The gap is always in one of these locations:

| Gap Type | Where to Fix | Example |
|----------|-------------|---------|
| **Missing skill** | `${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/{name}/SKILL.md` | No standard for screenshot upload |
| **Missing allowed_tool** | Host config YAML or settings.json | `sips` not in verify agent's tool list |
| **Bad prompt instruction** | Verify/implement prompt | "base64 the raw PNG" instead of "resize first" |
| **Missing permission** | Settings or sandbox config | `/tmp/` blocked, shell expansion blocked |
| **Bad default** | Tool config or environment | Retina screenshot with no resize step |

State the root cause as: **"[gap type] — [specific thing missing] in [specific file]"**

### 4. SKILL — write skill with deterministic test

Create `${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/{name}/SKILL.md`:

- **Exact commands** the agent should run (copy-paste ready)
- **Platform coverage** (iOS, web, CLI — whatever applies)
- **Common errors table** with cause and fix
- **Anti-patterns** — what NOT to do (the thing the agent was doing wrong)

Create `${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/{name}/test.sh`:

The test is the proof. It must:

```
a) Create realistic test fixtures (simulating real agent conditions)
b) Exercise the EXACT commands from the skill
c) Assert outputs are valid (dimensions, sizes, integrity)
d) Include a regression test for the specific failure that triggered structify
e) Exit 0 on success, exit 1 on failure
f) Run in < 30 seconds
g) Clean up after itself (tmpdir with trap)
```

**Run the test. It must pass before proceeding.**

Pattern reference: `${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/screenshot-to-linear/test.sh` (13 assertions,
tests resize pipeline + base64 integrity + contamination regression).

Pattern reference: `${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/gstack-browser-use/browse/test/` (integration
tests against real browser with fixtures).

### 5. HARDEN — update prompts and config

Skills alone aren't enough. Agents must be **told** to use the skill and
**allowed** to run its commands.

**Prompts** — update the relevant agent prompt with:
- The new standard procedure (referencing the skill)
- Explicit "DO NOT improvise this — use the skill" instruction
- The minimum quality gate (e.g., "never resize below 600px")

**Config** — update `allowed_tools` in:
- Host YAML or project config
- Global agent settings
- Per-project settings if applicable

**Verify the config change** — grep the config file to confirm the tool is listed.

### 6. POSTMORTEM — document for future investigators

Create `autosymph/docs/postmortems/YYYY-MM-DD-{slug}.md` (or equivalent docs
location for your project):

- **What happened** — timeline of the failure
- **Root cause** — the specific gap
- **Comparison** — working run vs failing run (table format)
- **Fix** — what was changed (skill, prompt, config)
- **How to investigate this class of issue** — diagnostic playbook for future

---

## Structify Checklist

Run through this before declaring the structify complete:

- [ ] Failure pattern named and documented
- [ ] Working reference found and compared (or documented why none exists)
- [ ] Root cause stated as "[gap type] — [specific thing] in [specific file]"
- [ ] Skill written with exact commands and common errors
- [ ] `test.sh` written with assertions covering the failure mode
- [ ] `test.sh` passes (all assertions green)
- [ ] Prompt updated to reference the skill
- [ ] Config updated with any missing tools/permissions
- [ ] Postmortem written with diagnostic playbook
- [ ] Changes committed

---

## Examples

### Screenshot Upload (YYYY-MM-DD)

| Step | What was done |
|------|---------------|
| DETECT | Verify agent uploaded 138x300 JPEG thumbnails — illegible |
| COMPARE | ISSUE-123 uploaded full-res PNGs via subagent. ISSUE-123 panic-resized. |
| ROOT CAUSE | Missing allowed_tool (`sips`), no resize-before-encode in verify prompt |
| SKILL | `screenshot-to-linear` — iOS + Playwright, exact sips commands, contamination warning |
| TEST | 13 assertions: retina→resize→encode→validate + contamination regression |
| HARDEN | Verify prompt Phase 6 rewritten, `sips`/`wc`/Playwright added to allowed_tools |
| POSTMORTEM | `autosymph/docs/postmortems/YYYY-MM-DD-example.md` |

---

## Integration with Autosymph

When used inside the autosymph loop, structify is triggered by `reject_structify`
from verify_review and runs inside the `investigating` state. The investigating
agent invokes this skill, follows all 6 steps, and signals `fixed` to retry verify.

When used standalone, invoke with `/structify` and describe the failing pipeline.
