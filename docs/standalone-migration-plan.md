# Autosymph Standalone Migration Plan

This plan migrates autosymph into this repository as a standalone,
reproducible package. Each phase should end with a
commit and a manual verification checkpoint before continuing.

## Principles

- Keep `autosymph/skills` as the canonical source for autosymph runtime skills.
- Treat agent-specific skill discovery paths as compatibility install targets,
  not source of truth.
- Keep active machine/project config out of the package. Ship examples instead.
- Remove assumptions about local absolute paths and personal
  project names from runtime docs, prompts, and tests.
- Preserve behavior first, then make paths portable.

## Phase 1: Copy Core Repo

Copy the current autosymph package and baseline project files into this
repository.

Expected files and directories:

- `src/autosymph/`
- `tests/`
- `scripts/`
- `prompts/`
- general `docs/*.md`
- `pyproject.toml`
- `uv.lock`
- `.gitignore`
- `README.md`
- `AGENTS.md`
- `CHANGELOG.md`

Do not copy generated or local-only files:

- `.venv/`
- `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`
- `__pycache__/`
- local agent settings files
- `docs/postmortems/`, unless a specific entry is rewritten into generic
  standalone documentation

Optional archive material:

- `PRD.md`
- `PRD-imp310-imp306.md`
- `workflow.yaml`

If kept, these should move to `docs/archive/` or `examples/`, not remain as
active standalone defaults.

Verification:

```bash
uv sync --extra dev
uv run pytest
git status --short
```

Commit:

```bash
git add -A
git commit -m "import autosymph core"
```

## Phase 2: Vendor Autosymph Skills

Add repo-local copies of autosymph runtime skills.

Expected layout:

```text
skills/
  autoplan/
  context-management/
  record-demo-video/
  screenshot-to-linear/
  structify/
  autosymph-monitor/
  verify-completion-audit/
  verify-finalize/
  verify-preflight/
```

Source today:

```text
<source-agent-config>/.agents/skills/{skill-name}/
```

Verification:

```bash
skills/autosymph-monitor/test.sh
skills/verify-preflight/test.sh
skills/verify-finalize/test.sh
skills/verify-completion-audit/test.sh
```

Commit:

```bash
git add -A
git commit -m "vendor autosymph runtime skills"
```

## Phase 3: Add Example Configs

Package templates from the active autosymph config directory as examples.

Expected layout:

```text
examples/
  config/
    devices/hostname.yaml.example
    projects/ios-project.yaml.example
    projects/web-project.yaml.example
    local.env.example
  project/
    .autosymph/verify/auth.md
    .autosymph/verify/ios.md
    .autosymph/verify/web.md
    .autosymph/verify/fixtures.md
```

Rewrite examples to remove:

- `/Users/example/...`
- user-local absolute paths
- personal project names
- personal secret names

Verification:

```bash
uv run autosymph config check examples/config/projects/ios-project.yaml.example
uv run autosymph config check examples/config/projects/web-project.yaml.example
```

Commit:

```bash
git add -A
git commit -m "add standalone config examples"
```

## Phase 4: Remove Local-Layout Assumptions

Make runtime paths portable across standalone checkouts.

Primary targets:

- `README.md`
- `AGENTS.md`
- `prompts/scheduled-models-refresh.md`
- `prompts/investigating.md`
- `prompts/verify.md`
- `prompts/verify-review.md`
- `prompts/test-verify-video.md`

Preferred skill path pattern:

```bash
${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}/verify-preflight/scripts/preflight.sh
```

This supports repo-local skills via:

```bash
export AUTOSYMPH_SKILLS_DIR=/path/to/autosymph/skills
```

and lets installer scripts mirror the skills into each agent's discovery path.

Verification:

```bash
rg "/Users/|personal-project-name" README.md AGENTS.md prompts tests src examples
uv run pytest
```

Commit:

```bash
git add -A
git commit -m "make autosymph standalone-path portable"
```

## Phase 5: Add Skill Install And Drift Checks

Add scripts that install repo-local skills into agent-specific discovery paths.

Expected files:

```text
scripts/install-skills.sh
scripts/check-skills.sh
```

Install behavior:

- Canonical source: `./skills`
- Default: symlink into supported agent skill discovery directories
- Optional `--copy`: copy skills for environments where symlinks are undesirable
- Optional `--check`: detect missing or drifted installs

Verification:

```bash
scripts/install-skills.sh --dry-run
scripts/check-skills.sh
```

Commit:

```bash
git add -A
git commit -m "add skill install and drift check scripts"
```

## Phase 6: Test Cleanup

Fix tests that currently assume a local repository layout or personal paths.

Primary targets:

- `tests/test_verify_prompt_sync.py`
- `tests/test_orchestrator.py`
- any test containing personal absolute paths or sibling
  `autosymph-config`

Tests should validate packaged examples and repo-local prompts instead of
reaching outside this repository.

Verification:

```bash
uv run pytest
uv run ruff check src tests
```

Commit:

```bash
git add -A
git commit -m "update tests for standalone layout"
```

## Phase 7: Documentation Pass

Make onboarding complete for a fresh checkout.

README should explain:

- How to install dependencies
- How to create config from `examples/config`
- How to install or point to skills
- How to run autosymph
- How to run the autosymph monitor
- Where project-specific `.autosymph/verify/*.md` files belong

Verification:

```bash
uv sync --extra dev
scripts/install-skills.sh --dry-run
uv run pytest
```

Manual verification: follow README setup instructions from a clean clone or a
fresh worktree.

Commit:

```bash
git add -A
git commit -m "document standalone autosymph setup"
```
