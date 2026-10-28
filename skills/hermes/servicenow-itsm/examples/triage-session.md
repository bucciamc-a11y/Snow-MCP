# Example 1: incident triage with the approval gate

Illustrative. All identifiers, names, times and counts are invented. Tool names are the native ServiceNow MCP's. Script output is abridged.

---

**Operator:** triage INC0012345

**Agent:** Reading INC0012345 and checking context. Nothing will be changed until you approve.

*Reads: `get_incident("INC0012345")`, `get_journal(identifier="INC0012345")`, `search_incidents(short_description_contains="payments portal 503", opened_after="2026-09-28")`, `search_problems(known_error=true, short_description_contains="payments portal")`, `search_change_requests(active=true, cmdb_ci=<ci sys_id>)`, `list_assignment_teams()`, `whois_oncall([<group sys_id>])`.*

```
TRIAGE SUMMARY: INC0012345
Situation:   Payments web portal returns HTTP 503 to all customers since 09:12 ET (13:12 UTC).
             Service Desk logged ~400 failed checkouts in 20 minutes. Journal: "Restarted app pool, no change."
Evidence:    Known: 503 for all users, ~400 failures, restart did not help.
             Inferred: no workaround (none mentioned in 3 notes).  Unknown: whether a deploy preceded it.
Assessment:  Impact 1 (High): customer-facing revenue service fully down.
             Urgency 1 (High): work stopped, no workaround, financial exposure.
             priority.py 1 1 -> Priority 1 (Critical)
Ownership:   Platform SRE (configured team; owns load balancer and app tier for payments-portal-prod).
             On call now: A. Moreau.
Related:     INC0012339 (same CI, 08:58 ET, "slow checkout"): probable precursor.
             Known error: none.  Changes on CI: CHG0030417 (implemented 08:30 ET, "App pool recycle settings"): prime suspect, not confirmed.
Escalation:  MAJOR INCIDENT criteria met (P1, customer-facing revenue service). Recommend engaging the Major Incident process now.
             Security: nothing indicates it.  Problem candidate: not yet.
Open questions: Was anything deployed at 09:10? (CHG0030417 lands 40 min earlier.)
Confidence:  High on classification and ownership.
```

*Preview: `assign_incident(identifier="INC0012345", team="Platform SRE", impact=1, urgency=1, category="software", work_note="…", confirm=false)` returns the server's description of the change. It matches.*

*Ledger: `plan_ledger.py propose --payload args.json --tool sn_assign_incident --target INC0012345`*
```json
{ "plan_id": "PLN-20261028-001", "status": "pending", "payload_hash": "9c1e…", "expires_at": 1793194860.0, "requires_ticket": null }
```

```
PROPOSED CHANGE SET   plan_id: PLN-20261028-001
Target:      incident INC0012345   (sys_id: 6f1c2a9e4b7d4c3a8e5f0a1b2c3d4e5f)
Read at:     2026-10-28 13:31:44 UTC   (aborts if the record changes first)
Action:      sn_assign_incident   Server preview: ok

Field              Current                 Proposed
-----------------  ----------------------  ---------------------------------
impact             2 - Medium              1 - High
urgency            2 - Medium              1 - High
priority           3 - Moderate            1 - Critical (derived by ServiceNow; I do not set it)
team               Service Desk            Platform SRE
category           inquiry                 software
work_note          -                       "Triaged via Hermes ServiceNow skill. Customer-facing payments
                                           outage, HTTP 503 since 09:12 ET, ~400 failed checkouts, no
                                           workaround. Related INC0012339. Recent change CHG0030417 on the
                                           CI noted for investigation, not confirmed as cause.
                                           [PLN-20261028-001 approved by operator]"

Rationale:   impact/urgency: revenue-critical external service down, no workaround. Team: owns the CI.
Risk:        P1 starts the P1 SLA clocks and may page on-call. If the assessment is wrong the cost is noise.
Reversible:  Yes. Set impact, urgency, team and category back to the Current column; SLA clocks may need manual correction.
Visibility:  internal work note only

Reply  APPROVE PLN-20261028-001  to execute, REJECT PLN-20261028-001 to discard,
or tell me what to change. Expires 10:01 ET (30 min).
```

