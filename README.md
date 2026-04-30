# autosymph

Agent orchestrator. Polls Linear for issues, spawns AI coding agents, manages the implement > verify > review > merge lifecycle. Runs multiple projects simultaneously.

## Requirements

Core autosymph only needs Python, uv, a Linear API key, and at least one
supported agent runner. Platform verification tools are optional and only
needed for projects whose test plans require them.

| Tool | Version | Why | Install |
|------|---------|-----|---------|
| Python | 3.11+ | autosymph runtime | `brew install python@3.12` or your preferred Python |
| `uv` | 0.10+ | venvs, isolated tool installs | `brew install uv` |
| Linear API key | — | `LINEAR_API_KEY` env var | https://linear.app/settings/api |
| Agent runner | Claude Code, Codex, or Pi | executes state prompts | install the runner(s) you configure |

### Optional: iOS verification

Install these only if a project needs simulator-based iOS verification.

| Tool | Version | Why | Install |
|------|---------|-----|---------|
| macOS | 14+ | iOS Simulator, `xcrun simctl` | n/a |
| Xcode CLT / Xcode | latest | `xcrun`, `xcodebuild`, `swift` | `xcode-select --install` and/or install Xcode |
| Python for `fb-idb` | 3.12 or 3.13, not 3.14 | host for `fb-idb`; see "Python 3.14 trap" below | `brew install python@3.12` |
| `fb-idb` | 1.1.7+ | iOS Debug Bridge, used by MCP iOS Simulator server for `ui_*` tools | `uv tool install --python 3.12 fb-idb` |
| `idb_companion` | latest | native sim driver | `brew install idb-companion` |

### Optional: web verification

Install these only if a project needs browser-based verification.

| Tool | Version | Why | Install |
|------|---------|-----|---------|
| Node.js | current LTS | project dev servers and Playwright | `brew install node` |
| Playwright browsers | current | headless browser checks | `npx playwright install chromium` |

### Python 3.14 trap (and why `fb-idb` needs its own venv)

Homebrew's `python@3.14` is currently built against a newer libexpat than the
one that ships in `/usr/lib/libexpat.1.dylib`. At runtime, dyld picks the
system one, the symbol lookup `_XML_SetAllocTrackerActivationThreshold` fails,
and **every `import pyexpat` in 3.14 dies with ImportError** — including pip,
xmlrpc, idb, etc. This is a homebrew formula bug, not a uv/venv issue.

autosymph's package metadata allows Python 3.11+, but iOS verification depends
on `fb-idb`. **The fix is to keep `fb-idb` off 3.14.** Install it in a 3.12
venv via uv tool:

```bash
uv tool install --python 3.12 fb-idb
# Verify:
~/.local/bin/idb --help          # works under 3.12
which idb                        # may still resolve to /opt/homebrew/bin/idb
```

If `which idb` still picks the broken homebrew one (which has a hardcoded
shebang to `/opt/homebrew/opt/python@3.14/bin/python3.14`), displace it:

```bash
mv /opt/homebrew/bin/idb /opt/homebrew/bin/idb.broken-py314
which idb                        # now resolves to ~/.local/bin/idb
```

You can leave `python3` resolving to 3.14 — only `idb` cared. Other tools
(pip, etc.) that need pyexpat should also use 3.12/3.13 explicitly.

### Diagnosing infra problems before a verify run wastes 10 minutes

Use the `verify-preflight` skill to fast-fail on missing fixtures or broken
tools (`auth.md`, `idb`, `xcrun simctl`, Playwright). It runs in <10s:

```bash
${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/verify-preflight/scripts/preflight.sh \
  --workspace <workspace-path> \
  [--ios] [--web] [--auth-gated]
```

`exit 0` = safe to proceed. `exit 2` = at least one blocker; mark affected
items BLOCKED. The verify prompt's Phase 0 invokes this automatically.

## Quick Start

```bash
cd autosymph
uv sync --extra dev
export LINEAR_API_KEY=lin_api_...

# One-time Linear setup:
# - Create label `runner:codex` to route individual issues through Codex.
# - Create label `runner:pi` only for projects that opt into Pi in YAML.
# - If Autoplan is enabled, also create the required `needs-review` label.

# Single project
uv run autosymph start -c ~/.autosymph/config/devices/$(hostname -s).yaml

# All projects (auto-discovers from config dir)
uv run autosymph start
```

## How It Works

autosymph polls Linear every 30s for issues in "Ready" status. When it finds one, it:

1. Creates a git worktree (isolated branch)
2. Spawns a Claude agent with a state-specific prompt
3. Tracks progress (turns, tokens, duration)
4. Transitions the issue through states: implement > verify > review > merge > done

