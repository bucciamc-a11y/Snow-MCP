# Problem management

Incidents restore service; problems remove causes. This skill detects problem candidates, checks for known errors, and (gated) creates a problem record and links incidents to it. It does not run root-cause analysis, update problem state, or resolve problems.

## When to recommend a problem

Recommend (never assume) when any of these holds:

| Trigger | Check |
|---|---|
| **Recurrence**: 3 or more incidents on the same CI, or same category and similar description, in 30 days | `search_incidents(short_description_contains=<term>, opened_after=<date>)`; confirm by reading the tickets, not just the count |
| **Major/P1 resolved without a cause** | P1 resolved with `no_root_cause` |
| **Workaround reused**: close notes of several tickets say "restarted / cleared cache" | Same workaround in 2+ tickets |
| **QA audit** raised `recurring_pattern` | From the audit |
| Operator asks | Direct request |

## Procedure

### 1. Check for an existing problem
- `search_problems(active=true, cmdb_ci=<sys_id>, limit=25)` and `search_problems(short_description_contains=<term>, active=true)`.
- `search_problems(known_error=true, short_description_contains=<term>)` for known errors.
- Found a match: `get_problem` to read cause, workaround, fix notes and linked incidents. Recommend linking the new incident(s) to it rather than opening another. If it is a known error with a workaround, quote the workaround for the incident.
- Found nothing: continue.

### 2. Draft the problem
Template: `templates/problem-record.md`. Fields (native `create_problem`): `short_description`, `description`, `impact`, `urgency`, `category`, `subcategory`, `cmdb_ci`, `business_service`, `team`.

The description must state: symptom pattern, the evidence (list the INC numbers with dates), services affected, current workaround, known suspects (labelled as hypotheses), and the question to answer ("why does X fail under Y?"). Problem impact/urgency reflect the *recurring* effect, not any single ticket.

Do not state a root cause you have not established. Use "suspected" and label evidence.

### 3. Propose
Two kinds of write, each gated:
1. `create_problem` (preview with `confirm=false`, ledger plan, approval).
2. After the problem exists, `link_incident_to_problem(problem, incident)` for each incident, one plan per link (maximum 10 per proposal). The tool refuses closed or canceled incidents, resolved or closed problems, and incidents already linked to a different problem; report such refusals and let the operator decide.

### 4. Verify
`get_problem` shows the linked incidents; confirm the count and numbers match what was approved.

## Pitfalls

- Opening a problem for a one-off. Require evidence of recurrence or an unexplained P1.
- Duplicating an existing problem. Always search first (including closed ones when the pattern is old: `active=false`).
- Linking an incident that is already tied to another problem. The tool refuses; do not work around it.
- Writing a root cause into the description as fact.
- Using the problem record to hold the incident's resolution. Keep each in its own record.

## Not in scope (v2)

`update_problem` and `resolve_problem` exist on the server but are not bound by this skill. Root-cause analysis, known-error publication and problem closure stay with the problem manager.
