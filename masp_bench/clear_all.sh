#!/usr/bin/env bash
# 清空 8 个 domain 在 traces/、results/ 下的产物。
set -euo pipefail

BASE=/home/chengzhi.ucsb/code/chengzhi/Agent_Safety/MASP_Bench/masp_bench
cd "$BASE" || exit 1

TASK_PREFIXES=(sde fin ec off hc dl lc si)
FRAMEWORK_SLUGS=(oai ag)
MODELS=(gpt-5.4 gpt-5.1)

for prefix in "${TASK_PREFIXES[@]}"; do
  for fw in "${FRAMEWORK_SLUGS[@]}"; do
    for model in "${MODELS[@]}"; do
      rm -f traces/"${prefix}"*_"${fw}-${model}-"*.jsonl
      rm -f results/"${prefix}"*_"${fw}-${model}-"*.json
      rm -f results/"${prefix}"*_"${fw}-${model}-"*.sqlite
    done
  done
done

[ -d workspaces ] && rm -rf workspaces/*

REPO=$(dirname "$BASE")
for f in "$REPO/fixtures/app-ecommerce" "$REPO/fixtures/app-admin"; do
  [ -d "$f/.git" ] && git -C "$f" worktree prune -v
done

echo "cleared traces/results for 8 domains under $BASE"
