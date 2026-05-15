#!/usr/bin/env bash
# Badcase-only rerun wrapper for OpenClaw Qwen via OpenRouter.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export HARNESS_MODEL="qwen3.5-plus"
export RERUN_LABEL="qwen3_5_plus_badcases"
export EXPECTED_BADCASE_COUNT=114

exec "$SCRIPT_DIR/run_rerun_openclaw_badcases.sh" \
  dl-t9 dl-t16 dl-t17 dl-t18 dl-t19 dl-t20 dl-t21 dl-t23 \
  ec-t3 ec-t5 ec-t6 ec-t7 ec-t8 ec-t11 ec-t15 ec-t16 ec-t21 ec-t26 ec-t33 ec-t35 ec-t36 ec-t41 ec-t43 ec-t45 \
  fin-t1 fin-t4 fin-t7 fin-t10 fin-t11 fin-t14 fin-t16 fin-t19 fin-t27 fin-t30 fin-t35 fin-t36 fin-t37 fin-t38 fin-t39 fin-t40 fin-t41 fin-t42 fin-t43 fin-t44 fin-t45 fin-t46 fin-t47 fin-t48 \
  hc-t7 hc-t9 hc-t10 hc-t11 hc-t12 hc-t13 hc-t14 hc-t16 hc-t34 hc-t35 hc-t36 hc-t37 hc-t38 hc-t39 hc-t40 hc-t41 hc-t42 hc-t43 hc-t44 hc-t45 hc-t46 \
  lc-t1 lc-t2 lc-t7 lc-t9 lc-t10 lc-t12 lc-t16 lc-t17 lc-t18 lc-t19 lc-t27 lc-t32 lc-t35 lc-t36 \
  off-t3 off-t5 off-t6 off-t10 off-t11 off-t15 off-t17 off-t18 off-t19 off-t37 off-t38 off-t39 off-t40 off-t42 \
  si-t1 si-t2 si-t4 si-t8 si-t10 si-t11 si-t12 si-t14 si-t15 si-t17 si-t18 si-t20 si-t23 si-t24 si-t26 si-t27 si-t28
