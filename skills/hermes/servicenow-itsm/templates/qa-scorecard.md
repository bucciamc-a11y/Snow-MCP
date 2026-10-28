# QA scorecard (copy and fill)

```
QA SCORECARD
Scope:    <state, window, group, priority>   Sample: <n of N>   Method: <all P1/P2 + seeded random, seed NNN>
Not assessable: <criteria/tickets lacking journal or other evidence>

<table from: qa_score.py scores.json --markdown>

FINDINGS
INC0000000  (P2, Network, score 54, FAIL)  flags: no_root_cause, close_code_mismatch
  - root_cause_identified 1: close notes say "restarted router" [resolved 2026-10-21 14:02]; no cause given
  - close_code_mismatch: code "Solution provided", notes say "user stopped responding"

PATTERNS
<recurrence by CI/category, common gaps, coaching themes; no individual names unless asked>

RECOMMENDED FOLLOW-UPS
<problem records (reference problem-management), KB articles, process changes>

OPTIONAL WRITE-BACK (gated, internal work note, one per open ticket)
QA <date>: score <n> (<PASS|FAIL>). <one-line top finding>. <one-line recommended action>.
```
