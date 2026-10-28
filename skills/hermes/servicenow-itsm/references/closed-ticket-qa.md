# Closed-ticket QA audit

Goal: judge whether resolved/closed incidents were genuinely resolved, with a documented cause. Read-only by default; writing findings back is an optional, gated work note.

## 1. Scope and sampling

Ask for or default to: state resolved or closed, closed in the last 7 days, all priorities, up to 25 tickets.

- Query: `search_incidents(state="resolved"|"closed", active=false, opened_after=…, assignment_group=…, priority=…, limit=50)`. Page with `next_offset`. The native filter keys on `opened_at`, not resolution date; say so and widen the window when the operator wants "closed this week".
- If more tickets match than the sample size, sample deliberately: all P1/P2, then random from P3/P4 (use a seeded shuffle so the sample is reproducible; state the seed). Report `n of N` and the sampling method.
- Explicit ticket lists are audited in full (maximum 25 per run).

## 2. Evidence gathering (per ticket)

`get_incident` for fields, `close_code` and `close_notes`. Journal (work notes and comments): `get_journal(identifier, field="both")` on native; the Composio profile can query `sys_journal_field`. If the journal tool is not available (server flag off), mark criteria that depend on it `not assessable` (score `null`). Do not infer silence as absence.

Also gather where relevant: reopen count, reassignment count, SLA outcome, linked problem/change, similar incidents (for recurrence).

## 3. Rubric (0 to 4 per criterion)

Scores: 0 missing, 1 poor, 2 partial, 3 good, 4 exemplary. Weighted to 100; pass at 70; the critical floor and P1/P2 rule below also apply.

| Criterion (weight) | 4 | 3 | 2 | 1 | 0 |
|---|---|---|---|---|---|
| **classification_accuracy (10)** | Category, subcategory, CI, service all match the actual fault | Minor mismatch in one field | Category right, CI missing or wrong | Category wrong but ticket still routable | Unclassified or misleading |
| **priority_justified (10)** | Impact/urgency evidenced in the record; matches matrix; adjusted when facts changed | Reasonable, little evidence | Defensible but unexplained | Clearly off by 2 or more levels | Never set or contradicts the facts |
| **assignment_correct (10)** | Right group first time, or one justified hop | One unnecessary hop | Two hops or late reassignment | Three or more hops, or bounced | Stuck with the wrong group at close |
| **root_cause_identified (25)** | Cause stated with evidence; prevention or follow-up noted; problem/change linked where warranted | Cause stated, thin evidence | Probable cause only, flagged as such | Symptom restated as cause ("service was down") | None; "restarted", "fixed", "resolved by user" |
| **resolution_quality (25)** | Specific, reproducible fix; verified with user or monitoring; permanent vs workaround stated; follow-ups raised | Specific fix, verification mentioned | Fix described, no verification | Vague fix; workaround presented as fix | No fix described; closed on timeout |
| **close_notes_clarity (10)** | A new engineer could repeat it; `close_code` consistent | Clear, minor gaps | Understandable, brief | Cryptic or copy-paste | Empty or placeholder |
| **user_comms_and_sla (10)** | Timely updates, user confirmation, SLA met or breach explained | Updates present, SLA met | Sparse updates | Long silence, SLA breached unexplained | No contact; closed without user knowledge |

Evidence rule: any score below 3 must cite evidence: a short quoted snippet, the field value, or a journal timestamp. Quotes are redacted of credentials and personal data.

## 4. Flags

| Flag | Raise when |
|---|---|
| `no_root_cause` | No cause stated beyond the symptom |
| `close_code_mismatch` | `close_code` contradicts the notes (for example "Solution provided" but notes say the user gave up) |
| `reopened_count_gt_0` | Ticket was reopened at least once |
| `sla_breached` | Response or resolve SLA breached |
| `resolved_without_verification` | No user or monitoring confirmation |
| `recurring_pattern` | Same CI or category appears 3+ times in 30 days (search to confirm); recommend a problem |

## 5. Scoring

Write the per-ticket scores to `scores.json` and run:

```
python3 ${HERMES_SKILL_DIR}/scripts/qa_score.py scores.json --markdown
```

It applies the weights, renormalises when criteria are `not assessable`, and returns `INSUFFICIENT_EVIDENCE` when under 60% of the weight could be assessed. Beyond the weighted total, a ticket **fails** if `root_cause_identified` or `resolution_quality` is 1 or lower, or if it is P1/P2 with `no_root_cause`. Use the script's output; do not recompute by hand.

## 6. Bias controls

- Score the record, not the engineer. Do not name individuals in pattern summaries unless the operator asks.
- Do not penalise missing detail for trivial P4 tickets (for example password resets) as harshly on root cause: score `root_cause_identified` against what a reasonable team would document for that class, and say so.
- Same standard across teams. If you audit several teams, use identical sample rules.
- Audit what is there. A system-generated close (auto-close after the resolved period) is not an engineer's failure; note it.

## 7. Report

Format in `templates/qa-scorecard.md`: the table from `qa_score.py`, per-ticket findings with evidence, patterns (recurrence, systemic gaps, coaching themes), recommended follow-ups (problem records, KB articles, process fixes), method (scope, sample, seed, what was not assessable).

## 8. Optional write-back

Only if asked. One `update_incident(work_note=…)` per ticket, via the full approval protocol, using the short form in the template. A ticket that is already Closed cannot be updated by the native `update_incident`; say so and give the finding in the report only. Never reopen tickets or change state in this workflow: recommend and let the operator decide.

## Pitfalls

- Auditing only the tickets that look bad. Use the sampling rule.
- Confusing "resolved quickly" with "resolved well".
- Treating the absence of a journal feed as an engineer's omission.
- Quoting ticket text that holds personal data into the report. Redact.
