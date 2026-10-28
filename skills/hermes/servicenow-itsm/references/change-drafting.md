# Change request drafting

Goal: a complete, reviewable **draft** a CAB can assess. The skill creates the change in `New` and stops. Assess, Authorize, Schedule, Implement, Review and Close are human steps; `transition_change_request` and `approve_change_request` are forbidden.

## Inputs to collect

Required from the operator or a linked INC/PRB (ask for anything missing; do not invent technical facts):

| Input | Notes |
|---|---|
| What changes, and why | Link INC/PRB numbers |
| CI and business service | Needs a sys_id. See *Resolving the CI* |
| Assignment team | From `list_assignment_teams()` on native |
| Window | Start and end, in the operator's timezone; convert to UTC |
| Implementer(s) and verifier(s) | Names or roles for the plan |
| Rollback capability | Backup, snapshot, previous version, feature flag |
| Type | normal, standard or emergency (see below) |

Mark any inference as `ASSUMPTION:` inside the draft and list assumptions to the operator before proposing.

## Resolving the CI

On native, `search_cis(name_contains=…, ci_class=…)` finds the CI when the server has `MCP_ENABLE_CMDB_READ`; otherwise take the CI sys_id from the linked incident (`get_incident` returns `cmdb_ci`), from a similar change (`get_change_request`), or from the operator. If you cannot obtain it, ask. Never fabricate a sys_id or guess by name. On the Composio profile, query `cmdb_ci` by name; if more than one row matches, list them and ask.

## Change type

| Type | Use when | Notes |
|---|---|---|
| standard | Pre-authorised, low-risk, repeatable, and the operator confirms a matching template exists | Do not infer this yourself |
| normal | Default. Needs assessment and CAB | Lead time usually 2+ days; the validator warns |
| emergency | Restores or prevents a P1/P2 outage, or closes a critical security exposure, right now | Must reference the incident; flagged for ECAB; the validator requires `related_incident` |

The administrator limits which types the connection may create. If creation is refused for a type, report it.

## Conflict check

`search_change_requests(active=true, cmdb_ci=<sys_id>, order_by="start_date", descending=false, limit=50)`. The tool filters on opened date only, not on planned start/end dates, so compare `start_date`/`end_date` yourself and report overlaps or changes within 24 hours either side. Repeat for the business service's other CIs if the operator names them. This is a lookup, not ServiceNow's native conflict detection; say so.

## The five mandatory sections

A draft missing any of them is not proposed. `scripts/validate_change.py` enforces structure; you own the substance.

### 1. Justification
Why now, why this change. Business or technical driver, linked tickets, and the consequence of not making the change. Not "maintenance".

### 2. Implementation plan
Numbered, ordered steps. For each: owner, duration, prerequisite. Include pre-checks (backups, health, approvals, comms, maintenance banner) and the exact window.

### 3. Risk and impact analysis (mandatory)
Contents:
- **Affected services and CIs**, user population, dependencies, and any overlapping changes found.
- **Failure modes**, each with likelihood (Low/Medium/High) and impact (Low/Medium/High), and the reason.
- **Overall risk rating**: High / Moderate / Low, with the reasoning.
- **Mitigations**: testing already done, staged rollout, off-peak window, extra staffing, monitoring.

Rating guide (score each factor 0 or 1, sum; 0-1 Low, 2-3 Moderate, 4+ High):

| Factor | Adds 1 when |
|---|---|
| Environment | Production, or a shared dependency of production |
| Blast radius | More than one service, or more than ~100 users, or customer-facing |
| Reversibility | No tested rollback, or rollback takes longer than the window |
| Novelty | First time this change is done, or by this team, or a major version jump |
| Timing | Peak business hours, month/quarter end, freeze period, or concurrent changes on the CI |
| Complexity | More than 10 steps, multiple teams, or cross-system dependencies |

If the factors and your stated rating disagree, change the rating or explain the gap. An irreversible step forces High.

### 4. Backout (rollback) plan (mandatory)
Contents:
- **Trigger criteria**: objective conditions that start the rollback (for example "smoke check 3 fails" or "5xx rate above 1% for 5 minutes").
- **Decision owner** and the deadline for deciding (the latest point to roll back inside the window).
- **Ordered rollback steps** with owners; written so someone who did not write the plan can run them.
- **Estimated rollback duration** and any **point of no return**.
- **Restore method**: named backup, snapshot, config export or version, and where it lives.
- **Post-rollback verification** and who confirms.

If there is no rollback, say so, rate the risk High, and describe the compensating controls. Do not write a fictional rollback.

### 5. Test (verification) plan
Post-implementation checks with objective pass/fail criteria, who runs them, what is monitored, and for how long.

## Field mapping

| Section | Native `create_change_request` | Composio generic create |
|---|---|---|
| Justification | `justification` | `justification` |
| Implementation | `implementation_plan` | `implementation_plan` |
| **Risk & impact** | **No field exists.** Embed in `description` under a heading `RISK & IMPACT`, after a short summary | `risk_impact_analysis` plus `risk` |
| Backout | `backout_plan` | `backout_plan` |
| Test | `test_plan` | `test_plan` |
| Assignment | `team` | `assignment_group` (sys_id) |
| Dates | `start_date`, `end_date` (UTC) | same |
| Impact | `impact` (1-3) | `impact` |

Limits on native: 4000 characters per text field. If a section is longer, tighten it; do not truncate silently. After creation, ask the operator whether they want the full risk assessment also added as a work note via `update_change_request` (a second gated write).

Footer in `description`: `Drafted via Hermes ServiceNow skill; plan PLN-…; not submitted for approval.`

## Procedure

1. Gather inputs and resolve references.
2. Run the conflict check.
3. Write the draft to `draft.json` (template: `templates/change-request.md`).
4. Run `python3 ${HERMES_SKILL_DIR}/scripts/validate_change.py draft.json --profile native` (or `composio`). Fix every error. Show warnings to the operator.
5. Show the draft in readable form, including the assumptions and any conflicts found.
6. Preview with `create_change_request(..., confirm=false)`; register with the ledger; present the Proposed Change Set. The target state is `New`.
7. On approval, execute, read back with `get_change_request`, and confirm the state is New and each section is populated.
8. Tell the operator the next human step: review, then submit through the normal assessment/CAB route. Offer to draft the CAB summary text; do not submit.

## Editing an existing draft

`update_change_request` can change plans, dates, CI, team and notes of an open change. It cannot change type or phase. Treat each edit as a new gated write. When editing, re-run the validator on the merged result so a half-updated draft never loses a mandatory section.

## Pitfalls

- Backout plan written as "revert changes". It must be executable and timed.
- Risk rating "Low" with a production, customer-facing, untested change.
- Window shorter than the implementation steps plus rollback time. Add up the durations.
- Dates entered in local time but sent as UTC. Convert and show both.
- Emergency type used to skip lead time for a convenience change. Refuse and explain.
