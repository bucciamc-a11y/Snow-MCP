# Problem record draft (copy and fill)

```
short_description: <pattern, e.g. "Intermittent 503s on payments-portal behind lb-prod">
category / subcategory: <...>
cmdb_ci: <sys_id>      business_service: <sys_id or omit>
impact: <1-3>  urgency: <1-3>     (reflect the recurring effect, not one ticket)
team: <from list_assignment_teams>

description:
  Symptom pattern: <...>
  Evidence: INC0000001 (2026-10-02), INC0000007 (2026-10-11), INC0000012 (2026-10-26)
  Services affected / user impact: <...>
  Current workaround: <...>
  Suspected causes (HYPOTHESES, not established): <...>
  Question to answer: <why does X happen under Y?>
  Related changes / known errors checked: <CHG..., PRB... or none>
  Drafted via Hermes ServiceNow skill; plan <PLN-...>.
```

Then, as separate gated plans: `link_incident_to_problem` for each evidence incident.
