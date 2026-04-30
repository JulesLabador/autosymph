# Manual Setup

`autosymph init` is the recommended setup path. This document is for cases
where the wizard isn't a fit:

- Air-gapped or CI environments where you can't make interactive Linear
  API calls.
- Bulk provisioning of many projects from a script.
- You explicitly want to opt out of the wizard's Linear mutations.

## 1. Install dependencies

```bash
uv sync --extra dev
```

## 2. Install runtime skills

The wizard installs these too, but if you're skipping it:

```bash
scripts/install-skills.sh --target "$HOME/.autosymph/skills"
```

## 3. Generate config from examples

```bash
mkdir -p ~/.autosymph/config/devices ~/.autosymph/config/projects

cp examples/config/devices/hostname.yaml.example \
  ~/.autosymph/config/devices/$(hostname -s | tr '[:upper:]' '[:lower:]').yaml
cp examples/config/projects/web-project.yaml.example \
  ~/.autosymph/config/projects/web-app.yaml
cp examples/config/local.env.example ~/.autosymph/config/local.env
```

Use `examples/config/projects/ios-project.yaml.example` instead of
`web-project.yaml.example` if your target is an iOS app.

## 4. Edit the copied files

In the device config (`devices/{hostname}.yaml`):

- Set each project's repo path under `projects.{slug}.repo`.
- Add `resources.ios_simulator` entries if you have iOS targets.

In the project config (`projects/{slug}.yaml`):

- Set `tracker.project` to the exact Linear project name.
- Set `prompts.root` to the absolute path of this checkout's `prompts/`
  directory. The example ships with a relative path that only works while
  you're running from the repo's `examples/` directory.
- Set `linear_states.*` to match your Linear team's actual state names.
  autosymph's defaults assume `Implementing` / `Verifying` / `In Review`
  etc. — if your workspace uses `In Progress`, adjust the config rather
  than renaming in Linear (especially if the workspace is shared).

In `local.env`:

- Add `LINEAR_API_KEY=lin_api_...` (use shell exports if you prefer; env
  vars take precedence over `local.env`).
- Optionally add `BRAINTRUST_API_KEY=...` for tracing.

## 5. Provision Linear workflow states

If your Linear team's workflow doesn't already have the autosymph state
names, you'll need to create them by hand. The default v1 set:

| Slot | Default name | Linear state type | Suggested color |
|---|---|---|---|
| todo | Ready | unstarted | `#bec2c8` |
| active | Implementing | started | `#5e6ad2` |
| verifying | Verifying | started | `#f2c94c` |
| investigating | Investigating | started | `#f2994a` |
| review | In Review | started | `#3b82f6` |
| rework | Rework | started | `#eb5757` |
| gate_approved | Merging | started | `#0bc4ad` |
| blocked | Blocked | started | `#ff4444` |
| terminal | Done | completed | `#5e6ad2` |
| terminal | Canceled | canceled | `#bec2c8` |
| terminal | Duplicate | canceled | `#bec2c8` |

Or, if you'd rather use `In Progress` instead of `Implementing` (etc.),
keep your Linear workspace as-is and adjust the project YAML's
`linear_states.active` value to match.

## 6. Validate

```bash
uv run autosymph config check ~/.autosymph/config/projects/web-app.yaml
scripts/check-skills.sh
```

## 7. Run

```bash
uv run autosymph start
```

For a one-off non-layered config file:

```bash
uv run autosymph start -c path/to/combined.yaml
```

## See also

- [`docs/init.md`](init.md) — what `autosymph init` does, what it
  doesn't, and how to undo.
