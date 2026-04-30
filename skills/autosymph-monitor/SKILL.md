---
name: autosymph-monitor
description: "Autosymph monitor cockpit — observe, diagnose, and heal agent loops. Attaches to a running autosymph instance via the status API, detects stalls/churn/crashes, self-heals via structify/investigation, and mutates Linear. Use when: user says /autosymph-monitor, 'monitor autosymph', 'babysit', or 'watch the agents'."
---

# Autosymph Monitor Agent

Observe. Diagnose. Heal. This is the operator cockpit for autosymph.

You attach to a running autosymph instance, poll for status, detect bad loops,
fix them (structify, investigate), and put issues back into the pipeline.

---

## Step 0: Load Config

Read the user's autosymph configuration to understand what you're monitoring.

### Find configs

```bash
# Project configs
ls "${AUTOSYMPH_CONFIG_DIR:-$HOME/.autosymph/config}"/projects/*.yaml 2>/dev/null

# Device config
ls "${AUTOSYMPH_CONFIG_DIR:-$HOME/.autosymph/config}"/devices/*.yaml 2>/dev/null

# Legacy single-file config
ls "${AUTOSYMPH_CONFIG_DIR:-$HOME/.autosymph/config}"/*.yaml 2>/dev/null | grep -v _template
```

### Parse and remember

For each project config, extract:
- **Project name** (tracker.project)
- **State definitions** (states.{name}.type, .prompt, .model, .max_turns, .allowed_tools)
- **Transitions** (states.{name}.transitions)
- **Polling interval** (polling.interval_ms)
- **Stall timeout** (claude.stall_timeout_ms)
- **Max concurrent agents** (agent.max_concurrent_agents)
- **Log root** (logging.log_root, default ~/.autosymph/logs)

From device config:
- **Simulators** (resources.ios_simulator)
- **Dev ports** (resources.dev_port_range)

Announce what you found:
```
Loaded 3 project configs: ios-app, web-app, api-service
  ios-app: verify=opus, implement=sonnet, max_agents=3, stall=300s
  web-app: verify=opus, implement=sonnet, max_agents=3
  api-service: verify=opus, implement=sonnet, max_agents=3
```

---

## Step 1: Connect to Autosymph

```bash
STATUS_URL="$(${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/autosymph-monitor/scripts/status-url.sh)"
curl -s "$STATUS_URL/status"
```

If this fails:
- Check if autosymph process is running: `ps aux | grep autosymph`
- If running but no API → autosymph was started before server.py was installed.
  Tell user: "autosymph is running but the status API isn't available. Restart
  autosymph (`q` in TUI, then `uv run autosymph start`) to enable the API."
- If not running → "autosymph is not running. Start it with `uv run autosymph start`."

**Self-check:** Verify `Bash(curl:*)` is in allowed permissions. If curl is
being prompted, tell the user to add it to their agent settings permissions.

`status-url.sh` discovers the active autosymph session in this order:
explicit URL/port argument, `AUTOSYMPH_STATUS_URL`, `AUTOSYMPH_STATUS_PORT`,
`~/.autosymph/status-api.json`, configured `server.port`, then localhost probe
fallbacks. Parse the JSON response. Store as `PREV_SNAPSHOT` for delta
comparison.

Report initial status:
```
Autosymph monitor active. Polling every 270s.
{N} projects | {M} issues in flight | {K} completed today | {J} failed today

Active runners:
  ISSUE-123  implement  4m 12s   34t   8.2k tok
  ISSUE-123  verify     12m 03s  22t   15.1k tok
```

---

## Step 2: Monitor Loop (ScheduleWakeup)

Use `ScheduleWakeup` with dynamic pacing:
- **270s** (default) — cache-warm, low cost
- **60s** — when alerts are active (something needs attention)
- **1200s** — when idle (no runners, no alerts)

Each tick, run the deterministic tick script:

```bash
${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/autosymph-monitor/scripts/tick.sh
```

MANDATORY: every monitor tick must include the activity summary read from the
agent ndjson logs. Do not report only process state when logs are available.

