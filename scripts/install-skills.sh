#!/bin/bash
# Install repo-local autosymph skills into skill discovery directories.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE_DIR="$ROOT/skills"
MODE="symlink"
DRY_RUN=0
CHECK_ONLY=0
FORCE=0
TARGETS=()

usage() {
  cat <<'EOF'
Usage: scripts/install-skills.sh [options]

Options:
  --dry-run          Print actions without writing.
  --copy             Copy skill directories instead of symlinking.
  --check            Check installed skills for missing/drifted entries.
  --force            Replace existing skill entries when installing.
  --target DIR       Install/check this target directory. Repeatable.
  -h, --help         Show this help.

Default targets:
  ${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}
  ${AGENTS_SKILLS_DIR:-$HOME/.agents/skills}
  ${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --copy) MODE="copy"; shift ;;
    --check) CHECK_ONLY=1; shift ;;
    --force) FORCE=1; shift ;;
    --target)
      [[ $# -ge 2 ]] || { echo "install-skills: --target requires DIR" >&2; exit 2; }
      TARGETS+=("$2")
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "install-skills: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "install-skills: source directory not found: $SOURCE_DIR" >&2
  exit 1
fi

if [[ ${#TARGETS[@]} -eq 0 ]]; then
  TARGETS=(
    "${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}"
    "${AGENTS_SKILLS_DIR:-$HOME/.agents/skills}"
    "${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
  )
fi

skill_names() {
  find "$SOURCE_DIR" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; | sort
}

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf 'DRY-RUN:'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

same_tree() {
  local src="$1" dst="$2"
  if [[ -L "$dst" ]]; then
    [[ "$(cd "$(dirname "$dst")" && pwd -P)/$(readlink "$dst")" == "$src" ]] && return 0
    [[ "$(cd "$dst" 2>/dev/null && pwd -P)" == "$src" ]] && return 0
    return 1
  fi
  [[ -d "$dst" ]] || return 1
  diff -qr "$src" "$dst" >/dev/null
}

check_target() {
  local target="$1"
  local failed=0
  echo "Checking $target"
  while IFS= read -r name; do
    local src="$SOURCE_DIR/$name"
    local dst="$target/$name"
    if [[ ! -e "$dst" && ! -L "$dst" ]]; then
      echo "  MISSING $name"
      failed=1
    elif same_tree "$src" "$dst"; then
      echo "  OK      $name"
    else
      echo "  DRIFT   $name"
      failed=1
    fi
  done < <(skill_names)
  return "$failed"
}

install_target() {
  local target="$1"
  echo "Installing skills to $target ($MODE)"
  run mkdir -p "$target"

  while IFS= read -r name; do
    local src="$SOURCE_DIR/$name"
    local dst="$target/$name"

    if [[ -e "$dst" || -L "$dst" ]]; then
      if same_tree "$src" "$dst"; then
        echo "  OK      $name"
        continue
      fi
      if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "  WOULD-CONFLICT $name (use --force to replace)"
        continue
      fi
      if [[ "$FORCE" -ne 1 ]]; then
        echo "  EXISTS  $name (use --force to replace)" >&2
        return 1
      fi
      run rm -rf "$dst"
    fi

    if [[ "$MODE" == "copy" ]]; then
      run cp -R "$src" "$dst"
    else
      run ln -s "$src" "$dst"
    fi
    echo "  INSTALLED $name"
  done < <(skill_names)
}

failed=0
for target in "${TARGETS[@]}"; do
  if [[ "$CHECK_ONLY" -eq 1 ]]; then
    check_target "$target" || failed=1
  else
    install_target "$target" || failed=1
  fi
done

exit "$failed"
