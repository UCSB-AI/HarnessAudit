#!/usr/bin/env bash
# Targeted reruns for repaired badcase tasks.
#
# Groups:
#   codex_badcases              88 structural badcases for Codex GPT-5.4, excluding the GPT-5.4 TCR top10.
#   openclaw_sonnet46_sample60  30 sampled existing OpenClaw Sonnet badcases + 30 sampled missing OpenClaw tasks.
#   claude_sonnet46_sample40    40 sampled Claude Code Sonnet badcases, excluding the GPT-5.4 TCR top10.
#   all                         Run the three groups sequentially.
#
# Examples:
#   RUN_GROUP=codex_badcases RUN_ALL_WORKERS=2 ./masp/run_targeted_badcase_reruns.sh
#   RUN_GROUP=openclaw_sonnet46_sample60 RUN_ALL_WORKERS=2 ./masp/run_targeted_badcase_reruns.sh
#   RUN_GROUP=openclaw_sonnet46_failed13 RUN_ALL_WORKERS=1 ./masp/run_targeted_badcase_reruns.sh
#   RUN_GROUP=claude_sonnet46_sample40 RUN_ALL_WORKERS=2 ./masp/run_targeted_badcase_reruns.sh
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
if command -v conda >/dev/null 2>&1; then
  conda activate masp-bench || exit 2
fi

GROUP="${1:-${RUN_GROUP:-all}}"
if [[ "$GROUP" == "all" ]]; then
  for child_group in codex_badcases openclaw_sonnet46_sample60 claude_sonnet46_sample40; do
    RUN_GROUP="$child_group" "$0" "$child_group" || exit $?
  done
  exit 0
fi

PY="${HARNESSAUDIT_BENCH_PYTHON:-python}"
RUN_TS="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="$PKG_ROOT/logs"
TIMEOUT="${AGENT_TIMEOUT:-360}"
JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.4-nano}"
RUN_ALL_WORKERS="${RUN_ALL_WORKERS:-2}"
JUDGE_WORKERS="${JUDGE_WORKERS:-2}"
SKIP_JUDGE="${SKIP_JUDGE:-0}"
FORCE_RERUN="${FORCE_RERUN:-1}"
DRY_RUN="${DRY_RUN:-0}"

TOP10_EXCLUDED_TASK_IDS=(
  hc-t45 hc-t39 hc-t43 ec-t21 hc-t34
  si-t3 fin-t48 lc-t10 hc-t42 off-t17
)

# All structural badcases from the current audit pass, excluding TOP10_EXCLUDED_TASK_IDS.
CODEX_BADCASE_TASK_IDS=(
  dl-t15 dl-t16 dl-t17 dl-t18 dl-t19 dl-t20 dl-t21 dl-t22 dl-t23 dl-t9
  ec-t16 ec-t26 ec-t36 ec-t40 ec-t41 ec-t45 ec-t6 ec-t7
  fin-t16 fin-t18 fin-t19 fin-t30 fin-t35 fin-t36 fin-t37 fin-t38 fin-t39 fin-t40 fin-t41 fin-t42 fin-t43 fin-t44 fin-t45 fin-t46 fin-t47
  hc-t11 hc-t12 hc-t13 hc-t14 hc-t15 hc-t16 hc-t18 hc-t33 hc-t35 hc-t36 hc-t37 hc-t38 hc-t40 hc-t41 hc-t44 hc-t46 hc-t9
  lc-t12 lc-t16 lc-t17 lc-t35 lc-t36 lc-t9
  off-t10 off-t18 off-t37 off-t38 off-t39 off-t40 off-t42
  si-t1 si-t10 si-t11 si-t12 si-t13 si-t14 si-t15 si-t16 si-t17 si-t18 si-t19 si-t2 si-t20 si-t22 si-t23 si-t24 si-t26 si-t27 si-t28 si-t4 si-t7 si-t8 si-t9
)

# Fixed-seed sample from the 72 OpenClaw/Sonnet badcase traces, excluding TOP10_EXCLUDED_TASK_IDS.
OPENCLAW_SONNET_EXISTING_BADCASE_SAMPLE_TASK_IDS=(
  dl-t16 dl-t17 dl-t21
  ec-t40 ec-t41 ec-t45 ec-t7
  fin-t35 fin-t38 fin-t46 fin-t47
  hc-t18 hc-t36 hc-t40 hc-t41 hc-t46
  lc-t16 lc-t36 lc-t9
  off-t10 off-t18
  si-t11 si-t15 si-t16 si-t18 si-t20 si-t26 si-t27 si-t28 si-t7
)

# Fixed-seed sample from tasks missing in openclaw/claude-sonnet-4-6 (210 - 158), excluding TOP10_EXCLUDED_TASK_IDS.
OPENCLAW_SONNET_MISSING_SAMPLE_TASK_IDS=(
  dl-t20 dl-t23
  ec-t13 ec-t14 ec-t33 ec-t34 ec-t38
  fin-t11 fin-t17 fin-t21 fin-t33 fin-t40 fin-t6
  hc-t10 hc-t11 hc-t14 hc-t44 hc-t7 hc-t9
  lc-t35 lc-t5 lc-t8
  off-t12 off-t40 off-t41
  si-t14 si-t2 si-t23 si-t24 si-t4
)

OPENCLAW_SONNET_FAILED13_TASK_IDS=(
  hc-t7 hc-t9
  lc-t35 lc-t5 lc-t8
  off-t12 off-t40 off-t41
  si-t14 si-t2 si-t23 si-t24 si-t4
)

# Fixed-seed sample from structural badcases for Claude Code/Sonnet, excluding TOP10_EXCLUDED_TASK_IDS.
CLAUDE_SONNET_BADCASE_SAMPLE_TASK_IDS=(
  dl-t15 dl-t18 dl-t19 dl-t20 dl-t21
  ec-t16 ec-t40
  fin-t18 fin-t30 fin-t36 fin-t40 fin-t43 fin-t44 fin-t47
  hc-t12 hc-t13 hc-t16 hc-t18 hc-t35 hc-t37 hc-t41 hc-t44 hc-t9
  lc-t9
  off-t10 off-t37 off-t38 off-t40
  si-t13 si-t15 si-t16 si-t19 si-t2 si-t20 si-t26 si-t28 si-t4 si-t7 si-t8 si-t9
)

case "$GROUP" in
  codex_badcases)
    HARNESS="codex"
    HARNESS_MODEL="${CODEX_MODEL:-gpt-5.4}"
    RERUN_LABEL="badcases_minus_gpt54_top10"
    EXPECTED_TASK_COUNT=88
    TASK_IDS=("${CODEX_BADCASE_TASK_IDS[@]}")
    ;;
  openclaw_sonnet46_sample60)
    HARNESS="openclaw"
    HARNESS_MODEL="${OPENCLAW_MODEL:-openrouter-claude-sonnet-4-6}"
    RERUN_LABEL="sonnet46_openrouter_30bad_30missing_minus_gpt54_top10"
    EXPECTED_TASK_COUNT=60
    TASK_IDS=(
      "${OPENCLAW_SONNET_EXISTING_BADCASE_SAMPLE_TASK_IDS[@]}"
      "${OPENCLAW_SONNET_MISSING_SAMPLE_TASK_IDS[@]}"
    )
    ;;
  openclaw_sonnet46_failed13)
    HARNESS="openclaw"
    HARNESS_MODEL="${OPENCLAW_MODEL:-openrouter-claude-sonnet-4-6}"
    RERUN_LABEL="sonnet46_openrouter_failed13_retry"
    EXPECTED_TASK_COUNT=13
    TASK_IDS=("${OPENCLAW_SONNET_FAILED13_TASK_IDS[@]}")
    ;;
  claude_sonnet46_sample40)
    HARNESS="claude"
    HARNESS_MODEL="${CLAUDE_MODEL:-claude-sonnet-4-6}"
    RERUN_LABEL="sonnet46_badcase40_minus_gpt54_top10"
    EXPECTED_TASK_COUNT=40
    TASK_IDS=("${CLAUDE_SONNET_BADCASE_SAMPLE_TASK_IDS[@]}")
    ;;
  *)
    echo "ERROR: unknown RUN_GROUP '$GROUP'." >&2
    echo "       Use: codex_badcases, openclaw_sonnet46_sample60, claude_sonnet46_sample40, or all." >&2
    exit 2
    ;;
esac

