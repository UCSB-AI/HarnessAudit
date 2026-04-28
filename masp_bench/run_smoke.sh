#!/usr/bin/env bash
# 60% experiment plus SDE: 131 tasks × 1 harness = 131 runs.
# All 24 categories covered. dl~48%, ec 50%, fin 50%, hc 70%, lc 80%, off 70%, si 67%, sde 100%.
# Usage examples:
#   nohup ./masp_bench/run_smoke.sh &              # log auto-created
#   RUN_ALL_WORKERS=1 ./masp_bench/run_smoke.sh    # serial task execution
#   JUDGE_WORKERS=1 ./masp_bench/run_smoke.sh      # serial judge calls per task
#   SKIP_JUDGE=1 ./masp_bench/run_smoke.sh         # disable LLM judge
set -uo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$PKG_ROOT/.." && pwd)"
cd "$REPO_ROOT" || exit 1

PY="${MASP_BENCH_PYTHON:-/home/chengzhi.ucsb/.conda/envs/masp-bench/bin/python}"
RUN_TS="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="$PKG_ROOT/logs"
LOG="$LOG_DIR/smoke_${RUN_TS}.log"

TIMEOUT="${AGENT_TIMEOUT:-360}"
CLAUDE_MODEL="${MASP_CLAUDE_MODEL:-claude-sonnet-4-6}"
CODEX_MODEL="${MASP_CODEX_MODEL:-gpt-5.4}"
OPENCLAW_MODEL="${MASP_OPENCLAW_MODEL:-gpt-5.4}"
JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.4}"
RUN_ALL_WORKERS="${RUN_ALL_WORKERS:-2}"
JUDGE_WORKERS="${JUDGE_WORKERS:-2}"
SKIP_JUDGE="${SKIP_JUDGE:-0}"
RESULTS_DIR="$PKG_ROOT/results"
TRACES_DIR="$PKG_ROOT/traces"
STATUS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/masp-run-smoke-status.XXXXXX")"
TASK_QUEUE=()

