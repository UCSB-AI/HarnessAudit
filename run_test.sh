cd /home/chengzhi.ucsb/code/chengzhi/Agent_Safety/MASP_Bench || exit 1
set -o pipefail
set -e

PY=/home/chengzhi.ucsb/.conda/envs/masp-bench/bin/python
LOG=/tmp/masp_batch.log
: > "$LOG"

# Traces 审计（末行须为 type=trace_end）：
#   - real_estate re-t1..re-t4 × 两模型：均已完整，无需重跑。
#   - 曾中断已删并重跑：jm-t4+gpt-5.1、lc-t4+gpt-5.4（半截 jsonl 已删除）。
# 若要整批重跑某域，可恢复下面注释中的 for，并删掉或注释掉「仅补跑」段。

# for t in sde1-t1 sde1-t2 sde1-t3 sde1-t4 sde1-t5 sde2-t1 sde2-t2 sde2-t3 sde2-t4 sde2-t5; do
# for t in fin-t1 fin-t2 fin-t3 fin-t4 fin-t5 fin-t6; do
# for t in ec-t1 ec-t2 ec-t3 ec-t4 ec-t5; do
# for t in off-t1 off-t2 off-t3 off-t4 off-t5 off-t6; do
# for t in hc-t1 hc-t2 hc-t3 hc-t4 hc-t5; do
# for t in jm-t1 jm-t2 jm-t3 jm-t4; do
# for t in dl-t1 dl-t2 dl-t3 dl-t4; do
# for t in lc-t1 lc-t2 lc-t3 lc-t4; do
# for t in si-t1 si-t2 si-t3 si-t4; do
# for t in re-t1 re-t2 re-t3 re-t4; do
#   for m in gpt-5.4-2026-03-05 gpt-5.1-2025-11-13; do
#     echo "=== $(date -Iseconds) $t $m ===" | tee -a "$LOG"
#     "$PY" -m masp_bench run "tasks/real_estate/${t}.yaml" \
#       --model "$m" \
#       --output results/ \
#       --trace-dir traces/ 2>&1 | tee -a "$LOG"
#   done
# done

# ---------- 仅补跑未完成项（跑完后可注释掉本段）----------
run_one() {
  local rel_yaml=$1
  local model=$2
  echo "=== $(date -Iseconds) $rel_yaml $model ===" | tee -a "$LOG"
  "$PY" -m masp_bench run "$rel_yaml" \
    --model "$model" \
    --output results/ \
    --trace-dir traces/ 2>&1 | tee -a "$LOG"
}

run_one tasks/journalism_media/jm-t4.yaml gpt-5.1-2025-11-13
run_one tasks/legal_compliance/lc-t4.yaml gpt-5.4-2026-03-05

echo "ALL_DONE $(date -Iseconds)" | tee -a "$LOG"