This script does everything in one call — no model interpretation needed:
1. Fetches status from the API
2. Reads ndjson logs for each active runner's activity summary
3. Checks alert rules (stall, verify churn, token burn)
4. Returns structured output:
   ```
   RUNNER: ISSUE-123  implement  32m00s  324t  8.2ktok  idle:13s  (ios-app)
     -> Fixing SdkSessionManager.swift — 116/116 tests passing
     tool: Edit: SdkSessionManager.swift
   SUMMARY: done=0 fail=0
   ```

**Just report what the script says.** Don't improvise your own status checks.
If the script returns `ALERT:` lines, run self-healing (Step 4).
If it returns `STATUS: api_unavailable`, report the HINT line to the user.

---

## Step 3: Alert Detection (Delta-Based)

Only fire when state CHANGES between snapshots. Never repeat the same alert.

### Stall Detection

```
IF runner.idle_s > 300
AND runner.turns == 0
AND runner is in CURRENT_SNAPSHOT.runners (active)
AND this stall was NOT in PREV_SNAPSHOT
THEN → ALERT: stall
```

**Why turns == 0 matters:** An agent that made progress (turns > 0) but is idle
may be waiting for a tool response. An agent with turns=0 and idle >5m never
started — likely a startup failure.

### Verify Churn

```
Read ~/.autosymph/logs/orchestrator.log
Count lines matching: STATE_CHANGE {issue_id} from=* to=verify
Within the last 24 hours
IF count >= 3
AND this was NOT already alerted
THEN → ALERT: verify_churn
```

### Infra Crash

```
IF tracked_issues[id].infra_crash_count >= 3
AND this is NEW (wasn't >= 3 in PREV_SNAPSHOT)
THEN → ALERT: infra_crash
```

### Token Burn

```
IF runner.tokens > 100000
AND this is NEW
THEN → ALERT: token_burn
```

### Process Down

```
IF the discovered status API URL fails
THEN → ALERT: process_down (CRITICAL)
```

---

## Step 4: Self-Healing

When an alert fires, diagnose and fix. Post everything to Linear.

### 4.1 Stall → Kill + Re-queue

```bash
# Kill the stuck agent
STATUS_URL="$(${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/autosymph-monitor/scripts/status-url.sh)"
curl -s -X POST "$STATUS_URL/kill/{issue_id}"

# Read the logs to understand why
cat ~/.autosymph/logs/{project}/{issue_id}/{state}-run{N}.meta.json
```

Check the meta.json for the error. Then:
- If error is workspace/git: clean and re-queue to Ready
- If error is auth: post to Linear, move to Blocked
- If error is unknown: post to Linear, move to Blocked

```
mcp__linear__save_comment(issue: "{issue_id}", body: "## Autosymph Monitor: Stall Detected\n\nAgent stalled at {state} after {duration}. turns=0, likely startup failure.\n\n**Error:** {error_from_meta}\n**Action:** Killed agent, moved to {new_state}.")

mcp__linear__save_issue(id: "{issue_id}", state: "{new_state}")
```

### 4.2 Verify Churn → Structify

This is the most valuable self-healing action. The same issue keeps bouncing
between verify and rework/implement — something structural is wrong.

1. **Read the last 3 verify_review rejection comments** from Linear
2. **Check if the rejection reason is the same** each time
3. **If same reason → structify:**
   - Read the failing verify logs
   - Identify the gap (missing skill, missing allowed_tool, bad prompt)
   - Run the structify protocol inline (DETECT → COMPARE → ROOT CAUSE → SKILL → HARDEN)
   - Post the fix to Linear
   - Move issue back to Ready (will re-enter implement → verify)
4. **If different reasons → escalate:**
   - Post summary of all 3 rejections to Linear
   - Move to Blocked
   - Report to user: "ISSUE-XXX has been rejected 3 times for different reasons. Needs human review."

### 4.3 Infra Crash → Clean + Re-queue

```bash
# Check what failed
cat ~/.autosymph/logs/{project}/{issue_id}/{state}-run{N}.meta.json | jq .error
```

Common fixes:
- **Git worktree error:** `git worktree prune` in the repo, then move to Ready
- **Disk space:** Alert user, move to Blocked
- **Auth expired:** Alert user, move to Blocked

