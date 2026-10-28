# Tool reference

Which tools exist and which flag enables each is in [README.md](../README.md). Permissions the integration account needs are in [INSTALL.md](../INSTALL.md).

## Fields returned

Incident: `sys_id, number, short_description, description, state, priority, urgency, impact,
category, subcategory, caller_id, assigned_to, assignment_group, business_service, cmdb_ci, active, opened_at,
opened_by, updated_at, resolved_at, closed_at, close_code, close_notes`. Change request: `sys_id, number, short_description,
description, state, type, priority, risk, impact, category, requested_by, assigned_to, assignment_group,
business_service, cmdb_ci, approval, justification, implementation_plan, backout_plan, test_plan, start_date,
end_date, opened_at, updated_at, closed_at, close_code, close_notes, active`. Problem: `sys_id, number, short_description, description, state, priority,
urgency, impact, category, subcategory, assignment_group, assigned_to, business_service, cmdb_ci, known_error, active,
cause_notes, workaround, fix_notes, resolution_code, related_incidents, first_reported_by_task, opened_at, opened_by,
updated_at, resolved_at, closed_at` (incidents additionally show `problem_id`). Work notes and comments are not part of these records; read them with `get_journal`
(`MCP_ENABLE_JOURNAL_READ`). **Excluded on purpose:** attachments, contact details and any other field. Long text fields are truncated (default 4000 chars). Reference and
choice fields are returned as display values. Records the account cannot read behave as "not found".

## Read tool inputs

| Tool | Inputs and validation | Needs |
|---|---|---|
| `search_incidents` | `state` (new/in_progress/on_hold/resolved/closed/canceled), `active`, `priority` 1-5, `urgency` 1-3, `impact` 1-3, `category` (`[A-Za-z0-9 _-]` ≤40), `assignment_group`/`caller_id` (32-hex sys_id), `short_description_contains` (≤100 chars, letters/digits/space/`. , : ; / @ # ( ) + _ -`; no `^`), `opened_after`/`opened_before` (UTC `YYYY-MM-DD[ HH:MM:SS]`), `order_by` (opened_at/updated_at/priority/number), `descending`, `limit` 1-50 (config cap), `offset` 0-10000 | read `incident` |
| `get_incident` | `identifier`: `INC` + 7-10 digits, or 32-hex sys_id | read `incident` |
| `get_change_request` | `identifier`: `CHG` + 7-10 digits, or 32-hex sys_id | read `change_request` |
| `get_journal` | `identifier`: `INC`/`PRB`/`CHG` + 7-10 digits (a number, not a sys_id), `field` (work_notes/comments/both), `limit` 1-50 (config cap), `offset` 0-10000. Returns `sys_created_on`, `sys_created_by`, `element`, `value` oldest first | read `sys_journal_field` (+ the record's table) |
| `search_cis` | `name_contains` (same character rules as incident text filters), `ci_class` (table name such as `cmdb_ci_server`); at least one is required; `limit`, `offset`. Returns `sys_id, name, sys_class_name, operational_status, install_status, support_group` | read `cmdb_ci` |
| `search_problems` | `state` (new/assess/root_cause_analysis/fix_in_progress/resolved/closed), `active`, `known_error`, `unassigned`, `priority` 1-5, `urgency` 1-3, `impact` 1-3, `category`, `assignment_group`/`cmdb_ci` (32-hex sys_id), `short_description_contains` (same rules as incidents), `opened_after`/`opened_before`, `order_by`, `descending`, `limit`, `offset` | read `problem` |
| `search_change_requests` | `state` (new/assess/authorize/scheduled/implement/review/closed/canceled), `active`, `type` (normal/standard/emergency), `priority` 1-5, `category`, `assignment_group`/`cmdb_ci` (32-hex sys_id), `short_description_contains` (same rules as incidents), `opened_after`/`opened_before`, `order_by` (also `start_date`), `descending`, `limit`, `offset` | read `change_request` |
| `get_problem` | `identifier`: `PRB` + 7-10 digits, or 32-hex sys_id | read `problem`, `incident` |

Write tools take the same strictly validated inputs plus `confirm` (default false = preview only); see the
tool descriptions the server publishes, and [README.md](../README.md) for the safety model.

Queries are built only from closed vocabularies/strict patterns and sent as properly URL-encoded
parameters; callers can never supply a path, table, field list or raw encoded query.
