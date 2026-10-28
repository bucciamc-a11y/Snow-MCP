# Error handling

Principle: after any failed or ambiguous write, **read the record before doing anything else**. Never retry a write without a fresh approval if it may have applied.

| Situation | What it usually means | Do |
|---|---|---|
| `not found` on number | Typo, wrong table, or ACL hides it | Re-check the number format (INC/CHG/PRB + 7 digits); try the sys_id; if still absent, say it may be hidden by access rules. Do not guess another ticket |
| 401 / 403 / "insufficient rights" | Integration user lacks a role or ACL | Report which operation failed. Do not try an alternative tool to get around it |
| 429 / rate limit | Throttled | Wait the indicated time (or 30 s), retry reads once; for bulk work reduce page size and batch |
| Timeout or 5xx on a read | Transient | Retry once; then report |
| Timeout or 5xx on a **write** | Write may have applied | Re-read. If applied, verify and report. If not, offer to re-propose (new plan_id, fresh approval) |
| `confirm=false` preview differs from your proposal | Your arguments were transformed or rejected | Trust the preview, correct the proposal, re-propose |
| Invalid `close_code` | Not a valid choice on this instance | Ask the operator for a valid value, or look at recently resolved tickets for examples. Never invent |
| "change type cannot be created" / type not allowed (often `emergency`) | The server only allows the types in `MCP_CHANGE_CREATE_TYPES` (default `normal,standard`) | A server guard working as designed; only the administrator changes it. Say so, offer `normal` or the manual route, never retry or reword to get around it |
| "no assignment teams are configured" on a change or problem `team` | `MCP_ASSIGNMENT_GROUPS` is empty | Same: administrator-controlled. Leave `team` for the operator to set in ServiceNow |
| `get_journal` / `search_cis` / another tool not listed | Its `MCP_ENABLE_*` flag is off | Say which capability is missing; fall back as in `tool-map.md` |
| Team not in `list_assignment_teams` | Admin-controlled list | Say so; propose another team or leave assignment to the operator |
| Several teams/CIs/groups match | Ambiguity | List candidates with identifiers; ask |
| No CI sys_id available (native) | `search_cis` not listed, or no match | Ask the operator or take it from the linked incident or a similar change |
| `link_incident_to_problem` refused | Closed incident, resolved problem, or already linked elsewhere | Report the reason verbatim; do not unlink or relink |
| Record changed since proposal | Someone else edited it | Abort, show the diff, re-propose under a new plan_id |
| Ledger: `PAYLOAD_HASH_MISMATCH` | Arguments differ from what was approved | Stop. Re-propose; never "fix" the payload quietly |
| Ledger: `PLAN_EXPIRED` | Approval window passed | Re-read; re-propose |
| Ledger: `NOT_APPROVAL` | Reply was not `APPROVE <plan_id>` | Show the exact phrase and the pending plan_ids |
| Read-back shows a different value than approved | Business rule, ACL, or a flow altered it | Report actual vs approved; do not retry; offer a corrective proposal |
| Priority differs from your matrix result | Customised priority lookup | Report; defer to the instance |
| Tool missing from this session | Connection lacks the capability | Say which capability is missing and give the manual route |
| Search returns nothing | Wrong filter, or truly none | Widen once (drop a filter); report the filters used. "None found with X" is a finding, not "none exist" |
| Pagination | `next_offset` not null | Continue up to the audit/queue cap; say when you stopped and how many remain |
| Output too large | Many records | Request fewer fields; summarise; keep full detail in a scratch file |

## Reporting an error to the operator

State: what you tried, the tool, the error (redacted), the record's current state (after re-reading), and the options. No apologies or speculation about server internals.

```
✘ Could not apply PLN-20261028-002 to INC0012345
  Tool:    assign_incident (confirm=true)   Error: timeout after 30 s
  Record now (re-read 09:41:12): team = Service Desk, impact = 2   -> change NOT applied
  Options: re-propose (new plan_id) | leave as is
```