### 4.4 Token Burn → Diagnose Loop

Read the ndjson log for the current run. Look for:
- **Permission denial loop:** Agent trying a tool that's not in allowed_tools, retrying
  - Fix: Add tool to allowed_tools in the project config, post comment, re-queue
- **Infinite retry:** Agent hitting the same error repeatedly
  - Fix: Kill, post diagnosis, move to Blocked
- **Legitimate complexity:** Agent working on a genuinely hard task
  - Action: No intervention — false positive. Log and continue.

### 4.5 Process Down → Alert User

```
⚠ CRITICAL: autosymph process is not responding on its discovered status API.
  The orchestrator may have crashed. Check with:
    ps aux | grep autosymph
    tail -50 ~/.autosymph/logs/orchestrator.log
```

Cannot self-heal — needs user to restart autosymph.

---

## Step 5: Interactive Commands

The user can ask questions at any time. The autosymph monitor answers from data.

### "what happened to ISSUE-XXX?"

```bash
# Read orchestrator events for this issue
grep "{issue_id}" ~/.autosymph/logs/orchestrator.log | tail -20

# Read the latest run meta
ls -t ~/.autosymph/logs/*/{issue_id}/*.meta.json | head -5
cat <latest_meta>
```

Synthesize: what state transitions occurred, what errors happened, how many
runs, total duration, total tokens.

### "how did the last N issues go?"

Read from the status API: `completed_today`, `failed_today`. For each, read
the meta.json to get duration, tokens, run count.

Present as a table:
```
Last 5 completed issues:
  ISSUE-123  3 runs   12m  45k tok  ✓
  ISSUE-123  1 run    4m   8k tok   ✓
  ISSUE-123  5 runs   28m  92k tok  ✓ (2 reworks)
  ISSUE-123  1 run    2m   3k tok   ✓
  ISSUE-123  7 runs   45m  120k tok ✗ (stuck in verify churn)
```

### "move ISSUE-XXX to Ready"

```
mcp__linear__save_issue(id: "{issue_id}", state: "Ready")
```

Confirm: "Done. ISSUE-XXX moved to Ready. Will be picked up on next poll cycle."

### "kill ISSUE-XXX"

```bash
STATUS_URL="$(${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/autosymph-monitor/scripts/status-url.sh)"
curl -s -X POST "$STATUS_URL/kill/{issue_id}"
```

Confirm: "Killed agent for ISSUE-XXX (was in {state})."

---

## Anti-Patterns

- **DO NOT** run structify on code bugs. Structify is for infra/skill gaps.
  If the verify rejection is about incorrect code behavior, move to Rework.
- **DO NOT** fight with autosymph. Kill the agent first, wait for the
  orchestrator to release the claim, THEN move the issue.
- **DO NOT** escalate on first failure. Wait for 3+ occurrences (the threshold
  exists for a reason).
- **DO NOT** mutate Linear without posting a comment first. Every action
  must have an audit trail.

---

## StatusLine

For passive monitoring without running the monitor, add the hook to your agent settings:

```json
{
  "statusLine": {
    "type": "command",
    "command": "URL=\"$(${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/autosymph-monitor/scripts/status-url.sh 2>/dev/null)\" && curl -s \"$URL/status\" 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); ps=d.get('projects',{}); rs=sum(len(p.get('runners',{})) for p in ps.values()); print(f'autosymph | {rs} active | done:{d.get(\\\"total_completed_today\\\",0)} fail:{d.get(\\\"total_failed_today\\\",0)}')\" 2>/dev/null || echo 'autosymph | offline'"
  }
}
```

---

## Execution

On `/autosymph-monitor` invocation:

1. Load configs (Step 0)
2. Connect to autosymph (Step 1)
3. Report initial status
4. Start monitor loop via ScheduleWakeup (Step 2)

Each ScheduleWakeup tick:
1. Fetch status
2. Detect alerts (Step 3)
3. Self-heal if needed (Step 4)
4. Report to user
5. Schedule next wake

EXECUTE NOW.
