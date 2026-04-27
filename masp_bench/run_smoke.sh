#!/usr/bin/env bash
# Smoke test: one NEW scaled task per (domain, category) × 3 harnesses.
# 22 tasks × 3 harnesses = 66 runs.
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
JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.4-xhigh}"
SKIP_JUDGE="${SKIP_JUDGE:-0}"
RESULTS_DIR="$PKG_ROOT/results"
TRACES_DIR="$PKG_ROOT/traces"

mkdir -p "$RESULTS_DIR" "$TRACES_DIR" "$(dirname "$LOG")"

JUDGE_ARGS=(--judge-model "$JUDGE_MODEL")
case "${SKIP_JUDGE,,}" in
  1|true|yes|y) JUDGE_ARGS+=(--skip-judge) ;;
esac

run_logged() {
  "$@" 2>&1 | tee -a "$LOG"
}

run_task() {
  local rel_yaml=$1

  # echo "--- $(date -Iseconds) task=${rel_yaml} harness=claude model=${CLAUDE_MODEL} ---" | tee -a "$LOG"
  # run_logged "$PY" -m masp_bench run "$rel_yaml" \
  #   --framework clawteam \
  #   --harness claude \
  #   --model "$CLAUDE_MODEL" \
  #   --agent-timeout "$TIMEOUT" \
  #   --output "$RESULTS_DIR/" \
  #   --trace-dir "$TRACES_DIR/" \
  #   "${JUDGE_ARGS[@]}"

  # echo "--- $(date -Iseconds) task=${rel_yaml} harness=codex model=${CODEX_MODEL} ---" | tee -a "$LOG"
  # run_logged "$PY" -m masp_bench run "$rel_yaml" \
  #   --framework clawteam \
  #   --harness codex \
  #   --model "$CODEX_MODEL" \
  #   --agent-timeout "$TIMEOUT" \
  #   --output "$RESULTS_DIR/" \
  #   --trace-dir "$TRACES_DIR/" \
  #   "${JUDGE_ARGS[@]}"

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

# ── Smoke test set: 22 new scaled tasks (one per domain/category) ──

# daily_life
run_task "tasks/daily_life/dining/dl-t11.yaml"
run_task "tasks/daily_life/housing/dl-t6.yaml"
run_task "tasks/daily_life/travel/dl-t12.yaml"
run_task "tasks/daily_life/wellness/dl-t10.yaml"

# ecommerce
run_task "tasks/ecommerce/after_sales/ec-t11.yaml"
run_task "tasks/ecommerce/shopping/ec-t10.yaml"
run_task "tasks/ecommerce/transaction/ec-t18.yaml"

# finance
run_task "tasks/finance/financial_planning/fin-t16.yaml"
run_task "tasks/finance/insurance/fin-t13.yaml"
run_task "tasks/finance/investing/fin-t12.yaml"
run_task "tasks/finance/lending/fin-t14.yaml"

# healthcare
run_task "tasks/healthcare/consultation/hc-t11.yaml"
run_task "tasks/healthcare/prescription/hc-t33.yaml"

# legal_compliance
run_task "tasks/legal_compliance/audit/lc-t19.yaml"
run_task "tasks/legal_compliance/contract/lc-t11.yaml"
run_task "tasks/legal_compliance/litigation/lc-t14.yaml"

# office
run_task "tasks/office/finance/off-t12.yaml"
run_task "tasks/office/office_asset/off-t11.yaml"
run_task "tasks/office/partnership/off-t17.yaml"
run_task "tasks/office/personnel/off-t13.yaml"

# social_interaction
run_task "tasks/social_interaction/report/si-t8.yaml"
run_task "tasks/social_interaction/voice/si-t10.yaml"

echo "SMOKE_DONE $(date -Iseconds) — 22 tasks × 1 harness = 22 runs" | tee -a "$LOG"
