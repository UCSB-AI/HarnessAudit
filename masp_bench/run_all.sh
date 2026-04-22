#!/usr/bin/env bash
# Run all MASP tasks across the configured frameworks/harnesses.
set -euo pipefail

BASE=/home/chengzhi.ucsb/code/chengzhi/Agent_Safety/MASP_Bench/masp_bench
cd "$BASE" || exit 1

PY=/home/chengzhi.ucsb/.conda/envs/masp-bench/bin/python
LOG=/tmp/masp_batch.log
: > "$LOG"

REQUESTED_MODEL=${REQUESTED_MODEL:-claude-sonnet-4}
FRAMEWORKS=(clawteam)
HARNESSES=(claude codex openclaw)

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

mkdir -p "$BASE/traces" "$BASE/results"

if [ "${CLEAR_OUTPUTS_BEFORE_RUN:-0}" = "1" ]; then
  "$BASE/clear_all.sh"
fi

run_one() {
  local rel_yaml=$1
  local framework=$2
  local harness=${3:-}
  echo "=== $(date -Iseconds) task=${rel_yaml} framework=${framework} harness=${harness:-n/a} requested_model=${REQUESTED_MODEL} ===" | tee -a "$LOG"
  if [ -n "$harness" ]; then
    "$PY" -m masp_bench run "$rel_yaml" \
      --model "$REQUESTED_MODEL" \
      --framework "$framework" \
      --harness "$harness" \
      --output "$BASE/results/" \
      --trace-dir "$BASE/traces/" 2>&1 | tee -a "$LOG"
  else
    "$PY" -m masp_bench run "$rel_yaml" \
      --model "$REQUESTED_MODEL" \
      --framework "$framework" \
      --output "$BASE/results/" \
      --trace-dir "$BASE/traces/" 2>&1 | tee -a "$LOG"
  fi
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
    for fw in "${FRAMEWORKS[@]}"; do
      if [ "$fw" = "clawteam" ]; then
        for harness in "${HARNESSES[@]}"; do
          run_one "$rel" "$fw" "$harness"
        done
      else
        run_one "$rel" "$fw"
      fi
    done
  done
done

echo "ALL_DONE $(date -Iseconds)" | tee -a "$LOG"
