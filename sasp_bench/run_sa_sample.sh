#!/usr/bin/env bash
# SASP sample run: 10 diverse tasks × openclaw_local.
# Usage:
#   ./sasp_bench/run_sa_sample.sh
#   RUN_ALL_WORKERS=4 ./sasp_bench/run_sa_sample.sh
#   SASP_MODEL=claude-sonnet-4-6 ./sasp_bench/run_sa_sample.sh
set -uo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$PKG_ROOT/.." && pwd)"
cd "$REPO_ROOT" || exit 1

PY="${MASP_BENCH_PYTHON:-/home/chengzhi.ucsb/.conda/envs/masp-bench/bin/python}"
RUN_TS="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="$PKG_ROOT/logs"
LOG="$LOG_DIR/run_sa_sample_${RUN_TS}.log"

TIMEOUT="${AGENT_TIMEOUT:-360}"
FRAMEWORK="openclaw_local"
MODEL="${SASP_MODEL:-gpt-5.4}"
JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.4}"
RUN_ALL_WORKERS="${RUN_ALL_WORKERS:-4}"
JUDGE_WORKERS="${JUDGE_WORKERS:-2}"
SKIP_JUDGE="${SKIP_JUDGE:-0}"
RESULTS_DIR="$PKG_ROOT/results"
TRACES_DIR="$PKG_ROOT/traces"
FRAMEWORK_RESULTS_DIR="$RESULTS_DIR/$FRAMEWORK"
FRAMEWORK_TRACES_DIR="$TRACES_DIR/$FRAMEWORK"
STATUS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/sasp-sample-status.XXXXXX")"
TASK_QUEUE=()

if [[ "$PY" == */* && ! -x "$PY" ]]; then
  echo "ERROR: MASP_BENCH_PYTHON is not executable: $PY" >&2
  exit 127
fi

if ! [[ "$RUN_ALL_WORKERS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: RUN_ALL_WORKERS must be a positive integer, got: $RUN_ALL_WORKERS" >&2
  exit 2
fi

mkdir -p "$FRAMEWORK_RESULTS_DIR" "$FRAMEWORK_TRACES_DIR" "$LOG_DIR"

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
echo "SASP_Bench Sample Run — $(date -Iseconds)"
echo "  Framework:     $FRAMEWORK"
echo "  Model:         $MODEL"
echo "  Judge:         $([ "$SKIP_JUDGE" = "0" ] && echo "ON ($JUDGE_MODEL)" || echo "OFF")"
echo "  Task workers:  $RUN_ALL_WORKERS"
echo "  Judge workers: $JUDGE_WORKERS"
echo "  Timeout:       ${TIMEOUT}s"
echo "  Results:       $FRAMEWORK_RESULTS_DIR"
echo "  Traces:        $FRAMEWORK_TRACES_DIR"
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

  if compgen -G "${FRAMEWORK_RESULTS_DIR}/${task_id}_*.json" > /dev/null 2>&1; then
    echo "SKIP $(date -Iseconds) task=${rel_yaml} (result exists)"
    echo skipped > "$status_file"
    return 0
  fi

  rm -f "${FRAMEWORK_TRACES_DIR}/${task_id}_"*.jsonl 2>/dev/null

  echo "--- $(date -Iseconds) task=${rel_yaml} framework=${FRAMEWORK} model=${MODEL} ---"
  "$PY" -m sasp_bench run "$rel_yaml" \
    --framework "$FRAMEWORK" \
    --model "$MODEL" \
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

# ── 10-task diverse sample: 7 domains, spread task IDs ──

# finance (2/6)
run_task "tasks/finance/sa-fin-t1.yaml"
run_task "tasks/finance/sa-fin-t5.yaml"

# healthcare (2/6)
run_task "tasks/healthcare/sa-hc-t2.yaml"
run_task "tasks/healthcare/sa-hc-t5.yaml"

# ecommerce (1/5)
run_task "tasks/ecommerce/sa-ec-t3.yaml"

# office (1/5)
run_task "tasks/office/sa-off-t2.yaml"

# daily_life (1/4)
run_task "tasks/daily_life/sa-dl-t3.yaml"

# legal_compliance (1/4)
run_task "tasks/legal_compliance/sa-lc-t2.yaml"

# social_interaction (2/4)
run_task "tasks/social_interaction/sa-si-t1.yaml"
run_task "tasks/social_interaction/sa-si-t4.yaml"

run_task_queue

TOTAL=${#TASK_QUEUE[@]}
SKIPPED=$(count_status skipped)
PASSED=$(count_status passed)
FAILED=$(count_status failed)
echo "SASP_SAMPLE_DONE $(date -Iseconds) — total=${TOTAL} skipped=${SKIPPED} passed=${PASSED} failed=${FAILED}"
