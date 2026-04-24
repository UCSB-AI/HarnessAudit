#!/usr/bin/env bash
# Sampled MASP regression runner for takeover/tool-set changes.
# Runs a small, representative subset of MASP tasks across all three harnesses.
set -euo pipefail

BASE=/home/chengzhi.ucsb/code/chengzhi/Agent_Safety/MASP_Bench/masp_bench
cd "$BASE" || exit 1

PY=/home/chengzhi.ucsb/.conda/envs/masp-bench/bin/python
LOG=/tmp/masp_sampled_regression.log
: > "$LOG"

TIMEOUT=${TIMEOUT:-360}

TASKS=(
  # tasks/daily_life/dining/dl-t1.yaml
  tasks/daily_life/housing/dl-t3.yaml
  tasks/finance/financial_planning/fin-t1.yaml
  tasks/legal_compliance/litigation/lc-t1.yaml
  tasks/office/finance/off-t4.yaml
  tasks/social_interaction/voice/si-t1.yaml
  tasks/sde/app_ecommerce/sde1-t3.yaml
  tasks/sde/app_admin/sde2-t3.yaml
)

HARNESSES=(claude codex openclaw)

run_one() {
  local rel_yaml=$1
  local harness=$2
  local model=""

  case "$harness" in
    claude) model="claude-sonnet-4-6" ;;
    codex) model="gpt-5.4" ;;
    openclaw) model="gpt-5.4" ;;
    *)
      echo "Unknown harness: $harness" >&2
      return 1
      ;;
  esac

  echo "--- $(date -Iseconds) task=${rel_yaml} harness=${harness} model=${model} ---" | tee -a "$LOG"
  "$PY" -m masp_bench run "$rel_yaml"     --framework clawteam     --harness "$harness"     --model "$model"     --agent-timeout "$TIMEOUT"     --output "$BASE/results/"     --trace-dir "$BASE/traces/" 2>&1 | tee -a "$LOG"
}

echo "[$(date -Iseconds)] starting sampled MASP regression" | tee -a "$LOG"
echo "[$(date -Iseconds)] tasks=${#TASKS[@]} harnesses=${#HARNESSES[@]} total_runs=$((${#TASKS[@]} * ${#HARNESSES[@]})) timeout=${TIMEOUT}" | tee -a "$LOG"

for rel_yaml in "${TASKS[@]}"; do
  for harness in "${HARNESSES[@]}"; do
    run_one "$rel_yaml" "$harness"
  done
done

echo "SAMPLED_MASP_REGRESSION_DONE $(date -Iseconds)" | tee -a "$LOG"
