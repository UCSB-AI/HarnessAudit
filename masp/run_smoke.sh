#!/usr/bin/env bash
# Remaining-task smoke runner for OpenClaw + OpenRouter GPT-5.4.
# Usage examples:
#   ./masp/run_smoke.sh
#   RUN_ALL_WORKERS=2 ./masp/run_smoke.sh
#   SKIP_JUDGE=1 RUN_ALL_WORKERS=4 ./masp/run_smoke.sh
#   FORCE_RERUN=0 ./masp/run_smoke.sh
set -uo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$PKG_ROOT/.." && pwd)"
cd "$REPO_ROOT" || exit 1

if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
fi

CONDA_BASE="$(conda info --base 2>/dev/null || true)"
if [[ -n "$CONDA_BASE" && -f "$CONDA_BASE/etc/profile.d/conda.sh" ]]; then
  # shellcheck disable=SC1091
  source "$CONDA_BASE/etc/profile.d/conda.sh"
fi
conda activate masp-bench || exit 2

normalize_base_url() {
  local raw="${1:-}"
  raw="${raw%/}"
  printf '%s' "$raw"
}

OFFICIAL_OPENAI_BASE_URL="https://api.openai.com/v1"
OPENAI_BASE_URL="$(normalize_base_url "${OPENAI_BASE_URL:-$OFFICIAL_OPENAI_BASE_URL}")"
OPENCLAW_OPENAI_BASE_URL="$(normalize_base_url "${OPENCLAW_OPENAI_BASE_URL:-$OPENAI_BASE_URL}")"

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "ERROR: OPENAI_API_KEY is required for the judge." >&2
  exit 2
fi

if [[ "${OPENAI_API_KEY}" == sk-or-* ]]; then
  echo "ERROR: OPENAI_API_KEY looks like an OpenRouter key. Use an official OpenAI key for this script." >&2
  exit 2
fi

if [[ "$OPENAI_BASE_URL" != "$OFFICIAL_OPENAI_BASE_URL" ]]; then
  echo "ERROR: OPENAI_BASE_URL must be $OFFICIAL_OPENAI_BASE_URL, got: $OPENAI_BASE_URL" >&2
  exit 2
fi

