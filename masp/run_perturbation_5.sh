#!/usr/bin/env bash
# Layer 3 sample: 5 tasks × 5 required perturbation variants.
# Note: hc-t10 has no perturbation spec; this sample uses hc-t18 for healthcare.
# Usage examples:
#   MASP_HARNESS=openclaw MASP_OPENCLAW_MODEL=qwen3.5-plus RUN_ALL_WORKERS=3 JUDGE_WORKERS=2 bash masp/run_perturbation_5.sh
#   MASP_HARNESS=codex MASP_CODEX_MODEL=gpt-5.4 RUN_ALL_WORKERS=3 JUDGE_WORKERS=2 bash masp/run_perturbation_5.sh
#   MASP_HARNESS=claude MASP_CLAUDE_MODEL=claude-sonnet-4-6 RUN_ALL_WORKERS=3 JUDGE_WORKERS=2 bash masp/run_perturbation_5.sh
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

PY="${HARNESSAUDIT_BENCH_PYTHON:-python}"
RUN_TS="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="$PKG_ROOT/logs"
LOG="$LOG_DIR/perturb5_${RUN_TS}.log"

TIMEOUT="${AGENT_TIMEOUT:-360}"
HARNESS="${MASP_HARNESS:-openclaw}"
CLAUDE_MODEL="${MASP_CLAUDE_MODEL:-claude-sonnet-4-6}"
CODEX_MODEL="${MASP_CODEX_MODEL:-gpt-5.4}"
OPENCLAW_MODEL="${MASP_OPENCLAW_MODEL:-gpt-5.4}"
case "$HARNESS" in
  claude) HARNESS_MODEL="$CLAUDE_MODEL" ;;
  codex) HARNESS_MODEL="$CODEX_MODEL" ;;
  openclaw) HARNESS_MODEL="$OPENCLAW_MODEL" ;;
  *)
    echo "ERROR: MASP_HARNESS must be one of: openclaw, claude, codex; got: $HARNESS" >&2
    exit 2
    ;;
esac

JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.4}"
RUN_ALL_WORKERS="${RUN_ALL_WORKERS:-2}"
JUDGE_WORKERS="${JUDGE_WORKERS:-2}"
SKIP_JUDGE="${SKIP_JUDGE:-0}"
FORCE_RERUN="${FORCE_RERUN:-0}"
PERTURBATION_INDEX="${PERTURBATION_INDEX:-$PKG_ROOT/perturbation_specs/index.jsonl}"
RESULTS_DIR="$PKG_ROOT/results/perturbations"
TRACES_DIR="$PKG_ROOT/traces/perturbations"
MODEL_DIR="${HARNESS_MODEL//\//__}"
HARNESS_RESULTS_DIR="$RESULTS_DIR/$HARNESS/$MODEL_DIR"
HARNESS_TRACES_DIR="$TRACES_DIR/$HARNESS/$MODEL_DIR"
STATUS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/masp-perturb5-status.XXXXXX")"
RUN_QUEUE=()

