# Verify State

## What

The verify agent proves the implementation works by executing the PR's test plan and capturing evidence (screenshots, video, pass/fail output). It does not write code — it validates what the implement agent produced.

## Why

Self-verification doesn't work. The agent that wrote the code is biased toward believing it works. The verify agent runs in a fresh session with no memory of the implementation, reads the test plan like an external tester, and produces evidence that a human reviewer can check.

The verify stage also catches infrastructure problems — wrong build flags, missing simulators, broken preview URLs — before the issue reaches human review. When these problems are infra (not code), the investigating state handles them.

## How it works

```
implement completes → verify agent runs
  |
  |-- Phase 1: reads PR description, finds Test Plan section
  |-- Phase 2: loads project instructions (auth.md, ios.md, web.md, fixtures.md)
  |-- Phase 3: classifies each test item (platform + evidence type)
  |-- Phase 4: authenticates (mandatory — auth.md or fail)
  |-- Phase 5: executes verification per platform
  |-- Phase 6: uploads evidence to Linear
  |-- Phase 7: routes based on results
  |
  |-- complete        → review (human gate)
  |-- complete_low_risk → finalize (skip review for trivial changes)
  |-- fail            → investigating
```

## Design decisions

### Fresh session, not inherited

`session: new` — the verify agent starts with zero context from the implement run. This is intentional. It forces the agent to rely on the PR and test plan as the sole source of truth, simulating an independent tester.

### 7-phase protocol

The verify prompt is the most structured of any state (166 lines). This is because verification is mechanical and error-prone — the agent needs explicit instructions for building iOS apps, interacting with simulators, using Playwright for web, and handling auth. Loose instructions lead to agents opening Safari, typing URLs, or skipping auth.

### Hard rules are non-negotiable

Nine rules override everything else:
1. Never open Safari or type URLs in browser
2. iOS: xcodebuild + install_app + launch_app + ui_tap with accessibility labels
3. Authentication is mandatory (Phase 4)
4. Use fixtures when available
5. Do not burn realtime/message quota for cosmetic checks
6. Minor fixes only — no structural changes
7. Human-only items are skipped, not faked
8. Manage large context safely
9. Finalize through `verify-finalize.sh`

These exist because early verify runs violated all of them. The rules are defensive.

### Risk-based routing

Issues labeled `risk:low` skip human review entirely (`complete_low_risk → finalize`). This is for trivial changes — string updates, config tweaks — where human review adds delay without value.

### Fail → investigating, not implement

Previously, verify failures routed back to implement (blind retry). Now they route to investigating, which diagnoses whether the failure is infrastructure (sim not booted), code (tests actually fail), or environment (credentials missing). This prevents the IMP-318 pattern of 17 identical retries.

### Evidence is required

The verify agent uploads screenshots and video to Linear for each visual test item. This creates an audit trail — the human reviewer can see what was actually tested, not just "PASS."

## Config

```yaml
verify:
  type: agent
  prompt: prompts/verify.md
  linear_state: verifying
  model: sonnet
  max_turns: 20
  session: new
  transitions:
    complete: review
    complete_low_risk: finalize
    fail: investigating
```

- **20 turns** — verification is bounded. If it can't verify in 20 turns, something is wrong.
- **session: new** — fresh context, no implementation bias.
- **model** — usually the same balanced model as implementation. With the
  default Claude runner, `sonnet` is the floating alias. Other runners should
  use their own model ids and set `runner:` explicitly when needed.
- **Resources** — the orchestrator acquires iOS simulators and dev ports from the resource pool before dispatch, passes them as env vars.

## Connections

- **implement** — produces the PR and test plan that verify consumes
- **investigating** — receives failures when verify can't complete (infra, env, config)
- **review** — receives successful verifications for human approval
- **finalize** — receives risk:low verifications directly (skip review)
