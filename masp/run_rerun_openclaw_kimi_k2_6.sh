#!/usr/bin/env bash
# Auto-generated from user-provided task lists.
# Usage examples:
#   nohup ./masp/run_rerun_openclaw_kimi_k2_6.sh &
#   RUN_ALL_WORKERS=4 ./masp/run_rerun_openclaw_kimi_k2_6.sh
#   SKIP_JUDGE=1 ./masp/run_rerun_openclaw_kimi_k2_6.sh
#   FORCE_RERUN=0 ./masp/run_rerun_openclaw_kimi_k2_6.sh
set -uo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$PKG_ROOT/.." && pwd)"
cd "$REPO_ROOT" || exit 1

PY="${HARNESSAUDIT_BENCH_PYTHON:-/home/cliu322/anaconda3/envs/masp/bin/python}"
RUN_TS="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="$PKG_ROOT/logs"
LOG="$LOG_DIR/rerun_kimi_k2_6_${RUN_TS}.log"

TIMEOUT="${AGENT_TIMEOUT:-360}"
HARNESS="openclaw"
HARNESS_MODEL="kimi-k2.6"
JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.4}"
RUN_ALL_WORKERS="${RUN_ALL_WORKERS:-2}"
JUDGE_WORKERS="${JUDGE_WORKERS:-2}"
SKIP_JUDGE="${SKIP_JUDGE:-0}"
FORCE_RERUN="${FORCE_RERUN:-1}"
RESULTS_DIR="$PKG_ROOT/results"
TRACES_DIR="$PKG_ROOT/traces"
MODEL_DIR="${HARNESS_MODEL//\//__}"
HARNESS_RESULTS_DIR="$RESULTS_DIR/$HARNESS/$MODEL_DIR"
HARNESS_TRACES_DIR="$TRACES_DIR/$HARNESS/$MODEL_DIR"
STATUS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/masp-rerun-status.XXXXXX")"
TASK_QUEUE=()

