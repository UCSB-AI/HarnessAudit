#!/usr/bin/env bash
# Badcase-only rerun wrapper for OpenClaw Kimi via OpenRouter.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export HARNESS_MODEL="kimi-k2.6"
export RERUN_LABEL="kimi_k2_6_badcases"
export EXPECTED_BADCASE_COUNT=77

exec "$SCRIPT_DIR/run_rerun_openclaw_badcases.sh" \
  dl-t12 dl-t13 dl-t15 dl-t16 dl-t17 dl-t18 dl-t19 dl-t21 \
  ec-t2 ec-t16 ec-t20 ec-t21 ec-t26 ec-t36 ec-t40 ec-t41 ec-t45 \
  fin-t16 fin-t18 fin-t26 fin-t30 fin-t35 fin-t36 fin-t37 fin-t38 fin-t39 fin-t40 fin-t41 fin-t42 fin-t44 fin-t46 fin-t47 fin-t48 \
  hc-t4 hc-t5 hc-t11 hc-t12 hc-t13 hc-t15 hc-t16 hc-t34 hc-t36 hc-t38 hc-t40 hc-t41 hc-t42 hc-t43 hc-t44 hc-t45 \
  lc-t1 lc-t10 lc-t12 lc-t16 lc-t17 lc-t19 lc-t35 \
  off-t3 off-t10 off-t33 off-t37 off-t38 off-t39 off-t42 \
  si-t3 si-t4 si-t8 si-t9 si-t10 si-t12 si-t14 si-t17 si-t19 si-t20 si-t22 si-t24 si-t27 si-t28
