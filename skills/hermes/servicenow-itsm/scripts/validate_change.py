#!/usr/bin/env python3
"""Validate a draft change request before it is proposed for approval.

    validate_change.py draft.json [--profile native|composio] [--now ISO8601]

Profiles
  native   ServiceNow MCP create_change_request: no risk field exists, so the
           risk and impact assessment must be embedded in `description` under a
           heading containing "RISK & IMPACT". Each text field is capped at 4000.
  composio generic table writes: separate `risk_impact_analysis` (+ `risk`) fields.

Exit 0 = valid (warnings allowed), 1 = errors. Output is JSON.
This checks structure and specificity. It cannot judge technical correctness;
that is the operator's and CAB's job.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone

MIN_LEN = {"short_description": 10, "description": 40, "justification": 40,
           "implementation_plan": 80, "backout_plan": 80, "test_plan": 40}
MAX_NATIVE = 4000
PLACEHOLDER = re.compile(r"\b(tbd|todo|to be determined|lorem|fixme|xxx)\b|<[^<>\n]{2,}>|\bn/?a\b", re.I)
NUMBERED = re.compile(r"^\s*\d+[.)]\s+\S", re.M)
RISK_HEADING = re.compile(r"risk\s*(&|and)\s*impact", re.I)


def _dt(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" ", "T"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _risk_text(draft, profile):
    if profile == "composio":
        return draft.get("risk_impact_analysis", "")
    desc = draft.get("description", "")
    m = RISK_HEADING.search(desc)
    return desc[m.start():] if m else ""


def _overall_rating(draft, risk_text, profile):
    """Overall rating: the `risk` field (composio) or a 'risk rating: X' statement in the text."""
    if profile == "composio" and draft.get("risk"):
        return str(draft["risk"]).lower()
    m = re.search(r"(?:overall\s+)?risk\s+rating\W+(high|moderate|medium|low)", risk_text, re.I)
    return m.group(1).lower() if m else None


def validate(draft, profile="native", now=None):
    errors, warnings = [], []
    now = now or datetime.now(timezone.utc)

    def err(msg): errors.append(msg)
    def warn(msg): warnings.append(msg)

    for field, minimum in MIN_LEN.items():
        val = str(draft.get(field, "") or "").strip()
        if not val:
            err("%s is required" % field)
        elif len(val) < minimum:
            err("%s is too short (%d < %d chars); be specific" % (field, len(val), minimum))
        if profile == "native" and len(val) > MAX_NATIVE:
            err("%s exceeds %d characters (native MCP limit)" % (field, MAX_NATIVE))
        if PLACEHOLDER.search(val):
            err("%s contains placeholder text (TBD/TODO/N/A/<...>)" % field)
    if len(str(draft.get("short_description", ""))) > 160:
        err("short_description exceeds 160 characters")

    if draft.get("type") not in ("normal", "standard", "emergency"):
        err("type must be normal, standard or emergency")
    if profile == "native":
        if not (draft.get("team") or draft.get("assignment_group")):
            err("team (assignment team) is required")
    elif not draft.get("assignment_group"):
        err("assignment_group (sys_id) is required")
    if not draft.get("cmdb_ci"):
        err("cmdb_ci (sys_id) is required; ask the operator, never guess")

    # --- risk & impact ---------------------------------------------------
    risk = _risk_text(draft, profile)
    if profile == "native" and not risk:
        err("description must contain a 'RISK & IMPACT' section (native MCP has no risk field)")
    if profile == "composio":
        if len(risk.strip()) < 80:
            err("risk_impact_analysis is required (>= 80 chars)")
        if draft.get("risk") not in ("high", "moderate", "low"):
            err("risk must be high, moderate or low")
    if risk:
        low = risk.lower()
        if not re.search(r"\b(high|moderate|medium|low)\b", low):
            err("risk section must state an overall rating (High/Moderate/Low)")
        for word in ("likelihood", "impact", "affected"):
            if word not in low:
                err("risk section must address '%s'" % word)
        if "mitigat" not in low:
            warn("risk section lists no mitigations")

    # --- backout (rollback) ---------------------------------------------
    bo = str(draft.get("backout_plan", "")).lower()
    if bo:
        if not re.search(r"trigger|criteria|threshold", bo):
            err("backout_plan needs explicit rollback trigger criteria")
        if len(NUMBERED.findall(bo)) < 2:
            err("backout_plan needs numbered rollback steps (at least 2)")
        if not re.search(r"\d+\s*(min|minute|minutes|h|hr|hour|hours)\b", bo):
            err("backout_plan needs an estimated rollback duration")
        if not re.search(r"verif|validat|confirm", bo):
            err("backout_plan needs a post-rollback verification step")
        if re.search(r"no (rollback|backout)|cannot be (rolled back|reverted)|irreversible", bo):
            if _overall_rating(draft, risk, profile) != "high":
                err("backout says the change is not reversible but overall risk is not rated High")
            warn("irreversible change: confirm a point-of-no-return and backup/restore plan exists")

    # --- implementation / test ------------------------------------------
    impl = str(draft.get("implementation_plan", ""))
    if impl:
        if len(NUMBERED.findall(impl)) < 2:
            err("implementation_plan needs numbered steps (at least 2)")
        if not re.search(r"owner|responsible|assignee", impl, re.I):
            warn("implementation_plan names no step owners")
    tp = str(draft.get("test_plan", "")).lower()
    if tp and not re.search(r"pass|fail|expected|success", tp):
        warn("test_plan has no objective pass/fail criteria")

    # --- schedule --------------------------------------------------------
    try:
        start, end = _dt(draft["start_date"]), _dt(draft["end_date"])
        if end <= start:
            err("end_date must be after start_date")
        elif (end - start).total_seconds() < 900:
            warn("change window is shorter than 15 minutes")
        if start < now:
            err("start_date is in the past")
        elif draft.get("type") == "normal" and (start - now).days < 2:
            warn("normal change starts in under 2 days; CAB lead time may not allow it")
    except (KeyError, ValueError):
        err("start_date and end_date are required, ISO 8601 (UTC)")

    if draft.get("type") == "emergency" and not draft.get("related_incident"):
        err("emergency change must reference the P1/P2 incident that justifies it (related_incident)")
    return {"valid": not errors, "errors": errors, "warnings": warnings}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("draft", help="JSON file or - for stdin")
    ap.add_argument("--profile", choices=["native", "composio"], default="native")
    ap.add_argument("--now", help="override current time (ISO 8601) for testing")
    args = ap.parse_args(argv)
    raw = sys.stdin.read() if args.draft == "-" else open(args.draft).read()
    result = validate(json.loads(raw), args.profile, _dt(args.now) if args.now else None)
    print(json.dumps(result, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
