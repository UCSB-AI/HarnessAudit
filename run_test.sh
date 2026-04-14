#!/usr/bin/env bash
# 一次性跑满 10 个 domain 下全部 task × 双模型（与 masp_bench/cli 写出字段一致）。
cd /home/chengzhi.ucsb/code/chengzhi/Agent_Safety/MASP_Bench || exit 1
set -o pipefail
set -e

PY=/home/chengzhi.ucsb/.conda/envs/masp-bench/bin/python
LOG=/tmp/masp_batch.log
: > "$LOG"

MODELS=(gpt-5.4-2026-03-05 gpt-5.1-2025-11-13)

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
  echo "=== $(date -Iseconds) $rel_yaml $model ===" | tee -a "$LOG"
  "$PY" -m masp_bench run "$rel_yaml" \
    --model "$model" \
    --output results/ \
    --trace-dir traces/ 2>&1 | tee -a "$LOG"
}

for domain_dir in "${DOMAIN_DIRS[@]}"; do
  mapfile -t yamls < <(find "tasks/${domain_dir}" -maxdepth 1 -name '*.yaml' -type f | sort)
  if [ "${#yamls[@]}" -eq 0 ]; then
    echo "skip (no yaml): tasks/${domain_dir}" | tee -a "$LOG"
    continue
  fi
  for yaml in "${yamls[@]}"; do
    rel=${yaml#./}
    for m in "${MODELS[@]}"; do
      run_one "$rel" "$m"
    done
  done
done

echo "ALL_DONE $(date -Iseconds)" | tee -a "$LOG"
