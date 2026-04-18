#!/usr/bin/env bash
# Clear SA traces/results
set -euo pipefail

BASE=/home/chengzhi.ucsb/code/chengzhi/Agent_Safety/MASP_Bench/sasp_bench
cd "$BASE" || exit 1

rm -f traces/sa-*.jsonl
rm -f results/sa-*.json
rm -f results/sa-*.sqlite

echo "cleared traces/ and results/ under $BASE"