OPENROUTER_BASE_URL="$(normalize_base_url "${OPENROUTER_BASE_URL:-https://openrouter.ai/api/v1}")"
if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "ERROR: OPENROUTER_API_KEY is required for OpenClaw models." >&2
  exit 2
fi
if [[ "$OPENROUTER_BASE_URL" != "https://openrouter.ai/api/v1" ]]; then
  echo "ERROR: OpenClaw models must use OpenRouter, got: $OPENROUTER_BASE_URL" >&2
  exit 2
fi

unset OPENCLAW_TRAJECTORY_DIR
unset MASP_MCP_LOG_DIR

export OPENAI_API_KEY
export OPENAI_BASE_URL
export OPENCLAW_OPENAI_BASE_URL
export OPENROUTER_API_KEY
export OPENROUTER_BASE_URL
export MASP_OPENCLAW_VALIDATE_TIMEOUT=120

openclaw plugins list --enabled --json > /tmp/openclaw_plugins_enabled.json || exit 2

PY="${HARNESSAUDIT_BENCH_PYTHON:-python}"
RUN_TS="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="$PKG_ROOT/logs"
LOG="$LOG_DIR/smoke_remaining_gpt54_${RUN_TS}.log"

TIMEOUT="${AGENT_TIMEOUT:-360}"
HARNESS="openclaw"
HARNESS_MODEL="${HARNESS_MODEL:-openai/gpt-5.4}"
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
STATUS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/masp-run-smoke-status.XXXXXX")"
TASK_MANIFEST="$LOG_DIR/smoke_remaining_gpt54_tasks_${RUN_TS}.txt"
SKIP_MANIFEST="$LOG_DIR/smoke_remaining_gpt54_skipped_${RUN_TS}.txt"

EXPECTED_TOTAL_TASKS=210
EXPECTED_BASELINE_SKIP_COUNT=71
BASELINE_SKIP_TASK_IDS=(
  "dl-t10"
  "dl-t12"
  "dl-t13"
  "dl-t14"
  "dl-t15"
  "dl-t4"
  "dl-t5"
  "dl-t7"
  "ec-t1"
  "ec-t10"
  "ec-t11"
  "ec-t12"
  "ec-t15"
  "ec-t18"
  "ec-t35"
  "ec-t37"
  "ec-t4"
  "ec-t40"
  "ec-t42"
  "ec-t43"
  "ec-t44"
  "ec-t45"
  "ec-t46"
  "ec-t9"
  "fin-t1"
  "fin-t10"
  "fin-t13"
  "fin-t15"
  "fin-t17"
  "fin-t2"
  "fin-t21"
  "fin-t5"
  "fin-t6"
  "fin-t8"
  "lc-t1"
  "lc-t18"
  "lc-t19"
  "lc-t2"
  "lc-t27"
  "lc-t3"
  "lc-t30"
  "lc-t32"
  "lc-t33"
  "lc-t36"
  "lc-t4"
  "lc-t5"
  "lc-t7"
  "lc-t8"
  "off-t1"
  "off-t11"
  "off-t14"
  "off-t15"
  "off-t16"
  "off-t2"
  "off-t3"
  "off-t34"
  "off-t35"
  "off-t4"
  "off-t5"
  "off-t6"
  "off-t7"
  "sde1-t1"
  "sde1-t2"
  "sde1-t3"
  "sde1-t4"
  "sde1-t5"
  "sde2-t1"
  "sde2-t2"
  "sde2-t3"
  "sde2-t4"
  "sde2-t5"
)

if ! [[ "$RUN_ALL_WORKERS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: RUN_ALL_WORKERS must be a positive integer, got: $RUN_ALL_WORKERS" >&2
  exit 2
fi

if ! [[ "$JUDGE_WORKERS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: JUDGE_WORKERS must be a positive integer, got: $JUDGE_WORKERS" >&2
  exit 2
fi

if [[ "${#BASELINE_SKIP_TASK_IDS[@]}" -ne "$EXPECTED_BASELINE_SKIP_COUNT" ]]; then
  echo "ERROR: baseline skip list expected $EXPECTED_BASELINE_SKIP_COUNT tasks, got ${#BASELINE_SKIP_TASK_IDS[@]}" >&2
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

declare -A BASELINE_SKIP_TASK_SET=()
for task_id in "${BASELINE_SKIP_TASK_IDS[@]}"; do
  BASELINE_SKIP_TASK_SET["$task_id"]=1
done

ALL_TASK_FILES=()
TASK_QUEUE=()
BASELINE_SKIPPED=()
while IFS= read -r abs_yaml; do
  ALL_TASK_FILES+=("$abs_yaml")
done < <(find "$REPO_ROOT/masp/tasks" -type f -name '*.yaml' | sort)

for abs_yaml in "${ALL_TASK_FILES[@]}"; do
  rel_yaml="${abs_yaml#$REPO_ROOT/}"
  task_id="$(basename "$abs_yaml" .yaml)"
  if [[ -n "${BASELINE_SKIP_TASK_SET[$task_id]+x}" ]]; then
    BASELINE_SKIPPED+=("$task_id")
    continue
  fi
  TASK_QUEUE+=("$rel_yaml")
done

TOTAL_DISCOVERED=${#ALL_TASK_FILES[@]}
TOTAL_SKIPPED_BASELINE=${#BASELINE_SKIPPED[@]}
TOTAL_QUEUED=${#TASK_QUEUE[@]}
EXPECTED_REMAINING=$((TOTAL_DISCOVERED - TOTAL_SKIPPED_BASELINE))

printf '%s\n' "${TASK_QUEUE[@]}" > "$TASK_MANIFEST"
printf '%s\n' "${BASELINE_SKIPPED[@]}" > "$SKIP_MANIFEST"

echo "========================================"
echo "HarnessAudit-Bench Remaining-Task Smoke Matrix — $(date -Iseconds)"
echo "  Harness:             $HARNESS"
echo "  Model:               $HARNESS_MODEL"
echo "  Judge:               $([ "$SKIP_JUDGE" = "0" ] && echo "ON ($JUDGE_MODEL)" || echo "OFF")"
echo "  Task workers:        $RUN_ALL_WORKERS"
echo "  Judge workers:       $JUDGE_WORKERS"
echo "  Timeout:             ${TIMEOUT}s"
echo "  Force rerun:         $FORCE_RERUN"
echo "  Judge base URL:      $OPENAI_BASE_URL"
echo "  OpenClaw OpenRouter: $OPENROUTER_BASE_URL"
echo "  Total task YAMLs:    $TOTAL_DISCOVERED"
echo "  Baseline skipped:    $TOTAL_SKIPPED_BASELINE"
echo "  Remaining queued:    $TOTAL_QUEUED"
echo "  Results:             $HARNESS_RESULTS_DIR"
echo "  Traces:              $HARNESS_TRACES_DIR"
echo "  Log:                 $LOG"
echo "  Task manifest:       $TASK_MANIFEST"
echo "  Skip manifest:       $SKIP_MANIFEST"
echo "========================================"

if [[ "$TOTAL_DISCOVERED" -ne "$EXPECTED_TOTAL_TASKS" ]]; then
  echo "NOTE: discovered $TOTAL_DISCOVERED task YAMLs, expected $EXPECTED_TOTAL_TASKS."
fi

if [[ "$TOTAL_SKIPPED_BASELINE" -ne "$EXPECTED_BASELINE_SKIP_COUNT" ]]; then
  echo "NOTE: skipped $TOTAL_SKIPPED_BASELINE baseline tasks, expected $EXPECTED_BASELINE_SKIP_COUNT."
fi

if [[ "$TOTAL_QUEUED" -ne "$EXPECTED_REMAINING" ]]; then
  echo "ERROR: queued task count mismatch: queued=$TOTAL_QUEUED expected=$EXPECTED_REMAINING" >&2
  exit 2
fi

run_one_task() {
  local rel_yaml=$1
  local task_id
  local status_file
  local exit_code

  task_id=$(basename "$rel_yaml" .yaml)
  status_file="$STATUS_DIR/${task_id}.status"

  if [[ "${FORCE_RERUN,,}" =~ ^(1|true|yes|y)$ ]]; then
    rm -f "${HARNESS_RESULTS_DIR}/${task_id}_"*.json 2>/dev/null || true
    rm -f "${HARNESS_RESULTS_DIR}/${task_id}_"*.sqlite 2>/dev/null || true
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
  "$PY" -m masp run "$rel_yaml" \
    --framework clawteam \
    --harness "$HARNESS" \
    --model "$HARNESS_MODEL" \
    --agent-timeout "$TIMEOUT" \
    --output "$RESULTS_DIR/" \
    --trace-dir "$TRACES_DIR/" \
    "${JUDGE_ARGS[@]}"
  exit_code=$?

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

TOTAL=$TOTAL_QUEUED
SKIPPED=$(count_status skipped)
PASSED=$(count_status passed)
FAILED=$(count_status failed)
echo "EXPERIMENT_DONE $(date -Iseconds) — total=${TOTAL} skipped=${SKIPPED} passed=${PASSED} failed=${FAILED}"
