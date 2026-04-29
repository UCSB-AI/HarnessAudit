#!/usr/bin/env bash
# Full SASP run: all single-agent tasks with openclaw_local.
# Usage examples:
#   nohup ./sasp_bench/run_sa.sh &
#   RUN_ALL_WORKERS=1 ./sasp_bench/run_sa.sh
#   JUDGE_WORKERS=1 ./sasp_bench/run_sa.sh
#   SKIP_JUDGE=1 ./sasp_bench/run_sa.sh
set -uo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$PKG_ROOT/.." && pwd)"
cd "$REPO_ROOT" || exit 1

PY="${MASP_BENCH_PYTHON:-/home/cliu322/anaconda3/envs/masp-bench/bin/python}"
RUN_TS="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="$PKG_ROOT/logs"
LOG="$LOG_DIR/run_sa_${RUN_TS}.log"

TIMEOUT="${AGENT_TIMEOUT:-360}"
FRAMEWORK="openclaw_local"
MODEL="${SASP_MODEL:-gpt-5.4}"
JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.4}"
RUN_ALL_WORKERS="${RUN_ALL_WORKERS:-2}"
JUDGE_WORKERS="${JUDGE_WORKERS:-2}"
SKIP_JUDGE="${SKIP_JUDGE:-0}"
RESULTS_DIR="$PKG_ROOT/results"
TRACES_DIR="$PKG_ROOT/traces"
FRAMEWORK_RESULTS_DIR="$RESULTS_DIR/$FRAMEWORK"
FRAMEWORK_TRACES_DIR="$TRACES_DIR/$FRAMEWORK"
STATUS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/sasp-run-status.XXXXXX")"
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

mapfile -t TASK_QUEUE < <(
  find "$PKG_ROOT/tasks" -type f -name '*.yaml' -printf '%P\n' \
    | sort \
    | sed 's#^#tasks/#'
)

if [[ "${#TASK_QUEUE[@]}" -eq 0 ]]; then
  echo "ERROR: no SASP task YAML files found under $PKG_ROOT/tasks" >&2
  exit 2
fi

echo "========================================"
echo "SASP_Bench Run - $(date -Iseconds)"
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

run_task_queue

TOTAL=${#TASK_QUEUE[@]}
SKIPPED=$(count_status skipped)
PASSED=$(count_status passed)
FAILED=$(count_status failed)
echo "SASP_RUN_DONE $(date -Iseconds) - total=${TOTAL} skipped=${SKIPPED} passed=${PASSED} failed=${FAILED}"
