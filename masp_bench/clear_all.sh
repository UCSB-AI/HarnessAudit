#!/usr/bin/env bash
# Clear MASP-Bench trace/result artifacts regardless of harness/model naming.
set -euo pipefail

BASE=/home/chengzhi.ucsb/code/chengzhi/Agent_Safety/MASP_Bench/masp_bench
TRACE_DIR="$BASE/traces"
RESULTS_DIR="$BASE/results"
cd "$BASE" || exit 1

count_files() {
  local dir=$1
  local pattern=$2
  if [ ! -d "$dir" ]; then
    echo 0
    return
  fi
  find "$dir" -maxdepth 1 -type f -name "$pattern" | wc -l | tr -d ' '
}

delete_files() {
  local dir=$1
  local pattern=$2
  [ -d "$dir" ] || return 0
  find "$dir" -maxdepth 1 -type f -name "$pattern" -delete
}

trace_count=$(count_files "$TRACE_DIR" '*.jsonl')
result_json_count=$(count_files "$RESULTS_DIR" '*.json')
result_sqlite_count=$(count_files "$RESULTS_DIR" '*.sqlite')

delete_files "$TRACE_DIR" '*.jsonl'
delete_files "$RESULTS_DIR" '*.json'
delete_files "$RESULTS_DIR" '*.sqlite'

[ -d workspaces ] && rm -rf workspaces/*

REPO=$(dirname "$BASE")
for f in "$REPO/fixtures/app-ecommerce" "$REPO/fixtures/app-admin"; do
  [ -d "$f/.git" ] && git -C "$f" worktree prune -v
done

echo "cleared ${trace_count} trace jsonl, ${result_json_count} result json, and ${result_sqlite_count} result sqlite files under $BASE"
