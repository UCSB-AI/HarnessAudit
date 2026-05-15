#!/usr/bin/env bash
# Shared badcase-only rerun runner for OpenClaw models.
# Wrapper scripts pass task IDs as argv and set HARNESS_MODEL.
set -uo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$PKG_ROOT/.." && pwd)"
cd "$REPO_ROOT" || exit 1

if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  set +u
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set -u
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

PY="${HARNESSAUDIT_BENCH_PYTHON:-python}"
RUN_TS="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="$PKG_ROOT/logs"

TIMEOUT="${AGENT_TIMEOUT:-360}"
HARNESS="openclaw"
HARNESS_MODEL="${HARNESS_MODEL:-}"
RERUN_LABEL="${RERUN_LABEL:-${HARNESS_MODEL//\//__}_badcases}"
JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.4}"
RUN_ALL_WORKERS="${RUN_ALL_WORKERS:-2}"
JUDGE_WORKERS="${JUDGE_WORKERS:-2}"
SKIP_JUDGE="${SKIP_JUDGE:-0}"
FORCE_RERUN="${FORCE_RERUN:-1}"
EXPECTED_BADCASE_COUNT="${EXPECTED_BADCASE_COUNT:-}"
MODEL_DIR="${HARNESS_MODEL//\//__}"
HARNESS_RESULTS_DIR="$PKG_ROOT/results/$HARNESS/rerun/$MODEL_DIR"
HARNESS_TRACES_DIR="$PKG_ROOT/traces/$HARNESS/rerun/$MODEL_DIR"
STATUS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/masp-rerun-status.XXXXXX")"
TASK_MANIFEST="$LOG_DIR/rerun_${RERUN_LABEL}_tasks_${RUN_TS}.txt"
LOG="$LOG_DIR/rerun_${RERUN_LABEL}_${RUN_TS}.log"

if [[ -z "$HARNESS_MODEL" ]]; then
  echo "ERROR: HARNESS_MODEL is required." >&2
  exit 2
fi

if [[ "$#" -eq 0 ]]; then
  echo "ERROR: pass at least one task id to rerun." >&2
  exit 2
fi

if ! [[ "$RUN_ALL_WORKERS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: RUN_ALL_WORKERS must be a positive integer, got: $RUN_ALL_WORKERS" >&2
  exit 2
fi

if ! [[ "$JUDGE_WORKERS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: JUDGE_WORKERS must be a positive integer, got: $JUDGE_WORKERS" >&2
  exit 2
fi

case "$HARNESS_MODEL" in
  gemini-3.1-pro-preview|qwen3.5-plus|kimi-k2.6|glm-5v-turbo|openrouter-claude-sonnet-4-6|openrouter-claude-opus-4-6)
    OPENROUTER_BASE_URL="$(normalize_base_url "${OPENROUTER_BASE_URL:-https://openrouter.ai/api/v1}")"
    if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
      echo "ERROR: OPENROUTER_API_KEY is required for $HARNESS_MODEL." >&2
      exit 2
    fi
    if [[ "$OPENROUTER_BASE_URL" != "https://openrouter.ai/api/v1" ]]; then
      echo "ERROR: $HARNESS_MODEL should use OpenRouter, got: $OPENROUTER_BASE_URL" >&2
      exit 2
    fi
    export OPENROUTER_API_KEY OPENROUTER_BASE_URL
    ;;
  *)
    echo "ERROR: unsupported badcase rerun model: $HARNESS_MODEL" >&2
    exit 2
    ;;
esac

unset OPENCLAW_TRAJECTORY_DIR
unset MASP_MCP_LOG_DIR
export MASP_OPENCLAW_VALIDATE_TIMEOUT="${MASP_OPENCLAW_VALIDATE_TIMEOUT:-120}"

openclaw plugins list --enabled --json > /tmp/openclaw_plugins_enabled.json || exit 2

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

declare -A TASK_YAML_BY_ID=()
while IFS= read -r abs_yaml; do
  task_id="$(basename "$abs_yaml" .yaml)"
  TASK_YAML_BY_ID["$task_id"]="${abs_yaml#$REPO_ROOT/}"
done < <(find "$REPO_ROOT/masp/tasks" -type f -name '*.yaml' | sort)

TASK_QUEUE=()
MISSING_TASK_IDS=()
for task_id in "$@"; do
  if [[ -z "${TASK_YAML_BY_ID[$task_id]+x}" ]]; then
    MISSING_TASK_IDS+=("$task_id")
    continue
  fi
  TASK_QUEUE+=("${TASK_YAML_BY_ID[$task_id]}")
done

if (( ${#MISSING_TASK_IDS[@]} > 0 )); then
  echo "ERROR: missing task YAMLs for IDs: ${MISSING_TASK_IDS[*]}" >&2
  exit 2
fi

if [[ -n "$EXPECTED_BADCASE_COUNT" && "${#TASK_QUEUE[@]}" -ne "$EXPECTED_BADCASE_COUNT" ]]; then
  echo "ERROR: expected $EXPECTED_BADCASE_COUNT badcase tasks, got ${#TASK_QUEUE[@]}" >&2
  exit 2
fi

printf '%s\n' "${TASK_QUEUE[@]}" > "$TASK_MANIFEST"

echo "========================================"
echo "HarnessAudit-Bench OpenClaw Badcase Re-Run — $(date -Iseconds)"
echo "  Harness:       $HARNESS"
echo "  Model:         $HARNESS_MODEL"
echo "  Judge:         $([ "$SKIP_JUDGE" = "0" ] && echo "ON ($JUDGE_MODEL)" || echo "OFF")"
echo "  Task workers:  $RUN_ALL_WORKERS"
echo "  Judge workers: $JUDGE_WORKERS"
echo "  Timeout:       ${TIMEOUT}s"
echo "  Force rerun:   $FORCE_RERUN"
echo "  Task count:    ${#TASK_QUEUE[@]}"
echo "  Results:       $HARNESS_RESULTS_DIR"
echo "  Traces:        $HARNESS_TRACES_DIR"
echo "  Log:           $LOG"
echo "  Manifest:      $TASK_MANIFEST"
echo "========================================"

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
    --output "$HARNESS_RESULTS_DIR" \
    --trace-dir "$HARNESS_TRACES_DIR" \
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

TOTAL=${#TASK_QUEUE[@]}
SKIPPED=$(count_status skipped)
PASSED=$(count_status passed)
FAILED=$(count_status failed)
echo "EXPERIMENT_DONE $(date -Iseconds) — total=${TOTAL} skipped=${SKIPPED} passed=${PASSED} failed=${FAILED}"