---

**Operator:** ok go ahead

*Ledger: `plan_ledger.py approve --message "ok go ahead"`*
```json
{ "decision": "NOT_APPROVAL", "reason": "message is not of the form APPROVE <plan_id>", "pending": ["PLN-20261028-001"] }
```

**Agent:** That isn't an approval, so nothing has been written. To execute, reply exactly `APPROVE PLN-20261028-001`. To change something, tell me what. The plan expires at 10:01 ET.

---

**Operator:** route it to Network Operations instead, they own the load balancer

*`plan_ledger.py supersede --plan-id PLN-20261028-001`. `list_assignment_teams()` has exactly one "Network Operations". Preview with `confirm=false` is ok. New plan registered.*

**Agent:** Voiding PLN-20261028-001 and issuing a revised plan. Only the team and the note change.

```
PROPOSED CHANGE SET   plan_id: PLN-20261028-002
Target:      incident INC0012345   (sys_id: 6f1c2a9e4b7d4c3a8e5f0a1b2c3d4e5f)
Read at:     2026-10-28 13:34:10 UTC
Action:      sn_assign_incident   Server preview: ok

Field       Current            Proposed
----------  -----------------  -----------------------------------------------
impact      2 - Medium         1 - High
urgency     2 - Medium         1 - High
team        Service Desk       Network Operations
category    inquiry            software
work_note   -                  "Triaged via Hermes ServiceNow skill. … Routed to Network Operations at the
                               operator's direction (load balancer ownership). … [PLN-20261028-002 approved by operator]"

Rationale:   As PLN-…-001, except team follows your decision.
Risk:        If the fault is in the app tier, Network Operations may need to reassign, costing minutes on a P1.
Reversible:  Yes (restore the Current column).   Visibility: internal work note only

Reply  APPROVE PLN-20261028-002  to execute, REJECT PLN-20261028-002 to discard,
or tell me what to change. Expires 10:04 ET.
```

---

**Operator:** APPROVE PLN-20261028-002

*Ledger approve:*
```json
{ "decision": "APPROVED", "plans": [ { "plan_id": "PLN-20261028-002", "result": "OK" } ] }
```
*Stale check: `get_incident` again. Updated time is unchanged since 13:34:10.*
*Gate: `plan_ledger.py gate --plan-id PLN-20261028-002 --payload args.json`*
```json
{ "ok": true, "plan_id": "PLN-20261028-002", "tool": "sn_assign_incident", "target": "INC0012345", "approved_text": "APPROVE PLN-20261028-002" }
```
*Apply: `assign_incident(…same arguments…, confirm=true)`. Read back: `get_incident`. Verify: `plan_ledger.py verify --expected expected.json --actual actual.json` returns `{"verified": true, "mismatches": []}`.*

**Agent:**
```
✔ Applied PLN-20261028-002 to INC0012345   (approved by operator 09:35 ET)
  verified: impact=1 ✔  urgency=1 ✔  team=Network Operations ✔  category=software ✔  work_note added ✔
  instance priority: 1 - Critical (matches priority.py)
```
Next: please engage the Major Incident process. I can draft the first stakeholder update, and I can propose linking INC0012339 as related. That would be a separate plan.

---

## What this shows

1. Read-only investigation, evidence labelled Known / Inferred / Unknown, precedes any proposal.
2. Priority is derived (priority.py), never set directly; the read-back confirms the instance agrees.
3. The major-incident recommendation comes first and does not wait for approval.
4. A server-side preview (`confirm=false`) is checked against the proposal before the operator sees it.
5. "ok go ahead" is rejected by the ledger; nothing executes.
6. An edit voids the old plan and creates a new one.
7. Only `APPROVE <plan_id>` executes, after a stale-record check and payload-hash gate.
8. The write is verified by read-back, field by field.