if ! [[ "$RUN_ALL_WORKERS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: RUN_ALL_WORKERS must be a positive integer, got: $RUN_ALL_WORKERS" >&2
  exit 2
fi
if ! [[ "$JUDGE_WORKERS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: JUDGE_WORKERS must be a positive integer, got: $JUDGE_WORKERS" >&2
  exit 2
fi

declare -A EXCLUDED_TASK_SET=()
for task_id in "${TOP10_EXCLUDED_TASK_IDS[@]}"; do
  EXCLUDED_TASK_SET["$task_id"]=1
done
for task_id in "${TASK_IDS[@]}"; do
  if [[ -n "${EXCLUDED_TASK_SET[$task_id]+x}" ]]; then
    echo "ERROR: excluded GPT-5.4 top10 task appeared in $GROUP: $task_id" >&2
    exit 2
  fi
done

if [[ "${#TASK_IDS[@]}" -ne "$EXPECTED_TASK_COUNT" ]]; then
  echo "ERROR: $GROUP expected $EXPECTED_TASK_COUNT tasks, got ${#TASK_IDS[@]}" >&2
  exit 2
fi

if [[ "$GROUP" == openclaw_sonnet46_* ]]; then
  if [[ -s "$HOME/.nvm/nvm.sh" ]]; then
    # shellcheck disable=SC1090
    source "$HOME/.nvm/nvm.sh"
    NVM_DEFAULT_NODE="$(nvm which default 2>/dev/null || true)"
    if [[ -x "$NVM_DEFAULT_NODE" ]]; then
      export PATH="$(dirname "$NVM_DEFAULT_NODE"):$PATH"
    fi
  fi
  OPENROUTER_BASE_URL="${OPENROUTER_BASE_URL:-https://openrouter.ai/api/v1}"
  OPENROUTER_BASE_URL="${OPENROUTER_BASE_URL%/}"
  if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
    echo "ERROR: OPENROUTER_API_KEY is required for $GROUP." >&2
    exit 2
  fi
  if [[ "$OPENROUTER_BASE_URL" != "https://openrouter.ai/api/v1" ]]; then
    echo "ERROR: $GROUP must use OpenRouter, got: $OPENROUTER_BASE_URL" >&2
    exit 2
  fi
  unset OPENCLAW_TRAJECTORY_DIR
  unset MASP_MCP_LOG_DIR
  export OPENROUTER_API_KEY OPENROUTER_BASE_URL
  export MASP_OPENCLAW_VALIDATE_TIMEOUT="${MASP_OPENCLAW_VALIDATE_TIMEOUT:-120}"
  NODE_VERSION="$(node -p 'process.versions.node' 2>/dev/null || true)"
  if [[ -z "$NODE_VERSION" ]]; then
    echo "ERROR: node is required for OpenClaw but was not found on PATH." >&2
    exit 2
  fi
  "$PY" - "$NODE_VERSION" <<'PY'
import sys
parts = tuple(int(part) for part in sys.argv[1].split(".")[:3])
required = (22, 12, 0)
if parts < required:
    raise SystemExit(
        f"ERROR: OpenClaw requires Node >= 22.12.0, got {sys.argv[1]}"
    )
PY
  if [[ ! "${DRY_RUN,,}" =~ ^(1|true|yes|y)$ ]]; then
    openclaw plugins list --enabled --json > /tmp/openclaw_plugins_enabled.json || exit 2
  fi
fi

MODEL_DIR="${HARNESS_MODEL//\//__}"
HARNESS_RESULTS_DIR="$PKG_ROOT/results/$HARNESS/rerun/$RERUN_LABEL/$MODEL_DIR"
HARNESS_TRACES_DIR="$PKG_ROOT/traces/$HARNESS/rerun/$RERUN_LABEL/$MODEL_DIR"
STATUS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/masp-targeted-rerun-status.XXXXXX")"
TASK_MANIFEST="$LOG_DIR/targeted_${GROUP}_tasks_${RUN_TS}.txt"
LOG="$LOG_DIR/targeted_${GROUP}_${RUN_TS}.log"

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
for task_id in "${TASK_IDS[@]}"; do
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

printf '%s\n' "${TASK_QUEUE[@]}" > "$TASK_MANIFEST"

echo "========================================"
echo "HarnessAudit-Bench Targeted Re-Run — $(date -Iseconds)"
echo "  Group:         $GROUP"
echo "  Harness:       $HARNESS"
echo "  Model:         $HARNESS_MODEL"
echo "  Judge:         $([ "$SKIP_JUDGE" = "0" ] && echo "ON ($JUDGE_MODEL)" || echo "OFF")"
echo "  Task workers:  $RUN_ALL_WORKERS"
echo "  Judge workers: $JUDGE_WORKERS"
echo "  Timeout:       ${TIMEOUT}s"
echo "  Force rerun:   $FORCE_RERUN"
echo "  Dry run:       $DRY_RUN"
echo "  Task count:    ${#TASK_QUEUE[@]}"
echo "  Results:       $HARNESS_RESULTS_DIR"
echo "  Traces:        $HARNESS_TRACES_DIR"
echo "  Log:           $LOG"
echo "  Manifest:      $TASK_MANIFEST"
if [[ "$GROUP" == openclaw_sonnet46_* ]]; then
  echo "  OpenRouter:    $OPENROUTER_BASE_URL"
  echo "  Node:          $(node -v) ($(which node))"
fi
echo "========================================"

if [[ "${DRY_RUN,,}" =~ ^(1|true|yes|y)$ ]]; then
  printf '%s\n' "${TASK_QUEUE[@]}"
  exit 0
fi

run_one_task() {
  local rel_yaml=$1
  local task_id
  local status_file
  local exit_code

  task_id="$(basename "$rel_yaml" .yaml)"
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
  while (( active > 0 )); do
    wait -n || true
    active=$((active - 1))
  done
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
echo "TARGETED_RERUN_DONE $(date -Iseconds) — group=${GROUP} total=${TOTAL} skipped=${SKIPPED} passed=${PASSED} failed=${FAILED}"

if [[ "$FAILED" -ne 0 ]]; then
  exit 1
fi
