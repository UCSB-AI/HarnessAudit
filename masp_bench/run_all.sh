#!/usr/bin/env bash
# Full benchmark run: ALL non-SDE tasks × selected harnesses.
# Default: LLM judge ON, all 3 harnesses enabled.
#
# Usage:
#   nohup ./run_all.sh &                              # background, logs fixed below
#   HARNESS=openclaw ./run_all.sh                      # single harness
#   HARNESS=claude,codex ./run_all.sh                  # two harnesses
#   SKIP_JUDGE=1 ./run_all.sh                          # disable LLM judge
#   JUDGE_WORKERS=1 ./run_all.sh                       # serial judge calls
#   RESUME_FROM=fin-t12 ./run_all.sh                   # resume after crash
set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$PKG_ROOT/.." && pwd)"
cd "$REPO_ROOT" || exit 1

STDOUT_LOG="$PKG_ROOT/run_all_stdout.log"
RUN_INFO="$PKG_ROOT/run_all.runinfo"
HEARTBEAT_FILE="$PKG_ROOT/run_all.heartbeat"
LOCK_DIR="$PKG_ROOT/run_all.lock"
HEARTBEAT_INTERVAL="${RUN_ALL_HEARTBEAT_INTERVAL:-30}"
STALE_AFTER="${RUN_ALL_STALE_AFTER:-120}"
HEARTBEAT_PID=""

touch "$STDOUT_LOG"
if [[ "${MASP_RUN_ALL_STDIO_REDIRECTED:-0}" != "1" ]]; then
  export MASP_RUN_ALL_STDIO_REDIRECTED=1
  exec > >(tee -a "$STDOUT_LOG") 2>&1
fi

is_live_pid() {
  local pid=$1
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null
}

heartbeat_is_fresh() {
  [[ -f "$HEARTBEAT_FILE" ]] || return 1
  local now heartbeat_mtime
  now=$(date +%s)
  heartbeat_mtime=$(stat -c %Y "$HEARTBEAT_FILE" 2>/dev/null || echo 0)
  (( now - heartbeat_mtime <= STALE_AFTER ))
}

write_lock_metadata() {
  {
    echo "pid=$$"
    echo "host=$(hostname -f 2>/dev/null || hostname)"
    echo "started_at=$(date -Iseconds)"
    printf 'cmdline='
    tr '\0' ' ' < "/proc/$$/cmdline" 2>/dev/null || printf '%s' "$0 $*"
    printf '\n'
  } > "$LOCK_DIR/metadata"
  echo "$$" > "$LOCK_DIR/pid"
}

write_run_info() {
  local host cmdline
  host=$(hostname -f 2>/dev/null || hostname)
  cmdline=$(tr '\0' ' ' < "/proc/$$/cmdline" 2>/dev/null || printf '%s' "$0 $*")
  {
    echo "started_at=$(date -Iseconds)"
    echo "host=$host"
    echo "pid=$$"
    echo "ppid=$PPID"
    echo "cwd=$PWD"
    echo "cmdline=$cmdline"
    echo "script=$0"
    echo "args=$*"
    echo "stdout_log=$STDOUT_LOG"
    echo "run_log=$PKG_ROOT/run_all.log"
    echo "heartbeat=$HEARTBEAT_FILE"
    echo "lock_dir=$LOCK_DIR"
  } > "$RUN_INFO"
}

acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    write_lock_metadata "$@"
    return 0
  fi

  local existing_pid
  existing_pid=$(cat "$LOCK_DIR/pid" 2>/dev/null || true)
  if heartbeat_is_fresh || is_live_pid "$existing_pid"; then
    echo "ERROR: run_all.sh is already running with pid=${existing_pid}" >&2
    echo "       lock_dir=$LOCK_DIR" >&2
    echo "       heartbeat=$HEARTBEAT_FILE" >&2
    exit 1
  fi

  echo "Removing stale lock: $LOCK_DIR (old pid=${existing_pid:-unknown})" >&2
  rm -rf "$LOCK_DIR"
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "ERROR: failed to acquire lock after removing stale lock: $LOCK_DIR" >&2
    exit 1
  fi
  write_lock_metadata "$@"
}

