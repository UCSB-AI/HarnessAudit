#!/usr/bin/env bash
# Smoke test: one task per domain × openclaw_local.
# Coverage: 7 domains × 1 framework = 7 runs.
set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$PKG_ROOT/.." && pwd)"
cd "$REPO_ROOT" || exit 1

PY="${MASP_BENCH_PYTHON:-/home/cliu322/anaconda3/envs/masp-bench/bin/python}"
LOG="$PKG_ROOT/smoke.log"
: > "$LOG"

TIMEOUT="${AGENT_TIMEOUT:-360}"
MODEL="${SASP_MODEL:-gpt-5.4}"
JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.4-xhigh}"
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

run_one() {
  local rel_yaml=$1
  echo "=== $(date -Iseconds) task=${rel_yaml} model=${MODEL} framework=openclaw_local ===" | tee -a "$LOG"
  run_logged "$PY" -m sasp_bench run "$rel_yaml" \
    --framework openclaw_local \
    --model "$MODEL" \
    --agent-timeout "$TIMEOUT" \
    --output "$RESULTS_DIR/" \
    --trace-dir "$TRACES_DIR/" \
    "${JUDGE_ARGS[@]}"
}

shopt -s nullglob
for domain_dir in "${DOMAIN_DIRS[@]}"; do
  yamls=("$PKG_ROOT"/tasks/"${domain_dir}"/*.yaml)
  mapfile -t yamls < <(printf '%s\n' "${yamls[@]}" | sort)

  if [[ ${#yamls[@]} -gt 0 ]]; then
    rel=${yamls[0]#"$PKG_ROOT"/}
    run_one "$rel"
  fi
done

echo "SA_SMOKE_DONE $(date -Iseconds)" | tee -a "$LOG"
