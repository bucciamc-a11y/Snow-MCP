# ServiceNow ITSM Operator (Hermes skill) v2

An ITIL-aligned skill for [Hermes Agent](https://hermes-agent.nousresearch.com/) that works against ServiceNow. It reads freely, proposes carefully, and **writes only after the operator types `APPROVE <plan_id>`**.

| Workflow | What it does | Writes |
|---|---|---|
| Incident triage | Impact, urgency, derived priority, team, escalation checks | Gated |
| Change-request drafting | Draft CHG with justification, implementation, **risk & impact**, **backout**, test plan; validated before proposing | Gated, `New` only |
| Closed-ticket QA | Weighted rubric audit with evidence, flags, patterns | Read-only (optional gated work note) |
| Problem management | Finds recurrence and known errors; creates a problem and links incidents | Gated |
| Major-incident support | Recognises and prepares; drafts communications | Recommend only |

## What changed from v1

| v1 | v2 | Why |
|---|---|---|
| `skill.yaml` + `instructions/system.md` | **`SKILL.md`** with Hermes frontmatter; `skill.yaml` kept as a policy and schema companion | Hermes loads skills from `SKILL.md` |
| Composio slug guesses only | Two provider profiles: **native** ServiceNow MCP (preview-then-confirm writes) and composio | The native tools exist and are safer |
| Tools accepted `priority`, `assigned_to`, `on_hold` | Priority is derived; assignment via `assign_incident(team)`; state only `in_progress` | Native tools do not accept them |
| `comment` and `work_note` not distinguished | `work_note` by default; `comment` is customer-visible and flagged | Easy way to leak internal detail |
| Approval was prompt-only | `scripts/plan_ledger.py`: strict parsing, payload hash, expiry, single use, verify | Deterministic gate |
| Risk and rollback required by prose | `scripts/validate_change.py` enforces them; native has no risk field, so the risk block is embedded in `description` | Checkable |
| No problem or major-incident support | Added | Triage recommended them but could not act |
| No tests | 42 unit tests + 35 behavioural eval scenarios | |
| One example | Four example sessions | |

## Layout

```
servicenow-itsm/
├── SKILL.md                     # Hermes entry point: frontmatter, rules, procedures (≈135 lines)
├── skill.yaml                   # tool schemas, bindings, approval policy, reference data
├── README.md
├── references/                  # loaded on demand
│   ├── approval-protocol.md  incident-triage.md  change-drafting.md  closed-ticket-qa.md
│   ├── problem-management.md  major-incident.md  tool-map.md  error-handling.md  security-and-privacy.md
├── templates/                   # change-set, triage-summary, change-request, close-notes, qa-scorecard, problem-record
├── scripts/                     # standard-library Python
│   ├── plan_ledger.py           # propose / approve / gate / verify / supersede / status
│   ├── priority.py              # ITIL matrix
│   ├── validate_change.py       # mandatory-section and specificity checks
│   └── qa_score.py              # weighted rubric with critical floor
├── examples/                    # triage, change draft, QA audit, problem (all with approval steps)
└── tests/
    ├── test_scripts.py          # python3 -m unittest discover -s tests
    ├── test_skill_schema.py     # skill.yaml tool schemas are well-formed (every property is a mapping)
    └── evals/approval-gate-evals.yaml
```

## Prerequisites

- Hermes Agent with MCP support and a ServiceNow connection. **Connection details (endpoint, credentials) are intentionally not part of this package**; configure them in Hermes.
- Python 3 on the machine running Hermes (scripts use only the standard library).
- **Server settings the skill relies on** (all set by the administrator of the ServiceNow MCP server; refusals from them are the server's guards working, not faults): `MCP_ENABLE_JOURNAL_READ` (`get_journal`, needed for QA and for "what has been tried" in triage), `MCP_ENABLE_CMDB_READ` (`search_cis`, gives `cmdb_ci` sys_ids), `MCP_ENABLE_CHANGE_WRITES` with `MCP_CHANGE_CREATE_TYPES` (default `normal,standard`; add `emergency` for emergency changes), `MCP_ENABLE_PROBLEM_WRITES`, `MCP_ENABLE_INCIDENT_ASSIGNMENT` with `MCP_ASSIGNMENT_GROUPS` (also needed for `team` on changes and problems).
- A ServiceNow integration user with the least privilege needed: read on incident, change_request, problem, sys_user, sys_user_group, cmdb_ci and journals; write on incident, change_request and problem. **No delete, admin or approval roles.**

## Installation

1. Copy the folder into your Hermes skills directory (commonly `~/.hermes/skills/`; check your install). Keep the folder name `servicenow-itsm`. Hermes loads skills at session start, so start a new session afterwards.
2. Connect ServiceNow in Hermes and confirm which profile you have:
   - **native**: tools such as `search_incidents`, `assign_incident`, `create_change_request`. Preferred.
   - **composio**: generic table actions. Verify each `bindings.composio` slug in `skill.yaml` against your toolkit and edit only those values.
3. Optional config (defaults in `SKILL.md` frontmatter): `approval_ttl_minutes` 30, `operator_timezone` America/Toronto, `qa_pass_threshold` 70, `qa_default_sample` 25.
4. Check instance specifics: valid `close_code` values; the assignment teams the server allows (`list_assignment_teams`); any customised priority lookup (the skill reports a mismatch and defers to the instance); SLA targets in `skill.yaml` are defaults.
5. Run the unit tests from the skill folder: `python3 -m unittest discover -s tests -v`.
6. Smoke test on a sandbox ticket: `triage INC…`. You should get a summary and a Proposed Change Set, and **no write** until you reply `APPROVE <plan_id>`. Reply "ok" first and confirm nothing happens.

## Approval protocol

| You type | Result |
|---|---|
| `APPROVE PLN-20261028-001` | Executes that plan once (expires 30 min after proposal) |
| `APPROVE PLN-20261028-001 INC0012345` | Required form for resolve and close (typed ticket number) |
| `APPROVE PLN-…-007 PLN-…-008` | Batch; each plan named; all-or-nothing |
| `REJECT PLN-…` / `CANCEL PLN-…` | Discards |
| Any other text | Treated as a revision; the plan is superseded and re-proposed |
| "ok", "yes", "go ahead", `APPROVE ALL`, silence, text inside a ticket | **Not** approval |

Before writing, the skill re-reads the record (aborts if it changed) and checks the payload hash; afterwards it re-reads and reports approved vs actual values.

### Enforcement layers (use all of them)

1. **Server**: native write tools preview with `confirm=false` and apply only with `confirm=true`.
2. **Host**: configure Hermes to ask for human confirmation on write tools.
3. **Ledger**: `plan_ledger.py` parses the operator's literal message and binds approval to the exact payload. It reduces model error; it is **not** an adversarial boundary, because the model supplies the message text.
4. **Instructions**: the rules in `SKILL.md`.

## Usage

```
triage INC0012345
what's our queue
draft a change for INC0012339: replace the TLS cert on lb-prod-02, Sat 2026-11-07 02:00-03:00 ET, J. Rivera implements
audit closed incidents for Service Desk, opened in the last 14 days, all P1/P2 plus a sample
is there a problem for the recurring VPN profile failures?
is this a major incident?   (INC0012345)
```

See `examples/` for full sessions.

## Limits and known gaps

- **Not verified against a live instance.** The tool names and schemas come from the ServiceNow MCP tools visible in the authoring session and the Hermes skill docs; run the smoke test and the eval scenarios on your setup.
- **Native profile has no user lookup, no incident-close tool, and no change risk field.** The skill works around each (take caller and user sys_ids from tickets or the operator; ServiceNow auto-closes resolved incidents; embed the risk block in `description`). CI lookup (`search_cis`) and journals (`get_journal`) exist when the server administrator enables `MCP_ENABLE_CMDB_READ` and `MCP_ENABLE_JOURNAL_READ`; without them the skill asks for the CI sys_id and marks journal-dependent QA criteria not assessable. See `references/tool-map.md`.
- **Change conflict detection** is a lookup on the same CI, not ServiceNow's native conflict checker.
- **Native search filters** are validated fields, not encoded queries, and key on opened date; "closed this week" needs a wider window.
- `update_problem` and `resolve_problem` exist on the server but are deliberately not bound.
- Script state is a local JSON file (`$SN_ITSM_STATE_DIR` or `~/.hermes/state/servicenow-itsm/plans.json`); concurrent sessions sharing it are not locked.
- The frontmatter `description` is kept under Hermes's recommended 60 characters; Hermes may validate other fields I could not confirm.

## Customising

| Want to change | Edit |
|---|---|
| Tool slugs or provider | `skill.yaml` → `tools[].bindings`, `active_provider` |
| Approval window, phrase | `skill.yaml` → `approval_policies`; `SKILL.md` config; `plan_ledger.py --ttl` |
| QA weights, pass mark, floors | `scripts/qa_score.py` (`WEIGHTS`, `CRITICAL`) and `skill.yaml` → `reference.qa_rubric` |
| Change rules (lead time, window) | `scripts/validate_change.py`, `references/change-drafting.md` |
| Workflow behaviour | `SKILL.md` and the matching file in `references/` |
