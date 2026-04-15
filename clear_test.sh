#!/usr/bin/env bash
# 清空 10 个 domain 在 traces/、results/ 下，
# gpt-5.4 / gpt-5.1 × openai_agents / autogen 的产物（jsonl / json / sqlite）。
set -euo pipefail

BASE=/home/chengzhi.ucsb/code/chengzhi/Agent_Safety/MASP_Bench
cd "$BASE" || exit 1

TASK_PREFIXES=(sde fin ec off hc jm dl lc si re)
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

# 可选：SDE 工作区与 fixture worktree 登记（取消注释即用）
[ -d workspaces ] && rm -rf workspaces/*
for f in fixtures/app-ecommerce fixtures/app-admin; do
  [ -d "$f/.git" ] && git -C "$f" worktree prune -v
done

echo "cleared traces/results for 10 domains (gpt-5.4/gpt-5.1 × oai/ag) under $BASE"
