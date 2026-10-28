#!/usr/bin/env python3
"""Compute weighted QA scores for closed-ticket audits.

    qa_score.py scores.json [--markdown] [--threshold 70]

Input: one object or a list of objects:
    {"ticket": "INC0012001", "priority": 2,
     "scores": {"classification_accuracy": 3, "priority_justified": 4,
                "assignment_correct": 3, "root_cause_identified": 1,
                "resolution_quality": 3, "close_notes_clarity": 2,
                "user_comms_and_sla": null},
     "flags": ["no_root_cause"]}
Scores are 0-4; null means "not assessable" (weights are renormalised over the
assessed criteria). Rules beyond the weighted sum:
  * critical floor: root_cause_identified or resolution_quality <= 1 fails the ticket
  * P1/P2 carrying a no_root_cause flag fails the ticket
  * if less than 60% of the weight could be assessed the result is INSUFFICIENT_EVIDENCE
"""
import argparse
import json
import sys

WEIGHTS = {
    "classification_accuracy": 10, "priority_justified": 10, "assignment_correct": 10,
    "root_cause_identified": 25, "resolution_quality": 25,
    "close_notes_clarity": 10, "user_comms_and_sla": 10,
}
CRITICAL = ("root_cause_identified", "resolution_quality")
KNOWN_FLAGS = {"no_root_cause", "close_code_mismatch", "reopened_count_gt_0", "sla_breached",
               "resolved_without_verification", "recurring_pattern"}


def score_ticket(item, threshold=70.0):
    scores = item.get("scores", {})
    unknown = set(scores) - set(WEIGHTS)
    if unknown:
        raise ValueError("unknown criteria: %s" % ", ".join(sorted(unknown)))
    assessed = {k: v for k, v in scores.items() if v is not None}
    for k, v in assessed.items():
        if not isinstance(v, (int, float)) or not 0 <= v <= 4:
            raise ValueError("%s must be 0-4 or null" % k)
    weight_assessed = sum(WEIGHTS[k] for k in assessed)
    coverage = weight_assessed / sum(WEIGHTS.values())
    total = round(100 * sum(WEIGHTS[k] * v / 4 for k, v in assessed.items()) / weight_assessed, 1) \
        if weight_assessed else 0.0
    flags = sorted(set(item.get("flags", [])))
    bad = [f for f in flags if f not in KNOWN_FLAGS]
    if bad:
        raise ValueError("unknown flags: %s" % ", ".join(bad))
    reasons = []
    if coverage < 0.6:
        result = "INSUFFICIENT_EVIDENCE"
        reasons.append("only %d%% of weight assessable" % round(coverage * 100))
    else:
        result = "PASS" if total >= threshold else "FAIL"
        if total < threshold:
            reasons.append("score %.1f below threshold %.0f" % (total, threshold))
        for crit in CRITICAL:
            if crit in assessed and assessed[crit] <= 1:
                result = "FAIL"; reasons.append("critical criterion %s scored %s" % (crit, assessed[crit]))
        if item.get("priority") in (1, 2) and "no_root_cause" in flags:
            result = "FAIL"; reasons.append("P1/P2 closed without a root cause")
    return {"ticket": item.get("ticket"), "priority": item.get("priority"), "score": total,
            "coverage_pct": round(coverage * 100), "result": result, "flags": flags, "reasons": reasons}


def render_markdown(rows):
    out = ["| Ticket | Pri | Score | Result | Flags |", "|---|---|---|---|---|"]
    for r in rows:
        out.append("| %s | %s | %s | %s | %s |" % (
            r["ticket"], ("P%s" % r["priority"]) if r["priority"] else "-",
            "n/a" if r["result"] == "INSUFFICIENT_EVIDENCE" else r["score"],
            r["result"], ", ".join(r["flags"]) or "-"))
    graded = [r for r in rows if r["result"] in ("PASS", "FAIL")]
    if graded:
        passed = sum(1 for r in graded if r["result"] == "PASS")
        mean = sum(r["score"] for r in graded) / len(graded)
        out += ["", "Pass rate: %d/%d (%.0f%%). Mean score: %.1f." % (passed, len(graded), 100 * passed / len(graded), mean)]
    skipped = len(rows) - len(graded)
    if skipped:
        out.append("%d ticket(s) had insufficient evidence and are excluded from the pass rate." % skipped)
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scores", help="JSON file or - for stdin")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--threshold", type=float, default=70.0)
    args = ap.parse_args(argv)
    data = json.loads(sys.stdin.read() if args.scores == "-" else open(args.scores).read())
    try:
        rows = [score_ticket(i, args.threshold) for i in (data if isinstance(data, list) else [data])]
    except ValueError as exc:
        print(json.dumps({"error": str(exc)})); return 1
    print(render_markdown(rows) if args.markdown else json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
