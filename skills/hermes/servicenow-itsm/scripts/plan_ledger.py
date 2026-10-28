#!/usr/bin/env python3
"""Approval ledger for the servicenow-itsm skill.

Deterministic gate for write actions. The model proposes a payload, the ledger
issues a plan_id and records the payload hash. The operator's literal chat
message is then parsed here, never by the model's judgement:

    propose  -> issue plan_id, store payload hash + expiry
    approve  -> parse the operator's message strictly (APPROVE / REJECT / CANCEL)
    gate     -> immediately before the write: payload hash must match, approval
                must be unexpired, and the plan is consumed (single use)
    verify   -> compare approved fields against the record read back after writing
    status   -> list pending plans
    supersede-> void a plan when the operator asks for edits

Limits: this reduces model error. It is not an adversarial boundary, because the
model supplies the message text. Real enforcement is the MCP preview/confirm
step plus the host's own approval prompt (see references/approval-protocol.md).

State: $SN_ITSM_STATE_DIR or ~/.hermes/state/servicenow-itsm/plans.json
Plan ids use the UTC date: PLN-YYYYMMDD-NNN.
Exit codes: 0 ok / approved, 2 not approved or gate failed, 3 rejected, 1 usage.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PLAN_RE = re.compile(r"^PLN-\d{8}-\d{3}$", re.I)
TICKET_RE = re.compile(r"^(INC|CHG|PRB)\d{7,}$", re.I)
REJECT_RE = re.compile(r"^\s*(REJECT|CANCEL)\s+(PLN-\d{8}-\d{3})\s*$", re.I)
APPROVE_RE = re.compile(r"^\s*APPROVE\s+(.+?)\s*$", re.I | re.S)
TICKET_TOOLS = ("resolve", "close")  # tool ids containing these need a typed ticket number
VOLATILE_KEYS = {"confirm"}          # excluded from the payload hash


def canonical_hash(payload):
    """sha256 over canonical JSON, ignoring volatile keys such as `confirm`."""
    clean = {k: v for k, v in payload.items() if k not in VOLATILE_KEYS}
    blob = json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def parse_message(message):
    """Return (kind, plan_ids, tickets, problem).

    kind is 'approve', 'reject', 'invalid' or 'none'. Anything other than the
    exact phrase APPROVE <plan_id>[ <plan_id>...][ <ticket>...] is not approval:
    extra words, wildcards, quotes or embedded text all yield 'invalid'/'none'.
    """
    rej = REJECT_RE.match(message or "")
    if rej:
        return "reject", [rej.group(2).upper()], [], None
    app = APPROVE_RE.match(message or "")
    if not app:
        return "none", [], [], "message is not of the form APPROVE <plan_id>"
    plans, tickets = [], []
    for tok in app.group(1).split():
        if PLAN_RE.match(tok):
            plans.append(tok.upper())
        elif TICKET_RE.match(tok):
            tickets.append(tok.upper())
        else:
            return "invalid", [], [], "unexpected token %r (no wildcards or extra words)" % tok
    if not plans:
        return "invalid", [], [], "no plan_id given"
    return "approve", plans, tickets, None


class Ledger:
    def __init__(self, state_dir=None, clock=time.time):
        base = state_dir or os.environ.get("SN_ITSM_STATE_DIR") or \
            Path.home() / ".hermes" / "state" / "servicenow-itsm"
        self.dir = Path(base)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "plans.json"
        self.clock = clock

    # -- storage ---------------------------------------------------------
    def _load(self):
        if not self.path.exists():
            return {"plans": {}}
        return json.loads(self.path.read_text())

    def _save(self, data):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        os.replace(tmp, self.path)

    def _status(self, plan):
        if plan["status"] in ("pending", "approved") and self.clock() > plan["expires_at"]:
            return "expired"
        return plan["status"]

    # -- operations ------------------------------------------------------
    def propose(self, payload, tool, target, ttl_minutes=30, requires_ticket=None):
        data = self._load()
        today = datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y%m%d")
        n = 1 + sum(1 for p in data["plans"] if p.startswith("PLN-%s-" % today))
        plan_id = "PLN-%s-%03d" % (today, n)
        if requires_ticket is None and any(w in tool.lower() for w in TICKET_TOOLS):
            if not TICKET_RE.match(target or ""):
                raise ValueError("resolve/close plans need --target set to the ticket number")
            requires_ticket = target.upper()
        now = self.clock()
        data["plans"][plan_id] = {
            "plan_id": plan_id, "tool": tool, "target": target,
            "payload_hash": canonical_hash(payload), "created_at": now,
            "expires_at": now + ttl_minutes * 60, "status": "pending",
            "requires_ticket": requires_ticket, "approved_text": None,
        }
        self._save(data)
        return data["plans"][plan_id]

    def approve(self, message):
        kind, plan_ids, tickets, problem = parse_message(message)
        data = self._load()
        pending = sorted(p for p, v in data["plans"].items() if self._status(v) == "pending")
        if kind in ("none", "invalid"):
            return {"decision": "NOT_APPROVAL", "reason": problem, "pending": pending}
        if kind == "reject":
            pid = plan_ids[0]
            plan = data["plans"].get(pid)
            if not plan:
                return {"decision": "UNKNOWN_PLAN", "plan_id": pid, "pending": pending}
            plan["status"] = "rejected"
            self._save(data)
            return {"decision": "REJECTED", "plan_id": pid}
        results, ok = [], True
        for pid in plan_ids:
            plan = data["plans"].get(pid)
            if not plan:
                results.append({"plan_id": pid, "result": "UNKNOWN_PLAN"}); ok = False; continue
            st = self._status(plan)
            if st != "pending":
                results.append({"plan_id": pid, "result": st.upper()}); ok = False; continue
            need = plan.get("requires_ticket")
            if need and need not in tickets:
                results.append({"plan_id": pid, "result": "TICKET_REQUIRED", "ticket": need}); ok = False; continue
            results.append({"plan_id": pid, "result": "OK"})
        if not ok:  # atomic: approve all listed plans or none
            return {"decision": "NOT_APPROVAL", "plans": results, "pending": pending}
        for pid in plan_ids:
            data["plans"][pid]["status"] = "approved"
            data["plans"][pid]["approved_text"] = message.strip()
            data["plans"][pid]["approved_at"] = self.clock()
        self._save(data)
        return {"decision": "APPROVED", "plans": results}

    def gate(self, plan_id, payload):
        plan_id = plan_id.upper()
        data = self._load()
        plan = data["plans"].get(plan_id)
        if not plan:
            return {"ok": False, "reason": "UNKNOWN_PLAN"}
        st = self._status(plan)
        if st != "approved":
            return {"ok": False, "reason": "PLAN_%s" % st.upper()}
        if canonical_hash(payload) != plan["payload_hash"]:
            return {"ok": False, "reason": "PAYLOAD_HASH_MISMATCH",
                    "hint": "payload differs from the approved one; re-propose under a new plan_id"}
        plan["status"] = "executed"
        plan["executed_at"] = self.clock()
        self._save(data)
        return {"ok": True, "plan_id": plan_id, "tool": plan["tool"], "target": plan["target"],
                "approved_text": plan["approved_text"]}

    def supersede(self, plan_id):
        data = self._load()
        plan = data["plans"].get(plan_id.upper())
        if not plan:
            return {"ok": False, "reason": "UNKNOWN_PLAN"}
        if plan["status"] in ("pending", "approved"):
            plan["status"] = "superseded"
            self._save(data)
        return {"ok": True, "plan_id": plan_id.upper(), "status": plan["status"]}

    def status(self):
        data = self._load()
        out = []
        for pid, plan in sorted(data["plans"].items()):
            st = self._status(plan)
            if st in ("pending", "approved"):
                out.append({"plan_id": pid, "status": st, "tool": plan["tool"],
                            "target": plan["target"],
                            "expires_in_s": max(0, int(plan["expires_at"] - self.clock()))})
        return {"open_plans": out}


def verify(expected, actual):
    """Compare approved field values with the record read back. Values compare as strings."""
    mismatches = []
    for key, want in expected.items():
        have = actual.get(key)
        if isinstance(have, dict):  # display_value=all style {"value":..,"display_value":..}
            have_vals = {str(have.get("value")), str(have.get("display_value"))}
        else:
            have_vals = {str(have)}
        if str(want) not in have_vals:
            mismatches.append({"field": key, "approved": want, "actual": have})
    return {"verified": not mismatches, "mismatches": mismatches}


def _read_json(path):
    return json.load(sys.stdin) if path == "-" else json.loads(Path(path).read_text())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("propose"); p.add_argument("--payload", required=True, help="JSON file or - for stdin")
    p.add_argument("--tool", required=True); p.add_argument("--target", required=True)
    p.add_argument("--ttl", type=int, default=30); p.add_argument("--require-ticket")
    a = sub.add_parser("approve"); a.add_argument("--message", required=True)
    g = sub.add_parser("gate"); g.add_argument("--plan-id", required=True); g.add_argument("--payload", required=True)
    s = sub.add_parser("supersede"); s.add_argument("--plan-id", required=True)
    sub.add_parser("status")
    h = sub.add_parser("hash"); h.add_argument("--payload", required=True)
    v = sub.add_parser("verify"); v.add_argument("--expected", required=True); v.add_argument("--actual", required=True)
    args = ap.parse_args(argv)

    led = Ledger()
    try:
        if args.cmd == "propose":
            out = led.propose(_read_json(args.payload), args.tool, args.target, args.ttl, args.require_ticket)
            code = 0
        elif args.cmd == "approve":
            out = led.approve(args.message)
            code = {"APPROVED": 0, "REJECTED": 3}.get(out["decision"], 2)
        elif args.cmd == "gate":
            out = led.gate(args.plan_id, _read_json(args.payload)); code = 0 if out["ok"] else 2
        elif args.cmd == "supersede":
            out = led.supersede(args.plan_id); code = 0 if out["ok"] else 2
        elif args.cmd == "status":
            out = led.status(); code = 0
        elif args.cmd == "hash":
            out = {"payload_hash": canonical_hash(_read_json(args.payload))}; code = 0
        else:
            out = verify(_read_json(args.expected), _read_json(args.actual)); code = 0 if out["verified"] else 2
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)})); return 1
    print(json.dumps(out, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
