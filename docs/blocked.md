# Blocked State

## What

Blocked is a human gate. The investigating agent determined it can't fix the problem and escalated. The issue sits in "Blocked" on the Linear board until a human resolves it.

## Why

Some failures can't be fixed by an agent: missing credentials, unclear requirements, orchestrator bugs that need a restart, or problems the investigating agent couldn't diagnose in 15 turns. Blocked is the explicit acknowledgment that automation has reached its limit and a human needs to intervene.

Without this state, unresolvable issues would either loop forever or get silently dropped. Blocked makes them visible.

## How it works

```
investigating escalates (or fails) → issue moves to "Blocked" in Linear
  |
  |-- Linear comment from investigating agent explains:
  |   what was tried, what the root cause is, what's needed
  |
  |-- .autosymph-result.json contains structured diagnosis:
  |   signal, summary, root_cause, changes, next_action
  |
  |-- human reads diagnosis, fixes the problem
  |-- human moves issue to approve
  |
  |-- approve → retry the state that originally failed (dynamic routing)
```

## Design decisions

### Gate, not agent

`type: gate` — no agent runs. The orchestrator polls Linear for the status change.

### Dynamic routing on approve

The `approve` transition statically targets `verify` in workflow.yaml, but the orchestrator overrides this dynamically. It routes back to `_last_failed_state` — the state that originally failed before investigating. If implement failed → investigating → blocked → approve, the issue goes back to implement, not verify.

### Tied to notifications

Blocked is only useful if you know about it. A future change will add notification channels (macOS notifications, Linear @-mentions, Slack) that fire when an issue transitions to blocked. Without notifications, blocked is just a quieter version of being dropped.

## Config

```yaml
blocked:
  type: gate
  linear_state: blocked
  transitions:
    approve: verify  # default — orchestrator overrides dynamically
```

## Connections

- **investigating** — dispatches to blocked when escalating or failing
- **all agent states** — indirectly, any state can reach blocked via investigating
- **notifications (planned)** — notification channels will fire on transition to blocked
