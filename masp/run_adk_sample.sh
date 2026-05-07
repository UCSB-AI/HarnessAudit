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

ADK_MODEL="${ADK_MODEL:-gpt-5.4}"
JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.4}"
AGENT_TIMEOUT="${AGENT_TIMEOUT:-360}"
JUDGE_WORKERS="${JUDGE_WORKERS:-2}"
SKIP_JUDGE="${SKIP_JUDGE:-0}"
PYTHON_BIN="${PYTHON_BIN:-python}"
SKIP_DONE="${SKIP_DONE:-1}"

TASKS=(
  "tasks/daily_life/dining/dl-t11.yaml"
  "tasks/daily_life/housing/dl-t13.yaml"
  "tasks/ecommerce/shopping/ec-t10.yaml"
  "tasks/ecommerce/shopping/ec-t1.yaml"
  "tasks/ecommerce/transaction/ec-t12.yaml"
  "tasks/finance/financial_planning/fin-t16.yaml"
  "tasks/finance/insurance/fin-t10.yaml"
  "tasks/finance/lending/fin-t11.yaml"
  "tasks/finance/investing/fin-t5.yaml"
  "tasks/healthcare/consultation/hc-t1.yaml"
  "tasks/legal_compliance/audit/lc-t9.yaml"
  "tasks/ecommerce/after_sales/ec-t22.yaml"
  "tasks/ecommerce/after_sales/ec-t28.yaml"
  "tasks/ecommerce/shopping/ec-t39.yaml"
  "tasks/office/finance/off-t12.yaml"
  "tasks/office/finance/off-t16.yaml"
  "tasks/office/office_asset/off-t3.yaml"
  "tasks/office/personnel/off-t10.yaml"
  "tasks/social_interaction/report/si-t7.yaml"
  "tasks/social_interaction/voice/si-t3.yaml"
)

echo "========================================"
echo "HarnessAudit-Bench Google ADK Sample"
echo "  Model:         $ADK_MODEL"
echo "  Judge:         $JUDGE_MODEL"
echo "  OpenAI API:    ${OPENAI_BASE_URL:-default}"
echo "  Timeout:       ${AGENT_TIMEOUT}s"
echo "  Judge workers: $JUDGE_WORKERS"
echo "  Skip done:     $SKIP_DONE"
echo "  Python:        $PYTHON_BIN"
echo "  Results:       $SCRIPT_DIR/results/adk/$ADK_MODEL"
echo "  Traces:        $SCRIPT_DIR/traces/adk/$ADK_MODEL"
echo "========================================"

cd "$REPO_ROOT" || exit 1

pass=0
fail=0
skip=0

for task in "${TASKS[@]}"; do
  task_id="$(basename "$task" .yaml)"
  result_glob="masp/results/adk/$ADK_MODEL/${task_id}_*.json"
  if [[ "$SKIP_DONE" == "1" ]] && compgen -G "$result_glob" > /dev/null; then
    echo "SKIP $(date -Is) task=$task existing_result=$result_glob"
    skip=$((skip + 1))
    continue
  fi

  echo "--- $(date -Is) task=$task framework=adk harness=adk model=$ADK_MODEL ---"
  cmd=(
    "$PYTHON_BIN" -m masp run "$task"
    --framework adk
    --harness adk
    --model "$ADK_MODEL"
    --agent-timeout "$AGENT_TIMEOUT"
    --output masp/results
    --trace-dir masp/traces
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
echo "Google ADK sample complete: pass=$pass skip=$skip fail=$fail total=${#TASKS[@]}"
echo "========================================"

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi
