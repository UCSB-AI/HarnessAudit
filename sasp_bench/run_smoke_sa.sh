#!/usr/bin/env bash
# Smoke test: single-agent tasks via OpenClaw local mode
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE" || exit 1

PY=/home/chengzhi.ucsb/.conda/envs/masp-bench/bin/python
LOG=/tmp/sasp_smoke.log
: > "$LOG"

MODEL=anthropic/claude-sonnet-4-6
FRAMEWORKS=(openclaw_local)

run_one() {
  local rel_yaml=$1
  local framework=$2
  echo "=== $(date -Iseconds) task=${rel_yaml} model=${MODEL} framework=${framework} ===" | tee -a "$LOG"
  "$PY" -m sasp_bench run "$rel_yaml" \
    --model "$MODEL" \
    --framework "$framework" \
    --output "$BASE/results/" \
    --trace-dir "$BASE/traces/" 2>&1 | tee -a "$LOG"
}

shopt -s nullglob globstar
for yaml in tasks/**/*.yaml; do
  rel=${yaml#./}
  for fw in "${FRAMEWORKS[@]}"; do
    run_one "$rel" "$fw"
  done
done

echo "SA_SMOKE_DONE $(date -Iseconds)" | tee -a "$LOG"
