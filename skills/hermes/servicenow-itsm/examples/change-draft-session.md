# Example 2: change-request draft with mandatory risk and rollback

Illustrative. Invented data. Native profile: no risk field, so the RISK & IMPACT block lives in `description`.

---

**Operator:** draft a change for INC0012339 follow-up: replace the expiring TLS cert on lb-prod-02. Window Saturday 7 Nov, 02:00 to 03:00 ET. J. Rivera implements, P. Shah verifies. We have a backup of the current cert on the file share.

**Agent:** Gathering inputs. Nothing is created until you approve a plan.

*Reads: `get_incident("INC0012339")` → CI sys_id `a3f9…` (lb-prod-02), service payments-portal-prod. `list_assignment_teams()` → "Network Operations". `search_change_requests(active=true, cmdb_ci="a3f9…", order_by="start_date", descending=false)`.*

**Conflict check:** CHG0030417 (App pool recycle settings) is implemented and closed. One scheduled change on the CI: CHG0030452, "lb-prod-02 firmware 4.2.1", Sat 7 Nov 06:00 to 09:00 UTC, which is 01:00 to 04:00 ET. **It overlaps your window.** I cannot resolve that for you. Options: move this change to a different window, or ask the owner of CHG0030452 to sequence them. Which?

**Operator:** move ours to Sat 14 Nov, same time

**Agent:** Window: 14 Nov 02:00 to 03:00 ET = 07:00 to 08:00 UTC (EST). No scheduled change on the CI in that window. The certificate expires on 10 Nov, which is **before** 14 Nov. That would take checkout offline for four days. I need your decision: keep 7 Nov and coordinate with CHG0030452, or treat this as an emergency change? (Emergency needs a P1/P2 incident behind it; INC0012339 is P3.)

**Operator:** ok keep 7 Nov, I'll talk to the firmware change owner. Draft it as normal.

*Draft saved as `draft.json`; first validation:*
`python3 ${HERMES_SKILL_DIR}/scripts/validate_change.py draft.json --profile native`
```json
{
  "valid": false,
  "errors": [
    "backout_plan needs an estimated rollback duration",
    "backout_plan needs a post-rollback verification step"
  ],
  "warnings": [ "implementation_plan names no step owners" ]
}
```

**Agent:** The validator caught two gaps in my first draft of the rollback section: no duration and no post-rollback check. I've added both (15 minutes, verified by P. Shah) and step owners. Re-running…

```json
{ "valid": true, "errors": [], "warnings": [] }
```

```
DRAFT CHANGE (type: normal)   CI: lb-prod-02   Team: Network Operations
Window: Sat 7 Nov 2026 02:00-03:00 ET (07:00-08:00 UTC)
Assumptions: ASSUMPTION: the cert backup on the file share was taken today and the restore path is the same as last quarter. Please confirm.
Conflict: CHG0030452 overlaps; you are coordinating with its owner. Recorded in the justification.

short_description: Replace expiring TLS certificate on lb-prod-02

description:
  Replace the TLS certificate on lb-prod-02 before it expires on 2026-11-10.

  RISK & IMPACT
  Affected services/CIs: payments-portal-prod, public checkout (~4000 daily users); lb-prod-01 stays on the old cert.
  Failure modes:
    - New cert chain incomplete: likelihood Low (same chain as lb-prod-01), impact High (HTTPS errors for checkout).
    - Wrong VIP binding: likelihood Low, impact High (outage until rollback, about 15 min).
  Overall risk rating: Moderate. Reason: production, customer-facing (2 factors); rollback tested last quarter, off-peak, single team, familiar procedure.
  Mitigations: off-peak window, cert pre-staged, lb-prod-01 verified first, smoke checks before the window closes.
  Drafted via Hermes ServiceNow skill; plan PLN-20261028-004; not submitted for approval.

justification: Certificate expires 2026-11-10; expiry takes checkout offline. Follow-up to INC0012339. Overlaps CHG0030452 (firmware 4.2.1); operator is sequencing with its owner.

implementation_plan:
  1. Network Ops (owner: J. Rivera, 10 min): back up the current cert and config.
  2. Network Ops (owner: J. Rivera, 15 min): import the new cert to lb-prod-02 and bind to the VIP.
  3. App Support (owner: P. Shah, 10 min): run smoke checks against the VIP.

backout_plan:
  Rollback trigger criteria: any smoke check fails, or the 5xx rate exceeds 1% for 5 minutes. Decision owner: J. Rivera; decide by 02:45 ET.
  1. Rebind the previous certificate from the backup taken in step 1.
  2. Reload the virtual server configuration.
  3. Verify HTTPS returns 200 with the old chain (P. Shah confirms).
  Estimated rollback time: 15 minutes. Point of no return: none. Restore source: cert backup on the file share.

test_plan: Expected: HTTPS 200 on /health, new cert serial visible, 5xx rate at baseline for 30 minutes (pass). Any failure means invoke the backout plan.
```

*Preview: `create_change_request(..., confirm=false)` → server preview matches. Ledger: `propose --tool sn_create_change_request --target new-change`.*

```
PROPOSED CHANGE SET   plan_id: PLN-20261028-004
Target:      change_request (new)      Read at: n/a (create)
Action:      sn_create_change_request   Server preview: ok   State on create: New (draft)
Fields:      all of the above; type normal; impact 2; cmdb_ci a3f9…; team Network Operations;
             start 2026-11-07 07:00:00 UTC; end 2026-11-07 08:00:00 UTC
Risk:        Creates a draft only; it is not submitted. A draft with an overlapping window is visible to CAB.
Reversible:  A human can cancel the draft in ServiceNow (this skill cannot cancel changes).
Visibility:  internal

Reply  APPROVE PLN-20261028-004  to create, REJECT PLN-20261028-004 to discard,
or tell me what to change. Expires 09:58 ET.
```

---

**Operator:** APPROVE PLN-20261028-004

*Ledger: APPROVED. Gate: ok. `create_change_request(..., confirm=true)` → CHG0030461. `get_change_request("CHG0030461")` read back.*

**Agent:**
```
✔ Applied PLN-20261028-004: created CHG0030461 (state New)
  verified: type=normal ✔  cmdb_ci ✔  team ✔  window ✔  justification ✔  implementation ✔
            RISK & IMPACT block present ✔  backout_plan ✔  test_plan ✔
```
Next (human): review the draft, resolve the overlap with CHG0030452, then submit CHG0030461 for assessment through your normal CAB route. I can draft the CAB summary text if you want.

---

## What this shows

1. A scheduling conflict is found and surfaced before drafting; the skill does not decide it.
2. The skill pushes back on a timeline problem (cert expiry before the proposed window) and on misuse of the emergency type.
3. The validator catches an incomplete rollback section; the draft is fixed before the operator sees a proposal.
4. Risk and rollback are mandatory, specific and consistent (rating matches the factors).
5. Creation is gated like every write; the result is `New` only, and the skill names the human next step.
