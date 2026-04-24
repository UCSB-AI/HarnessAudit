#!/usr/bin/env bash
# Smoke test: one task per domain × openclaw_local.
# Coverage: 7 domains × 1 framework = 7 runs.
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE" || exit 1

PY=/home/chengzhi.ucsb/.conda/envs/masp-bench/bin/python
LOG=/tmp/sasp_smoke.log
: > "$LOG"

TIMEOUT=360
MODEL=openai/gpt-5.4

DOMAIN_DIRS=(
  daily_life
  ecommerce
  finance
  healthcare
  legal_compliance
  office
  social_interaction
)

run_one() {
  local rel_yaml=$1
  echo "=== $(date -Iseconds) task=${rel_yaml} model=${MODEL} framework=openclaw_local ===" | tee -a "$LOG"
  "$PY" -m sasp_bench run "$rel_yaml" \
    --framework openclaw_local \
    --model "$MODEL" \
    --agent-timeout "$TIMEOUT" \
    --output "$BASE/results/" \
    --trace-dir "$BASE/traces/" 2>&1 | tee -a "$LOG"
}

shopt -s nullglob
for domain_dir in "${DOMAIN_DIRS[@]}"; do
  yamls=(tasks/"${domain_dir}"/*.yaml)
  IFS=$'\n' yamls=($(printf "%s\n" "${yamls[@]}" | sort))
  unset IFS

  if [[ ${#yamls[@]} -gt 0 ]]; then
    rel=${yamls[0]#./}
    run_one "$rel"
  fi
done

echo "SA_SMOKE_DONE $(date -Iseconds)" | tee -a "$LOG"
