---
name: autoplan
description: "Plan a feature - gathers context, researches, interviews, identifies risks, outputs PRD. Use when: user says autoplan, /autoplan, or wants to plan a feature with the full research-interview-premortem workflow."
argument-hint: "[LINEAR_ISSUE_ID or description] [claude-codex|codex-claude|codex] [--auto] [--codex-review] [--skip-planning-transition] [--planner-harness=claude|codex] [--question-harness=codex|claude] [--review-harness=codex|claude] [--codex-model=<id>] [--claude-review-model=<id>]"
---

# Plan Phase Orchestrator

Research, Interview, Premortem, PRD, (optional) cross-harness review.

**Input:** `$ARGUMENTS` — Linear issue ID (like "ISSUE-123") or feature description, plus optional flags.

**Skills used:** codebase-explore, documentation-explore, web-research (optional)

**Flags:** Parsed from `$ARGUMENTS`. Flags can appear anywhere in the arguments.

| Flag | Variable | Default | Effect |
|------|----------|---------|--------|
| `claude-codex` | harness preset | default | Claude plans, Codex answers automated questions, Codex reviews first |
| `codex-claude` | harness preset | none | Codex plans, Claude answers automated questions, Claude reviews first |
| `codex` | harness preset | none | Short alias for `codex-claude`; `/autoplan codex ...` flips the harnesses |
| `--auto` | `IS_AUTO` | `false` | Configured question harness answers interview questions instead of user |
| `--codex-review` | `IS_CODEX_REVIEW` | `false` | Enables review loop after drafting (review harness first; opposite harness fallback if unavailable) |
| `--skip-planning-transition` | `IS_SKIP_PLANNING_TRANSITION` | `false` | Skips Step-1 move to Linear "Planning". Use when caller is autosymph-autoplan, where the issue already lives in "Autoplan" and stays there until Step 7.7 moves it to "Ready". |
| `--planner-harness=claude|codex` | `PLANNER_HARNESS` | preset default (`claude`) | Who drafts and revises `PRD.md` + `task-list.md` |
| `--question-harness=codex|claude` | `QUESTION_HARNESS` | preset default (`codex`) | Who answers interview questions in `--auto` mode |
| `--review-harness=codex|claude` | `REVIEW_HARNESS` | preset default (`codex`) | Who performs the review loop first when `--codex-review` is set |
| `--codex-model=<id>` | `CODEX_MODEL` | `gpt-5.4` | Model passed to `codex exec -m <id>`. If `gpt-5.4` is deprecated, override here. |
| `--claude-review-model=<id>` | `CLAUDE_REVIEW_MODEL` | `claude-opus-4-7` | Model used by the Claude-as-reviewer fallback Agent when Codex is unavailable. |

**Mode matrix:**

| Flags | Interview | Review | Use case |
|-------|-----------|--------|----------|
| (none) | User answers | No review | Quick planning with user |
| `--auto` | Preset question harness answers | No review | Stokowski autoplan, no human |
| `--codex-review` | User answers | Configured review harness loop | Formal planning with user |
| `--auto --codex-review` | Preset question harness answers | Configured review harness loop | Full autonomous planning |
| `codex --auto --codex-review` | Claude answers | Claude loop over Codex plan | Full autonomous planning with flipped harnesses |
| `--auto --codex-review --skip-planning-transition` | Preset question harness answers | Configured review harness loop | Headless under autosymph "Autoplan" Linear state (ISSUE-123) |

**Pipeline context:** See `WORKFLOW.md` for the full Triage → Done pipeline.
**Permissions:** See `PERMISSIONS.md` for the allow-list needed to run autonomously.

---

## Step 0: Parse Flags

