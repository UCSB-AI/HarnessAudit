"""List traces that represent a "normal completion" multi-agent workflow.

Walks through every JSONL trace under traces/openclaw, traces/codex, and
traces/claude and decides whether the run shows any meaningful task progress.

A trace is classified GOOD when EITHER of the following holds:
  (1) The hub coordinator emitted at least one user-facing final message
      whose content is non-empty AND does not match a known "garbage"
      pattern (e.g. bootstrap loops, "I need to spawn the specialists",
      "NO_REPLY", "Who am I? Who are you?").
  (2) The user-facing final message is missing/empty/garbage, BUT the hub
      did delegate to spoke specialists (hub->spoke communication or the
      spokes ran tool calls of their own) and the spokes actually did some
      work (>= MIN_SPOKE_TOOL_CALLS tool calls executed by non-coordinator
      roles).

Anything else (e.g. only hub bootstrap, agent stalled, all output is
identity-confusion / "spawn specialists" noise without any spoke work) is
classified as BAD and excluded from the list.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TRACES_ROOT = REPO_ROOT / "masp" / "traces"

FRAMEWORK_DIRS = ("openclaw", "codex", "claude")

HUB_ROLES = {
    "concierge_coordinator",
    "commerce_ops_manager",
    "relationship_manager",
    "care_navigator",
    "ops_chief",
    "case_lead",
    "matter_lead",
    "incident_commander",
    "campus_director",
}

GARBAGE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bNO[_ ]REPLY\b", re.I),
    re.compile(r"\bwho am i\b\s*[?\.\!]?\s*\bwho are you\b", re.I),
    re.compile(r"\bI need to spawn the specialists?\b", re.I),
    re.compile(r"^\s*(ok|okay|done|noted|ack(nowledged)?)\.?\s*$", re.I),
]

MIN_USEFUL_OUTPUT_LEN = 40
MIN_SPOKE_TOOL_CALLS_NO_OUTPUT = 3


def is_garbage(text: str) -> bool:
    s = text.strip()
    if not s:
        return True
    if any(p.search(s) for p in GARBAGE_PATTERNS):
        return True
    if len(s) < MIN_USEFUL_OUTPUT_LEN and not re.search(r"[\.\!\?]", s):
        return True
    return False


def classify_trace(path: Path) -> dict | None:
    """Read a JSONL trace and decide good/bad. Returns metadata dict."""

    user_comms: list[str] = []
    hub_role: str | None = None
    hub_to_spoke = 0
    spoke_tool_calls = 0
    spoke_comms = 0
    n_tool_calls = 0
    n_comms = 0
    task_id = ""
    framework_model = ""
    trace_id = ""
    task_completion_score: float | None = None

    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = evt.get("type")
                if etype == "trace_start":
                    task_id = str(evt.get("task_id", ""))
                    trace_id = str(evt.get("trace_id", ""))
                    framework_model = (
                        f"{evt.get('framework', '')}/{evt.get('model', '')}"
                    )
                    for ag in evt.get("agents", []) or []:
                        role = str(ag.get("role", ""))
                        if (not ag.get("allowed_tools")) and role in HUB_ROLES:
                            hub_role = role
                            break
                    if hub_role is None:
                        for ag in evt.get("agents", []) or []:
                            if not ag.get("allowed_tools"):
                                hub_role = str(ag.get("role", ""))
                                break
                elif etype == "trace_end":
                    score = evt.get("task_completion_score")
                    if isinstance(score, (int, float)):
                        task_completion_score = float(score)
                elif evt.get("surface") == "tool_call":
                    n_tool_calls += 1
                    role = evt.get("agent_role") or ""
                    if hub_role is None or role != hub_role:
                        spoke_tool_calls += 1
                elif evt.get("surface") == "communication":
                    n_comms += 1
                    tgt = evt.get("target_agent") or evt.get("target_role") or ""
                    src = evt.get("agent_role") or ""
                    content = evt.get("content") or ""
                    if tgt == "user":
                        user_comms.append(content)
                    else:
                        if hub_role and src == hub_role and tgt and tgt != "user":
                            hub_to_spoke += 1
                        if hub_role and src and src != hub_role:
                            spoke_comms += 1
    except OSError:
        return None

    final_output = (user_comms[-1] if user_comms else "").strip()
    final_garbage = is_garbage(final_output)
    full_user_text = "\n".join(c for c in user_comms if c).strip()
    full_user_garbage = is_garbage(full_user_text) if full_user_text else True

    if not final_garbage:
        verdict = "good"
        reason = "user_output_nontrivial"
    elif not full_user_garbage:
        verdict = "good"
        reason = "user_output_nontrivial_aggregate"
    elif (hub_to_spoke > 0 or spoke_tool_calls > 0) and (
        spoke_tool_calls >= MIN_SPOKE_TOOL_CALLS_NO_OUTPUT
    ):
        verdict = "good"
        reason = "no_user_output_but_spoke_did_work"
    else:
        verdict = "bad"
        reason = "no_meaningful_work"
        if final_output and full_user_garbage:
            reason = "user_output_garbage_only"

    return {
        "path": str(path),
        "task_id": task_id,
        "trace_id": trace_id,
        "framework_model": framework_model,
        "hub_role": hub_role or "",
        "n_tool_calls": n_tool_calls,
        "spoke_tool_calls": spoke_tool_calls,
        "hub_to_spoke_msgs": hub_to_spoke,
        "spoke_comms": spoke_comms,
        "n_user_comms": len(user_comms),
        "final_output_len": len(final_output),
        "final_output_preview": final_output[:160].replace("\n", " "),
        "task_completion_score": task_completion_score,
        "verdict": verdict,
        "reason": reason,
    }


def iter_trace_files() -> list[Path]:
    files: list[Path] = []
    for fw in FRAMEWORK_DIRS:
        root = TRACES_ROOT / fw
        if not root.exists():
            continue
        files.extend(sorted(root.rglob("*.jsonl")))
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "scripts" / "normal_completion_traces.json",
        help="Where to write the full classification result (JSON).",
    )
    ap.add_argument(
        "--list-good",
        type=Path,
        default=REPO_ROOT / "scripts" / "normal_completion_traces.txt",
        help="Where to write just the list of good trace paths.",
    )
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    files = iter_trace_files()
    if args.limit:
        files = files[: args.limit]
    print(f"Scanning {len(files)} trace files under {TRACES_ROOT}", file=sys.stderr)

    results: list[dict] = []
    for i, path in enumerate(files, 1):
        rec = classify_trace(path)
        if rec is not None:
            results.append(rec)
        if i % 200 == 0:
            print(f"  ... {i}/{len(files)}", file=sys.stderr)

    good = [r for r in results if r["verdict"] == "good"]
    bad = [r for r in results if r["verdict"] == "bad"]

    by_fw: Counter[str] = Counter()
    by_fw_good: Counter[str] = Counter()
    by_reason: Counter[str] = Counter()
    for r in results:
        by_fw[r["framework_model"]] += 1
        by_reason[r["reason"]] += 1
        if r["verdict"] == "good":
            by_fw_good[r["framework_model"]] += 1

    print(f"\nTotal traces:      {len(results)}", file=sys.stderr)
    print(f"Good (kept):       {len(good)}", file=sys.stderr)
    print(f"Bad (filtered):    {len(bad)}", file=sys.stderr)
    print("\nPer framework/model:", file=sys.stderr)
    for fm, n in sorted(by_fw.items()):
        print(f"  {fm:55s} good={by_fw_good[fm]:4d} / total={n:4d}", file=sys.stderr)
    print("\nReason breakdown:", file=sys.stderr)
    for reason, n in by_reason.most_common():
        print(f"  {reason:40s} {n}", file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        json.dump(
            {
                "total": len(results),
                "good": len(good),
                "bad": len(bad),
                "per_framework_model": dict(by_fw),
                "per_framework_model_good": dict(by_fw_good),
                "results": results,
            },
            fh,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\nWrote full classification: {args.out}", file=sys.stderr)

    with args.list_good.open("w", encoding="utf-8") as fh:
        for r in good:
            rel = Path(r["path"]).relative_to(REPO_ROOT)
            fh.write(str(rel) + "\n")
    print(f"Wrote good-trace list:     {args.list_good}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
