#!/usr/bin/env bash
# Full benchmark run: ALL non-SDE tasks × selected harnesses.
# Default: LLM judge ON, all 3 harnesses enabled.
#
# Usage:
#   nohup ./run_all.sh > run_all.log 2>&1 &          # background, all defaults
#   HARNESS=openclaw ./run_all.sh                      # single harness
#   HARNESS=claude,codex ./run_all.sh                  # two harnesses
#   SKIP_JUDGE=1 ./run_all.sh                          # disable LLM judge
#   RESUME_FROM=fin-t12 ./run_all.sh                   # resume after crash
set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$PKG_ROOT/.." && pwd)"
cd "$REPO_ROOT" || exit 1

# Source .env for API keys
set -a && source "$REPO_ROOT/.env" 2>/dev/null && set +a || true

PY="${MASP_BENCH_PYTHON:-/home/chengzhi.ucsb/.conda/envs/masp-bench/bin/python}"
LOG="$PKG_ROOT/run_all.log"
TIMEOUT="${AGENT_TIMEOUT:-360}"
CLAUDE_MODEL="${MASP_CLAUDE_MODEL:-claude-sonnet-4-6}"
CODEX_MODEL="${MASP_CODEX_MODEL:-gpt-5.4}"
OPENCLAW_MODEL="${MASP_OPENCLAW_MODEL:-gpt-5.4}"
JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.4-xhigh}"
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
JUDGE_ARGS=(--judge-model "$JUDGE_MODEL")
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

  # If trace exists (agent completed) but no report (judge failed), rejudge only
  local trace_pattern="${TRACES_DIR}/${task_id}_*-${harness}-*.jsonl"
  local trace_file
  trace_file=$(compgen -G "$trace_pattern" 2>/dev/null | head -1)
  if [[ -n "$trace_file" ]]; then
    local trace_size
    trace_size=$(wc -c < "$trace_file")
    if [[ "$trace_size" -gt 5000 ]]; then
      echo "--- $(date -Iseconds) REJUDGE task=${task_id} harness=${harness} ---" | tee -a "$LOG"
      if "$PY" -m masp_bench rejudge "$trace_file" \
          --judge-model "$JUDGE_MODEL" \
          --output "$RESULTS_DIR/" \
          "${JUDGE_ARGS[@]}" 2>&1 | tee -a "$LOG"; then
        ((DONE+=1))
      else
        echo "  REJUDGE_FAILED ${task_id} ${harness} (exit $?)" | tee -a "$LOG"
        ((FAILED+=1))
      fi
      return 0
    fi
    # Small trace (<5KB) = agent didn't complete — remove and do full run
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
echo "  Skipped:     $SKIPPED (trace exists)" | tee -a "$LOG"
echo "========================================" | tee -a "$LOG"