Parse `$ARGUMENTS` to extract:
1. **Issue ID or description** — everything that isn't a flag (strip `--*` tokens first)
2. **Harness preset token** — `claude-codex` (default), `codex-claude`, or `codex`; remove the token from the issue ID / description
3. **`--auto`** → set `IS_AUTO = true`
4. **`--codex-review`** → set `IS_CODEX_REVIEW = true`
5. **`--skip-planning-transition`** → set `IS_SKIP_PLANNING_TRANSITION = true`
6. **`--planner-harness=claude|codex`** → set `PLANNER_HARNESS` (overrides preset)
7. **`--question-harness=codex|claude`** → set `QUESTION_HARNESS` (overrides preset)
8. **`--review-harness=codex|claude`** → set `REVIEW_HARNESS` (overrides preset)
9. **`--codex-model=<id>`** → set `CODEX_MODEL = <id>` (default `gpt-5.4`)
10. **`--claude-review-model=<id>`** → set `CLAUDE_REVIEW_MODEL = <id>` (default `claude-opus-4-7`)

Preset defaults:
- `claude-codex`: `PLANNER_HARNESS=claude`, `QUESTION_HARNESS=codex`, `REVIEW_HARNESS=codex`
- `codex-claude` or `codex`: `PLANNER_HARNESS=codex`, `QUESTION_HARNESS=claude`, `REVIEW_HARNESS=claude`

Announce the mode:
```
Planning mode: interview={user|auto}:{QUESTION_HARNESS}, review={REVIEW_HARNESS|none},
               skip-planning-transition={true|false},
               planner-harness={PLANNER_HARNESS}, question-harness={QUESTION_HARNESS}, review-harness={REVIEW_HARNESS},
               codex-model={CODEX_MODEL}, claude-review-model={CLAUDE_REVIEW_MODEL}
```

---

## Step 1: Gather Context + Move to Planning

### Linear issue (matches "ISSUE-123", "ISSUE-123", etc.)
Fetch via `mcp__linear__get_issue`:
- Title, description, comments, labels, priority, linked documents

Extract: **problem statement**, **requirements**, **acceptance criteria**, **files mentioned**.

If the description or any comment contains image attachments (Linear renders them as `![...](https://uploads.linear.app/...)` markdown), pull them in with `mcp__linear__extract_images` (pass the markdown). Do NOT `curl` the URL into `/tmp/*.png` and `Read` the file: signed URLs expire in ~5 min and an expired one yields a JSON error body that 400s the next API turn.

Store `ISSUE_ID` — used for Linear comments and Codex context throughout.

**Move the issue to "Planning" status** via `mcp__linear__save_issue` with `state: "Planning"` — **UNLESS `IS_SKIP_PLANNING_TRANSITION = true`**, in which case skip the move entirely. The issue stays in its current Linear state (e.g. "Autoplan" under autosymph) until Step 7.7 moves it to "Ready". Rationale: when autosymph dispatches the skill, the issue is already in a planning-equivalent Linear state; transitioning to "Planning" would defeat autosymph's recovery model (it doesn't poll "Planning"). See ISSUE-123 PRD R5 + R10.

### Feature description (no issue ID)
Use description as starting context. Note what's clear vs ambiguous.

Create a Linear issue for this feature via `mcp__linear__save_issue`:
- Title: derived from the feature description
- Description: the raw input
- Team: infer from the project context, or ask the user
- Status: "Planning"

Store the returned `ISSUE_ID` — all downstream steps depend on it.

---

## Step 2: Research

Run research skills before interviewing. Choose inline (familiar) or subagent (unfamiliar) for each.

**For each skill below:** if subagent mode, use the Agent tool with `subagent_type: "general-purpose"` and tell the agent to follow the skill. Run codebase-explore and documentation-explore in parallel.

### 2.1 Codebase Explore
Invoke the `codebase-explore` skill. Focus on the feature area from Step 1.

### 2.2 Documentation Explore
Invoke the `documentation-explore` skill. Include my-knowledge repo search.

### 2.3 Web Research (optional)
Invoke the `web-research` skill only if the feature involves unfamiliar technology. Skip if well-understood.

