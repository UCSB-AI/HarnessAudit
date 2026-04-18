#!/usr/bin/env bash
# Smoke test: gpt-5.1 × 双 framework，除 sde 外每个 category 只跑第一个 task。
set -euo pipefail

BASE=/home/chengzhi.ucsb/code/chengzhi/Agent_Safety/MASP_Bench/masp_bench
cd "$BASE" || exit 1

PY=/home/chengzhi.ucsb/.conda/envs/masp-bench/bin/python
LOG=/tmp/masp_smoke.log
: > "$LOG"

MODEL=gpt-5.1
FRAMEWORKS=(openai_agents autogen)

DOMAIN_DIRS=(
  daily_life
  ecommerce
  finance
  healthcare
  legal_compliance
  office
  social_interaction
)

run_one() {
  local rel_yaml=$1
  local framework=$2
  echo "=== $(date -Iseconds) task=${rel_yaml} model=${MODEL} framework=${framework} ===" | tee -a "$LOG"
  "$PY" -m masp_bench run "$rel_yaml" \
    --model "$MODEL" \
    --framework "$framework" \
    --output "$BASE/results/" \
    --trace-dir "$BASE/traces/" 2>&1 | tee -a "$LOG"
}

shopt -s nullglob globstar
for domain_dir in "${DOMAIN_DIRS[@]}"; do
  declare -A seen_cat
  yamls=(tasks/"${domain_dir}"/**/*.yaml)
  IFS=$'\n' yamls=($(printf "%s\n" "${yamls[@]}" | sort))
  unset IFS

  for yaml in "${yamls[@]}"; do
    cat_dir=$(dirname "$yaml")
    cat_name=$(basename "$cat_dir")
    if [[ -z "${seen_cat[$cat_name]+x}" ]]; then
      seen_cat[$cat_name]=1
      rel=${yaml#./}
      for fw in "${FRAMEWORKS[@]}"; do
        run_one "$rel" "$fw"
      done
    fi
  done
  unset seen_cat
done

echo "SMOKE_DONE $(date -Iseconds)" | tee -a "$LOG"
