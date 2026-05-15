#!/usr/bin/env bash
# Run the full multi-agent HarnessAudit task suite.

set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage: [VAR=...] bash multi_agent/run_ma.sh

Runs all multi_agent/tasks/*.yaml files unless TASK_FILE or TASK_LIST is set.

Common variables:
  HARNESS=openclaw|claude|codex|oai|adk   default: openclaw
  FRAMEWORK=clawteam|oai|adk              default: clawteam, or HARNESS for oai/adk
  MODEL=gpt-5.4                           model passed to the harness
  TASK_WORKERS=1                           concurrent task runs
  JUDGE_MODEL=gpt-5.4                      judge model
  JUDGE_WORKERS=4                          concurrent judge calls per task
  SKIP_JUDGE=1                             skip LLM judges
  SKIP_EXISTING=1                          skip tasks with existing result JSONs
  PYTHON_CMD="conda run -n masp-bench python"
  EXTRA_ARGS="--allow-stale-perturbation"
EOF
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

HARNESS="${HARNESS:-${MASP_HARNESS:-openclaw}}"
MODEL="${MODEL:-${MASP_MODEL:-gpt-5.4}}"
if [[ -n "${FRAMEWORK:-}" ]]; then
  FRAMEWORK="$FRAMEWORK"
elif [[ "$HARNESS" == "oai" || "$HARNESS" == "adk" ]]; then
  FRAMEWORK="$HARNESS"
else
  FRAMEWORK="clawteam"
fi

TASK_WORKERS="${TASK_WORKERS:-${WORKERS:-1}}"
JUDGE_WORKERS="${JUDGE_WORKERS:-4}"
JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.4}"
SKIP_JUDGE="${SKIP_JUDGE:-0}"
AGENT_TIMEOUT="${AGENT_TIMEOUT:-300}"
MAX_TURNS="${MAX_TURNS:-30}"
OUTPUT_DIR="${OUTPUT_DIR:-multi_agent/results}"
TRACE_DIR="${TRACE_DIR:-multi_agent/traces}"
TASK_ROOT="${TASK_ROOT:-multi_agent/tasks}"
TASK_LIST="${TASK_LIST:-}"
TASK_FILE="${TASK_FILE:-}"
SKIP_EXISTING="${SKIP_EXISTING:-0}"
EXTRA_ARGS="${EXTRA_ARGS:-}"
PYTHON_CMD_STR="${PYTHON_CMD:-python}"
read -r -a PYTHON_CMD <<< "$PYTHON_CMD_STR"
read -r -a EXTRA_ARGV <<< "$EXTRA_ARGS"

MODEL_DIR="${MODEL//\//__}"
LOG_DIR="${LOG_DIR:-$OUTPUT_DIR/_run_logs/$HARNESS/$MODEL_DIR}"
mkdir -p "$LOG_DIR" "$OUTPUT_DIR" "$TRACE_DIR"

if [[ "$HARNESS" == "openclaw" ]]; then
  echo "[run_ma] capturing OpenClaw enabled plugin snapshot"
  openclaw plugins list --enabled --json > /tmp/openclaw_plugins_enabled.json
fi

TASKS_TMP="$(mktemp)"
cleanup() {
  rm -f "$TASKS_TMP"
}
trap cleanup EXIT

if [[ -n "$TASK_FILE" ]]; then
  printf '%s\n' "$TASK_FILE" > "$TASKS_TMP"
elif [[ -n "$TASK_LIST" ]]; then
  cp "$TASK_LIST" "$TASKS_TMP"
else
  find "$TASK_ROOT" -name '*.yaml' | sort > "$TASKS_TMP"
fi

TOTAL_TASKS="$(wc -l < "$TASKS_TMP" | tr -d ' ')"
echo "[run_ma] framework=$FRAMEWORK harness=$HARNESS model=$MODEL tasks=$TOTAL_TASKS task_workers=$TASK_WORKERS judge_model=$JUDGE_MODEL judge_workers=$JUDGE_WORKERS"

run_one() {
  local task="$1"
  local task_id
  task_id="$(basename "$task" .yaml)"
  local log_file="$LOG_DIR/${task_id}.log"
  local result_scope="$OUTPUT_DIR/$HARNESS/$MODEL_DIR"

  if [[ "$SKIP_EXISTING" == "1" ]] && compgen -G "$result_scope/${task_id}_*.json" > /dev/null; then
    echo "[run_ma] skip existing $task_id"
    return 0
  fi

  local args=(
    -m multi_agent run "$task"
    --framework "$FRAMEWORK"
    --model "$MODEL"
    --output "$OUTPUT_DIR"
    --trace-dir "$TRACE_DIR"
    --max-turns "$MAX_TURNS"
    --agent-timeout "$AGENT_TIMEOUT"
    --judge-model "$JUDGE_MODEL"
    --judge-workers "$JUDGE_WORKERS"
  )
  if [[ "$FRAMEWORK" == "clawteam" ]]; then
    args+=(--harness "$HARNESS")
  fi
  if [[ "$SKIP_JUDGE" == "1" || "$SKIP_JUDGE" == "true" || "$SKIP_JUDGE" == "yes" ]]; then
    args+=(--skip-judge)
  fi
  if (( ${#EXTRA_ARGV[@]} > 0 )); then
    args+=("${EXTRA_ARGV[@]}")
  fi

  echo "[run_ma] start $task_id -> $log_file"
  if "${PYTHON_CMD[@]}" "${args[@]}" > "$log_file" 2>&1; then
    echo "[run_ma] done  $task_id"
  else
    local status=$?
    echo "[run_ma] fail  $task_id status=$status log=$log_file" >&2
    return "$status"
  fi
}

fail=0
active=0
while IFS= read -r task; do
  [[ -z "$task" ]] && continue
  run_one "$task" &
  active=$((active + 1))
  if (( active >= TASK_WORKERS )); then
    if ! wait -n; then
      fail=1
    fi
    active=$((active - 1))
  fi
done < "$TASKS_TMP"

while (( active > 0 )); do
  if ! wait -n; then
    fail=1
  fi
  active=$((active - 1))
done

exit "$fail"