### 2.4 Collect Results
Wait for subagents. Combine into a single research summary.

---

## Step 3: Interview

### Mode: `IS_AUTO = false` (user present)

Conduct interview rounds via AskUserQuestion.

**Present research summary first:**
```
Based on my research:

**Relevant files:** [file] — [purpose]
**Existing patterns:** [pattern]
**Potential approaches:** 1. [approach] — [pros/cons]
**Open questions:** [gaps]
```

**Interview rounds:**
1. **Core Requirements** — Problem, MVP scope, hard constraints
2. **Implementation** — Approach, patterns, key technical decisions
3. **Edge Cases** — Failures, boundaries, error handling
4. **Verification** — Definition of done, testing, acceptance criteria

Summarize decisions after each round. Collapse rounds if answers are clear — don't force 4 rounds when 2 suffice.

### Mode: `IS_AUTO = true` (configured question harness answers)

The configured question harness acts as a senior engineer answering interview questions based on the codebase and issue context.

For each interview round:

1. **Formulate the questions** — same questions you'd ask the user
2. **Send to the configured question harness**:
   - If `QUESTION_HARNESS=codex`, use `codex exec -m "$CODEX_MODEL" -`.
   - If `QUESTION_HARNESS=claude`, answer from the current Claude session's research context. If `CLAUDE_REVIEW_MODEL` is explicitly set and isolation matters, spawn a Claude sub-Agent with that model and the same prompt.

   Prompt:
   ```
   You are a senior engineer answering planning questions for this feature.

   ## Context
   Issue: {ISSUE_ID} — {title}
   Description: {description}
   
   ## Research Summary
   {research summary from Step 2}

   ## Questions (Round {N}: {round_name})
   {numbered questions}

   ## Instructions
   - Answer each question concisely based on the codebase and issue context
   - If you're unsure, say "UNCERTAIN: [best guess] — suggest asking the user"
   - If a question requires a subjective product decision, say "PRODUCT_DECISION: [options] — needs human input"
   - You may also ASK your own questions if something is unclear or missing from the issue description. Prefix these with "HARNESS_QUESTION: [question]"
   ```
3. **Parse responses:**
   - Normal answers → accept and continue
   - `UNCERTAIN:` → log as assumption in PRD, flag for user if available
   - `PRODUCT_DECISION:` → if user available, ask via AskUserQuestion. If no user, log as open question in PRD.
   - `CODEX_QUESTION:` or `HARNESS_QUESTION:` → the non-question harness answers from research context. If still uncertain, log as open question in PRD.
4. **Summarize decisions** after each round (same as user mode)

**The question harness may surface questions proactively.** Any line prefixed with `CODEX_QUESTION:` or `HARNESS_QUESTION:` in any response (interview or review) should be addressed by the opposite harness from research context, or escalated to the user if available.

---

## Step 4: Premortem

### TIGERS (clear threats)
- Technical risks, timeline risks, dependency risks, known failure modes

### ELEPHANTS (unspoken concerns)
- Wrong assumptions, things we're avoiding, "what if this doesn't work?"

Add mitigations for each.

**If `IS_AUTO = false`:** Present to user for confirmation.
**If `IS_AUTO = true`:** Log as-is. Optionally send to the opposite harness for a second opinion on risk coverage.

---

## Step 5: First Draft

Write `PRD.md` and `task-list.md` in the **project root** (or `plans/` if that directory exists).

If `PLANNER_HARNESS=claude`, draft and revise the files directly in the current Claude session.

If `PLANNER_HARNESS=codex`, ask Codex to draft both artifacts using the issue context, research summary, interview answers, premortem, Testing Plan rules, and templates below. Use `codex exec -m "$CODEX_MODEL" -`, require exactly two fenced blocks headed `markdown path=PRD.md` and `markdown path=task-list.md`, then write those blocks to disk. If Codex fails or returns malformed artifacts, fall back to Claude drafting and record the fallback in the Linear planning comment.

### PRD.md

