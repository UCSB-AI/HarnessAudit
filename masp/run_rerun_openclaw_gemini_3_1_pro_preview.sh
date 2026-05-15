#!/usr/bin/env bash
# Badcase-only rerun wrapper for OpenClaw Gemini via OpenRouter.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export HARNESS_MODEL="gemini-3.1-pro-preview"
export RERUN_LABEL="gemini_3_1_pro_preview_badcases"
export EXPECTED_BADCASE_COUNT=84

exec "$SCRIPT_DIR/run_rerun_openclaw_badcases.sh" \
  dl-t9 dl-t15 dl-t16 dl-t17 dl-t18 dl-t19 dl-t20 dl-t21 dl-t22 dl-t23 \
  ec-t16 ec-t21 ec-t26 ec-t28 ec-t36 ec-t38 ec-t40 ec-t41 ec-t45 \
  fin-t13 fin-t19 fin-t33 fin-t35 fin-t36 fin-t37 fin-t40 fin-t41 fin-t42 fin-t44 fin-t45 fin-t46 fin-t47 \
  hc-t9 hc-t11 hc-t12 hc-t13 hc-t14 hc-t15 hc-t16 hc-t18 hc-t33 hc-t34 hc-t39 hc-t40 hc-t42 hc-t43 hc-t44 hc-t45 hc-t46 \
  lc-t9 lc-t10 lc-t12 lc-t16 lc-t17 lc-t32 lc-t35 \
  off-t10 off-t17 off-t37 off-t38 off-t39 off-t40 off-t42 \
  si-t1 si-t2 si-t4 si-t7 si-t8 si-t9 si-t11 si-t12 si-t13 si-t14 si-t16 si-t17 si-t18 si-t19 si-t20 si-t22 si-t23 si-t24 si-t26 si-t27 si-t28
