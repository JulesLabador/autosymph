# Review State

## What

Review is a human gate. No agent runs. The issue sits in "In Review" on the Linear board until a human approves or requests rework.

## Why

Not everything should be fully autonomous. The review gate exists for issues that aren't `risk:low` — any change with meaningful complexity deserves human eyes before merge. The verify agent proved the code works mechanically; the reviewer checks whether it's the right approach, whether it introduces tech debt, and whether edge cases were considered.

## How it works

```
verify completes → issue moves to "In Review" in Linear
  |
  |-- human reviews PR + verification evidence
  |
  |-- approve   → finalize (normal transition)
  |-- reject    → rework (gate-specific rework_to mechanism, max 3 cycles)
```

Note: rejection is not a normal transition in workflow.yaml. It's handled by the gate-specific `rework_to` field, which the state machine evaluates separately from the `transitions` map. The YAML only lists `approve: finalize` under transitions; rework routing comes from `rework_to: rework`.

## Design decisions

### Gate, not agent

`type: gate` — the orchestrator does not dispatch an agent. It polls Linear for status changes. When the human moves the issue to "Merging" (approve) or "Rework" (reject), the state machine picks it up on the next tick.

### Rework cap

`max_rework: 3` — after 3 rework cycles, the issue returns to "Ready" (`rework_exhausted: todo`). This prevents infinite loops where the agent can't satisfy the reviewer. At that point, the issue needs re-planning or a human implementing it.

### Risk:low skips this entirely

Issues with the `risk:low` label go directly from verify to finalize. The review gate is only for changes where human judgment adds value.

## Config

```yaml
review:
  type: gate
  linear_state: review
  rework_to: rework
  max_rework: 3
  rework_exhausted: todo
  transitions:
    approve: finalize
```

## Connections

- **verify** — produces the evidence and verification summary that the reviewer examines
- **rework** — receives rejected issues for another implementation attempt
- **finalize** — receives approved issues for merge