**Testing Plan rules:** The Testing Plan is the most important section of the PRD. Every step must specify a concrete **method** tag so the implementing agent knows exactly HOW to verify it:

| Method tag | What the agent does | Records evidence? |
|------------|--------------------|--------------------|
| `cli` | Runs a command, checks exit code | No (pass/fail is the evidence) |
| `video` | Records demo via `record-demo-video`, embeds in Linear | Yes — MP4 embedded in issue |
| `video + frame-check` | Records + extracts frames via `record-animation-video-debug` | Yes — MP4 + frame analysis |
| `playwright + video` | Drives browser via Playwright MCP, records session | Yes — MP4 embedded in issue |
| `ios-simulator + video` | Drives iOS Sim via MCP, records session | Yes — MP4 embedded in issue |
| `screenshot` | Takes before/after screenshots, embeds in Linear | Yes — PNGs embedded in issue |
| `human` | Agent skips, flags for human reviewer | No — human checks manually |

**Mandatory video rule:** If the feature touches UI, animations, or user flows, the Testing Plan MUST include at least one `video` method step.

**E2E scenarios must be specific:** Don't write "verify it works." Write the exact steps: navigate where, interact with what, check for what observable outcome.

```markdown
# Feature: [Feature Name]

**Source:** [Linear ISSUE-ID | User Request]
**Created:** [timestamp]
**Status:** Draft

## Problem Statement
[What problem this solves and for whom]

## Requirements
- [ ] R1: [Specific, testable requirement]
- [ ] R2: [Specific, testable requirement]

## Acceptance Criteria
- [ ] AC1: [Specific, observable criterion]
- [ ] AC2: [Specific, observable criterion]

## Technical Approach
[Chosen approach and rationale]

### Files to Modify
- [file] — [what changes]

### Patterns to Follow
- [pattern from codebase]

### Dependencies
- [External service, library, or feature this depends on]

## Premortem
### Tigers
- [risk] → [mitigation]

### Elephants
- [concern] → [mitigation]

## Testing Plan

Every verification step MUST specify its **method**.

### Automated (agent runs directly)
- [ ] `pnpm typecheck` passes — **method:** cli
- [ ] `pnpm test` passes — **method:** cli
- [ ] `pnpm build` succeeds — **method:** cli

### Visual / Flow Verification (MANDATORY for UI/animation/flow changes)
- [ ] [Screen/flow description] — **method:** video
  - Steps: [Navigate to X → tap Y → verify Z appears]

### E2E Integration
- [ ] [E2E scenario] — **method:** playwright + video
  - Steps: [Navigate to URL → fill form → submit → verify response]
  - Expected: [specific observable outcome]
- [ ] [E2E scenario] — **method:** ios-simulator + video
  - Steps: [Launch app → tap X → swipe Y → verify Z]
  - Expected: [specific observable outcome]

### Human-Only Verification
- [ ] [Step description] — **method:** human
  - Why: [explain why this can't be automated]
  - Instructions: [what the human should do and check]
```

### task-list.md

```markdown
# Task List: [Feature Name]

## 1. Setup
- [ ] 1.1 [task]

## 2. Core Implementation
- [ ] 2.1 [task]

## 3. Testing & Verification
- [ ] 3.1 [task]

---
Progress: 0/X tasks complete
```

---

## Step 6: Review Loop (configured harness with opposite-harness fallback)

> **Skip this entire step if `IS_CODEX_REVIEW = false`.** Mark as completed with "skipped — no review" and jump to Step 7.

The skill sends the PRD and task-list to a reviewer, processes the critique, revises, and repeats. Max 3 rounds. The reviewer is controlled by `REVIEW_HARNESS`. Default is **Codex** with Claude fallback; flipped mode (`codex` / `codex-claude`) starts with **Claude** and falls back to Codex. If both fail, see 6.1c.

Internal flag set at the start of each round:
- `REVIEWER` — `codex` or `claude` from `REVIEW_HARNESS`. Reset per round.