start_heartbeat() {
  (
    while true; do
      {
        echo "time=$(date -Iseconds)"
        echo "host=$(hostname -f 2>/dev/null || hostname)"
        echo "pid=$$"
        echo "heartbeat_pid=$BASHPID"
        echo "ppid=$PPID"
        echo "run_info=$RUN_INFO"
        echo "stdout_log=$STDOUT_LOG"
      } > "$HEARTBEAT_FILE"
      sleep "$HEARTBEAT_INTERVAL"
    done
  ) &
  HEARTBEAT_PID=$!
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [[ -n "$HEARTBEAT_PID" ]] && kill -0 "$HEARTBEAT_PID" 2>/dev/null; then
    kill "$HEARTBEAT_PID" 2>/dev/null || true
    wait "$HEARTBEAT_PID" 2>/dev/null || true
  fi
  {
    echo "stopped_at=$(date -Iseconds)"
    echo "exit_status=$status"
  } >> "$RUN_INFO"
  if [[ -d "$LOCK_DIR" ]] && [[ "$(cat "$LOCK_DIR/pid" 2>/dev/null || true)" == "$$" ]]; then
    rm -rf "$LOCK_DIR"
  fi
  exit "$status"
}

acquire_lock "$@"
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
write_run_info "$@"
start_heartbeat

# Source .env for API keys
set -a && source "$REPO_ROOT/.env" 2>/dev/null && set +a || true

PY="${MASP_BENCH_PYTHON:-/home/chengzhi.ucsb/.conda/envs/masp-bench/bin/python}"
LOG="$PKG_ROOT/run_all.log"
TIMEOUT="${AGENT_TIMEOUT:-360}"
CLAUDE_MODEL="${MASP_CLAUDE_MODEL:-claude-sonnet-4-6}"
CODEX_MODEL="${MASP_CODEX_MODEL:-gpt-5.4}"
OPENCLAW_MODEL="${MASP_OPENCLAW_MODEL:-gpt-5.4}"
JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.4-xhigh}"
JUDGE_WORKERS="${JUDGE_WORKERS:-4}"
SKIP_JUDGE="${SKIP_JUDGE:-0}"
# HARNESS="${HARNESS:-claude,codex,openclaw}"
HARNESS="${HARNESS:-openclaw}"
RESUME_FROM="${RESUME_FROM:-}"
RESULTS_DIR="$PKG_ROOT/results"
TRACES_DIR="$PKG_ROOT/traces"

mkdir -p "$RESULTS_DIR" "$TRACES_DIR"

# Parse harness list
IFS=',' read -ra HARNESSES <<< "$HARNESS"

# Judge args
JUDGE_ARGS=(--judge-model "$JUDGE_MODEL" --judge-workers "$JUDGE_WORKERS")
case "${SKIP_JUDGE,,}" in
  1|true|yes|y) JUDGE_ARGS+=(--skip-judge) ;;
esac

# Counters
TOTAL=0
DONE=0
FAILED=0
SKIPPED=0
RESUMING=true
[[ -z "$RESUME_FROM" ]] && RESUMING=false

echo "========================================" | tee -a "$LOG"
echo "MASP_Bench Full Run — $(date -Iseconds)" | tee -a "$LOG"
echo "  Harnesses:  ${HARNESSES[*]}" | tee -a "$LOG"
echo "  Judge:      $([ "$SKIP_JUDGE" = "0" ] && echo "ON ($JUDGE_MODEL)" || echo "OFF")" | tee -a "$LOG"
echo "  Judge workers: $JUDGE_WORKERS" | tee -a "$LOG"
echo "  Timeout:    ${TIMEOUT}s" | tee -a "$LOG"
echo "  Resume:     ${RESUME_FROM:-none}" | tee -a "$LOG"
echo "========================================" | tee -a "$LOG"

