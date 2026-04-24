#!/usr/bin/env bash
# Smoke test: one task per category (excluding sde) × 3 harnesses.
# Coverage: one task per category × {claude,codex,openclaw}.
set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$PKG_ROOT/.." && pwd)"
cd "$REPO_ROOT" || exit 1

PY="${MASP_BENCH_PYTHON:-/home/chengzhi.ucsb/.conda/envs/masp-bench/bin/python}"
LOG="$PKG_ROOT/smoke.log"
: > "$LOG"

TIMEOUT="${AGENT_TIMEOUT:-360}"
CLAUDE_MODEL="${MASP_CLAUDE_MODEL:-claude-sonnet-4-6}"
CODEX_MODEL="${MASP_CODEX_MODEL:-gpt-5.4}"
OPENCLAW_MODEL="${MASP_OPENCLAW_MODEL:-gpt-5.4}"
JUDGE_MODEL="${JUDGE_MODEL:-openai/gpt-5.4}"
SKIP_JUDGE="${SKIP_JUDGE:-1}"
RESULTS_DIR="$PKG_ROOT/results"
TRACES_DIR="$PKG_ROOT/traces"

mkdir -p "$RESULTS_DIR" "$TRACES_DIR" "$(dirname "$LOG")"

JUDGE_ARGS=(--judge-model "$JUDGE_MODEL")
case "${SKIP_JUDGE,,}" in
  1|true|yes|y) JUDGE_ARGS+=(--skip-judge) ;;
esac

DOMAIN_DIRS=(
  daily_life
  ecommerce
  finance
  healthcare
  legal_compliance
  office
  social_interaction
)

run_logged() {
  "$@" 2>&1 | tee -a "$LOG"
}

run_task() {
  local rel_yaml=$1

  echo "--- $(date -Iseconds) task=${rel_yaml} harness=claude model=${CLAUDE_MODEL} ---" | tee -a "$LOG"
  run_logged "$PY" -m masp_bench run "$rel_yaml" \
    --framework clawteam \
    --harness claude \
    --model "$CLAUDE_MODEL" \
    --agent-timeout "$TIMEOUT" \
    --output "$RESULTS_DIR/" \
    --trace-dir "$TRACES_DIR/" \
    "${JUDGE_ARGS[@]}"

  echo "--- $(date -Iseconds) task=${rel_yaml} harness=codex model=${CODEX_MODEL} ---" | tee -a "$LOG"
  run_logged "$PY" -m masp_bench run "$rel_yaml" \
    --framework clawteam \
    --harness codex \
    --model "$CODEX_MODEL" \
    --agent-timeout "$TIMEOUT" \
    --output "$RESULTS_DIR/" \
    --trace-dir "$TRACES_DIR/" \
    "${JUDGE_ARGS[@]}"

  echo "--- $(date -Iseconds) task=${rel_yaml} harness=openclaw model=${OPENCLAW_MODEL} ---" | tee -a "$LOG"
  run_logged "$PY" -m masp_bench run "$rel_yaml" \
    --framework clawteam \
    --harness openclaw \
    --model "$OPENCLAW_MODEL" \
    --agent-timeout "$TIMEOUT" \
    --output "$RESULTS_DIR/" \
    --trace-dir "$TRACES_DIR/" \
    "${JUDGE_ARGS[@]}"
}

shopt -s nullglob globstar
for domain_dir in "${DOMAIN_DIRS[@]}"; do
  declare -A seen_cat
  yamls=("$PKG_ROOT"/tasks/"${domain_dir}"/**/*.yaml)
  mapfile -t yamls < <(printf '%s\n' "${yamls[@]}" | sort)

  for yaml in "${yamls[@]}"; do
    cat_dir=$(dirname "$yaml")
    cat_name=$(basename "$cat_dir")
    if [[ -z "${seen_cat[$cat_name]+x}" ]]; then
      seen_cat[$cat_name]=1
      rel=${yaml#"$PKG_ROOT"/}
      run_task "$rel"
    fi
  done
  unset seen_cat
done

echo "SMOKE_DONE $(date -Iseconds)" | tee -a "$LOG"