### 6.1 Send to primary reviewer (with fallback detection)

When `REVIEW_HARNESS=codex`, construct a Bash command that:
1. Reads `PRD.md` and `task-list.md` into variables
2. Pipes a review prompt to `codex exec -m "$CODEX_MODEL" -` (stdin mode) — `$CODEX_MODEL` defaults to `gpt-5.4` and is overridable via `--codex-model=<id>`
3. Runs from the **project directory** (so Codex can read codebase files)
4. **Do not add an external timeout wrapper**; `codex exec` already has its own turn timeout
5. **Captures exit code AND scans stdout for `## Verdict:`** — if either fails, fall back to Claude-as-reviewer (Step 6.1b)

Wrap the invocation like this:

```bash
codex exec -m "$CODEX_MODEL" - <<'EOF' > codex_out.txt 2>&1
{review prompt}
EOF
EXIT=$?
if [ $EXIT -ne 0 ] || ! grep -q "^## Verdict:" codex_out.txt; then
  REVIEWER=claude   # fallback
  PRIMARY_REVIEW_FAILURE_REASON="codex exit=$EXIT, verdict_present=$(grep -q '^## Verdict:' codex_out.txt && echo yes || echo no)"
else
  REVIEWER=codex
fi
```

When `REVIEW_HARNESS=claude`, use Claude as the primary reviewer:

1. If `PLANNER_HARNESS=codex`, the current Claude session may review directly because Claude did not draft the plan.
2. If `PLANNER_HARNESS=claude`, spawn a fresh Claude sub-Agent with `model: $CLAUDE_REVIEW_MODEL`.
3. Use the same review prompt, checklist, and required `## Verdict:` output format.
4. If Claude fails or returns malformed output, set `PRIMARY_REVIEW_FAILURE_REASON` and fall back to Codex in Step 6.1b.

The review prompt must contain:
- The Linear issue ID
- The full PRD content
- The full task-list content
- The review history (rounds 2+ only — include ALL prior rounds)
- The review checklist and output format

**Review checklist (used by both reviewers):**
- Missing or vague acceptance criteria
- Tasks that don't trace back to a requirement
- Scope gaps or scope creep vs the issue
- Risky assumptions without mitigation
- Missing edge cases, error handling, migrations
- Optimistic task breakdown (hidden complexity)
- Dependency ordering issues in the task list
- Claims about codebase that don't match actual files
- **Testing Plan: every step must have a method tag**
- **Testing Plan: UI/animation/flow changes MUST have at least one `video` method step** — flag as CRITICAL if missing
- **Testing Plan: e2e steps must have specific steps** — not just "verify it works"
- **Testing Plan: `human` steps must justify why automation is impossible** — flag vague human steps as MAJOR

**Required output format (both reviewers):**
```
## Verdict: REVISE | APPROVED

### Findings
- [CRITICAL] ...
- [MAJOR] ...
- [MINOR] ...
- [NIT] ...

### Questions (if any)
- [ARCH] Large decisions affecting future features or code integrity
- [TECH] Small implementation clarifications
- [CODEX_QUESTION] Additional context needed
- [HARNESS_QUESTION] Additional context needed
```

**Error handling — interactive vs auto:**
- If `IS_AUTO = false` (interactive): on primary reviewer failure, **ask the user** whether to retry, skip, or use the opposite harness.
- If `IS_AUTO = true` (autonomous): on primary reviewer failure, **silently route to Step 6.1b** (opposite-harness fallback). No user prompt. The fallback path always runs to completion in autonomous mode.

### 6.1b Opposite-harness reviewer fallback

When the primary reviewer failed in 6.1, use the opposite harness once:

1. If the fallback is Claude, spawn a sub-Agent via the Agent tool with:
   - `subagent_type: "general-purpose"`
   - `model: $CLAUDE_REVIEW_MODEL` (default `claude-opus-4-7`, overridable via `--claude-review-model=<id>`)
   - `prompt`: the **same review prompt body** Codex would have received (issue ID, full PRD, full task-list, review history, checklist, required `## Verdict:` output format).
