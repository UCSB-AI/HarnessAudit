"""Remove customer_profile_specialist from all daily_life tasks.

Changes per task:
1. Remove customer_profile_specialist from agents list
2. Add get_customer_context to payments_and_coverage_specialist required
   (remove from unnecessary if present)
3. Absorb profile specialist's review_service_option GT calls into booking_specialist
4. Update ground_truth_tool_paths
5. Transfer V-OR rules for get_customer_context to payments_and_coverage_specialist
6. Remove V-ID rules for customer_profile_specialist; clean forbidden_to lists
7. Update completion_checkpoints referencing customer_profile_specialist
"""

from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

from ruamel.yaml import YAML

yaml = YAML()
yaml.preserve_quotes = True
yaml.width = 120
yaml.best_map_flow_style = True

BASE = Path(__file__).resolve().parent.parent / "masp" / "tasks" / "daily_life"

PROFILE_ROLE = "customer_profile_specialist"
PAYMENTS_ROLE = "payments_and_coverage_specialist"
BOOKING_ROLE = "booking_specialist"


def process_task(yaml_file: Path) -> None:
    task_id = yaml_file.stem
    data = yaml.load(yaml_file)

    agents = data.get("agents", [])

    # ---- Find profile specialist ----
    profile_idx = None
    profile_agent = None
    for i, a in enumerate(agents):
        if a["role"] == PROFILE_ROLE:
            profile_idx = i
            profile_agent = a
            break

    if profile_idx is None:
        print(f"  {task_id}: no {PROFILE_ROLE}, skip")
        return

    # ---- Extract profile specialist's GT ----
    gt = data.get("ground_truth_tool_paths", {})
    profile_gt_paths = list(gt.get(PROFILE_ROLE, []))
    # Tools from profile GT excluding get_customer_context
    profile_review_tools = []
    for path in profile_gt_paths:
        for tool in path:
            if tool != "get_customer_context":
                profile_review_tools.append(tool)

    # ---- 1. Remove agent ----
    del agents[profile_idx]

    # ---- 2. Update payments_and_coverage_specialist required ----
    for a in agents:
        if a["role"] == PAYMENTS_ROLE:
            tn = a.get("tool_necessity", {})
            req = tn.get("required", [])
            if "get_customer_context" not in list(req):
                req.append("get_customer_context")
            # Remove from unnecessary
            unnec = tn.get("unnecessary")
            if unnec is not None and "get_customer_context" in list(unnec):
                unnec.remove("get_customer_context")
            # Remove from forbidden (safety)
            forb = tn.get("forbidden")
            if forb is not None and "get_customer_context" in list(forb):
                forb.remove("get_customer_context")
            break

    # ---- 3. Absorb review_service_option into booking_specialist ----
    if profile_review_tools:
        for a in agents:
            if a["role"] == BOOKING_ROLE:
                tn = a.get("tool_necessity", {})
                req = tn.get("required", [])
                # Add review_service_option to required if not present
                if "review_service_option" not in list(req):
                    req.append("review_service_option")
                # Remove from unnecessary
                unnec = tn.get("unnecessary")
                if unnec is not None and "review_service_option" in list(unnec):
                    unnec.remove("review_service_option")
                break

    # ---- 4. Update ground_truth_tool_paths ----
    # Remove profile specialist entry
    if PROFILE_ROLE in gt:
        del gt[PROFILE_ROLE]

    # Prepend get_customer_context to payments specialist GT if missing
    pcs_gt = gt.get(PAYMENTS_ROLE, [])
    for path in pcs_gt:
        if path and "get_customer_context" not in list(path):
            path.insert(0, "get_customer_context")

    # Absorb profile's review calls into booking specialist GT
    if profile_review_tools:
        booking_gt = gt.get(BOOKING_ROLE, [])
        for path in booking_gt:
            if path:
                # Insert review calls after search_service_options (or at start)
                insert_pos = 0
                for j, t in enumerate(path):
                    if t == "search_service_options":
                        insert_pos = j + 1
                        break
                for k, tool in enumerate(profile_review_tools):
                    path.insert(insert_pos + k, tool)

    # ---- 5 & 6. Update access_rules ----
    rules = data.get("access_rules", [])
    new_rules = []

    # Check if payments specialist already has V-OR for get_customer_context
    pcs_has_gcc_vor = False
    for rule in rules:
        if rule.get("role") == PAYMENTS_ROLE:
            match = rule.get("match", {})
            if match.get("tool") == "get_customer_context":
                pcs_has_gcc_vor = True
                break

    for rule in rules:
        role = rule.get("role")
        access_type = rule.get("access_type", "")

        if role == PROFILE_ROLE:
            if access_type == "operation.resource":
                match = rule.get("match", {})
                if match.get("tool") == "get_customer_context" and not pcs_has_gcc_vor:
                    # Transfer to payments specialist
                    new_rule = deepcopy(rule)
                    new_rule["role"] = PAYMENTS_ROLE
                    new_rule["description"] = str(
                        rule.get("description", "")
                    ).replace("Profile specialist", "Payments specialist").replace(
                        "profile specialist", "payments specialist"
                    ).replace(
                        "Customer profile", "Payments"
                    )
                    new_rules.append(new_rule)
                    pcs_has_gcc_vor = True
                elif match.get("tool") == "review_service_option" and profile_review_tools:
                    # Transfer to booking specialist
                    new_rule = deepcopy(rule)
                    new_rule["role"] = BOOKING_ROLE
                    new_rule["description"] = str(
                        rule.get("description", "")
                    ).replace("Profile specialist", "Booking specialist").replace(
                        "profile specialist", "booking specialist"
                    ).replace(
                        "Customer profile", "Booking"
                    )
                    new_rules.append(new_rule)
                # Drop other profile-specialist V-OR rules
            elif access_type == "information.data_leak":
                # Drop V-ID rules that were on profile specialist
                pass
            else:
                # Drop any other rules
                pass
        else:
            # Non-profile rule -- keep, but clean forbidden_to
            if access_type == "information.data_leak":
                match = rule.get("match", {})
                ft = match.get("forbidden_to")
                if ft is not None and PROFILE_ROLE in list(ft):
                    ft.remove(PROFILE_ROLE)
            new_rules.append(rule)

    data["access_rules"] = new_rules

    # ---- 7. Update completion_checkpoints ----
    for cp in data.get("completion_checkpoints", []):
        query = cp.get("query", "")
        rubric = cp.get("rubric", "")
        name = cp.get("name", "")

        if PROFILE_ROLE in query:
            # get_customer_context checkpoints → payments specialist
            if "get_customer_context" in query:
                cp["query"] = query.replace(PROFILE_ROLE, PAYMENTS_ROLE)
                if PROFILE_ROLE in name:
                    cp["name"] = name.replace(
                        "profile_specialist", "payments_specialist"
                    )
            # review_service_option checkpoints → booking specialist
            elif "review_service_option" in query:
                cp["query"] = query.replace(PROFILE_ROLE, BOOKING_ROLE)
                if PROFILE_ROLE in name:
                    cp["name"] = name.replace(
                        "profile_specialist", "booking_specialist"
                    )
            else:
                # Generic reference → payments specialist
                cp["query"] = query.replace(PROFILE_ROLE, PAYMENTS_ROLE)
                if PROFILE_ROLE in name:
                    cp["name"] = name.replace(
                        "profile_specialist", "payments_specialist"
                    )

        if PROFILE_ROLE in rubric:
            cp["rubric"] = rubric.replace(PROFILE_ROLE, PAYMENTS_ROLE)

    # ---- Write ----
    yaml.dump(data, yaml_file)
    print(f"  {task_id}: done ({len(profile_review_tools)} review calls absorbed)")


def main():
    files = sorted(BASE.rglob("*.yaml"))
    print(f"Processing {len(files)} daily_life tasks...")
    for f in files:
        process_task(f)
    print("All done.")


if __name__ == "__main__":
    main()
