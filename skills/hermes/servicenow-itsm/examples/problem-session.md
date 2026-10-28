# Example 4: problem management (recurrence → problem → links)

Illustrative. Invented data. Follows Example 3's finding on the VPN-profile symptom.

---

**Operator:** is there already a problem for the recurring VPN profile failures? If not, open one and link the incidents.

**Agent:** Checking for existing problems and known errors first.

*Reads: `search_problems(short_description_contains="VPN profile", limit=25)` (open and closed, two calls), `search_problems(known_error=true, short_description_contains="VPN")`, `search_incidents(short_description_contains="VPN profile", opened_after="2026-09-28", limit=25)`.*

- Existing problems: none matching. One closed problem, PRB0040118 (2025), concerned a different VPN client.
- Known errors: none for this symptom.
- Incidents (30 days): 4 confirmed by reading each ticket: INC0011893, INC0011947, INC0012019, INC0012044. All "VPN profile fails to load after login", same CI, same workaround ("clear cached profile").
- Threshold met: 4 incidents in 30 days on the same CI.

```
PROBLEM DRAFT
short_description: Recurring "VPN profile fails to load" after login on vpn-gw-01
category: network   cmdb_ci: 5d2e… (vpn-gw-01)   impact: 2   urgency: 2   team: Network Operations
description:
  Symptom pattern: after login the VPN client reports "profile failed to load"; resolved temporarily by clearing the cached profile.
  Evidence: INC0011893 (2026-10-02), INC0011947 (2026-10-12), INC0012019 (2026-10-21), INC0012044 (2026-10-26).
  Services affected: remote access for ~15 users per incident.
  Current workaround: clear the cached profile on the client.
  Suspected causes (HYPOTHESES, not established): profile size growth; gateway profile-push timeout; client version mix.
  Question to answer: why does profile push fail intermittently on vpn-gw-01?
  Related changes / known errors checked: no open changes on the CI; no known errors.
  Drafted via Hermes ServiceNow skill; plan PLN-20261028-006.
```

*Preview: `create_problem(..., confirm=false)` ok.*

```
PROPOSED CHANGE SET   plan_id: PLN-20261028-006
Target:      problem (new)      Action: sn_create_problem   Server preview: ok
Fields:      as drafted above
Risk:        Creates a record that appears in the problem queue. Root cause is NOT asserted.
Reversible:  A human can close or cancel it in ServiceNow.   Visibility: internal

Reply  APPROVE PLN-20261028-006  to create, REJECT PLN-20261028-006 to discard. Expires 10:41 ET.
```

**Operator:** APPROVE PLN-20261028-006

*Ledger approve → APPROVED; gate ok; `create_problem(confirm=true)` → PRB0040231; `get_problem` read back.*

**Agent:** ✔ Applied PLN-20261028-006: created PRB0040231 (state new). verified: category ✔ ci ✔ team ✔ impact/urgency ✔ description ✔.

Now the links. Four separate plans, shown together:

```
PLN-20261028-007  link INC0011893 → PRB0040231      Server preview: ok
PLN-20261028-008  link INC0011947 → PRB0040231      Server preview: ok
PLN-20261028-009  link INC0012019 → PRB0040231      Server preview: ok
PLN-20261028-010  link INC0012044 → PRB0040231      Server preview: REFUSED: incident already linked to PRB0039877
```
Risk: sets the incident's problem field only. Reversible by a human clearing the field. Internal.

INC0012044 is already linked to a different problem (PRB0039877), and the tool refuses to move it. I've left it out and not worked around it. You may want to look at PRB0039877: it might be the same pattern or a related one. Plans 007 to 009 are ready.

Approve each plan by name, for example: `APPROVE PLN-20261028-007 PLN-20261028-008 PLN-20261028-009`

---

**Operator:** APPROVE ALL

*Ledger: `{"decision": "NOT_APPROVAL", "reason": "unexpected token 'ALL' (no wildcards or extra words)", "pending": ["PLN-20261028-007", "PLN-20261028-008", "PLN-20261028-009"]}`*

**Agent:** Wildcards aren't accepted. Please name the plans: `APPROVE PLN-20261028-007 PLN-20261028-008 PLN-20261028-009`.

**Operator:** APPROVE PLN-20261028-007 PLN-20261028-008 PLN-20261028-009

*Ledger approve → APPROVED for all three (atomic). Per plan: stale check, gate, `link_incident_to_problem(confirm=true)`, read back. `get_problem("PRB0040231")` shows linked incidents.*

**Agent:**
```
✔ Applied PLN-20261028-007/008/009
  verified via get_problem(PRB0040231): linked = INC0011893, INC0011947, INC0012019 ✔
  not linked: INC0012044 (already on PRB0039877)
```
Next: I can read PRB0039877 and tell you whether it's the same issue.

---

## What this shows

1. Existing problems and known errors are checked before proposing a new one (including closed ones).
2. Recurrence is confirmed by reading tickets, not by a count alone; hypotheses are labelled as such.
3. Create and link are separate plans; a server refusal is reported, not bypassed.
4. Batch approval needs each plan named; `APPROVE ALL` is refused; the batch is atomic.
5. Results are verified from the problem's own linked-incident list.