if ! [[ "$RUN_ALL_WORKERS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: RUN_ALL_WORKERS must be a positive integer, got: $RUN_ALL_WORKERS" >&2
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

echo "========================================"
echo "HarnessAudit-Bench Layer 3 Perturbation Sample — $(date -Iseconds)"
echo "  Harness:       $HARNESS"
echo "  Model:         $HARNESS_MODEL"
echo "  Judge:         $([ "$SKIP_JUDGE" = "0" ] && echo "ON ($JUDGE_MODEL)" || echo "OFF")"
echo "  Task workers:  $RUN_ALL_WORKERS"
echo "  Judge workers: $JUDGE_WORKERS"
echo "  Timeout:       ${TIMEOUT}s"
echo "  Force rerun:   $FORCE_RERUN"
echo "  Index:         $PERTURBATION_INDEX"
echo "  Results:       $HARNESS_RESULTS_DIR"
echo "  Traces:        $HARNESS_TRACES_DIR"
echo "  Log:           $LOG"
echo "========================================"

build_queue() {
  "$PY" - "$PERTURBATION_INDEX" <<'PY'
import json
import sys
from pathlib import Path

index = Path(sys.argv[1])
tasks = {"dl-t11", "ec-t13", "fin-t16", "hc-t18", "off-t12"}
seen: set[tuple[str, str]] = set()
for line in index.read_text().splitlines():
    if not line.strip():
        continue
    row = json.loads(line)
    task_id = row.get("task_id")
    variant_id = row.get("variant_id")
    if task_id in tasks and row.get("required") and variant_id:
        key = (task_id, variant_id)
        if key in seen:
            continue
        seen.add(key)
        print("\t".join([
            row["task_path"],
            variant_id,
            row.get("attack_type", ""),
        ]))
PY
}

while IFS=$'\t' read -r rel_yaml variant_id attack_type; do
  [[ -n "$rel_yaml" && -n "$variant_id" ]] || continue
  RUN_QUEUE+=("${rel_yaml}"$'\t'"${variant_id}"$'\t'"${attack_type}")
done < <(build_queue)

run_one_variant() {
  local rel_yaml=$1
  local variant_id=$2
  local attack_type=$3
  local task_id
  task_id=$(basename "$rel_yaml" .yaml)
  local prefix="${task_id}_${variant_id}"
  local status_file="$STATUS_DIR/${prefix}.status"

  if [[ "${FORCE_RERUN,,}" =~ ^(1|true|yes|y)$ ]]; then
    rm -f "${HARNESS_RESULTS_DIR}/${prefix}_"*.json 2>/dev/null || true
    rm -f "${HARNESS_RESULTS_DIR}/${prefix}_"*.sqlite 2>/dev/null || true
    rm -f "${HARNESS_TRACES_DIR}/${prefix}_"*.jsonl 2>/dev/null || true
  else
    if compgen -G "${HARNESS_RESULTS_DIR}/${prefix}_*.json" > /dev/null 2>&1; then
      echo "SKIP $(date -Iseconds) task=${rel_yaml} perturbation=${variant_id} (result exists)"
      echo skipped > "$status_file"
      return 0
    fi
    rm -f "${HARNESS_TRACES_DIR}/${prefix}_"*.jsonl 2>/dev/null || true
  fi

  echo "--- $(date -Iseconds) task=${rel_yaml} perturbation=${variant_id} type=${attack_type} harness=${HARNESS} model=${HARNESS_MODEL} ---"
  "$PY" -m masp run "$rel_yaml" \
    --framework clawteam \
    --harness "$HARNESS" \
    --model "$HARNESS_MODEL" \
    --perturbation-id "$variant_id" \
    --perturbation-index "$PERTURBATION_INDEX" \
    --agent-timeout "$TIMEOUT" \
    --output "$RESULTS_DIR/" \
    --trace-dir "$TRACES_DIR/" \
    "${JUDGE_ARGS[@]}" 2>&1
  local exit_code=$?
  if [[ "$exit_code" -eq 0 ]]; then
    echo "PASS $(date -Iseconds) task=${rel_yaml} perturbation=${variant_id}"
    echo passed > "$status_file"
  else
    echo "FAIL $(date -Iseconds) task=${rel_yaml} perturbation=${variant_id} exit=${exit_code}"
    echo failed > "$status_file"
  fi
  return "$exit_code"
}

run_queue() {
  local active=0
  local item rel_yaml variant_id attack_type
  for item in "${RUN_QUEUE[@]}"; do
    IFS=$'\t' read -r rel_yaml variant_id attack_type <<< "$item"
    run_one_variant "$rel_yaml" "$variant_id" "$attack_type" &
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

run_queue

TOTAL=${#RUN_QUEUE[@]}
SKIPPED=$(count_status skipped)
PASSED=$(count_status passed)
FAILED=$(count_status failed)
echo "PERTURBATION_SAMPLE_DONE $(date -Iseconds) — total=${TOTAL} skipped=${SKIPPED} passed=${PASSED} failed=${FAILED}"

if [[ "$FAILED" -ne 0 ]]; then
  exit 1
fi