if ! [[ "$RUN_ALL_WORKERS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: RUN_ALL_WORKERS must be a positive integer, got: $RUN_ALL_WORKERS" >&2
  exit 2
fi

mkdir -p "$HARNESS_RESULTS_DIR" "$HARNESS_TRACES_DIR" "$LOG_DIR"

exec > >(tee -a "$LOG") 2>&1

cleanup() {
  rm -rf "$STATUS_DIR"
}
trap cleanup EXIT

JUDGE_ARGS=(--judge-model "$JUDGE_MODEL" --judge-workers "$JUDGE_WORKERS")
case "${SKIP_JUDGE,,}" in
  1|true|yes|y) JUDGE_ARGS+=(--skip-judge) ;;
esac

echo "========================================"
echo "HarnessAudit-Bench Re-Run (Kimi) — $(date -Iseconds)"
echo "  Harness:       $HARNESS"
echo "  Model:         $HARNESS_MODEL"
echo "  Judge:         $([ "$SKIP_JUDGE" = "0" ] && echo "ON ($JUDGE_MODEL)" || echo "OFF")"
echo "  Task workers:  $RUN_ALL_WORKERS"
echo "  Judge workers: $JUDGE_WORKERS"
echo "  Timeout:       ${TIMEOUT}s"
echo "  Force rerun:   $FORCE_RERUN"
echo "  Results:       $HARNESS_RESULTS_DIR"
echo "  Traces:        $HARNESS_TRACES_DIR"
echo "  Log:           $LOG"
echo "========================================"

run_task() {
  TASK_QUEUE+=("$1")
}

run_one_task() {
  local rel_yaml=$1
  local task_id
  task_id=$(basename "$rel_yaml" .yaml)
  local status_file="$STATUS_DIR/${task_id}.status"

  if [[ "${FORCE_RERUN,,}" =~ ^(1|true|yes|y)$ ]]; then
    rm -f "${HARNESS_RESULTS_DIR}/${task_id}_"*.json 2>/dev/null || true
    rm -f "${HARNESS_TRACES_DIR}/${task_id}_"*.jsonl 2>/dev/null || true
  else
    if compgen -G "${HARNESS_RESULTS_DIR}/${task_id}_*.json" > /dev/null 2>&1; then
      echo "SKIP $(date -Iseconds) task=${rel_yaml} (result exists)"
      echo skipped > "$status_file"
      return 0
    fi
    rm -f "${HARNESS_TRACES_DIR}/${task_id}_"*.jsonl 2>/dev/null || true
  fi

  echo "--- $(date -Iseconds) task=${rel_yaml} harness=${HARNESS} model=${HARNESS_MODEL} ---"
  "$PY" -m masp run "$rel_yaml"     --framework clawteam     --harness "$HARNESS"     --model "$HARNESS_MODEL"     --agent-timeout "$TIMEOUT"     --output "$RESULTS_DIR/"     --trace-dir "$TRACES_DIR/"     "${JUDGE_ARGS[@]}" 2>&1
  local exit_code=$?
  if [[ "$exit_code" -eq 0 ]]; then
    echo "PASS $(date -Iseconds) task=${rel_yaml}"
    echo passed > "$status_file"
  else
    echo "FAIL $(date -Iseconds) task=${rel_yaml} exit=${exit_code}"
    echo failed > "$status_file"
  fi
  return "$exit_code"
}

run_task_queue() {
  local active=0
  local rel_yaml
  for rel_yaml in "${TASK_QUEUE[@]}"; do
    run_one_task "$rel_yaml" &
    active=$((active + 1))
    if (( active >= RUN_ALL_WORKERS )); then
      wait -n || true
      active=$((active - 1))
    fi
  done
  wait || true
}

count_status() {
  local status=$1
  find "$STATUS_DIR" -type f -name '*.status' -exec grep -lx "$status" {} + 2>/dev/null | wc -l
}

# Task queue (154 tasks)
run_task "masp/tasks/daily_life/dining/dl-t11.yaml"
run_task "masp/tasks/daily_life/travel/dl-t12.yaml"
run_task "masp/tasks/daily_life/housing/dl-t13.yaml"
run_task "masp/tasks/daily_life/housing/dl-t15.yaml"
run_task "masp/tasks/daily_life/travel/dl-t16.yaml"
run_task "masp/tasks/daily_life/wellness/dl-t17.yaml"
run_task "masp/tasks/daily_life/dining/dl-t18.yaml"
run_task "masp/tasks/daily_life/travel/dl-t19.yaml"
run_task "masp/tasks/daily_life/travel/dl-t2.yaml"
run_task "masp/tasks/daily_life/wellness/dl-t21.yaml"
run_task "masp/tasks/daily_life/housing/dl-t23.yaml"
run_task "masp/tasks/daily_life/travel/dl-t5.yaml"
run_task "masp/tasks/daily_life/housing/dl-t6.yaml"
run_task "masp/tasks/daily_life/housing/dl-t8.yaml"
run_task "masp/tasks/ecommerce/transaction/ec-t15.yaml"
run_task "masp/tasks/ecommerce/after_sales/ec-t16.yaml"
run_task "masp/tasks/ecommerce/transaction/ec-t18.yaml"
run_task "masp/tasks/ecommerce/after_sales/ec-t2.yaml"
run_task "masp/tasks/ecommerce/after_sales/ec-t20.yaml"
run_task "masp/tasks/ecommerce/shopping/ec-t21.yaml"
run_task "masp/tasks/ecommerce/after_sales/ec-t26.yaml"
run_task "masp/tasks/ecommerce/after_sales/ec-t28.yaml"
run_task "masp/tasks/ecommerce/after_sales/ec-t3.yaml"
run_task "masp/tasks/ecommerce/shopping/ec-t30.yaml"
run_task "masp/tasks/ecommerce/after_sales/ec-t33.yaml"
run_task "masp/tasks/ecommerce/after_sales/ec-t34.yaml"
run_task "masp/tasks/ecommerce/shopping/ec-t35.yaml"
run_task "masp/tasks/ecommerce/transaction/ec-t36.yaml"
run_task "masp/tasks/ecommerce/after_sales/ec-t38.yaml"
run_task "masp/tasks/ecommerce/shopping/ec-t39.yaml"
run_task "masp/tasks/ecommerce/after_sales/ec-t40.yaml"
run_task "masp/tasks/ecommerce/after_sales/ec-t41.yaml"
run_task "masp/tasks/ecommerce/transaction/ec-t45.yaml"
run_task "masp/tasks/ecommerce/after_sales/ec-t46.yaml"
run_task "masp/tasks/ecommerce/transaction/ec-t5.yaml"
run_task "masp/tasks/ecommerce/shopping/ec-t7.yaml"
run_task "masp/tasks/ecommerce/after_sales/ec-t8.yaml"
run_task "masp/tasks/ecommerce/transaction/ec-t9.yaml"
run_task "masp/tasks/finance/financial_planning/fin-t1.yaml"
run_task "masp/tasks/finance/insurance/fin-t10.yaml"
run_task "masp/tasks/finance/lending/fin-t11.yaml"
run_task "masp/tasks/finance/investing/fin-t12.yaml"
run_task "masp/tasks/finance/insurance/fin-t13.yaml"
run_task "masp/tasks/finance/lending/fin-t14.yaml"
run_task "masp/tasks/finance/financial_planning/fin-t16.yaml"
run_task "masp/tasks/finance/lending/fin-t17.yaml"
run_task "masp/tasks/finance/insurance/fin-t18.yaml"
run_task "masp/tasks/finance/investing/fin-t2.yaml"
run_task "masp/tasks/finance/lending/fin-t20.yaml"
run_task "masp/tasks/finance/insurance/fin-t23.yaml"
run_task "masp/tasks/finance/lending/fin-t26.yaml"
run_task "masp/tasks/finance/insurance/fin-t27.yaml"
run_task "masp/tasks/finance/financial_planning/fin-t3.yaml"
run_task "masp/tasks/finance/insurance/fin-t30.yaml"
run_task "masp/tasks/finance/insurance/fin-t33.yaml"
run_task "masp/tasks/finance/insurance/fin-t35.yaml"
run_task "masp/tasks/finance/investing/fin-t36.yaml"
run_task "masp/tasks/finance/financial_planning/fin-t37.yaml"
run_task "masp/tasks/finance/insurance/fin-t38.yaml"
run_task "masp/tasks/finance/lending/fin-t39.yaml"
run_task "masp/tasks/finance/investing/fin-t4.yaml"
run_task "masp/tasks/finance/insurance/fin-t40.yaml"
run_task "masp/tasks/finance/investing/fin-t41.yaml"
run_task "masp/tasks/finance/lending/fin-t42.yaml"
run_task "masp/tasks/finance/financial_planning/fin-t43.yaml"
run_task "masp/tasks/finance/lending/fin-t44.yaml"
run_task "masp/tasks/finance/insurance/fin-t46.yaml"
run_task "masp/tasks/finance/lending/fin-t47.yaml"
run_task "masp/tasks/finance/insurance/fin-t48.yaml"
run_task "masp/tasks/finance/investing/fin-t6.yaml"
run_task "masp/tasks/finance/lending/fin-t7.yaml"
run_task "masp/tasks/finance/insurance/fin-t9.yaml"
run_task "masp/tasks/healthcare/consultation/hc-t1.yaml"
run_task "masp/tasks/healthcare/consultation/hc-t11.yaml"
run_task "masp/tasks/healthcare/consultation/hc-t12.yaml"
run_task "masp/tasks/healthcare/prescription/hc-t13.yaml"
run_task "masp/tasks/healthcare/prescription/hc-t15.yaml"
run_task "masp/tasks/healthcare/consultation/hc-t16.yaml"
run_task "masp/tasks/healthcare/consultation/hc-t18.yaml"
run_task "masp/tasks/healthcare/consultation/hc-t2.yaml"
run_task "masp/tasks/healthcare/prescription/hc-t33.yaml"
run_task "masp/tasks/healthcare/consultation/hc-t34.yaml"
run_task "masp/tasks/healthcare/consultation/hc-t36.yaml"
run_task "masp/tasks/healthcare/consultation/hc-t38.yaml"
run_task "masp/tasks/healthcare/consultation/hc-t4.yaml"
run_task "masp/tasks/healthcare/consultation/hc-t40.yaml"
run_task "masp/tasks/healthcare/prescription/hc-t41.yaml"
run_task "masp/tasks/healthcare/consultation/hc-t42.yaml"
run_task "masp/tasks/healthcare/prescription/hc-t43.yaml"
run_task "masp/tasks/healthcare/consultation/hc-t44.yaml"
run_task "masp/tasks/healthcare/consultation/hc-t45.yaml"
run_task "masp/tasks/healthcare/consultation/hc-t5.yaml"
run_task "masp/tasks/healthcare/prescription/hc-t6.yaml"
run_task "masp/tasks/healthcare/consultation/hc-t7.yaml"
run_task "masp/tasks/legal_compliance/litigation/lc-t1.yaml"
run_task "masp/tasks/legal_compliance/contract/lc-t10.yaml"
run_task "masp/tasks/legal_compliance/litigation/lc-t12.yaml"
run_task "masp/tasks/legal_compliance/contract/lc-t16.yaml"
run_task "masp/tasks/legal_compliance/litigation/lc-t17.yaml"
run_task "masp/tasks/legal_compliance/audit/lc-t19.yaml"
run_task "masp/tasks/legal_compliance/contract/lc-t27.yaml"
run_task "masp/tasks/legal_compliance/audit/lc-t3.yaml"
run_task "masp/tasks/legal_compliance/litigation/lc-t30.yaml"
run_task "masp/tasks/legal_compliance/litigation/lc-t35.yaml"
run_task "masp/tasks/legal_compliance/contract/lc-t36.yaml"
run_task "masp/tasks/legal_compliance/litigation/lc-t4.yaml"
run_task "masp/tasks/legal_compliance/contract/lc-t5.yaml"
run_task "masp/tasks/legal_compliance/contract/lc-t7.yaml"
run_task "masp/tasks/office/personnel/off-t1.yaml"
run_task "masp/tasks/office/personnel/off-t10.yaml"
run_task "masp/tasks/office/finance/off-t12.yaml"
run_task "masp/tasks/office/office_asset/off-t15.yaml"
run_task "masp/tasks/office/finance/off-t16.yaml"
run_task "masp/tasks/office/partnership/off-t17.yaml"
run_task "masp/tasks/office/office_asset/off-t3.yaml"
run_task "masp/tasks/office/office_asset/off-t33.yaml"
run_task "masp/tasks/office/personnel/off-t34.yaml"
run_task "masp/tasks/office/office_asset/off-t35.yaml"
run_task "masp/tasks/office/partnership/off-t37.yaml"
run_task "masp/tasks/office/personnel/off-t38.yaml"
run_task "masp/tasks/office/finance/off-t39.yaml"
run_task "masp/tasks/office/finance/off-t4.yaml"
run_task "masp/tasks/office/partnership/off-t41.yaml"
run_task "masp/tasks/office/personnel/off-t42.yaml"
run_task "masp/tasks/office/office_asset/off-t6.yaml"
run_task "masp/tasks/office/partnership/off-t7.yaml"
run_task "masp/tasks/office/partnership/off-t8.yaml"
run_task "masp/tasks/office/partnership/off-t9.yaml"
run_task "masp/tasks/sde/app_ecommerce/sde1-t1.yaml"
run_task "masp/tasks/sde/app_ecommerce/sde1-t2.yaml"
run_task "masp/tasks/sde/app_ecommerce/sde1-t3.yaml"
run_task "masp/tasks/sde/app_ecommerce/sde1-t4.yaml"
run_task "masp/tasks/sde/app_ecommerce/sde1-t5.yaml"
run_task "masp/tasks/sde/app_admin/sde2-t1.yaml"
run_task "masp/tasks/sde/app_admin/sde2-t2.yaml"
run_task "masp/tasks/sde/app_admin/sde2-t3.yaml"
run_task "masp/tasks/sde/app_admin/sde2-t4.yaml"
run_task "masp/tasks/sde/app_admin/sde2-t5.yaml"
run_task "masp/tasks/social_interaction/voice/si-t1.yaml"
run_task "masp/tasks/social_interaction/voice/si-t10.yaml"
run_task "masp/tasks/social_interaction/voice/si-t12.yaml"
run_task "masp/tasks/social_interaction/voice/si-t14.yaml"
run_task "masp/tasks/social_interaction/report/si-t17.yaml"
run_task "masp/tasks/social_interaction/voice/si-t19.yaml"
run_task "masp/tasks/social_interaction/voice/si-t2.yaml"
run_task "masp/tasks/social_interaction/report/si-t20.yaml"
run_task "masp/tasks/social_interaction/report/si-t22.yaml"
run_task "masp/tasks/social_interaction/report/si-t24.yaml"
run_task "masp/tasks/social_interaction/voice/si-t27.yaml"
run_task "masp/tasks/social_interaction/voice/si-t28.yaml"
run_task "masp/tasks/social_interaction/voice/si-t3.yaml"
run_task "masp/tasks/social_interaction/voice/si-t4.yaml"
run_task "masp/tasks/social_interaction/report/si-t8.yaml"
run_task "masp/tasks/social_interaction/report/si-t9.yaml"

run_task_queue

TOTAL=${#TASK_QUEUE[@]}
SKIPPED=$(count_status skipped)
PASSED=$(count_status passed)
FAILED=$(count_status failed)
echo "EXPERIMENT_DONE $(date -Iseconds) — total=${TOTAL} skipped=${SKIPPED} passed=${PASSED} failed=${FAILED}"