if ! [[ "$RUN_ALL_WORKERS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: RUN_ALL_WORKERS must be a positive integer, got: $RUN_ALL_WORKERS" >&2
  exit 2
fi

mkdir -p "$RESULTS_DIR" "$TRACES_DIR" "$LOG_DIR"

# Single log: script self-redirects stdout+stderr → log file (+ tty if interactive)
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
echo "MASP_Bench Smoke Run — $(date -Iseconds)"
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
  # Extract task_id from filename (e.g. "dl-t1" from "dl-t1.yaml")
  local task_id
  task_id=$(basename "$rel_yaml" .yaml)
  local status_file="$STATUS_DIR/${task_id}.status"

  # Skip if result already exists
  if compgen -G "${RESULTS_DIR}/${task_id}_*.json" > /dev/null 2>&1; then
    echo "SKIP $(date -Iseconds) task=${rel_yaml} (result exists)"
    echo skipped > "$status_file"
    return 0
  fi

  # Clean up incomplete trace from previous crash
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

# ── 60% sample plus SDE: 131 tasks across 8 domains, 24 categories ──

# daily_life (10/21 = 48%)
run_task "tasks/daily_life/wellness/dl-t7.yaml"
run_task "tasks/daily_life/housing/dl-t13.yaml"
run_task "tasks/daily_life/travel/dl-t14.yaml"
run_task "tasks/daily_life/housing/dl-t20.yaml"
run_task "tasks/daily_life/travel/dl-t5.yaml"
run_task "tasks/daily_life/housing/dl-t15.yaml"
run_task "tasks/daily_life/wellness/dl-t4.yaml"
run_task "tasks/daily_life/wellness/dl-t10.yaml"
run_task "tasks/daily_life/wellness/dl-t21.yaml"
run_task "tasks/daily_life/dining/dl-t22.yaml"

# ecommerce (19/38 = 50%)
run_task "tasks/ecommerce/shopping/ec-t1.yaml"
run_task "tasks/ecommerce/after_sales/ec-t11.yaml"
run_task "tasks/ecommerce/after_sales/ec-t40.yaml"
run_task "tasks/ecommerce/after_sales/ec-t43.yaml"
run_task "tasks/ecommerce/after_sales/ec-t46.yaml"
run_task "tasks/ecommerce/transaction/ec-t18.yaml"
run_task "tasks/ecommerce/after_sales/ec-t4.yaml"
run_task "tasks/ecommerce/after_sales/ec-t6.yaml"
run_task "tasks/ecommerce/shopping/ec-t7.yaml"
run_task "tasks/ecommerce/shopping/ec-t10.yaml"
run_task "tasks/ecommerce/shopping/ec-t35.yaml"
run_task "tasks/ecommerce/shopping/ec-t37.yaml"
run_task "tasks/ecommerce/shopping/ec-t44.yaml"
run_task "tasks/ecommerce/transaction/ec-t9.yaml"
run_task "tasks/ecommerce/transaction/ec-t12.yaml"
run_task "tasks/ecommerce/transaction/ec-t15.yaml"
run_task "tasks/ecommerce/shopping/ec-t21.yaml"
run_task "tasks/ecommerce/transaction/ec-t42.yaml"
run_task "tasks/ecommerce/transaction/ec-t45.yaml"

# finance (20/40 = 50%)
run_task "tasks/finance/financial_planning/fin-t1.yaml"
run_task "tasks/finance/investing/fin-t5.yaml"
run_task "tasks/finance/financial_planning/fin-t15.yaml"
run_task "tasks/finance/investing/fin-t45.yaml"
run_task "tasks/finance/investing/fin-t6.yaml"
run_task "tasks/finance/insurance/fin-t10.yaml"
run_task "tasks/finance/insurance/fin-t13.yaml"
run_task "tasks/finance/insurance/fin-t33.yaml"
run_task "tasks/finance/insurance/fin-t38.yaml"
run_task "tasks/finance/insurance/fin-t40.yaml"
run_task "tasks/finance/insurance/fin-t48.yaml"
run_task "tasks/finance/investing/fin-t2.yaml"
run_task "tasks/finance/lending/fin-t8.yaml"
run_task "tasks/finance/insurance/fin-t18.yaml"
run_task "tasks/finance/financial_planning/fin-t37.yaml"
run_task "tasks/finance/financial_planning/fin-t43.yaml"
run_task "tasks/finance/lending/fin-t47.yaml"
run_task "tasks/finance/lending/fin-t17.yaml"
run_task "tasks/finance/investing/fin-t19.yaml"
run_task "tasks/finance/investing/fin-t21.yaml"

# healthcare (21/30 = 70%)
run_task "tasks/healthcare/consultation/hc-t1.yaml"
run_task "tasks/healthcare/consultation/hc-t2.yaml"
run_task "tasks/healthcare/consultation/hc-t3.yaml"
run_task "tasks/healthcare/consultation/hc-t4.yaml"
run_task "tasks/healthcare/prescription/hc-t13.yaml"
run_task "tasks/healthcare/prescription/hc-t15.yaml"
run_task "tasks/healthcare/consultation/hc-t34.yaml"
run_task "tasks/healthcare/consultation/hc-t36.yaml"
run_task "tasks/healthcare/prescription/hc-t37.yaml"
run_task "tasks/healthcare/prescription/hc-t39.yaml"
run_task "tasks/healthcare/consultation/hc-t40.yaml"
run_task "tasks/healthcare/prescription/hc-t6.yaml"
run_task "tasks/healthcare/consultation/hc-t18.yaml"
run_task "tasks/healthcare/prescription/hc-t33.yaml"
run_task "tasks/healthcare/prescription/hc-t35.yaml"
run_task "tasks/healthcare/consultation/hc-t38.yaml"
run_task "tasks/healthcare/prescription/hc-t41.yaml"
run_task "tasks/healthcare/consultation/hc-t42.yaml"
run_task "tasks/healthcare/prescription/hc-t43.yaml"
run_task "tasks/healthcare/consultation/hc-t45.yaml"
run_task "tasks/healthcare/prescription/hc-t46.yaml"

# legal_compliance (16/20 = 80%)
run_task "tasks/legal_compliance/audit/lc-t9.yaml"
run_task "tasks/legal_compliance/contract/lc-t10.yaml"
run_task "tasks/legal_compliance/audit/lc-t19.yaml"
run_task "tasks/legal_compliance/contract/lc-t36.yaml"
run_task "tasks/legal_compliance/audit/lc-t3.yaml"
run_task "tasks/legal_compliance/contract/lc-t7.yaml"
run_task "tasks/legal_compliance/litigation/lc-t12.yaml"
run_task "tasks/legal_compliance/contract/lc-t16.yaml"
run_task "tasks/legal_compliance/litigation/lc-t17.yaml"
run_task "tasks/legal_compliance/contract/lc-t18.yaml"
run_task "tasks/legal_compliance/litigation/lc-t2.yaml"
run_task "tasks/legal_compliance/litigation/lc-t4.yaml"
run_task "tasks/legal_compliance/contract/lc-t27.yaml"
run_task "tasks/legal_compliance/litigation/lc-t30.yaml"
run_task "tasks/legal_compliance/litigation/lc-t32.yaml"
run_task "tasks/legal_compliance/contract/lc-t33.yaml"

# office (19/27 = 70%)
run_task "tasks/office/office_asset/off-t3.yaml"
run_task "tasks/office/finance/off-t5.yaml"
run_task "tasks/office/office_asset/off-t6.yaml"
run_task "tasks/office/office_asset/off-t2.yaml"
run_task "tasks/office/personnel/off-t10.yaml"
run_task "tasks/office/office_asset/off-t11.yaml"
run_task "tasks/office/partnership/off-t14.yaml"
run_task "tasks/office/office_asset/off-t15.yaml"
run_task "tasks/office/finance/off-t16.yaml"
run_task "tasks/office/office_asset/off-t35.yaml"
run_task "tasks/office/finance/off-t4.yaml"
run_task "tasks/office/partnership/off-t7.yaml"
run_task "tasks/office/partnership/off-t17.yaml"
run_task "tasks/office/partnership/off-t37.yaml"
run_task "tasks/office/personnel/off-t42.yaml"
run_task "tasks/office/personnel/off-t1.yaml"
run_task "tasks/office/personnel/off-t18.yaml"
run_task "tasks/office/personnel/off-t34.yaml"
run_task "tasks/office/personnel/off-t38.yaml"

# social_interaction (16/24 = 67%)
run_task "tasks/social_interaction/report/si-t7.yaml"
run_task "tasks/social_interaction/report/si-t9.yaml"
run_task "tasks/social_interaction/report/si-t11.yaml"
run_task "tasks/social_interaction/report/si-t13.yaml"
run_task "tasks/social_interaction/report/si-t15.yaml"
run_task "tasks/social_interaction/report/si-t18.yaml"
run_task "tasks/social_interaction/report/si-t20.yaml"
run_task "tasks/social_interaction/report/si-t22.yaml"
run_task "tasks/social_interaction/report/si-t24.yaml"
run_task "tasks/social_interaction/report/si-t26.yaml"
run_task "tasks/social_interaction/voice/si-t3.yaml"
run_task "tasks/social_interaction/voice/si-t12.yaml"
run_task "tasks/social_interaction/voice/si-t16.yaml"
run_task "tasks/social_interaction/voice/si-t19.yaml"
run_task "tasks/social_interaction/voice/si-t27.yaml"
run_task "tasks/social_interaction/voice/si-t28.yaml"

run_task_queue

TOTAL=${#TASK_QUEUE[@]}
SKIPPED=$(count_status skipped)
PASSED=$(count_status passed)
FAILED=$(count_status failed)
echo "EXPERIMENT_DONE $(date -Iseconds) — total=${TOTAL} skipped=${SKIPPED} passed=${PASSED} failed=${FAILED}"
