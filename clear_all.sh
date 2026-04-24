#!/usr/bin/env bash
# Clear benchmark-generated trace/result files under both MASP and SASP.
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DIRS=(
  "$BASE/masp_bench/traces"
  "$BASE/masp_bench/results"
  "$BASE/sasp_bench/traces"
  "$BASE/sasp_bench/results"
)

count_removed=0

for dir in "${DIRS[@]}"; do
  if [[ ! -d "$dir" ]]; then
    echo "skip missing $dir"
    continue
  fi

  removed=$(find "$dir" -maxdepth 1 -type f | wc -l | tr -d ' ')
  find "$dir" -maxdepth 1 -type f -delete
  count_removed=$((count_removed + removed))
  echo "cleared ${removed} files from ${dir#$BASE/}"
done

echo "cleared ${count_removed} files in total"
