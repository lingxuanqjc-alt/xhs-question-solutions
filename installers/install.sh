#!/usr/bin/env sh
set -eu

force=0
destination_root=${HOME:?HOME is not set}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --force) force=1 ;;
    --root)
      shift
      [ "$#" -gt 0 ] || { echo "--root requires a path" >&2; exit 2; }
      destination_root=$1
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
source_dir="$repo_root/.agents/skills/xhs-question-solutions"
targets="$destination_root/.agents/skills/xhs-question-solutions
$destination_root/.claude/skills/xhs-question-solutions"

[ -d "$source_dir" ] || { echo "Source skill not found: $source_dir" >&2; exit 1; }

if [ "$force" -eq 0 ]; then
  printf '%s\n' "$targets" | while IFS= read -r target; do
    [ ! -e "$target" ] || { echo "Skill already exists at $target. Re-run with --force." >&2; exit 1; }
  done
fi

timestamp=$(date +%Y%m%d-%H%M%S)
printf '%s\n' "$targets" | while IFS= read -r target; do
  mkdir -p "$(dirname -- "$target")"
  if [ -e "$target" ]; then
    backup="$target.backup-$timestamp"
    mv -- "$target" "$backup"
    echo "Backed up existing skill to $backup"
  fi
  cp -R -- "$source_dir" "$target"
  echo "Installed skill to $target"
done
