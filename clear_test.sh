BASE=/home/chengzhi.ucsb/code/chengzhi/Agent_Safety/MASP_Bench
cd "$BASE" || exit 1

rm -f traces/sde*.jsonl
rm -f results/sde*.json results/sde*.sqlite

rm -f traces/fin*.jsonl
rm -f results/fin*.json results/fin*.sqlite

rm -f traces/ec*.jsonl
rm -f results/ec*.json results/ec*.sqlite

rm -f traces/off*.jsonl
rm -f results/off*.json results/off*.sqlite

[ -d workspaces ] && rm -rf workspaces/*

for f in fixtures/app-ecommerce fixtures/app-admin; do
  if [ -d "$f/.git" ]; then
    git -C "$f" worktree prune -v
  fi
done