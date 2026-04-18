#!/usr/bin/env bash
# Smoke test: single-agent tasks × gpt-5.1 × 双 framework
set -euo pipefail

BASE=/home/chengzhi.ucsb/code/chengzhi/Agent_Safety/MASP_Bench/sasp_bench
cd "$BASE" || exit 1

PY=/home/chengzhi.ucsb/.conda/envs/masp-bench/bin/python
LOG=/tmp/sasp_smoke.log
: > "$LOG"

MODEL=gpt-5.1
FRAMEWORKS=(openai_agents)

run_one() {
  local rel_yaml=$1
  local framework=$2
  echo "=== $(date -Iseconds) task=${rel_yaml} model=${MODEL} framework=${framework} ===" | tee -a "$LOG"
  "$PY" -m sasp_bench run "$rel_yaml" \
    --model "$MODEL" \
    --framework "$framework" \
    --output "$BASE/results/" \
    --trace-dir "$BASE/traces/" 2>&1 | tee -a "$LOG"
}

shopt -s nullglob globstar
for yaml in tasks/**/*.yaml; do
  rel=${yaml#./}
  for fw in "${FRAMEWORKS[@]}"; do
    run_one "$rel" "$fw"
  done
done

echo "SA_SMOKE_DONE $(date -Iseconds)" | tee -a "$LOG"
