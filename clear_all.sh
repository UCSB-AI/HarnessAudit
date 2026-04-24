#!/usr/bin/env bash
# Clear benchmark-generated traces/results/workspaces and smoke logs.
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -d "$BASE/masp_bench" || ! -d "$BASE/sasp_bench" ]]; then
  echo "refusing to clear: expected MASP_Bench repo root, got $BASE" >&2
  exit 1
fi

DIRS=(
  "$BASE/masp_bench/traces"
  "$BASE/masp_bench/results"
  "$BASE/masp_bench/workspaces"
  "$BASE/sasp_bench/traces"
  "$BASE/sasp_bench/results"
  "$BASE/workspaces"
)

LOGS=(
  "$BASE/masp_bench/smoke_all.log"
  "$BASE/masp_bench/smoke.log"
  "$BASE/sasp_bench/smoke.log"
)

count_removed=0

clear_dir_contents() {
  local dir=$1
  if [[ ! -d "$dir" ]]; then
    echo "skip missing $dir"
    return
  fi

  local removed
  removed=$(find "$dir" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')
  find "$dir" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
  echo "cleared ${removed} entries from ${dir#$BASE/}"
  count_removed=$((count_removed + removed))
}

clear_file() {
  local file=$1
  if [[ ! -e "$file" ]]; then
    echo "skip missing $file"
    return
  fi

  rm -f -- "$file"
  echo "removed $file"
  count_removed=$((count_removed + 1))
}

for dir in "${DIRS[@]}"; do
  clear_dir_contents "$dir"
done

for log_file in "${LOGS[@]}"; do
  clear_file "$log_file"
done

echo "cleared ${count_removed} entries in total"