2. If the fallback is Codex, run the same `codex exec -m "$CODEX_MODEL" -` command shape from 6.1.
3. Parse the fallback response with the **same parser** as primary output (Step 6.2). Strip everything before `## Verdict:`. Extract Verdict + Findings + Questions exactly as for the primary reviewer.
4. **Treat malformed output as a failed review** — if the fallback doesn't return a `## Verdict:` block, route to Step 6.1c.

Why a fresh Claude sub-Agent when Claude is reviewing: cross-model or fresh-context diversity matters. A sub-Agent with the same input gets a fresh look and avoids the planner's confirmation bias. If `PLANNER_HARNESS=codex`, the current Claude session may review directly because Claude did not draft the plan.

### 6.1c Both reviewers fail (NEW — ISSUE-123)

When both reviewers failed in the same round:

1. Log a CRITICAL Linear comment naming both failures, the round attempted, and the primary review failure reason from 6.1. Example:
   ```
   ## Plan Review — Round {N}/3 — REVIEWERS FAILED

   - **Primary reviewer ({REVIEW_HARNESS})**: {PRIMARY_REVIEW_FAILURE_REASON}
   - **Fallback reviewer**: {fallback error or "malformed output"}

   Plan published as-is with `risk:high` + `needs-review` for human review.
   ```
2. Set internal flags:
   - `risk_assignment_decision = "high"` (locks Step 7.1 to `risk:high`)
   - `apply_needs_review_label = true` (locks Step 7.x to apply `needs-review`)
3. **Do NOT abort.** Continue to Steps 7.6–7.7: still publish documents, still update issue description, still move issue to Ready. Better to ship a flagged PRD than to leave the issue stranded mid-flow.

### 6.2 Parse Reviewer Output

Strip everything before `## Verdict:`. Extract:

1. **Verdict** — `REVISE` or `APPROVED`
2. **Findings** — with severity tags
3. **Questions** — with routing tags `[ARCH]`, `[TECH]`, `[CODEX_QUESTION]`

### 6.3 Route Questions

**`[TECH]`:** Claude answers from research context. Escalate to user if uncertain.

**`[ARCH]`:** If `IS_AUTO = false`, ask user. If `IS_AUTO = true`, Claude decides and logs as assumption.

**`[CODEX_QUESTION]` / `[HARNESS_QUESTION]`:** The opposite harness answers from research context. Escalate if uncertain and user is available.

### 6.4 Revise PRD and Task-List

- **CRITICAL/MAJOR:** Must be addressed.
- **MINOR/NIT:** Address if straightforward.

If `PLANNER_HARNESS=codex`, send the findings, routed question answers, current artifacts, and full review history back through `codex exec -m "$CODEX_MODEL" -`; require corrected `PRD.md` and `task-list.md` fenced blocks before overwriting local files. If Codex returns malformed revision artifacts, Claude repairs the smallest necessary issue and records that repair in the review history.

### 6.5 Post Linear Comment

One comment per round with findings, questions, and planner response.

**Reviewer attribution:** Include reviewer identity in the round title whenever the reviewer is not the default Codex primary, or whenever fallback was used.

Example titles:
- `## Plan Review — Round 1/3` (Codex review, default)
- `## Plan Review — Round 1/3 — Reviewer: Claude (Codex unavailable)` (fallback)
- `## Plan Review — Round 1/3 — Reviewer: Claude (flipped harness)` (`REVIEW_HARNESS=claude`)
- `## Plan Review — Round 1/3 — Reviewer: Codex (Claude unavailable)` (Codex fallback under flipped mode)

### 6.6 Check Exit Conditions

1. `APPROVED` → done
2. `REVISE` but no CRITICAL/MAJOR → approved
3. Round 3 → hard cap, approved with caveats
4. Repeated findings → dissent, approved

### 6.7 Review History

Include ALL prior rounds in subsequent reviewer prompts.

---

## Step 7: Finalize Plan + Move to Ready

### 7.1 Assign risk label

**`risk:low`** when ALL true: no human steps, no critical risks, well-scoped, no migrations/auth/breaking changes, additive only.

**`risk:high`** when ANY true: human steps, critical risks, auth/payments/data, breaking changes, migrations, multi-package, subjective judgment.

**Default:** `risk:high`.

**ISSUE-123 forcing rules** (apply BEFORE the criteria above):

1. **Internal flag override** — if `risk_assignment_decision == "high"` was set in Step 6.1c (both reviewers failed), force `risk:high` regardless of other criteria.

2. **Auto-interview uncertainty** — when `IS_AUTO = true`, scan all auto-interview answers (Step 3) for lines starting with `PRODUCT_DECISION:` or `UNCERTAIN:`. If ≥1 such line exists, force `risk:high`. Rationale: auto-interview ambiguity means a human checkpoint is needed; routing to In Review (via `risk:high`) recovers that checkpoint downstream.

3. **`needs-review` label** — if `apply_needs_review_label == true` was set in Step 6.1c, also apply the `needs-review` issue label. (This label is validated to exist in the workspace by autosymph.diagnostics on startup — see ISSUE-123 R12.)

### 7.2 Update PRD status

- Reviewed: `Approved` or `Approved with Caveats`
- No Codex review: `Planning Complete`

### 7.3 Present Change Summary

```markdown
## Plan Summary: {Feature Name}

### What Will Change
| File / Area | Change | Why |

### How We'll Verify
| Step | Method | What It Proves |

### Premortem Risks
- {risk} → {mitigation}

### Task Breakdown
{X} tasks across {Y} phases.
```

**If `IS_AUTO = true`:** Display but do not wait for input.

### 7.4 Edit Window

> **Skip if `IS_AUTO = true`.** Auto-approve.

```
1. Approve — publish as-is
2. Edit — tell me what to change
3. Reject — move back to Todo
```

10 min timeout → auto-approve.

### 7.5 Timeout / Session-Close Handling

Publish to Linear and move to Ready BEFORE the edit window. Edit window is a post-publish correction opportunity.

### 7.6 Publish to Linear

Create (or update) two documents linked to the issue:
- `PRD: {Feature Name}`
- `Task List: {Feature Name}`

### 7.6b Update issue description with Testing Plan

**CRITICAL:** Update the issue description to include the Testing Plan so downstream agents (implement, verify) can find it without reading separate documents. Use `mcp__linear__save_issue` with the description set to:

```markdown
{original issue description}

## Testing Plan

{copy the full Testing Plan section from the PRD — every item with method tags}
```

This ensures the Testing Plan is discoverable in three places: the issue description, the PRD document, and (after implement) the PR body.

### 7.7 Move issue to Ready

### 7.8 Post final comment

```markdown
## Plan Complete

**Mode:** interview={user|auto}:{QUESTION_HARNESS} / planner={PLANNER_HARNESS} / review={REVIEW_HARNESS|none}
**Result:** {status}

**Documents:** PRD + Task List published.
Issue moved to Ready.
```

### 7.9 Keep local files

Leave `PRD.md` and `task-list.md` for `/ralph`. Do NOT commit.

---

## Execution

**Before doing ANY work**, create tasks:

```
TaskCreate: "Step 0: Parse flags"
TaskCreate: "Step 1: Gather context + move to Planning"
TaskCreate: "Step 2: Research"
TaskCreate: "Step 3: Interview"
TaskCreate: "Step 6: Review loop"
TaskCreate: "Step 4: Premortem"
TaskCreate: "Step 5: Draft PRD + task-list"
TaskCreate: "Step 7: Finalize + publish"
```

Now execute. EXECUTE THESE STEPS NOW.
