# Investigating State

## What

When any agent state fails (implement, verify, rework, or finalize), autosymph transitions the issue to `investigating` instead of retrying blindly. A stronger reasoning model resumes the failed session and debugs the root cause. It classifies the failure, gathers targeted evidence, and either fixes the problem, applies a workaround, or escalates to a human.

## Why

A real example that motivated this state: a single issue ran 17 identical verify failures over 25 minutes because the resource pool didn't acquire an iOS simulator (label mismatch — auto-detection wasn't implemented). The agent had no instructions for what to do when infrastructure breaks, so it flailed and timed out, 17 times. The NDJSON traces showed the exact same tool calls every run. Zero learning between retries.

The investigating state exists to make the system debug itself the way a human would: read the error, figure out why, fix the root cause, test it, then retry.

## How it works

```
any agent state fails (implement, verify, rework, finalize)
  -> investigating (strong reasoning model, 15 turns, session: inherit)
       |
       |-- reads failure context from the inherited session
       |-- classifies: orchestrator bug / target repo / environment / flaky / insufficient visibility
       |-- gathers targeted evidence based on classification
       |-- writes .autosymph-result.json with outcome
       |
       |-- fixed              -> retry the state that failed (dynamic routing)
       |-- workaround_applied -> retry the state that failed (durable fix tracked in next_action)
       |-- not_autosymph      -> back to implement (verify failures only — code needs rework)
       |-- escalate           -> blocked gate (human intervenes)
       |-- fail               -> blocked gate
```

## Design decisions

### Classify first, then investigate

The prompt requires the agent to categorize the failure before gathering evidence. This prevents wasting turns — checking the resource pool source code is useless when the failure is a TypeScript test error. Classification drives which evidence to gather.

Categories:
- **orchestrator bug** — resource pool, runner, state machine, prompt issue
- **target repo bug** — tests fail, code is wrong, build broken
- **environment/resource** — sim not booted, port taken, credentials missing
- **infra_gap (structify)** — missing skill, prompt gap, config gap. Agent improvised a pipeline that should be standardized. Triggers the structify protocol: compare working vs failing runs → write skill with test → update prompts/config → postmortem.
- **flaky/transient** — API timeout, network error, stall
- **insufficient visibility** — can't determine cause from available logs

### Four outcome signals, not two

`fixed` and `escalate` aren't enough:

- **workaround_applied** — the immediate problem is unblocked (e.g., sim booted manually) but the root cause persists (e.g., resource pool label matching is still broken). The orchestrator retries verify, but the `next_action` field documents what still needs fixing. Without this distinction, workarounds mask unfixed bugs.

- **not_autosymph** — the failure is in the target repo's code or tests, not in orchestrator infrastructure. This routes back to `implement` for rework instead of more investigation. Without this, the investigating agent would try to "fix" a legitimate test failure by patching infrastructure.

### Session resume, not a fresh agent

The investigating agent resumes the failed verify session (`session: inherit`). This gives it:
- Full conversation context (every file read, every command run, the exact error)
- The worktree in exactly the state the failed agent left it
- Prompt cache warmth (cheaper)

A fresh agent would need to re-read the PR, re-find the code, re-discover the failure — repeating 80% of the work just to reach the same failure point.

### Parameterized prompt

The orchestrator templates variables into the prompt at dispatch time:
- `{target_worktree}` — the worktree path
- `{autosymph_root}` — path to autosymph source
- `{failed_state}` — which state failed (e.g., "verify")
- `{orchestrator_log_path}` — path to orchestrator.log
- `{result_file}` — where to write the result JSON
- `{linear_issue_id}` — the issue identifier

This avoids hardcoding paths that vary across machines and makes the prompt reusable.

### Structured result file

The agent writes `.autosymph-result.json` in the workspace root:

```json
{
  "signal": "fixed | workaround_applied | escalate | not_autosymph",
  "summary": "one-line description",
  "root_cause": "specific cause identified",
  "changes": ["list of files changed and why"],
  "next_action": "what should happen next"
}
```

The orchestrator reads `signal` for routing. The rest is for humans and for the Linear comment. `next_action` is particularly important for `workaround_applied` — it documents what the durable fix should be.

### Commit guardrails

The investigating agent can modify autosymph source code (it has access to `{autosymph_root}`), but with rules:
- Only commit when the root cause is in autosymph
- Never commit target repo changes (that's the implement agent's job)
- Always branch, never main
- If the fix requires an orchestrator restart, escalate — the agent can't restart itself

### Stop conditions

Once the agent writes the result file, it stops. No more debugging after escalation. This prevents the agent from burning turns after it's already decided it needs help.

### Signal mapping

The result file's `signal` field maps to workflow.yaml transition keys via the orchestrator's `_determine_signal()`:

