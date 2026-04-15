#!/usr/bin/env bash
# 一次性跑满 10 个 domain 下全部 task × 双模型 × 双 framework。
set -euo pipefail

BASE=/home/chengzhi.ucsb/code/chengzhi/Agent_Safety/MASP_Bench
cd "$BASE" || exit 1

PY=/home/chengzhi.ucsb/.conda/envs/masp-bench/bin/python
LOG=/tmp/masp_batch.log
: > "$LOG"

MODELS=(gpt-5.4 gpt-5.1)
FRAMEWORKS=(openai_agents autogen)

# tasks/ 子目录顺序：稳定、可读
DOMAIN_DIRS=(
  daily_life
  ecommerce
  finance
  healthcare
  journalism_media
  legal_compliance
  office
  real_estate
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
    --output results/ \
    --trace-dir traces/ 2>&1 | tee -a "$LOG"
}

shopt -s nullglob
for domain_dir in "${DOMAIN_DIRS[@]}"; do
  yamls=(tasks/"${domain_dir}"/*.yaml)
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
