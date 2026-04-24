#!/usr/bin/env bash
# Top-level smoke driver: run MASP first, then SASP.
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MASP_SCRIPT="$BASE/masp_bench/run_smoke.sh"
SASP_SCRIPT="$BASE/sasp_bench/run_smoke_sa.sh"

LOG="$BASE/masp_bench/smoke_all.log"
mkdir -p "$(dirname "$LOG")"
: > "$LOG"

echo "[$(date -Iseconds)] launching MASP smoke script" | tee -a "$LOG"
masp_rc=0
bash "$MASP_SCRIPT" || masp_rc=$?

echo "[$(date -Iseconds)] launching SASP smoke script" | tee -a "$LOG"
sasp_rc=0
bash "$SASP_SCRIPT" || sasp_rc=$?

echo "[$(date -Iseconds)] MASP exit=$masp_rc, SASP exit=$sasp_rc" | tee -a "$LOG"
echo "ALL_SMOKE_DONE $(date -Iseconds)" | tee -a "$LOG"

if [[ $masp_rc -ne 0 || $sasp_rc -ne 0 ]]; then
  exit 1
fi
