# Autonomous Engineering Pipeline

End-to-end workflow from raw thought to merged code. Designed for an autosymph-style orchestrator (Stokowski, custom Agent SDK daemon, or `claude -p` loop) polling Linear.

## Linear Statuses

```
[raw thoughts] → Triage → Todo → Planning → Ready → In Progress → In Review → Merging → Done
                                                          ↑                |
                                                          └── Rework ←─────┘
```

| Status | Type | Owner | What Happens |
|--------|------|-------|-------------|
| **Triage** | triage | Triage Agent | Raw intake. Unstructured ideas from any source. |
| **Todo** | unstarted | Human (or auto) | Enriched, parameterized, prioritized. Ready for planning. |
| **Planning** | started | Claude (plan-claude-codex) | Research, interview, Codex review loop, PRD published. |
| **Ready** | unstarted | Queue | Plan approved. PRD + task-list on Linear. Waiting for agent. |
| **In Progress** | started | Agent (agent harness) | Implementing against the PRD. |
| **In Review** | started | Human/Bot | PR open, awaiting review. |
| **Rework** | started | Agent (re-dispatch) | Addressing review feedback, then back to In Review. |
| **Merging** | started | Agent | PR approved. Merge + CI in progress. |
| **Done** | completed | — | Merged and verified. |
| **Canceled** | canceled | Human | Won't do. |
| **Duplicate** | canceled | Human/Agent | Duplicate of another issue. |

## Phase 1: Intake (Triage → Todo)

### Sources
Raw thoughts arrive in Triage from:
- Voice memos (Implicit briefing / Siri)
- Slack messages
- Email threads
- User feedback
- Automated alerts (error monitoring, CI failures)
- Manual creation in Linear

Issues in Triage are raw and unstructured — often just a sentence or a link.

### Triage Agent
A subagent (or scheduled `/loop`) polls for issues in Triage and enriches them:

1. **Parse the raw input** — extract intent, affected area, urgency signals
2. **Set project** — assign to the correct Linear project based on content
3. **Set assignee** — route to the right person (or leave unassigned for pool)
4. **Set priority** — 1=Urgent, 2=High, 3=Normal, 4=Low based on severity/impact signals
5. **Set labels** — categorize (bug, feature, infra, docs, etc.)
6. **Set dependencies** — link blocking/blocked-by issues if referenced
7. **Set ordering** — position in the backlog based on priority and dependencies
8. **Rewrite title** — clean, actionable title (verb + noun + context)
9. **Expand description** — structured description with problem statement, if derivable from raw input
10. **Move to Todo**

The triage agent should NOT plan or scope — just organize. Planning happens in the next phase.

### Autosymph config for Triage
```yaml
# Triage is NOT in active_states — a separate lightweight agent handles it.
# Either a /loop cron, a dedicated autosymph instance, or a hook.
```

## Phase 2: Planning (Todo → Ready)

Triggered by: human moves issue to Todo (or triage agent does it automatically).

**Who runs it:** `/plan-claude-codex {ISSUE_ID}` — manually or via automation.

**What happens:**
1. Claude gathers context (Linear issue + codebase + docs)
2. Claude interviews the user (skip in automated mode)
3. Claude runs premortem (TIGERS + ELEPHANTS)
4. Claude drafts PRD + task-list
5. Codex reviews via `codex exec` pipe (up to 3 rounds)
6. Review comments posted to Linear per round
7. Final PRD + task-list published as Linear documents linked to issue
8. Issue moved to **Ready**

**Automated planning:** In an autosymph setup, a second orchestrator (or the same one with a separate workflow) can poll for Todo issues and run planning autonomously. The interview step is skipped — requirements are derived from the issue description and codebase research.

**State transitions:**
- Todo → Planning (when plan-claude-codex starts)
- Planning → Ready (when plan is approved)

## Phase 3: Implementation (Ready → In Review)

Triggered by: autosymph polls for issues in `Ready` state.

**Who runs it:** autosymph dispatches an agent (Codex or Claude via ralph/`claude -p`).

**What happens:**
1. Agent creates isolated workspace (git worktree)
2. Agent fetches PRD + task-list from Linear documents
3. Agent implements task by task, verifying each
4. Agent creates PR with summary + test plan
5. Agent moves issue to **In Review**

**State transitions:**
- Ready → In Progress (agent starts working)
- In Progress → In Review (PR created)

## Phase 4: Review (In Review → Merging | Rework)

Triggered by: PR review (human, Cursor bugbot, mesa.dev).

**Two outcomes:**
- **Approved** → Human or bot moves to **Merging**
- **Changes requested** → Human or bot moves to **Rework**

## Phase 5: Rework (Rework → In Review)

Triggered by: autosymph polls for issues in `Rework` state.

**Who runs it:** autosymph re-dispatches an agent to the same workspace.

**What happens:**
1. Agent reads PR review comments
2. Agent addresses feedback
3. Agent pushes fixes, re-requests review
4. Agent moves issue back to **In Review**

**State transitions:**
- Rework → In Progress (agent picks up)
- In Progress → In Review (fixes pushed)

## Phase 6: Merge (Merging → Done)

Triggered by: issue moved to Merging (after PR approval).

**Who runs it:** Agent or CI automation.

**What happens:**
1. Agent squash-merges the PR
2. CI runs (tests, deploy)
3. Agent moves issue to **Done**

**State transitions:**
- Merging → Done (merge + CI pass)
- Merging → Rework (CI fails — agent needs to fix)

## Autosymph configuration

```yaml
# WORKFLOW.md front matter for the implementation orchestrator
tracker:
  kind: linear
  project_slug: "product-engineering"
  api_key: "$LINEAR_API_KEY"
  active_states: ["Ready", "Rework"]
  terminal_states: ["Done", "Canceled", "Duplicate"]

polling:
  interval_ms: 30000

agent:
  max_concurrent_agents: 3

codex:
  command: "claude -p"  # or "codex app-server" for Codex
  approval_policy: "unless-allow-listed"
  turn_timeout_ms: 3600000
```

**Key: `active_states` only includes `Ready` and `Rework`.** autosymph ignores everything else — Triage, Todo, Planning, In Progress, In Review, and Merging are managed by other agents or humans.

## Agent Responsibilities by Phase

| Phase | Agent | Polls For | Moves To |
|-------|-------|-----------|----------|
| Intake | Triage agent | Triage | Todo |
| Planning | plan-claude-codex | Todo (manual or automated) | Planning → Ready |
| Implementation | autosymph agent | Ready | In Progress → In Review |
| Rework | autosymph agent | Rework | In Progress → In Review |
| Merge | autosymph agent or CI | Merging | Done |