Each state is either an **agent** (AI does work), a **gate** (human approves), or **terminal** (done).

### Linear Labels

Autosymph uses Linear issue labels for optional per-issue routing:

| Label | Purpose |
|-------|---------|
| `runner:codex` | Dispatch this issue with the Codex runner instead of the configured default. |
| `runner:pi` | Dispatch this issue with the Pi runner. Only valid when `pi` is explicitly enabled under `runners.available`. |
| `needs-review` | Required when Autoplan is enabled; used by the review fallback flow. |

Create these labels once in Linear workspace/project settings before handing them to users in an onboarding checklist. `runner:codex` is safe to create by default; `runner:pi` should be created only for teams that have the `pi` CLI installed and opt into it in config.

## Configuration

autosymph uses a **layered config** system: devices define WHERE, projects define HOW.

```
autosymph-config/
  devices/
    {hostname}.yaml        # device: simulators, ports, agent limits, repo paths
  projects/
    {project-slug}.yaml    # project: states, prompts, tracker, hooks
```

At startup, autosymph finds your device config by hostname, then merges each project listed in it with the project workflow definition.

### Device Config

Defines the environment for this machine: what resources are available, where repos live, agent concurrency limits.

```yaml
# autosymph-config/devices/{hostname}.yaml

machine_name: "my-machine"

resources:
  ios_simulator:
    - name: "iPhone 17 Pro"
      udid: "auto"              # resolved via xcrun simctl
  dev_port_range: [3001, 3002, 3003]

agent:
  max_concurrent_agents: 3      # how many agents can run at once
  max_concurrent_agents_by_state:
    implement: 2
    finalize: 1

claude:
  model: claude-sonnet-4-6
  max_turns: 100

logging:
  log_root: ~/.autosymph/logs

projects:
  my-project:
    repo: ~/path/to/my-project  # where the git repo lives on THIS machine
  another-project:
    repo: ~/path/to/another
```

### Project Config

Defines the workflow for a project: what Linear project to poll, what states exist, what prompts to use.

```yaml
# autosymph-config/projects/{project-slug}.yaml

tracker:
  kind: linear
  project: "My Project"         # Linear project name (exact match)
  api_key: "$LINEAR_API_KEY"    # env var reference
  # assignee_filter: "me"       # optional: only poll issues assigned to you

polling:
  interval_ms: 30000            # poll every 30s

linear_states:
  todo: "Ready"                 # Linear status name for each workflow stage
  active: "Implementing"
  verifying: "Verifying"
  review: "In Review"
  gate_approved: "Merging"
  rework: "Rework"
  terminal: [Done, Canceled, Duplicate]

workspace:
  root: ~/.autosymph/workspaces # repo path comes from device config

hooks:
  before_run: |                 # runs before each agent dispatch
    git fetch origin main
    git rebase origin/main 2>/dev/null || true

prompts:
  root: ../../autosymph/prompts # canonical prompt directory
  global_prompt: global.md      # prepended to every state prompt

runners:
  default: claude                # default harness when no override matches
  available:
    claude:
      type: claude
    codex:
      type: codex
    # Pi is opt-in because most machines do not have the `pi` CLI installed.
    # pi:
    #   type: pi
  auto_match: []
  # To auto-route matching issues, replace the empty list with:
  # auto_match:
  #   - runner: codex
  #     title: "refactor|cleanup"

states:
  implement:
    type: agent
    # runner: codex              # optional per-state harness override
    prompt: implement.md
    linear_state: active
    session: inherit            # resume previous session on retry
    transitions:
      complete: verify
      complete_low_risk: verify

  verify:
    type: agent
    prompt: verify.md
    linear_state: verifying
    model: claude-opus-4-7      # override model for verification (see Model Registry below)
    session: new
    transitions:
      complete: review
      fail: implement

  review:
    type: gate                  # human approval required
    linear_state: review
    rework_to: rework
    max_rework: 3
    rework_exhausted: todo      # give up after 3 rework cycles
    transitions:
      approve: finalize

  rework:
    type: agent
    prompt: implement.md
    linear_state: rework
    session: inherit
    transitions:
      complete: verify

  finalize:
    type: agent
    prompt: merge.md
    linear_state: gate_approved
    session: inherit
    transitions:
      complete: done

  done:
    type: terminal
    linear_state: terminal
```

### Where to update state prompts

