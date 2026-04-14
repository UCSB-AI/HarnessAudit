cd /home/chengzhi.ucsb/code/chengzhi/Agent_Safety/MASP_Bench || exit 1
set -o pipefail
set -e

PY=/home/chengzhi.ucsb/.conda/envs/masp-bench/bin/python
LOG=/tmp/masp_sde_batch.log: > "$LOG"

# for t in sde1-t1 sde1-t2 sde1-t3 sde1-t4 sde1-t5 sde2-t1 sde2-t2 sde2-t3 sde2-t4 sde2-t5; do
# for t in fin-t1 fin-t2 fin-t3 fin-t4 fin-t5 fin-t6; do
# for t in ec-t1 ec-t2 ec-t3 ec-t4 ec-t5; do
for t in off-t1 off-t2 off-t3 off-t4 off-t5 off-t6; do
  for m in gpt-5.4-2026-03-05 gpt-5.1-2025-11-13; do
    echo "=== $(date -Iseconds) $t $m ===" | tee -a "$LOG"
    "$PY" -m masp_bench run "tasks/office/${t}.yaml" \
      --model "$m" \
      --output results/ \
      --trace-dir traces/ 2>&1 | tee -a "$LOG"
  done
done

echo "ALL_DONE $(date -Iseconds)" | tee -a "$LOG"