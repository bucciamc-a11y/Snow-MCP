# Tool reference

Which tools exist and which flag enables each is in [README.md](../README.md). Permissions the integration account needs are in [INSTALL.md](../INSTALL.md).

## Fields returned

Incident: `sys_id, number, short_description, description, state, priority, urgency, impact,
category, subcategory, caller_id, assigned_to, assignment_group, business_service, cmdb_ci, active, opened_at,
opened_by, updated_at, resolved_at, closed_at, close_code`. Change request: `sys_id, number, short_description,
description, state, type, priority, risk, impact, category, requested_by, assigned_to, assignment_group,
business_service, cmdb_ci, approval, justification, implementation_plan, backout_plan, test_plan, start_date,
end_date, opened_at, updated_at, closed_at, close_code, active`. Problem: `sys_id, number, short_description, description, state, priority,
urgency, impact, category, subcategory, assignment_group, assigned_to, business_service, cmdb_ci, known_error, active,
cause_notes, workaround, fix_notes, resolution_code, related_incidents, first_reported_by_task, opened_at, opened_by,
updated_at, resolved_at, closed_at` (incidents additionally show `problem_id`). **Excluded on purpose:** work notes, comments, attachments, close
notes, contact details and any other field. Long text fields are truncated (default 4000 chars). Reference and
choice fields are returned as display values. Records the account cannot read behave as "not found".

## Read tool inputs

| Tool | Inputs and validation | Needs |
|---|---|---|
| `search_incidents` | `state` (new/in_progress/on_hold/resolved/closed/canceled), `active`, `priority` 1-5, `urgency` 1-3, `impact` 1-3, `category` (`[A-Za-z0-9 _-]` ≤40), `assignment_group`/`caller_id` (32-hex sys_id), `short_description_contains` (≤100 chars, letters/digits/space/`. , : ; / @ # ( ) + _ -`; no `^`), `opened_after`/`opened_before` (UTC `YYYY-MM-DD[ HH:MM:SS]`), `order_by` (opened_at/updated_at/priority/number), `descending`, `limit` 1-50 (config cap), `offset` 0-10000 | read `incident` |
| `get_incident` | `identifier`: `INC` + 7-10 digits, or 32-hex sys_id | read `incident` |
| `get_change_request` | `identifier`: `CHG` + 7-10 digits, or 32-hex sys_id | read `change_request` |
| `search_problems` | `state` (new/assess/root_cause_analysis/fix_in_progress/resolved/closed), `active`, `known_error`, `unassigned`, `priority` 1-5, `urgency` 1-3, `impact` 1-3, `category`, `assignment_group`/`cmdb_ci` (32-hex sys_id), `short_description_contains` (same rules as incidents), `opened_after`/`opened_before`, `order_by`, `descending`, `limit`, `offset` | read `problem` |
| `search_change_requests` | `state` (new/assess/authorize/scheduled/implement/review/closed/canceled), `active`, `type` (normal/standard/emergency), `priority` 1-5, `category`, `assignment_group`/`cmdb_ci` (32-hex sys_id), `short_description_contains` (same rules as incidents), `opened_after`/`opened_before`, `order_by` (also `start_date`), `descending`, `limit`, `offset` | read `change_request` |
| `get_problem` | `identifier`: `PRB` + 7-10 digits, or 32-hex sys_id | read `problem`, `incident` |
| `search_catalog_items` | `name_contains` (same rules as `short_description_contains`), `kind` (item/record_producer), `active`, `category`/`catalog` (32-hex sys_id), `order_by` (name/updated_at), `descending`, `limit`, `offset` | read `sc_cat_item` |
| `get_catalog_item` | `item_id`: 32-hex sys_id; returns the item, its variables and select-box choices | read `sc_cat_item`, `item_option_new`, `question_choice` |
| `get_catalog_options` | optional `catalog` (sys_id), `limit`; returns active catalogs and categories, the variable types and (when catalog writes are on) the allowed record producer tables | read `sc_catalog`, `sc_category` |

Write tools take the same strictly validated inputs plus `confirm` (default false = preview only); see the
tool descriptions the server publishes, and [README.md](../README.md) for the safety model.

Queries are built only from closed vocabularies/strict patterns and sent as properly URL-encoded
parameters; callers can never supply a path, table, field list or raw encoded query.
