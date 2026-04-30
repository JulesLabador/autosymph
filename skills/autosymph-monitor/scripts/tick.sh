#!/bin/bash
# ============================================================================
# autosymph-monitor tick — deterministic status fetch + activity summary
#
# Fetches autosymph status API + reads ndjson logs for each active runner.
# Returns structured output the model just reports — no interpretation needed.
#
# Usage:  tick.sh [status-url-or-port]
# Output: structured text, one section per runner + summary line
# Exit:   0 = ok, 1 = autosymph not running
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if ! STATUS_BASE_URL="$("$SCRIPT_DIR/status-url.sh" "${1:-}" 2>/dev/null)"; then
  if pgrep -f "autosymph" > /dev/null 2>&1; then
    echo "STATUS: api_unavailable"
    echo "HINT: autosymph is running but no status API endpoint was discoverable. Restart autosymph or set AUTOSYMPH_STATUS_URL."
  else
    echo "STATUS: not_running"
    echo "HINT: autosymph is not running. Start with: uv run autosymph start"
  fi
  exit 1
fi
LOG_ROOT="${AUTOSYMPH_LOG_ROOT:-$HOME/.autosymph/logs}"

# Fetch status from the discovered autosymph API.
STATUS=$(curl -s "${STATUS_BASE_URL}/status" 2>/dev/null)
if [ -z "$STATUS" ]; then
  # Check if process is running
  if pgrep -f "autosymph" > /dev/null 2>&1; then
    echo "STATUS: api_unavailable"
    echo "HINT: autosymph is running but the discovered status API is not responding. Restart autosymph or set AUTOSYMPH_STATUS_URL."
  else
    echo "STATUS: not_running"
    echo "HINT: autosymph is not running. Start with: uv run autosymph start"
  fi
  exit 1
fi

# Parse status JSON
STATUS_JSON="$STATUS" LOG_ROOT="$LOG_ROOT" python3 -c "
import json, sys, os, glob

status = json.loads(os.environ['STATUS_JSON'])
log_root = os.environ['LOG_ROOT']

alerts = []
runners = []

for slug, project in status.get('projects', {}).items():
    for ident, r in project.get('runners', {}).items():
        dur = r.get('duration_s', 0)
        idle = int(r.get('idle_s', 0))
        turns = r.get('turns', 0)
        tokens = r.get('tokens', 0)
        state = r.get('state', '?')

        dur_str = f'{int(dur//60)}m{int(dur%60):02d}s'
        tok_str = f'{tokens/1000:.1f}k' if tokens >= 1000 else str(tokens)

        # Read latest ndjson log for activity summary
        activity = '?'
        last_tool = '?'
        issue_lower = ident.lower().replace('-', '-')
        log_dir = os.path.join(log_root, slug, issue_lower)
        if not os.path.isdir(log_dir):
            log_dir = os.path.join(log_root, issue_lower)

        if os.path.isdir(log_dir):
            ndjson_files = sorted(glob.glob(os.path.join(log_dir, f'{state}-run*.ndjson')))
            if ndjson_files:
                latest = ndjson_files[-1]
                msgs = []
                tools = []
                try:
                    with open(latest) as f:
                        for line in f:
                            try:
                                obj = json.loads(line)
                                if obj.get('type') == 'assistant':
                                    for b in obj.get('message',{}).get('content',[]):
                                        if b.get('type') == 'text' and len(b.get('text','').strip()) > 20:
                                            msgs.append(b['text'][:120].replace('\n',' '))
                                        if b.get('type') == 'tool_use':
                                            n = b.get('name','')
                                            if n in ('Edit','Write'):
                                                tools.append(f\"{n}: {b.get('input',{}).get('file_path','?').split('/')[-1]}\")
                                            elif n == 'Bash':
                                                tools.append(f\"Bash: {b.get('input',{}).get('command','')[:50]}\")
                                            else:
                                                tools.append(n)
                            except: pass
                except: pass
                if msgs:
                    activity = msgs[-1][:100]
                if tools:
                    last_tool = tools[-1][:60]

        # Alert checks
        if idle > 300 and turns == 0:
            alerts.append(f'ALERT: stall — {ident} idle:{idle}s turns:0')
        if tokens > 100000:
            alerts.append(f'ALERT: token_burn — {ident} tokens:{tok_str}')

        print(f'RUNNER: {ident}  {state}  {dur_str}  {turns}t  {tok_str}tok  idle:{idle}s  ({slug})')
        print(f'  -> {activity}')
        if last_tool != '?':
            print(f'  tool: {last_tool}')

# Check for verify churn in orchestrator.log
orch_log = os.path.join(log_root, 'orchestrator.log')
if os.path.isfile(orch_log):
    from datetime import datetime, timedelta
    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    churn_counts = {}
    try:
        with open(orch_log) as f:
            for line in f:
                if 'STATE_CHANGE' in line and 'to=verify' in line:
                    parts = line.split()
                    if len(parts) >= 3:
                        ts = parts[0]
                        issue = parts[2]
                        if ts >= cutoff:
                            churn_counts[issue] = churn_counts.get(issue, 0) + 1
        for issue, count in churn_counts.items():
            if count >= 3:
                alerts.append(f'ALERT: verify_churn — {issue} entered verify {count}x in 24h')
    except: pass

# Completed/failed
completed = status.get('total_completed_today', 0)
failed = status.get('total_failed_today', 0)
completed_list = status.get('completed_issues', [])
failed_list = status.get('failed_issues', [])

if not any(p.get('runners') for p in status.get('projects',{}).values()):
    print('NO_RUNNERS')

for a in alerts:
    print(a)

print(f'SUMMARY: done={completed} fail={failed}')
if completed_list:
    print(f'  completed: {\" \".join(completed_list)}')
if failed_list:
    print(f'  failed: {\" \".join(failed_list)}')
"
