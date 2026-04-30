# Finalize State

## What

The finalize agent squash-merges the PR, links the Linear issue, and moves the issue to Done. It's the last agent state in the pipeline.

## Why

Merge is mechanical but has steps that are easy to mess up — ensuring the PR description is complete, addressing last-minute review comments, squash-merging (not regular merge), linking the issue. Automating this ensures consistency and prevents issues from getting stuck in "approved but never merged" limbo.

## How it works

```
review approves (or verify completes risk:low) → finalize agent runs
  |
  |-- ensures PR description is complete (summary + test plan)
  |-- addresses any outstanding review comments
  |-- squash-merges the PR
  |-- links Linear issue to the merged PR
  |-- moves issue to Done
  |
  |-- complete → done (terminal)
  |-- fail     → investigating
```

## Design decisions

### Short turn budget

15 turns. Merge is a checklist, not creative work. If it can't merge in 15 turns, something is blocking (merge conflicts, CI failures, permissions) and the investigating agent should diagnose it.

### Model selection

Finalize should use a balanced, low-latency model. With the default Claude
runner, `sonnet` is the floating alias. Other runners should use their own model
ids and set `runner:` explicitly when needed.

### Session inherit

The finalize agent resumes the session from implement/verify. It has context on what was built and why, which helps when addressing last-minute review comments or writing a final PR description.

### Squash-merge, not regular merge

The prompt prescribes squash-merge. This keeps the main branch history clean — one commit per issue instead of a chain of "implement task 1," "fix lint," "address review" commits.

### Fail → investigating

Previously, finalize had no fail transition (dropped silently). Now failures route to investigating. Common finalize failures: merge conflicts with main, CI checks failing, permissions issues. All diagnosable.

## Config

```yaml
finalize:
  type: agent
  prompt: prompts/merge.md
  linear_state: gate_approved
  model: sonnet
  max_turns: 15
  session: inherit
  transitions:
    complete: done
    fail: investigating
```

## Connections

- **review** — dispatches finalize when the reviewer approves
- **verify** — dispatches finalize directly for risk:low issues
- **done** — terminal state, issue is complete. No agent runs — the state machine untracks the issue and the orchestrator records it as completed. Terminal states (`done`, `canceled`, `duplicate`) do not have standalone docs; they are defined in workflow.yaml under `linear_states.terminal`.
- **investigating** — receives failures when merge can't complete
