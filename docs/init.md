# `autosymph init` — Interactive Onboarding Wizard

`autosymph init` walks a fresh user through the setup steps autosymph
requires: Linear API key validation, project selection, workflow-state
provisioning, repo path, optional iOS simulator selection, optional
Braintrust tracing, and writes the config files atomically.

## What the wizard touches

After a successful run, the wizard has written:

- `~/.autosymph/config/devices/{hostname}.yaml` — device-level config
  (machine name, simulators, agent concurrency, project list).
- `~/.autosymph/config/projects/{slug}.yaml` — project-level config
  (Linear tracker, workflow state mapping, prompts.root, runners,
  state machine).
- `~/.autosymph/config/local.env` — `LINEAR_API_KEY` and optional
  `BRAINTRUST_API_KEY`. File mode is set to `0600`.
- `~/.autosymph/config/wizard-mutations.log` — append-only audit log
  of every Linear workflow state the wizard created. Use this if you
  ever need to roll back the Linear-side changes.

It also creates Linear workflow states (with explicit confirmation):
`Ready`, `Implementing`, `Verifying`, `Investigating`, `In Review`,
`Rework`, `Merging`, `Blocked`, `Done`, `Canceled`, `Duplicate` — only
the ones not already present in your team's workflow.

## What the wizard does NOT touch

- **Linear MCP for your coding agent.** Claude Code, Codex, etc. need
  Linear MCP configured separately in your harness settings. The wizard
  prints a one-line reminder and stops there — it does not edit
  `~/.claude/settings.json` or equivalents.
- **Model registry refresh cron.** Keeping `src/autosymph/models.py`
  current via the `autosymph-update-configs` skill + GitHub Actions cron
  is a separate setup. Run `uv run autosymph models check` periodically
  (or wire up the cron yourself) until the wizard supports it.
- **Autoplan onboarding.** v1 generates a non-autoplan project config.
  If you want the autoplan state and `Reviewing Evidence` review-gate,
  you'll need to add them to the generated YAMLs by hand for now.
- **Renaming existing Linear states.** If your workspace has
  `In Progress` and you want it renamed to `Implementing`, do that in
  Linear yourself. The wizard offers to use the existing name as an alias
  ("write `active: In Progress` in your config"), or to create a new
  `Implementing` state alongside it.
- **`uv sync` / autosymph install.** The wizard assumes autosymph itself
  is already installed and runnable.

## Re-running

`autosymph init` is fresh-setup-only in v1. If any of these exist, the
wizard exits with guidance and refuses to prompt:

- `~/.autosymph/config/devices/{hostname}.yaml`
- any `~/.autosymph/config/projects/*.yaml`
- non-empty `~/.autosymph/config/local.env`
- a stale `~/.autosymph/config/.wizard.lock`

To add a new project to a configured device, edit the YAMLs by hand. To
restart from scratch:

```bash
rm -rf ~/.autosymph/config && uv run autosymph init
```

## Undoing a run

The wizard logs every Linear state it created to
`~/.autosymph/config/wizard-mutations.log`. Each line is a tab-separated
`(timestamp, canonical_name, linear_state_id)` triple. Use the
`linear_state_id` to delete the state through the Linear UI or API if
you decide you don't want it.

To revert the local config side, just delete `~/.autosymph/config/`.

## Failure modes

| Exit | Meaning |
|---|---|
| 0 | Success, OR the user aborted at the combined-preview confirmation (no changes made). |
| 1 | Existing config detected, prompts.root missing (autosymph installed without source), or Linear mutation failed mid-bundle. The wizard's stderr output names the specific recovery path for each case. |
| 130 | Interrupted (ctrl-C). User-config files are atomic temp-then-rename, so no partial files are left on disk; Linear state may be partial — see `wizard-mutations.log`. |

## Known v1 limitations

- Single project per `init` run. Multi-project setups: run `init` once,
  then add subsequent projects by editing the device YAML manually.
- No partial-update flow. Existing config blocks the wizard entirely.
- Cannot rename existing Linear workflow states. Always uses the existing
  Linear name (via the alias map) or creates a new state.
- `prompts.root` is derived from autosymph's source layout. If you've
  installed autosymph as a wheel without source, the wizard exits with a
  clear message — install from source instead.
