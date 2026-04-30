# Rework State

## What

The rework agent addresses review feedback and re-submits for verification. It uses the same prompt as implement but with a shorter turn budget, and it inherits the session from the previous implement/rework run.

## Why

When a human reviewer rejects a PR, the feedback is specific — "this should use a different API," "edge case X isn't handled," "the test plan is missing Y." The rework agent reads this feedback, makes targeted changes, and re-enters the verify → review loop.

Rework is separate from implement because the context is different. Implement starts from scratch with an issue description. Rework starts with an existing PR, review comments, and a session history of what was already tried.

## How it works

```
review rejects → rework agent runs (session: inherit)
  |
  |-- reads review comments on the PR
  |-- makes targeted changes to address feedback
  |-- updates the PR
  |
  |-- complete → verify (must re-verify after changes)
  |-- fail     → investigating
```

## Design decisions

### Same prompt as implement

Rework uses `prompts/implement.md`. The agent doesn't need different instructions — the review comments provide the "what to change" context. The global prompt's rule 5 ("if stuck after 3 attempts, report the blocker") applies here too.

### Session inherit

The rework agent resumes the session from the previous implement or rework run. This gives it full context — what was already tried, what the reviewer said, what the codebase looks like. Starting fresh would waste turns re-discovering the codebase.

### Must re-verify

Rework always routes to verify, not directly to review. Any code change, no matter how small, must be re-proven. This prevents "I just fixed a typo" changes from introducing regressions.

### Rework cap (3 cycles)

After 3 rework cycles (tracked by the state machine), the issue returns to "Ready." This prevents infinite loops where the agent can't satisfy the reviewer. The counter resets when an issue reaches "Done."

### Shorter turn budget

20 turns instead of implement's 40. Rework changes should be targeted, not open-ended. If the agent can't address review feedback in 20 turns, the feedback might need re-scoping.

### Model selection

Rework usually uses the same model class as implementation. With the default
Claude runner, `sonnet` is the floating alias. Other runners should use their
own model ids and set `runner:` explicitly when needed.

## Config

```yaml
rework:
  type: agent
  prompt: prompts/implement.md
  linear_state: rework
  model: sonnet
  max_turns: 20
  session: inherit
  transitions:
    complete: verify
    fail: investigating
```

## Connections

- **review** — dispatches rework when the reviewer rejects
- **verify** — receives reworked PRs for re-verification
- **investigating** — receives failures when rework crashes or stalls
