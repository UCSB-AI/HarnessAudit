#!/usr/bin/env bash
# 一次性跑满 8 个 domain 下全部 task × 双模型 × 双 framework。
set -euo pipefail

BASE=/home/chengzhi.ucsb/code/chengzhi/Agent_Safety/MASP_Bench/masp_bench
cd "$BASE" || exit 1

PY=/home/chengzhi.ucsb/.conda/envs/masp-bench/bin/python
LOG=/tmp/masp_batch.log
: > "$LOG"

MODELS=(claude-sonnet-4)
FRAMEWORKS=(clawteam)

DOMAIN_DIRS=(
  daily_life
  ecommerce
  finance
  healthcare
  legal_compliance
  office
  sde
  social_interaction
)

run_one() {
  local rel_yaml=$1
  local model=$2
  local framework=$3
  echo "=== $(date -Iseconds) task=${rel_yaml} model=${model} framework=${framework} ===" | tee -a "$LOG"
  "$PY" -m masp_bench run "$rel_yaml" \
    --model "$model" \
    --framework "$framework" \
    --output "$BASE/results/" \
    --trace-dir "$BASE/traces/" 2>&1 | tee -a "$LOG"
}

shopt -s nullglob globstar
for domain_dir in "${DOMAIN_DIRS[@]}"; do
  yamls=(tasks/"${domain_dir}"/**/*.yaml)
  IFS=$'\n' yamls=($(printf "%s\n" "${yamls[@]}" | sort))
  unset IFS
  if [ "${#yamls[@]}" -eq 0 ]; then
    echo "skip (no yaml): tasks/${domain_dir}" | tee -a "$LOG"
    continue
  fi
  for yaml in "${yamls[@]}"; do
    rel=${yaml#./}
    for m in "${MODELS[@]}"; do
      for fw in "${FRAMEWORKS[@]}"; do
        run_one "$rel" "$m" "$fw"
      done
    done
  done
done

echo "ALL_DONE $(date -Iseconds)" | tee -a "$LOG"
