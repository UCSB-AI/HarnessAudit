#!/usr/bin/env bash
# Smoke test: one task per category (excluding sde) × 3 harnesses.
# Coverage: 22 categories × 3 harnesses = 66 runs.
set -euo pipefail

BASE=/home/chengzhi.ucsb/code/chengzhi/Agent_Safety/MASP_Bench/masp_bench
cd "$BASE" || exit 1

PY=/home/chengzhi.ucsb/.conda/envs/masp-bench/bin/python
LOG=/tmp/masp_smoke.log
: > "$LOG"

TIMEOUT=360

DOMAIN_DIRS=(
  daily_life
  ecommerce
  finance
  healthcare
  legal_compliance
  office
  social_interaction
)

run_task() {
  local rel_yaml=$1

  echo "--- $(date -Iseconds) task=${rel_yaml} harness=claude model=claude-sonnet-4-6 ---" | tee -a "$LOG"
  "$PY" -m masp_bench run "$rel_yaml" \
    --framework clawteam \
    --harness claude \
    --model claude-sonnet-4-6 \
    --agent-timeout "$TIMEOUT" \
    --output "$BASE/results/" \
    --trace-dir "$BASE/traces/" 2>&1 | tee -a "$LOG"

  echo "--- $(date -Iseconds) task=${rel_yaml} harness=codex model=gpt-5.4 ---" | tee -a "$LOG"
  "$PY" -m masp_bench run "$rel_yaml" \
    --framework clawteam \
    --harness codex \
    --model gpt-5.4 \
    --agent-timeout "$TIMEOUT" \
    --output "$BASE/results/" \
    --trace-dir "$BASE/traces/" 2>&1 | tee -a "$LOG"

  echo "--- $(date -Iseconds) task=${rel_yaml} harness=openclaw model=gpt-5.4 ---" | tee -a "$LOG"
  "$PY" -m masp_bench run "$rel_yaml" \
    --framework clawteam \
    --harness openclaw \
    --model gpt-5.4 \
    --agent-timeout "$TIMEOUT" \
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
      run_task "$rel"
    fi
  done
  unset seen_cat
done

echo "SMOKE_DONE $(date -Iseconds)" | tee -a "$LOG"
