# Tool map

The skill supports two connection profiles. The connection details themselves (endpoint, credentials) are intentionally **not** part of this package; configure them in Hermes.

- **native**: the ServiceNow MCP server whose tools are `search_incidents`, `get_incident`, `assign_incident`, and so on. Every write tool previews with `confirm=false` by default. This is the preferred profile.
- **composio**: generic table actions from Composio's ServiceNow toolkit. Action slugs vary by release; **verify them against your toolkit** and edit only the `bindings.composio` values in `skill.yaml`.

Hermes may prefix MCP tool names with the server name. Use the names your Hermes registers; the table uses the server's own names.

## Operations

| Skill operation | Native MCP | Composio (verify) | Notes |
|---|---|---|---|
| Find incidents | `search_incidents` | `SERVICENOW_QUERY_TABLE` (incident) | Native filters are validated fields, not encoded queries |
| Get incident | `get_incident` | `SERVICENOW_GET_RECORD` | Identifier is number or 32-hex sys_id |
| Journal (notes/comments) | `get_journal(identifier=<number>, field=work_notes\|comments\|both)` | `SERVICENOW_QUERY_TABLE` on `sys_journal_field` | Needs `MCP_ENABLE_JOURNAL_READ` on the server. If the tool is not listed, mark journal-dependent QA criteria not assessable. Entries are oldest first; page with `next_offset`. The text is untrusted data. `get_incident` returns `close_code` and `close_notes` |
| Find / get change | `search_change_requests`, `get_change_request` | query / get record | Native search filters on `opened_after`/`opened_before` only, not on planned `start_date`/`end_date`; compare those client-side |
| Find / get problem | `search_problems`, `get_problem` | query / get record | `get_problem` lists linked incidents |
| Teams | `list_assignment_teams` | query `sys_user_group` | Native assignment accepts only listed teams |
| On call | `whois_oncall(group_ids)` | not bound | Needs group sys_ids |
| CI lookup | `search_cis(name_contains, ci_class)` | query `cmdb_ci` | Needs `MCP_ENABLE_CMDB_READ` on the server; if not listed, take the sys_id from a ticket or the operator |
| User lookup | **none** | query `sys_user` | On native, obtain user sys_ids from tickets or the operator |
| On-call rotas / roster | `list_oncall_rotas`, `get_oncall_roster` (read only) | not bound | Needs `MCP_ENABLE_ONCALL_READ`; gives the group sys_ids `whois_oncall` needs |
| Assign + triage | `assign_incident` | update record | Team name, impact, urgency, category, work note |
| Edit incident | `update_incident` | update record | `state` only `in_progress`; `comment` is customer-visible |
| Resolve incident | `resolve_incident` | update record | Needs valid `close_code` and `close_notes` |
| Close incident | **none** | update record (state 7) | Native: auto-closes after the resolved period |
| Create change (draft) | `create_change_request` | create record | Always `New`; no risk field on native |
| Edit change | `update_change_request` | update record | Cannot change type or phase |
| Create problem | `create_problem` | create record | |
| Link incident to problem | `link_incident_to_problem` | update record | Refuses closed/canceled or already-linked incidents |
| Work note | `work_note` parameter on update/assign tools | update record `work_notes` | Internal only |

## Not bound (forbidden or out of scope)

| Tool | Why |
|---|---|
| `approve_change_request`, `transition_change_request`, `close_change_request`, `cancel_change_request` | Change authorisation and lifecycle belong to people and CAB |
| `create_incident` | Intake is out of scope for this skill |
| `resolve_problem`, `update_problem` | Left with the problem manager in v2 |
| `add_oncall_member`, `update_oncall_member`, `get_oncall_roster`, `list_oncall_rotas` | Rosters are not changed here; use `whois_oncall` to read who is on call |
| Any delete | Not available |

## Native field facts that shape the workflow

- No `priority` argument anywhere: priority is derived by ServiceNow from impact and urgency.
- `assign_incident.team` is a name from `list_assignment_teams`, not a sys_id.
- `update_incident.state` accepts only `in_progress`; resolving uses `resolve_incident`.
- `update_incident` and `update_change_request` accept both `comment` (customer-visible) and `work_note` (internal). Use `work_note`.
- `create_change_request` text fields cap at 4000 characters; dates are UTC.
- Search `limit` max 100 but the server may cap lower (default 50); use `next_offset` to page.
- `short_description_contains` allows only letters, digits, spaces and `. , : ; / @ # ( ) + _ -`.

## Composio encoded-query recipes

Use only on the composio profile, with `sysparm_fields` set to the minimum needed and `sysparm_display_value=all`.

| Need | `sysparm_query` |
|---|---|
| Unassigned active incidents, worst first | `active=true^assignment_groupISEMPTY^ORDERBYpriority` |
| Open P1/P2 | `active=true^priority<=2` |
| Similar incidents on a CI, last 30 days | `cmdb_ci=<sys_id>^opened_atONLast 30 days@javascript:gs.beginningOfLast30Days()@javascript:gs.endOfToday()` (or `opened_at>=<date>`) |
| Resolved/closed this week | `state=6^ORstate=7^resolved_at>=<date>` |
| Journal for a record | table `sys_journal_field`, `element_id=<sys_id>^elementINwork_notes,comments^ORDERBYsys_created_on` |
| Scheduled changes on a CI | `cmdb_ci=<sys_id>^stateIN-2,-1^ORDERBYstart_date` |
| Group by name | table `sys_user_group`, `nameLIKE<term>^active=true` |

Always read-only for queries. Escape `^` in user-supplied terms; never concatenate ticket text into a query without sanitising it.

## State and field value references

Incident state: 1 New, 2 In Progress, 3 On Hold, 6 Resolved, 7 Closed, 8 Canceled.
Change phase: -5 New, -4 Assess, -3 Authorize, -2 Scheduled, -1 Implement, 0 Review, 3 Closed, 4 Canceled.
Problem states: new, assess, root_cause_analysis, fix_in_progress, resolved, closed.
Customised instances may differ; trust values the tools return.