- Edit prompt bodies in [prompts](prompts), not in `autosymph-config`.
- `verify` state behavior lives in [verify.md](prompts/verify.md).
- `verify_review` behavior lives in [verify-review.md](prompts/verify-review.md).
- `implement` and `rework` share [implement.md](prompts/implement.md).
- `finalize` uses [merge.md](prompts/merge.md).
- Shared instructions across all states live in [global.md](prompts/global.md).
- Edit `autosymph-config/**/*.yaml` only when you want to change routing, models, tool permissions, state machines, or `prompts.root`.

### Where what lives

Quick map of what goes where. When something feels off, this is the table to consult first.

| File / dir | What's in it | Edit when |
|---|---|---|
| `autosymph-config/devices/{hostname}.yaml` | This machine's environment: simulators, dev ports, repo paths, agent concurrency, default Claude model. Lists which projects to run on this host. | New device, new simulator, repo moved on disk, want to throttle agent concurrency on this host. |
| `autosymph-config/projects/{slug}.yaml` | One project's workflow: tracker (Linear project name), state machine (`implement`/`verify`/`review`/…), prompts, transitions, per-state model and tool permissions. | Adding a state, changing a per-state model, retargeting a Linear status name, tightening `allowed_tools`. |
| `autosymph-config/local.env` *(gitignored)* | Per-device secrets and startup defaults. Loaded by `autosymph start` before config discovery. Shell vars win when both are set. | Storing `IMPLICIT_E2E_SECRET` or pinning `AUTOSYMPH_CONFIG_DIR` so launches don't have to repeat them. |
| `autosymph-config/local.env.example` | Template showing the expected keys. | Adding a new local-env key the team should know about. |
| `autosymph-config/verify-templates/*.md` | Copy-paste-ready prompt fragments (`auth.md`, `fixtures.md`) the verify agent reuses. | Verify needs new shared boilerplate. |
| `autosymph/prompts/*.md` | The actual agent prompts: `implement.md`, `verify.md`, `verify-review.md`, `merge.md`, `global.md`, `autoplan.md`, `investigating.md`, `scheduled-models-refresh.md`. | Changing what an agent *does* — the YAML routes to a prompt; the prompt is the behavior. |
| `autosymph/src/autosymph/models.py` | Source of truth for "current" Claude model ids. Rewritten by `autosymph models refresh --apply`. | Never by hand — let the scheduled refresh PR do it. |
| `autosymph/src/autosymph/` | Orchestrator code. Don't edit unless you mean to. | Bug fix or new state type. |

**Layered vs legacy.** Layered (`devices/` + `projects/`) is the only supported format going forward. The single-file format (`autosymph-config/{hostname}.yaml` at top level) is the old layout from before the device/project split — autosymph still falls back to it if `devices/{hostname}.yaml` produces zero valid configs, but the fallback is silent and a footgun. **If you see a top-level `{hostname}.yaml` next to `devices/`, migrate it: split simulators/repo paths into `devices/{hostname}.yaml` and tracker/states into `projects/{slug}.yaml`, then delete the top-level file.**

To run a one-off non-layered config (testing, debugging, throwaway), pass `-c`:

```bash
uv run autosymph start -c path/to/combined.yaml
```

## Multi-Instance

When `autosymph start` discovers multiple projects in your device config, it launches a **Supervisor** that:

- Spawns one orchestrator per project
- Shares simulators and ports across all projects (global pool)
- Enforces a global agent concurrency cap with fair-share allocation
- Shows a grouped TUI with per-project sections
- Auto-restarts crashed orchestrators (max 3 retries, exponential backoff)

```bash
# Run all projects from device config
uv run autosymph start

# Limit total concurrent agents across all projects
uv run autosymph start --max-agents 4
```

### TUI Controls

| Key | Action |
|-----|--------|
| `q` | Quit all |
| `r` | Force refresh (poll now) |
| `j` / `k` | Scroll down / up |

## CLI Reference

```bash
uv run autosymph start                    # start orchestrator (TUI)
uv run autosymph start -c config.yaml     # single config
uv run autosymph start --max-agents 4     # global agent cap
uv run autosymph start -v                 # verbose logging (no TUI)
uv run autosymph start --daemon           # background, logs only
uv run autosymph config check             # validate config
uv run autosymph logs IMP-123             # show logs for issue
uv run autosymph logs --project implicit IMP-123  # namespaced logs
uv run autosymph models check             # fail if any config references a stale model
uv run autosymph models refresh           # dry-run: bump registry from /v1/models
uv run autosymph models refresh --apply   # rewrite src/autosymph/models.py
```

## Model Registry

### Aliases vs pinned ids — pick aliases by default

