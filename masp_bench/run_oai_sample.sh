#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
fi

OAI_MODEL="${OAI_MODEL:-gpt-5.4}"
JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.4}"
AGENT_TIMEOUT="${AGENT_TIMEOUT:-360}"
JUDGE_WORKERS="${JUDGE_WORKERS:-2}"
SKIP_JUDGE="${SKIP_JUDGE:-0}"

TASKS=(
  "tasks/daily_life/dining/dl-t11.yaml"
  "tasks/ecommerce/after_sales/ec-t13.yaml"
  "tasks/finance/financial_planning/fin-t16.yaml"
  "tasks/healthcare/consultation/hc-t10.yaml"
  "tasks/office/finance/off-t12.yaml"
)

echo "========================================"
echo "MASP_Bench OpenAI Agents SDK Sample"
echo "  Model:         $OAI_MODEL"
echo "  Judge:         $JUDGE_MODEL"
echo "  Timeout:       ${AGENT_TIMEOUT}s"
echo "  Judge workers: $JUDGE_WORKERS"
echo "  Results:       $SCRIPT_DIR/results/oai/$OAI_MODEL"
echo "  Traces:        $SCRIPT_DIR/traces/oai/$OAI_MODEL"
echo "========================================"

cd "$REPO_ROOT" || exit 1

pass=0
fail=0

for task in "${TASKS[@]}"; do
  echo "--- $(date -Is) task=$task framework=oai harness=oai model=$OAI_MODEL ---"
  cmd=(
    python -m masp_bench run "$task"
    --framework oai
    --harness oai
    --model "$OAI_MODEL"
    --agent-timeout "$AGENT_TIMEOUT"
    --output masp_bench/results
    --trace-dir masp_bench/traces
    --judge-model "$JUDGE_MODEL"
    --judge-workers "$JUDGE_WORKERS"
  )
  if [[ "$SKIP_JUDGE" == "1" ]]; then
    cmd+=(--skip-judge)
  fi

  if "${cmd[@]}"; then
    echo "PASS $(date -Is) task=$task"
    pass=$((pass + 1))
  else
    status=$?
    echo "FAIL $(date -Is) task=$task exit=$status"
    fail=$((fail + 1))
  fi
done

echo "========================================"
echo "OpenAI Agents SDK sample complete: pass=$pass fail=$fail total=${#TASKS[@]}"
echo "========================================"

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi
