#!/usr/bin/env python3
"""ITIL priority matrix helper (ServiceNow out-of-box lookup).

    priority.py <impact 1-3> <urgency 1-3> [--instance-priority N]

Prints JSON. With --instance-priority it flags a mismatch, which usually means
the instance has a customised priority data lookup (defer to the instance).
"""
import argparse
import json
import sys

MATRIX = {  # (impact, urgency) -> priority
    (1, 1): 1, (1, 2): 2, (1, 3): 3,
    (2, 1): 2, (2, 2): 3, (2, 3): 4,
    (3, 1): 3, (3, 2): 4, (3, 3): 5,
}
LABELS = {1: "Critical", 2: "High", 3: "Moderate", 4: "Low", 5: "Planning"}
IMPACT = {1: "High", 2: "Medium", 3: "Low"}
URGENCY = {1: "High", 2: "Medium", 3: "Low"}


def priority(impact, urgency):
    if (impact, urgency) not in MATRIX:
        raise ValueError("impact and urgency must each be 1, 2 or 3")
    p = MATRIX[(impact, urgency)]
    return {"impact": impact, "impact_label": IMPACT[impact],
            "urgency": urgency, "urgency_label": URGENCY[urgency],
            "priority": p, "priority_label": LABELS[p]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("impact", type=int)
    ap.add_argument("urgency", type=int)
    ap.add_argument("--instance-priority", type=int)
    args = ap.parse_args(argv)
    try:
        out = priority(args.impact, args.urgency)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    if args.instance_priority is not None:
        out["instance_priority"] = args.instance_priority
        out["matches_instance"] = args.instance_priority == out["priority"]
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
