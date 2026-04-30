# Implement State

## What

The implement agent writes code to satisfy a Linear issue's requirements and opens a PR with a testable plan. It's the first agent state in the pipeline — issues enter here from "Ready" in Linear.

## Why

The implement agent is the workhorse. It reads the issue, explores the codebase, writes code, runs tests, and produces a PR that the verify agent can mechanically validate. The separation between implement and verify exists because self-verification is unreliable — the agent that wrote the code is biased toward believing it works.

The PR's test plan is the contract between implement and verify. If the test plan is vague ("settings works"), verification is impossible. If it's specific and observable ("Settings page loads with user name displayed"), the verify agent can prove it independently.

## How it works

```
Ready (Linear) → implement agent runs
  |
  |-- reads issue description + acceptance criteria + task list
  |-- explores existing code in the affected area
  |-- implements each task, committing after each logical unit
  |-- runs the project's test suite
  |-- opens a PR with summary + test plan
  |
  |-- complete      → verify
  |-- complete_low_risk → verify
  |-- fail          → investigating
```

## Design decisions

### Test plan is written for the verify agent, not humans

Each test plan item must be specific, observable, platform-tagged (`[iOS]`, `[web]`, `[CLI]`), and automatable. The implement agent writes it for an agent that has never seen the code. This is the handoff mechanism — the verify agent consumes the test plan as its checklist.

### No self-verification

The implement agent does NOT take screenshots, record video, or verify its own work beyond running the test suite. This prevents the "I wrote it and it looks fine" bias. Verification is the verify agent's job, with a fresh session and read-only-ish permissions.

### Commits per logical unit

The agent commits after each logical unit of work, not one giant commit at the end. This makes rework easier — if review requests changes or investigating routes a target-repo failure back to implement, the git history shows what was done.

### PR format is prescribed

Title: `{issue identifier}: {concise description}`
Body: `## Summary` (what and why) + `## Test Plan` (checkboxes with platform tags)

This format is consumed by verify, review, and finalize agents downstream.

## Config

```yaml
implement:
  type: agent
  prompt: prompts/implement.md
  linear_state: active
  model: sonnet
  max_turns: 40
  session: inherit
  transitions:
    complete: verify
    complete_low_risk: verify
    fail: investigating
```

- **40 turns** — the highest budget of any state. Implementation is open-ended.
- **session: inherit** — on rework, resumes the prior session so the agent has context from previous attempts.
- **model** — choose a cost-effective implementation model. With the default
  Claude runner, `sonnet` is the floating alias. Other runners should use their
  own model ids and set `runner:` explicitly when needed.

## Connections

- **verify** — receives the PR and test plan from implement
- **rework** — uses the same prompt as implement, runs when review rejects
- **investigating** — receives failures when implement crashes or stalls