| Result file signal | State machine signal | Workflow transition key | Target |
|---|---|---|---|
| `fixed` | `Signal.COMPLETE` | `complete` | Dynamic: return to failed state |
| `workaround_applied` | `Signal.WORKAROUND` | `workaround` | Dynamic: return to failed state |
| `not_autosymph` | `Signal.NOT_AUTOSYMPH` | `not_autosymph` | Static: `implement` |
| `escalate` | `Signal.ESCALATE` | `escalate` | Static: `blocked` |

### Dynamic routing back to the failed state

The investigating state's `complete` and `workaround` transitions are statically defined as `verify` in workflow.yaml, but the orchestrator overrides them dynamically. It tracks which state failed in `_last_failed_state` and routes `fixed`/`workaround_applied` back to that state:

- implement fails → investigating → fixed → retry implement
- verify fails → investigating → fixed → retry verify
- finalize fails → investigating → fixed → retry finalize

The `not_autosymph` signal is only valid for verify failures (the code is wrong, not the infra). The orchestrator enforces this: if an investigating agent writes `not_autosymph` for a non-verify failure, the orchestrator overrides it to `escalate`. This prevents misclassification from routing a finalize failure back to implement.

## Config

```yaml
investigating:
  type: agent
  prompt: prompts/investigating.md
  linear_state: investigating
  model: opus
  max_turns: 15
  session: inherit
  permission_mode: acceptEdits
  transitions:
    complete: verify           # fixed → retry failed state (orchestrator overrides dynamically)
    workaround: verify         # workaround applied → retry failed state (durable fix still needed)
    not_autosymph: implement   # target repo issue → rework the code (verify failures only)
    escalate: blocked          # needs human
    fail: blocked              # investigation itself failed
```

- **model** — debugging requires stronger reasoning than implementation or
  verification. With the default Claude runner, `opus` is the floating alias.
  Other runners should use their own high-reasoning model ids and set `runner:`
  explicitly when needed.
- **15 turns** — enough for classify + investigate + fix. Escalate if it's not enough.
- **session: inherit** — resumes the failed session for full context.
- **permission_mode: acceptEdits** — needs to modify autosymph source code for fixes.

## Connections

- **blocked gate** — where escalated issues land. Human approves to retry. Will fire notifications once notification channels are built.
- **all agent states** — implement, verify, rework, and finalize all have `fail: investigating`.
- **implement state** — receives `not_autosymph` issues that need code rework, not infrastructure debugging.

## Structify (infra_gap classification)

When the investigating agent classifies a failure as `infra_gap`, it runs the
**structify protocol** instead of the standard debug-and-fix flow.

### What triggers structify

The `reject_structify` decision from verify_review, which fires on **first
occurrence** (not after 3+ retries). Detects:
- Permission denied on tools the agent needs
- Screenshot quality below threshold
- Upload corruption from pipeline issues
- Agent improvising a multi-step pipeline that a skill should standardize

### What structify does

1. **Compare** — find a successful run on another issue, diff the two ndjson logs
2. **Root cause** — identify the specific gap (missing tool, missing prompt instruction, missing skill)
3. **Write skill + test** — deterministic test that proves the pipeline works
4. **Harden** — update prompts and config to prevent recurrence
5. **Postmortem** — document in `autosymph/docs/postmortems/`
6. **Signal `fixed`** — retry verify with the skill/config now in place

### Why first-failure detection matters

A real example: a single issue burned 6 verify runs (~56 min, ~85K output tokens)
before the screenshot pipeline was diagnosed. The infra gap was detectable on
run 1: `sips` was permission-denied, screenshots were resized to 138x300. The
verify_review agent saw this but could only REJECT back to verify, which
retried the same broken pipeline. Structify short-circuits this: one rejection,
one investigation, one fix, then retry.

### Orchestrator routing

verify_review `reject_structify` → investigating (not verify)

Requires orchestrator.py to recognize `reject_structify` as a decision value and
route to `investigating` instead of the default `verify` retry. The investigating
agent's session inherits from verify_review (for access to the failure context).

## Files

| File | Role |
|------|------|
| `prompts/investigating.md` | Agent prompt with protocol, inputs, outcome contract, structify protocol |
| `prompts/verify-review.md` | Evidence quality gate — detects `reject_structify` triggers |
| `workflow.yaml` | State definition, transitions, model/turn config |
| `src/autosymph/orchestrator.py` | Result file reader, signal routing, template substitution |
| `src/autosymph/state_machine.py` | `ESCALATE`, `WORKAROUND`, `NOT_AUTOSYMPH` signals |
| `src/autosymph/config.py` | `investigating` and `blocked` in LinearStatesConfig |