run_harness() {
  local yaml=$1 harness=$2 model=$3
  local task_id
  task_id=$(basename "$yaml" .yaml)

  # Skip if report already exists (agent + judge both completed)
  local report_pattern="${RESULTS_DIR}/${task_id}_*-${harness}-*.json"
  if compgen -G "$report_pattern" > /dev/null 2>&1; then
    echo "  SKIP ${task_id} ${harness} (report exists)" | tee -a "$LOG"
    ((SKIPPED+=1))
    return 0
  fi

  # If a trace exists without a report, treat it as incomplete/orphaned and rerun.
  local trace_pattern="${TRACES_DIR}/${task_id}_*-${harness}-*.jsonl"
  local trace_file
  trace_file=$(compgen -G "$trace_pattern" 2>/dev/null | head -1 || true)
  if [[ -n "$trace_file" ]]; then
    echo "  Removing orphan trace before rerun: $trace_file" | tee -a "$LOG"
    rm -f "$trace_file"
  fi

  echo "--- $(date -Iseconds) task=${task_id} harness=${harness} model=${model} ---" | tee -a "$LOG"
  if "$PY" -m masp_bench run "$yaml" \
      --framework clawteam \
      --harness "$harness" \
      --model "$model" \
      --agent-timeout "$TIMEOUT" \
      --output "$RESULTS_DIR/" \
      --trace-dir "$TRACES_DIR/" \
      "${JUDGE_ARGS[@]}" 2>&1 | tee -a "$LOG"; then
    ((DONE+=1))
  else
    echo "  FAILED ${task_id} ${harness} (exit $?)" | tee -a "$LOG"
    ((FAILED+=1))
  fi
}

run_task() {
  local rel_yaml=$1
  local task_id
  task_id=$(basename "$rel_yaml" .yaml)

  # Resume logic
  if $RESUMING; then
    if [[ "$task_id" == "$RESUME_FROM" ]]; then
      RESUMING=false
      echo "  Resuming from ${task_id}" | tee -a "$LOG"
    else
      return 0
    fi
  fi

  ((TOTAL+=1))

  for h in "${HARNESSES[@]}"; do
    case "$h" in
      claude)   run_harness "$rel_yaml" claude "$CLAUDE_MODEL" ;;
      codex)    run_harness "$rel_yaml" codex "$CODEX_MODEL" ;;
      openclaw) run_harness "$rel_yaml" openclaw "$OPENCLAW_MODEL" ;;
      *)        echo "  Unknown harness: $h" | tee -a "$LOG" ;;
    esac
  done
}

# ── Discover all non-SDE task YAMLs ──
DOMAIN_DIRS=(
  daily_life
  ecommerce
  finance
  healthcare
  legal_compliance
  office
  social_interaction
)

shopt -s nullglob globstar
for domain_dir in "${DOMAIN_DIRS[@]}"; do
  yamls=("$PKG_ROOT"/tasks/"${domain_dir}"/**/*.yaml)
  mapfile -t yamls < <(printf '%s\n' "${yamls[@]}" | sort)

  for yaml in "${yamls[@]}"; do
    # Skip asset YAMLs
    [[ "$yaml" == *"/assets/"* ]] && continue
    rel=${yaml#"$PKG_ROOT"/}
    run_task "$rel"
  done
done

echo "========================================" | tee -a "$LOG"
echo "DONE $(date -Iseconds)" | tee -a "$LOG"
echo "  Total tasks: $TOTAL" | tee -a "$LOG"
echo "  Completed:   $DONE" | tee -a "$LOG"
echo "  Failed:      $FAILED" | tee -a "$LOG"
echo "  Skipped:     $SKIPPED (report exists)" | tee -a "$LOG"
echo "========================================" | tee -a "$LOG"
