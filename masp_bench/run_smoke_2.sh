#!/usr/bin/env bash
# Remaining 79 tasks not covered by run_smoke.sh.
# Usage:
#   nohup ./masp_bench/run_smoke_2.sh &
#   RUN_ALL_WORKERS=1 ./masp_bench/run_smoke_2.sh
#   SKIP_JUDGE=1 ./masp_bench/run_smoke_2.sh
set -uo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$PKG_ROOT/.." && pwd)"
cd "$REPO_ROOT" || exit 1

PY="${MASP_BENCH_PYTHON:-/home/cliu322/anaconda3/envs/masp-bench/bin/python}"
RUN_TS="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="$PKG_ROOT/logs"
LOG="$LOG_DIR/smoke2_${RUN_TS}.log"

TIMEOUT="${AGENT_TIMEOUT:-360}"
OPENCLAW_MODEL="${MASP_OPENCLAW_MODEL:-gpt-5.4}"
JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.4}"
RUN_ALL_WORKERS="${RUN_ALL_WORKERS:-4}"
JUDGE_WORKERS="${JUDGE_WORKERS:-2}"
SKIP_JUDGE="${SKIP_JUDGE:-0}"
RESULTS_DIR="$PKG_ROOT/results"
TRACES_DIR="$PKG_ROOT/traces"
STATUS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/masp-run-smoke2-status.XXXXXX")"
TASK_QUEUE=()

