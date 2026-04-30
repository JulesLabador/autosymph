# Changelog

## Unreleased — Verify review routing and realtime-safe fixtures

### Changed

- **Verify review gate** — `approve with caveats` is now reserved for true
  human-only or unreasonable-method-tag caveats. Missing implementation,
  required deploy/migration gaps, auth/backend outages, and missing visual
  uploads are now explicit reject or structify cases instead of moving issues
  into Review as if they were landable.
- **Verify prompt** — visual verification now prefers documented fixture/demo
  modes for screenshot, layout, copy, accessibility, and visual-regression
  checks. Live realtime/message delivery should only be used when the test item
  explicitly verifies the relay path.
- **Verify templates** — auth and fixture templates now include a
  realtime/message-quota safety section so projects can document no-realtime
  signed-in fixture modes before verifier agents run.

### Context

Several distinct failure modes were being collapsed into "review" or "blocked":
missing implementation, required human-device gates, auth/backend outages,
missing sibling deploys, and visual evidence upload gaps. The policy now keeps
actual merge blockers out of Review while still allowing explicit human-only
gates to reach a human.

## Unreleased — Linear run comments include digest + failure modes

### Changed

- **State comments** — completion comments now keep the NDJSON attachment link
  near the top, then render a concise `Activity summary` section instead of a
  numbered replay of summarized log lines.
- **Failure modes** — comments now include a `Failure modes` section populated
  from agent errors, failed tool results, and non-zero run outcomes. Successful
  runs explicitly say when no failures were captured.

## Unreleased — `terminal_statuses=[]` disables blockedBy filter

### Fixed

- **Empty-list deadlock** — `fetch_actionable_issues` now treats
  `terminal_statuses=[]` identically to `None` (filter disabled). Previously an
  empty list activated the filter with an empty terminal set, which would have
  caused every blocked issue to be skipped permanently if a user shipped
  `linear_states.terminal: []` in their YAML. Default config still ships
  `["Done", "Canceled", "Duplicate"]`, so production behavior is unchanged.

### Added

- **`test_empty_terminal_statuses_disables_filter`** — regression test locking
  in the new behavior.

## Unreleased — Respect Linear blockedBy relations

### Added

- **`IssueRef` dataclass** — lightweight `(identifier, state)` pair used to
  represent blocker references.
- **`LinearIssue.blocked_by`** — list of `IssueRef` populated from Linear's
  `inverseRelations` (type = "blocks").
- **`fetch_actionable_issues(..., terminal_statuses=...)`** — when supplied,
  filters out any issue whose blockers are not all in a terminal state
  (`Done`, `Canceled`, `Duplicate` by default). Each skip is logged with the
  blocker identifier and current state for telemetry. Passing `None` disables
  the filter (backwards-compat).
- **Self-block guard** — if Linear returns an issue blocking itself (data
  anomaly), log a warning and ignore the edge instead of stalling.

### Changed

- **`Orchestrator.poll_tick`** — passes `self.config.linear_states.terminal` to
  `fetch_actionable_issues`, so the actionable set respects `blockedBy` chains.

### Context

Without this filter, autosymph dispatched chained issues in parallel even
though Linear marked them as blocked, burning concurrent agents on dependent
work that couldn't possibly succeed. The fix is purely a fetch-time filter — no
state-machine changes.

## Unreleased — Investigating state + blocked gate

### Added

- **Investigating state** — when any agent state fails (implement, verify,
  rework, finalize), autosymph now routes to a debugging agent instead of
  retrying blindly. The agent classifies the failure, gathers targeted
  evidence, and either fixes the root cause, applies a workaround, or
  escalates to a human.
- **Blocked gate** — human escalation point. Issues land here when the
  investigating agent can't self-fix. Human approves to retry the failed state.
- **3 new signals** — `ESCALATE`, `WORKAROUND`, `NOT_AUTOSYMPH` added to the
  state machine.
- **Result file contract** — investigating agent writes
  `.autosymph-result.json` with structured diagnosis (`signal`, `summary`,
  `root_cause`, `changes`, `next_action`). Orchestrator reads it for routing.
- **Dynamic routing** — `fixed`/`workaround_applied` route back to whichever
  state originally failed, not a hardcoded target.
- **`not_autosymph` guard** — orchestrator enforces that `not_autosymph`
  signal is only valid from verify failures. Non-verify uses are overridden to
  `escalate`.
- **Prompt template variables** — investigating prompt receives
  `{target_worktree}`, `{autosymph_root}`, `{failed_state}`,
  `{orchestrator_log_path}`, `{result_file}`, `{linear_issue_id}` from the
  orchestrator at dispatch time.
- **`prompts/investigating.md`** — classify-first debugging protocol with 4
  outcome signals, commit guardrails, and stop conditions.
- **`docs/`** — What/Why/How/Design decisions/Config/Connections documentation
  for all 7 states: implement, verify, investigating, review, rework,
  finalize, blocked.
- **Linear statuses** — projects need to add "Investigating" and "Blocked"
  states to their team workflow when adopting these features.

### Changed

- **verify `fail` transition** — now routes to `investigating` instead of
  `implement`.
- **implement, rework, finalize** — all gained `fail: investigating`
  transitions (previously had no fail handler).
- **`linear_states`** — added `verifying`, `investigating`, `blocked` fields
  (verifying was previously implicit via code default).

### Context

Without the investigating state, autosymph could retry an identical failing
verify dozens of times if the underlying infrastructure issue (e.g. missing
simulator, label mismatch, broken auth fixture) was never diagnosed. The
investigating state prevents this pattern: diagnose once, fix or escalate,
instead of retrying blindly.