The `claude` CLI accepts model **aliases** (`opus`, `sonnet`, `haiku`) that
auto-resolve to whatever the harness considers current at dispatch time:

```yaml
states:
  verify:
    model: opus           # alias — auto-floats, never goes stale
    # vs
    model: claude-opus-4-7  # pinned — drifts, needs `models refresh` to bump
```

**Use aliases by default** — they're subscription-friendly (no `ANTHROPIC_API_KEY`
needed, OAuth works fine), zero deploy burden, and make the entire
"refresh-the-registry" flow optional. The default `claude.model` config value
is `sonnet` for this reason.

**Pin only when you need reproducibility** — release runs, audited bisects, or
billing tiers where the specific id matters. Pinning is opt-in: the registry
+ `models check` + `autosymph-update-configs` skill exist for that audience.
For everyone else, aliases are the right answer and the rest of this section
is informational.

### Registry (for pinned-id users)

`src/autosymph/models.py` is the single source of truth for "current" Claude
model ids. It defines `LATEST` (one id per opus/sonnet/haiku family) and
`KNOWN_STALE` (old ids → suggested replacements). Everything else flows from it:

- **`autosymph models check`** scans every loaded config and exits 1 if any
  `claude.model` or per-state `model:` references an id in `KNOWN_STALE`. Wire
  it into pre-commit / CI to catch drift at commit time.
- **`autosymph models refresh`** queries Anthropic `GET /v1/models` and prints
  the diff if a newer id has shipped in any family. `--apply` rewrites
  `models.py`; old `LATEST` ids are demoted into `KNOWN_STALE` so existing
  configs surface as stale on the next `models check`.
- **Scheduled bump PR** — `prompts/scheduled-models-refresh.md` is a paste-able
  prompt for `/schedule weekly`. The agent runs the refresh, opens a PR if the
  diff is non-empty, no-ops otherwise. Never auto-merges.

This means model bumps are visible (PRs in git history), enforced (CI fails on
stale refs), and safe (humans approve before any config changes).

### Setup

One-time, in this order:

1. **Set `ANTHROPIC_API_KEY`** in your shell env. Required only for `models
   refresh`. `models check` does not need it.

2. **Register the scheduled bump-PR agent.** The harness only lets a human
   register schedules — autosymph cannot do this for itself. Run:
   ```
   /schedule weekly @autosymph/prompts/scheduled-models-refresh.md
   ```
   The prompt is self-contained: it fetches `/v1/models`, runs `models refresh
   --apply`, runs the test subset, opens a PR with the diff in the body, and
   no-ops cleanly if the registry is already current. **Never auto-merges.**

3. **(Recommended) Add a pre-commit hook** so stale model ids can't sneak into
   YAML between weekly bumps. In `.pre-commit-config.yaml`:
   ```yaml
   - repo: local
     hooks:
       - id: autosymph-models-check
         name: autosymph models check
         entry: bash -c 'cd autosymph && uv run autosymph models check'
         language: system
         pass_filenames: false
         files: ^autosymph-config/.*\.yaml$
   ```

### Day-to-day usage

- After a scheduled refresh PR merges, `models check` will start failing for
  any YAML config still on the demoted id. Fix the YAML, commit. (Or wait for
  the pre-commit hook to catch you on the next config edit.)
- To bump manually outside the schedule: run `uv run autosymph models refresh
  --apply` from the repository root, then open a PR by hand.
- To audit drift right now without bumping: `uv run autosymph models check`.

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `LINEAR_API_KEY` | Linear API key (required) |
| `AUTOSYMPH_CONFIG_DIR` | Config directory (default: `~/.autosymph/config`) |
| `BRAINTRUST_API_KEY` | Optional BYOK Braintrust tracing/eval-loop integration. Install with `autosymph[tracing]` and use your own Braintrust project/API key. |
| `ANTHROPIC_API_KEY` | Required for `autosymph models refresh` only |

## Directory Layout

```
~/.autosymph/
  logs/
    {project-slug}/
      {issue-slug}/
        {state}-run{N}.ndjson     # raw agent output
        {state}-run{N}.meta.json  # run metadata
  workspaces/
    {project-slug}/
      {issue-slug}/               # git worktrees
```

## State Types

| Type | What happens | Example |
|------|-------------|---------|
| `agent` | AI agent runs with a prompt | implement, verify, finalize |
| `gate` | Waits for human approval in Linear | review |
| `terminal` | Issue is done | done |

## Risk Labels

Issues labeled `risk:low` skip the human review gate and go straight from verify to finalize. Everything else requires human approval.

## Tests

```bash
uv run pytest -v        # 64 tests
uv run ruff check src/  # lint
```
