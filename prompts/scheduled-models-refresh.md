# Scheduled: refresh model registry + open PR if bumped

> **Deprecated — use the `autosymph-update-configs` skill instead.**
> The `autosymph-update-configs` skill, when installed,
> supersedes this prompt. It's multi-provider (not just Anthropic), enforces
> rock-solid sources structurally (provider enumeration API + callable round-trip),
> updates YAML configs in addition to the registry, and ships with a deterministic
> test (`test.sh`) that mocks provider HTTP. Invoke via `/autosymph-update-configs`.
>
> Keeping this file as a fallback for environments where skills aren't available.
> The remainder of this prompt is the legacy Anthropic-only flow.

---

You are a scheduled agent. Your one job: keep `src/autosymph/models.py`
in sync with Anthropic's `/v1/models` API. If a newer model has shipped in any
family, open a PR; otherwise exit silently.

## Inputs (from environment)

- `ANTHROPIC_API_KEY` — required for `autosymph models refresh`
- `GH_TOKEN` (or `gh auth status` already authenticated) — required for the PR
- Working directory: the autosymph repository root

## Procedure

1. **Sync.** From the repository root, `git fetch origin main && git checkout main && git pull --ff-only`.

2. **Dry-run first.** Run `uv run autosymph models refresh`.
   - Exit 0 with `OK — registry already current`: nothing to do. Stop here. Do not open a PR.
   - Exit 0 with `Registry bump available`: capture stdout — those bump lines go in the PR body.

3. **Apply.** `uv run autosymph models refresh --apply`. This rewrites `src/autosymph/models.py`.

4. **Verify.** `uv run pytest tests/test_models.py tests/test_cli_models.py`.
   - Tests must pass. If any fail, do **not** open a PR — abort and surface the failure.

5. **Branch + commit.** Branch name: `chore/bump-model-registry-YYYY-MM-DD`.
   - `git checkout -b chore/bump-model-registry-$(date +%Y-%m-%d)`
   - `git add src/autosymph/models.py`
   - Commit message:
     ```
     chore(autosymph): bump model registry to latest from /v1/models

     Refreshed by scheduled agent. Diff:
     <paste the bump lines from step 2>
     ```

6. **Push + PR.**
   - `git push -u origin <branch>`
   - `gh pr create --title "chore(autosymph): bump model registry" --body "<see template below>"`

## PR body template

```
## Summary
- Bumped Claude model registry to match `/v1/models` response.
- Old `LATEST` ids demoted into `KNOWN_STALE` so existing configs surface as
  stale on `autosymph models check`.

## Bumps
<paste the `family: old → new` lines from `models refresh`>

## Test plan
- [x] `uv run pytest tests/test_models.py tests/test_cli_models.py`
- [ ] Spot-check a workflow YAML with `autosymph models check` to confirm stale
      refs surface (or that nothing surfaces if all configs are current).

## Why this is auto-generated
The registry is the source of truth for "current" models. A scheduled agent
refreshes it weekly to avoid silent drift between Anthropic's release cadence
and what autosymph configs reference. Human review is the safety gate — this PR
does NOT auto-merge.
```

## Hard rules

- **Never auto-merge.** PR is the audit trail; humans approve.
- **Never `git push --force`.**
- **Never edit YAML configs in this run** — only `models.py`. Configs are
  bumped separately by humans (or by a follow-up PR after this one merges and
  `autosymph models check` starts failing in CI).
- **Never bypass `pytest`.** If tests fail, the API response is suspect (e.g.
  Anthropic shipped a malformed entry) — leave it for humans.
- **No-op cleanly.** If step 2 reports current, exit 0 with no side effects, no
  branch, no PR, no comment. Idempotent reruns must be free.
