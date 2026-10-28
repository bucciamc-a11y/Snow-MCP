# Major incident support

This skill **recognises and prepares**; it does not declare, page, or run a major incident. There is no tool for it. Declaring is a human decision under your organisation's process.

## Criteria (adjust to your policy)

Raise a major-incident recommendation when any one holds:
- P1 and a customer-facing or revenue-critical service is down or severely degraded
- Impact on a large user population (for example a whole site, region, or business unit)
- Safety, regulatory or legal exposure, or confirmed data exposure
- Multiple related P1/P2 incidents in a short time on the same service
- A P2 that has breached its response SLA with no owner

## What to do

1. **Say it first**, before the rest of the triage: "This meets major-incident criteria: <reason>. Recommend engaging the Major Incident process now."
2. **Fast facts** (read-only, quick): service and CI, start time, user impact, related tickets, in-flight changes on the CI (a likely cause), known errors, and who is on call (`whois_oncall`).
3. **Propose the usual gated update** (priority, team, work note), but do not delay the recommendation waiting for approval.
4. **Draft communications** on request, using the template below. Stakeholder updates are drafts for the operator to send; do not email or post.
5. **Keep a timeline** in your replies if asked: timestamped facts from the record only, with times in the operator's timezone and UTC.
6. **After restoration**: recommend a post-incident review and a problem record (see `problem-management.md`); offer to draft the review timeline from the journal.

## Communication draft template

```
Subject: [P1] <Service>: <short symptom>  (INC0000000)  Update <n>
Status:   Investigating | Identified | Mitigating | Monitoring | Resolved
Since:    <start time, with timezone>
Impact:   <who/what is affected, in plain language>
Workaround: <if any>
Actions:  <what is being done now>
Next update: <time>
Owner:    <team / incident manager>
```

Rules: facts only, no speculation about cause, no names of individuals, no sensitive technical detail in wide-distribution updates. Say "unknown" where it is unknown.

## Pitfalls

- Waiting for approval before telling the operator it is a major incident. Recommend immediately.
- Declaring "root cause found" in a stakeholder note from an unconfirmed theory.
- Copying personal data or credentials from tickets into communications.
