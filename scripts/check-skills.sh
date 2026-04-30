#!/bin/bash
# Validate packaged skills and optionally check installed targets for drift.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE_DIR="$ROOT/skills"
STRICT=0
TARGETS=()

usage() {
  cat <<'EOF'
Usage: scripts/check-skills.sh [options]

Options:
  --strict       Fail if target installs are missing or drifted.
  --target DIR   Check this install target for drift. Repeatable.
  -h, --help     Show this help.

Without --target, this validates the repo-local skills and reports default
install target status without failing a fresh clone.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --strict) STRICT=1; shift ;;
    --target)
      [[ $# -ge 2 ]] || { echo "check-skills: --target requires DIR" >&2; exit 2; }
      TARGETS+=("$2")
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "check-skills: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "check-skills: source directory not found: $SOURCE_DIR" >&2
  exit 1
fi

failed=0
count=0

echo "Checking repo-local skills in $SOURCE_DIR"
while IFS= read -r skill; do
  count=$((count + 1))
  name="$(basename "$skill")"
  if [[ ! -s "$skill/SKILL.md" ]]; then
    echo "  FAIL    $name missing SKILL.md"
    failed=1
    continue
  fi
  while IFS= read -r script; do
    if [[ ! -x "$script" ]]; then
      echo "  FAIL    $name script is not executable: ${script#$ROOT/}"
      failed=1
    fi
  done < <(find "$skill" -type f -name "*.sh" | sort)
  echo "  OK      $name"
done < <(find "$SOURCE_DIR" -mindepth 1 -maxdepth 1 -type d | sort)

if [[ "$count" -eq 0 ]]; then
  echo "  FAIL    no skills found"
  failed=1
fi

if [[ ${#TARGETS[@]} -eq 0 ]]; then
  TARGETS=(
    "${AUTOSYMPH_SKILLS_DIR:-$HOME/.autosymph/skills}"
    "${AGENTS_SKILLS_DIR:-$HOME/.agents/skills}"
    "${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
  )
fi

echo "Checking install targets"
for target in "${TARGETS[@]}"; do
  if [[ ! -d "$target" ]]; then
    echo "  INFO    $target not present"
    [[ "$STRICT" -eq 1 ]] && failed=1
    continue
  fi
  if "$ROOT/scripts/install-skills.sh" --check --target "$target"; then
    :
  else
    [[ "$STRICT" -eq 1 ]] && failed=1
  fi
done

if [[ "$failed" -eq 0 ]]; then
  echo "Skill check OK"
else
  echo "Skill check found issues" >&2
fi

exit "$failed"