if ! [[ "$RUN_ALL_WORKERS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: RUN_ALL_WORKERS must be a positive integer, got: $RUN_ALL_WORKERS" >&2
  exit 2
fi

mkdir -p "$RESULTS_DIR" "$TRACES_DIR" "$LOG_DIR"

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
echo "MASP_Bench Smoke Run 2 — $(date -Iseconds)"
echo "  Harness:       openclaw"
echo "  Model:         $OPENCLAW_MODEL"
echo "  Judge:         $([ "$SKIP_JUDGE" = "0" ] && echo "ON ($JUDGE_MODEL)" || echo "OFF")"
echo "  Task workers:  $RUN_ALL_WORKERS"
echo "  Judge workers: $JUDGE_WORKERS"
echo "  Timeout:       ${TIMEOUT}s"
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

  if compgen -G "${RESULTS_DIR}/${task_id}_*.json" > /dev/null 2>&1; then
    echo "SKIP $(date -Iseconds) task=${rel_yaml} (result exists)"
    echo skipped > "$status_file"
    return 0
  fi

  rm -f "${TRACES_DIR}/${task_id}_"*.jsonl 2>/dev/null

  echo "--- $(date -Iseconds) task=${rel_yaml} harness=openclaw model=${OPENCLAW_MODEL} ---"
  "$PY" -m masp_bench run "$rel_yaml" \
    --framework clawteam \
    --harness openclaw \
    --model "$OPENCLAW_MODEL" \
    --agent-timeout "$TIMEOUT" \
    --output "$RESULTS_DIR/" \
    --trace-dir "$TRACES_DIR/" \
    "${JUDGE_ARGS[@]}" 2>&1
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

# ── Remaining 79 tasks across 7 domains ──

# daily_life (11/21)
run_task "tasks/daily_life/dining/dl-t11.yaml"
run_task "tasks/daily_life/dining/dl-t18.yaml"
run_task "tasks/daily_life/housing/dl-t23.yaml"
run_task "tasks/daily_life/housing/dl-t6.yaml"
run_task "tasks/daily_life/housing/dl-t8.yaml"
run_task "tasks/daily_life/housing/dl-t9.yaml"
run_task "tasks/daily_life/travel/dl-t12.yaml"
run_task "tasks/daily_life/travel/dl-t16.yaml"
run_task "tasks/daily_life/travel/dl-t19.yaml"
run_task "tasks/daily_life/travel/dl-t2.yaml"
run_task "tasks/daily_life/wellness/dl-t17.yaml"

# ecommerce (19/38)
run_task "tasks/ecommerce/after_sales/ec-t13.yaml"
run_task "tasks/ecommerce/after_sales/ec-t16.yaml"
run_task "tasks/ecommerce/after_sales/ec-t2.yaml"
run_task "tasks/ecommerce/after_sales/ec-t20.yaml"
run_task "tasks/ecommerce/after_sales/ec-t22.yaml"
run_task "tasks/ecommerce/after_sales/ec-t24.yaml"
run_task "tasks/ecommerce/after_sales/ec-t26.yaml"
run_task "tasks/ecommerce/after_sales/ec-t28.yaml"
run_task "tasks/ecommerce/after_sales/ec-t3.yaml"
run_task "tasks/ecommerce/after_sales/ec-t33.yaml"
run_task "tasks/ecommerce/after_sales/ec-t34.yaml"
run_task "tasks/ecommerce/after_sales/ec-t38.yaml"
run_task "tasks/ecommerce/after_sales/ec-t41.yaml"
run_task "tasks/ecommerce/after_sales/ec-t8.yaml"
run_task "tasks/ecommerce/shopping/ec-t14.yaml"
run_task "tasks/ecommerce/shopping/ec-t30.yaml"
run_task "tasks/ecommerce/shopping/ec-t39.yaml"
run_task "tasks/ecommerce/transaction/ec-t36.yaml"
run_task "tasks/ecommerce/transaction/ec-t5.yaml"

# finance (20/40)
run_task "tasks/finance/financial_planning/fin-t16.yaml"
run_task "tasks/finance/financial_planning/fin-t3.yaml"
run_task "tasks/finance/insurance/fin-t23.yaml"
run_task "tasks/finance/insurance/fin-t27.yaml"
run_task "tasks/finance/insurance/fin-t30.yaml"
run_task "tasks/finance/insurance/fin-t35.yaml"
run_task "tasks/finance/insurance/fin-t46.yaml"
run_task "tasks/finance/insurance/fin-t9.yaml"
run_task "tasks/finance/investing/fin-t12.yaml"
run_task "tasks/finance/investing/fin-t36.yaml"
run_task "tasks/finance/investing/fin-t41.yaml"
run_task "tasks/finance/investing/fin-t4.yaml"
run_task "tasks/finance/lending/fin-t11.yaml"
run_task "tasks/finance/lending/fin-t14.yaml"
run_task "tasks/finance/lending/fin-t20.yaml"
run_task "tasks/finance/lending/fin-t26.yaml"
run_task "tasks/finance/lending/fin-t39.yaml"
run_task "tasks/finance/lending/fin-t42.yaml"
run_task "tasks/finance/lending/fin-t44.yaml"
run_task "tasks/finance/lending/fin-t7.yaml"

# healthcare (9/30)
run_task "tasks/healthcare/consultation/hc-t10.yaml"
run_task "tasks/healthcare/consultation/hc-t11.yaml"
run_task "tasks/healthcare/consultation/hc-t12.yaml"
run_task "tasks/healthcare/consultation/hc-t14.yaml"
run_task "tasks/healthcare/consultation/hc-t16.yaml"
run_task "tasks/healthcare/consultation/hc-t44.yaml"
run_task "tasks/healthcare/consultation/hc-t5.yaml"
run_task "tasks/healthcare/consultation/hc-t7.yaml"
run_task "tasks/healthcare/prescription/hc-t9.yaml"

# legal_compliance (4/20)
run_task "tasks/legal_compliance/audit/lc-t8.yaml"
run_task "tasks/legal_compliance/contract/lc-t5.yaml"
run_task "tasks/legal_compliance/litigation/lc-t1.yaml"
run_task "tasks/legal_compliance/litigation/lc-t35.yaml"

# office (8/27)
run_task "tasks/office/finance/off-t12.yaml"
run_task "tasks/office/finance/off-t19.yaml"
run_task "tasks/office/finance/off-t39.yaml"
run_task "tasks/office/office_asset/off-t33.yaml"
run_task "tasks/office/office_asset/off-t40.yaml"
run_task "tasks/office/partnership/off-t41.yaml"
run_task "tasks/office/partnership/off-t8.yaml"
run_task "tasks/office/partnership/off-t9.yaml"

# social_interaction (8/24)
run_task "tasks/social_interaction/report/si-t17.yaml"
run_task "tasks/social_interaction/report/si-t8.yaml"
run_task "tasks/social_interaction/voice/si-t1.yaml"
run_task "tasks/social_interaction/voice/si-t10.yaml"
run_task "tasks/social_interaction/voice/si-t14.yaml"
run_task "tasks/social_interaction/voice/si-t2.yaml"
run_task "tasks/social_interaction/voice/si-t23.yaml"
run_task "tasks/social_interaction/voice/si-t4.yaml"

# software_engineering (10/10 = 100%)
run_task "tasks/sde/app_ecommerce/sde1-t1.yaml"
run_task "tasks/sde/app_ecommerce/sde1-t2.yaml"
run_task "tasks/sde/app_ecommerce/sde1-t3.yaml"
run_task "tasks/sde/app_ecommerce/sde1-t4.yaml"
run_task "tasks/sde/app_ecommerce/sde1-t5.yaml"
run_task "tasks/sde/app_admin/sde2-t1.yaml"
run_task "tasks/sde/app_admin/sde2-t2.yaml"
run_task "tasks/sde/app_admin/sde2-t3.yaml"
run_task "tasks/sde/app_admin/sde2-t4.yaml"
run_task "tasks/sde/app_admin/sde2-t5.yaml"

run_task_queue

TOTAL=${#TASK_QUEUE[@]}
SKIPPED=$(count_status skipped)
PASSED=$(count_status passed)
FAILED=$(count_status failed)
echo "EXPERIMENT_DONE $(date -Iseconds) — total=${TOTAL} skipped=${SKIPPED} passed=${PASSED} failed=${FAILED}"
