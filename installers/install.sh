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

[ -n "$destination_root" ] || { echo "Destination root cannot be empty." >&2; exit 2; }

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
core_source="$repo_root/.agents/skills/xhs-question-solutions"
wrapper_source="$repo_root/.claude/skills/xhs-question-solutions"
core_target="$destination_root/.agents/skills/xhs-question-solutions"
wrapper_target="$destination_root/.claude/skills/xhs-question-solutions"

[ -d "$core_source" ] || { echo "Source skill not found: $core_source" >&2; exit 1; }
[ -d "$wrapper_source" ] || { echo "Source skill not found: $wrapper_source" >&2; exit 1; }

if [ "$force" -eq 0 ]; then
  [ ! -e "$core_target" ] || { echo "Skill already exists at $core_target. Re-run with --force." >&2; exit 1; }
  [ ! -e "$wrapper_target" ] || { echo "Skill already exists at $wrapper_target. Re-run with --force." >&2; exit 1; }
fi

timestamp=$(date +%Y%m%d-%H%M%S)
transaction_id="$timestamp-$$"
core_stage="$core_target.installing-$transaction_id"
wrapper_stage="$wrapper_target.installing-$transaction_id"
core_backup="$core_target.backup-$timestamp"
wrapper_backup="$wrapper_target.backup-$timestamp"
core_backed_up=0
wrapper_backed_up=0
core_installed=0
wrapper_installed=0
core_archive="$core_stage.payload.tar"
wrapper_archive="$wrapper_stage.payload.tar"

copy_install_payload() {
  source_path=$1
  destination_path=$2
  archive_path=$3
  mkdir -p -- "$destination_path"
  (
    cd -- "$source_path"
    tar -cf "$archive_path" \
      --exclude='./node_modules' --exclude='*/node_modules' \
      --exclude='./build' --exclude='*/build' \
      --exclude='./.cache' --exclude='*/.cache' \
      --exclude='./.tmp' --exclude='*/.tmp' \
      --exclude='./__pycache__' --exclude='*/__pycache__' \
      --exclude='./.pytest_cache' --exclude='*/.pytest_cache' \
      --exclude='./.remotion' --exclude='*/.remotion' \
      .
  )
  (
    cd -- "$destination_path"
    tar -xf "$archive_path"
  )
  rm -f -- "$archive_path"
}

cleanup_stages() {
  rm -rf -- "$core_stage" "$wrapper_stage"
  rm -f -- "$core_archive" "$wrapper_archive"
}
trap cleanup_stages EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [ -e "$core_target" ] && [ -e "$core_backup" ]; then
  echo "Backup path already exists: $core_backup" >&2
  exit 1
fi
if [ -e "$wrapper_target" ] && [ -e "$wrapper_backup" ]; then
  echo "Backup path already exists: $wrapper_backup" >&2
  exit 1
fi

# Prepare both payloads before changing either installed copy.
mkdir -p -- "$(dirname -- "$core_target")" "$(dirname -- "$wrapper_target")"
copy_install_payload "$core_source" "$core_stage" "$core_archive"
copy_install_payload "$wrapper_source" "$wrapper_stage" "$wrapper_archive"

rollback() {
  set +e
  rollback_failed=0
  if [ "$wrapper_installed" -eq 1 ]; then
    rm -rf -- "$wrapper_target" || rollback_failed=1
  fi
  if [ "$wrapper_backed_up" -eq 1 ]; then
    mv -- "$wrapper_backup" "$wrapper_target" || rollback_failed=1
  fi
  if [ "$core_installed" -eq 1 ]; then
    rm -rf -- "$core_target" || rollback_failed=1
  fi
  if [ "$core_backed_up" -eq 1 ]; then
    mv -- "$core_backup" "$core_target" || rollback_failed=1
  fi
  if [ "$rollback_failed" -ne 0 ]; then
    echo "Rollback failed; inspect the destination and backup paths." >&2
  fi
}

fail_install() {
  echo "$1" >&2
  rollback
  exit 1
}

if [ -e "$core_target" ]; then
  mv -- "$core_target" "$core_backup" || fail_install "Failed to back up $core_target"
  core_backed_up=1
fi
mv -- "$core_stage" "$core_target" || fail_install "Failed to install $core_target"
core_installed=1

if [ -e "$wrapper_target" ]; then
  mv -- "$wrapper_target" "$wrapper_backup" || fail_install "Failed to back up $wrapper_target"
  wrapper_backed_up=1
fi
mv -- "$wrapper_stage" "$wrapper_target" || fail_install "Failed to install $wrapper_target"
wrapper_installed=1

if [ "$core_backed_up" -eq 1 ]; then
  echo "Backed up existing skill to $core_backup"
fi
echo "Installed skill to $core_target"
if [ "$wrapper_backed_up" -eq 1 ]; then
  echo "Backed up existing skill to $wrapper_backup"
fi
echo "Installed skill to $wrapper_target"
