#!/usr/bin/env bash
# Run the full single-agent HarnessAudit task suite with openclaw_local.

set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage: [VAR=...] bash single_agent/run_sa.sh

Runs all single_agent/tasks/*.yaml files unless TASK_FILE or TASK_LIST is set.

Common variables:
  MODEL=gpt-5.4                           model passed to openclaw_local
  TASK_WORKERS=1                           concurrent task runs
  JUDGE_MODEL=gpt-5.4                      judge model
  JUDGE_WORKERS=4                          concurrent judge calls per task
  SKIP_JUDGE=1                             skip LLM judges
  SKIP_EXISTING=1                          skip tasks with existing result JSONs
  PYTHON_CMD="conda run -n masp-bench python"
  TASK_FILE=single_agent/tasks/finance/sa-fin-t1.yaml
  TASK_LIST=/path/to/tasks.txt
  EXTRA_ARGS="..."
EOF
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

FRAMEWORK="openclaw_local"
HARNESS="openclaw_local"
MODEL="${MODEL:-${SA_MODEL:-gpt-5.4}}"
TASK_WORKERS="${TASK_WORKERS:-${WORKERS:-1}}"
JUDGE_WORKERS="${JUDGE_WORKERS:-4}"
JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.4}"
SKIP_JUDGE="${SKIP_JUDGE:-0}"
AGENT_TIMEOUT="${AGENT_TIMEOUT:-300}"
MAX_TURNS="${MAX_TURNS:-15}"
OUTPUT_DIR="${OUTPUT_DIR:-single_agent/results}"
TRACE_DIR="${TRACE_DIR:-single_agent/traces}"
TASK_ROOT="${TASK_ROOT:-single_agent/tasks}"
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

echo "[run_sa] capturing OpenClaw enabled plugin snapshot"
openclaw plugins list --enabled --json > /tmp/openclaw_plugins_enabled.json

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
echo "[run_sa] framework=$FRAMEWORK harness=$HARNESS model=$MODEL tasks=$TOTAL_TASKS task_workers=$TASK_WORKERS judge_model=$JUDGE_MODEL judge_workers=$JUDGE_WORKERS"

run_one() {
  local task="$1"
  local task_id
  task_id="$(basename "$task" .yaml)"
  local log_file="$LOG_DIR/${task_id}.log"
  local result_scope="$OUTPUT_DIR/$HARNESS/$MODEL_DIR"

  if [[ "$SKIP_EXISTING" == "1" ]] && compgen -G "$result_scope/${task_id}_*.json" > /dev/null; then
    echo "[run_sa] skip existing $task_id"
    return 0
  fi

  local args=(
    -m single_agent run "$task"
    --framework "$FRAMEWORK"
    --model "$MODEL"
    --output "$OUTPUT_DIR"
    --trace-dir "$TRACE_DIR"
    --max-turns "$MAX_TURNS"
    --agent-timeout "$AGENT_TIMEOUT"
    --judge-model "$JUDGE_MODEL"
    --judge-workers "$JUDGE_WORKERS"
  )
  if [[ "$SKIP_JUDGE" == "1" || "$SKIP_JUDGE" == "true" || "$SKIP_JUDGE" == "yes" ]]; then
    args+=(--skip-judge)
  fi
  if (( ${#EXTRA_ARGV[@]} > 0 )); then
    args+=("${EXTRA_ARGV[@]}")
  fi

  echo "[run_sa] start $task_id -> $log_file"
  if "${PYTHON_CMD[@]}" "${args[@]}" > "$log_file" 2>&1; then
    echo "[run_sa] done  $task_id"
  else
    local status=$?
    echo "[run_sa] fail  $task_id status=$status log=$log_file" >&2
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
