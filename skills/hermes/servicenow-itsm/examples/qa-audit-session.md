# Example 3: closed-ticket QA audit

Illustrative. Invented tickets and numbers. Read-only throughout; no approval is needed until a write-back is requested.

---

**Operator:** audit closed incidents for Service Desk, opened in the last 14 days. All P1 and P2, plus a sample of the rest.

**Agent:** Scoping, then sampling. This audit will not change any ticket.

*Reads: `list_assignment_teams()` → Service Desk. `search_incidents(state="resolved", active=false, assignment_group=<sys_id>, opened_after="2026-10-14", limit=100)` and the same for `state="closed"`, paging with `next_offset`.*

Method: 41 matching tickets. All 3 P1/P2 included, plus 5 of 38 P3/P4 drawn with seed 20261028. Sample: 8 of 41. `get_incident` returns work notes for these tickets, so journal-based criteria are assessable. Note: the filter keys on *opened* date, not resolution date.

*Scores written to `scores.json`, then `python3 ${HERMES_SKILL_DIR}/scripts/qa_score.py scores.json --markdown`:*

| Ticket | Pri | Score | Result | Flags |
|---|---|---|---|---|
| INC0011874 | P2 | 88.8 | PASS | - |
| INC0011902 | P1 | 57.5 | FAIL | no_root_cause, sla_breached |
| INC0011931 | P2 | 77.5 | PASS | - |
| INC0011950 | P3 | 46.2 | FAIL | close_code_mismatch, resolved_without_verification |
| INC0011988 | P3 | 75.0 | PASS | - |
| INC0012007 | P4 | 87.5 | PASS | - |
| INC0012019 | P3 | 73.8 | FAIL | recurring_pattern |
| INC0012036 | P4 | n/a | INSUFFICIENT_EVIDENCE | - |

Pass rate: 4/7 (57%). Mean score: 72.3.
1 ticket(s) had insufficient evidence and are excluded from the pass rate.

```
FINDINGS
INC0011902  (P1, score 57.5, FAIL)  flags: no_root_cause, sla_breached
  - root_cause_identified 1: close notes "Restarted IIS on app-07, users confirmed working" [resolved 2026-10-20 15:12 UTC]; no cause, no follow-up.
  - user_comms_and_sla 2: first update 52 min after open; P1 respond target is 15 min.
  - Below threshold, and independently failed by the rule "P1/P2 closed without a root cause".

INC0011950  (P3, score 46.2, FAIL)  flags: close_code_mismatch, resolved_without_verification
  - close_code "Solution provided" but notes say "caller not reachable, closing" [2026-10-22 10:03 UTC]
  - resolution_quality 1: no fix described; no user confirmation.

INC0012019  (P3, score 73.8, FAIL)  flags: recurring_pattern
  - Above the 70 threshold, but fails the critical floor: root_cause_identified 1 (critical floor): "cleared cache". Same VPN-profile symptom appears in 4 incidents since 2026-10-02.

INC0012036  (P4): INSUFFICIENT_EVIDENCE (score n/a). Journal empty and close notes blank; only classification and priority assessable (20% of weight).

PATTERNS
- "Restart/clear cache" closures without a cause on 2 of 8 tickets, both on the app tier.
- Two tickets closed without user contact.
- VPN-profile symptom recurs: 4 incidents in 26 days on the same CI.

RECOMMENDED FOLLOW-UPS
- Open a problem for the VPN-profile recurrence (I can check for an existing one and draft it; see problem workflow).
- Add "cause + verification" to the resolve template for the team.
- Review P1 first-response handling for INC0011902.
```

**Operator:** can you post the INC0011950 and INC0012019 findings on the tickets?

**Agent:** INC0011950 is Closed, and the update tool cannot edit closed incidents, so the finding stays in this report. INC0012019 is Resolved, so a work note is possible.

```
PROPOSED CHANGE SET   plan_id: PLN-20261028-005
Target:      incident INC0012019   (sys_id: 2b7e…)      Read at: 2026-10-28 14:02:31 UTC
Action:      sn_add_work_note → update_incident(work_note=…)   Server preview: ok

Field       Current   Proposed
----------  --------  ------------------------------------------------------------
work_note   -         "QA 2026-10-28: score 73.8 (FAIL: critical floor, no root cause). Recurring VPN-profile
                       symptom (4 incidents since 2026-10-02). Recommend a problem record.
                       [PLN-20261028-005 approved by operator]"

Rationale:   Records the audit finding on the ticket.   Risk: none beyond a visible internal note.
Reversible:  Notes cannot be removed by this skill.     Visibility: internal work note only

Reply  APPROVE PLN-20261028-005  to post, REJECT PLN-20261028-005 to discard. Expires 10:32 ET.
```

---

## What this shows

1. A reproducible sample (all P1/P2 + seeded random) with the method stated.
2. Scores come from `qa_score.py`, not mental arithmetic; the critical floor and the P1/P2 rule override a passing total.
3. Evidence is quoted for every low score; "insufficient evidence" is distinguished from "bad".
4. The audit never reopens or alters tickets. Write-back is optional, gated, internal, and limited to what the tool allows.
5. Patterns feed the problem workflow rather than blaming individuals.
