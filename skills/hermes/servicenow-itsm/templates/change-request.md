# Change request draft (fill all sections; no placeholders)

Save the filled values as `draft.json` and run `scripts/validate_change.py`. Keys:
`short_description, description, justification, implementation_plan, backout_plan, test_plan,
type, team (native) | assignment_group (composio), cmdb_ci, start_date, end_date (UTC ISO 8601),
related_incident (required for emergency), risk_impact_analysis + risk (composio only)`.

## short_description (10-160 chars)
<verb + object + target, e.g. "Replace expiring TLS certificate on lb-prod-02">

## description (native: summary, then the RISK & IMPACT block)
<2-4 lines: what changes and the outcome>

RISK & IMPACT
Affected services/CIs: <services, CIs, user population, dependencies, overlapping changes>
Failure modes:
  - <mode>: likelihood <Low|Medium|High>, impact <Low|Medium|High>, because <reason>
Overall risk rating: <High|Moderate|Low>. Reason: <why, using the factor list in references/change-drafting.md>
Mitigations: <testing done, staging, window, staffing, monitoring>

Drafted via Hermes ServiceNow skill; plan <PLN-...>; not submitted for approval.

## justification
<Driver, linked INC/PRB, consequence of not changing.>

## implementation_plan
1. <step> (owner: <name/role>, <n> min; prerequisite: <...>)
2. ...
Window: <start>-<end> <tz>. Pre-checks: <backup, health, comms>.

## backout_plan
Rollback trigger criteria: <objective conditions>. Decision owner: <who>, decide by <time>.
1. <rollback step> (owner, duration)
2. ...
3. Verify <post-rollback check and who confirms>.
Estimated rollback time: <n> minutes. Point of no return: <step or "none">. Restore source: <backup/snapshot/version and location>.

## test_plan
Checks (pass/fail): <check -> expected result>. Run by <who>. Monitor <metrics> for <duration>. Fail means: <invoke rollback>.
