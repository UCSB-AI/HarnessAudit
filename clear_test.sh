#!/usr/bin/env bash
# 清空10 个 domain 在 traces/、results/ 下的产物（jsonl / json / sqlite）。
BASE=/home/chengzhi.ucsb/code/chengzhi/Agent_Safety/MASP_Bench
cd "$BASE" || exit 1

# --- SDE ---
rm -f traces/sde*.jsonl
rm -f results/sde*.json results/sde*.sqlite

# --- Finance ---
rm -f traces/fin*.jsonl
rm -f results/fin*.json results/fin*.sqlite

# --- Ecommerce ---
rm -f traces/ec*.jsonl
rm -f results/ec*.json results/ec*.sqlite

# --- Office ---
rm -f traces/off*.jsonl
rm -f results/off*.json results/off*.sqlite

# --- Healthcare ---
rm -f traces/hc*.jsonl
rm -f results/hc*.json results/hc*.sqlite

# --- Journalism / media ---
rm -f traces/jm*.jsonl
rm -f results/jm*.json results/jm*.sqlite

# --- Daily life ---
rm -f traces/dl*.jsonl
rm -f results/dl*.json results/dl*.sqlite

# --- Legal / compliance ---
rm -f traces/lc*.jsonl
rm -f results/lc*.json results/lc*.sqlite

# --- Social interaction ---
rm -f traces/si*.jsonl
rm -f results/si*.json results/si*.sqlite

# --- Real estate ---
rm -f traces/re*.jsonl
rm -f results/re*.json results/re*.sqlite

# 可选：SDE 工作区与 fixture worktree 登记（取消注释即用）
[ -d workspaces ] && rm -rf workspaces/*
for f in fixtures/app-ecommerce fixtures/app-admin; do
  [ -d "$f/.git" ] && git -C "$f" worktree prune -v
done

echo "cleared traces/results for all 10 domains under $BASE"
