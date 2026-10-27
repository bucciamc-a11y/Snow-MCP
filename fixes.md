# Fixes log

Issues found while building, testing and reviewing the project, and how each was remediated.
Everything below was found by running the tests, ruff, a live loopback smoke test of the real server,
or by code review. Final state: **346 tests pass (183 at first release), `ruff check` clean, live smoke test OK**.

| # | Found by | Issue | Fix |
|---|---|---|---|
| 1 | tests | `search_incidents` failed to register: the page-size parameter used an f-string (`settings.max_page_size`) inside a type annotation, which FastMCP cannot evaluate under `from __future__ import annotations` (`InvalidSignature`) | Static description text; the real cap is still enforced in `build_incident_search(max_limit=...)` |
| 2 | tests | Async pytest fixture opened/closed the MCP session manager's task group in different tasks (`cancel scope in a different task`) | Replaced with an `asynccontextmanager` + `with_stack` decorator so setup and teardown run in the same task |
| 3 | tests | Test banned the substring "request", which false-matched the legitimate tool `get_change_request` | Guard now requires every tool name to start with `search_`/`get_` and bans write verbs (`create/update/delete/approve/script/execute/post/put`) |
| 4 | review/tests | Identifier/text regexes used `$`, which also matches before a trailing newline | Switched to `\Z`. Inputs were already `.strip()`ed so exploitability was nil, but the patterns are now strict; test updated to use an embedded newline |
| 5 | tests | httpx logs full request URLs (including user-supplied search text) at INFO; the audit-log test caught this when logging was not configured as in production | Extracted `quiet_noisy_loggers()` (httpx/httpcore/mcp/uvicorn.access → WARNING), used by `configure_logging` and by the test; verified no search text/secrets in logs |
| 6 | my own edit (bug) | While adding the Docker wildcard-bind option, the new `wildcard_ok` branch skipped the "TLS proxy acknowledged" check, so `0.0.0.0` + flag without `MCP_BEHIND_TLS_PROXY` would have started plain HTTP | Check restructured so a proxy acknowledgement (or native TLS) is **always** required; the wildcard flag only relaxes the address rule, and only for `0.0.0.0`/`::`. Regression tests added |
| 7 | tests | Treated `203.0.113.7` (a documentation range Python classes as private) as public, so a "public address" test passed wrongly | Private-interface test uses `is_private and not is_global and not is_unspecified`; tests use real public IPs (`8.8.8.8`, `1.1.1.1`); `0.0.0.0` and `::` are explicitly refused |
| 8 | self-review | PowerShell `Set-Content -Encoding utf8` wrote a UTF-8 **BOM** into `install.sh` and the systemd unit (a BOM before `#!` breaks the shebang / unit parsing) and some `.py` files | Stripped BOMs from all project text files; subsequent edits write BOM-less UTF-8 |
| 9 | self-review | A PowerShell edit injected a literal `\n` into a comment in `config.env.example` | Replaced with a real newline |
| 10 | self-review | `configure.sh`: `[[ -n $prev_hash ]] && echo …` as the last command in a brace group can trip `set -e` | Rewritten as an `if` statement |
| 11 | ruff | Implicit string concatenation in a dict, combinable `if` branches, `datetime.UTC`, blind `except Exception`, blind `pytest.raises(Exception)`, nested `with`, import order | Fixed each; the one intentional blanket `except` in the tool wrapper is annotated (`noqa: BLE001`) because it must not leak internals |
| 12 | smoke test | Error text read "a incident number" | Grammar fixed ("an incident number …") |
| 13 | requirement change | Debian 13 + Traefik-issued certificate: the container-based Traefik cannot reach `127.0.0.1` on the host, and the original TLS guard only allowed loopback | Plain HTTP is now allowed only on loopback/private interfaces (e.g. Docker bridge gateway) behind a confirmed TLS proxy, still requiring `X-Forwarded-Proto: https`; `configure.sh` detects Docker gateways; Traefik file-provider example added; `apt-get update` added before installing `python3-venv`; unit orders after `docker.service` |
| 14 | requirement change | Wanted a transportable Docker deployment | Added `Dockerfile` (non-root, `python:3.13-slim-trixie`), `docker/docker-compose.yml` (no published ports, read-only, `cap_drop: ALL`, Docker secrets via `*_FILE`, Traefik labels), `scripts/docker-setup.sh`, and a single entry point `setup.sh` |
| 15 | requirement change | Wanted Hermes connection instructions once install completes | Added `scripts/hermes-instructions.sh` (token entry without shell history, `config.yaml` block, skill install, `hermes mcp test`, troubleshooting); both `setup.sh` paths print it at the end and it can be re-run any time |

| 16 | requirement change | Wanted create/edit/update/resolve incidents, change approval, on-call review/update, and triage + assignment to chosen teams (originally out of scope) | Added 11 tools behind **per-feature flags, all OFF by default** (unregistered unless enabled, so `tools/list` is the truth). Every write tool previews first (`confirm=false`) and needs a second call with `confirm=true`. Safeguards: assignment only to an admin-configured team allowlist; incident edits cannot change assignment or close/cancel; approvals only for records pending on the integration user and only for allowed change types (emergency excluded by default); on-call edits limited to roster-member order/dates; no DELETE anywhere; writes never retried (timeouts report "outcome UNKNOWN"); separate write rate limit; audit lines record tool/field names/`executed` but no values. See README "How writes are kept safe" |
| 17 | tests | Moving the lookup helper to a shared `_list` made `{"result": {}}` (empty object) for a by-number lookup raise `bad_response` instead of "not found" | `_list` treats an empty object as an empty list; existing regression test now passes |
| 18 | tests (new) | Example env files had trailing `# comments` on value lines (systemd does not support them, so `X=true # note` would not enable a feature) and my PowerShell append produced CRLF line endings in `config.env.example` | Comments moved to their own lines; all files normalised to LF; new `test_examples.py` fails on trailing comments, BOMs or CRLF in shipped scripts/configs |
| 19 | ruff | Literal bidi-override characters ended up in a test file (flagged PLE2502), implicit string concatenation, unused import/variable | Replaced with `\u202e` escapes and tidied; ruff clean |
| 20 | self-review | Hermes skill and instructions still described a read-only server | Replaced `servicenow-readonly` skill with `hermes-skill/servicenow` (preview/confirm rules, prompt-injection rule, triage and approval workflows); `hermes-instructions.sh` now builds the `tools.include:` list from the features you enabled and reminds you to keep Hermes' own approval prompts on for writes |

## Verified

* `pytest`: 183 passed (validation/injection attempts, write-tool gating, preview/confirm, guards and allowlists, config + TLS rules, mocked ServiceNow client, bearer auth, throttling, body limits, MCP end-to-end, read-only tool set, log redaction).
* `ruff check src tests`: clean.
* Live smoke test (real `uvicorn` process on loopback): `/healthz` OK; unauthenticated → rejected; missing `X-Forwarded-Proto` → 400; `tools/list` returns exactly the 3 read tools by default and exactly the 14 expected tools with every feature enabled; injection attempt in an identifier rejected; wrong Host header → 421.
* Static review: no write HTTP verbs, no `verify=False`, no redirects followed, no `shell=True`/`eval`/`exec`, no real secrets in any file (only placeholders in `*.example` and test-only fake values).

## Known gaps (not fixed — cannot be verified in this environment)

* The shell scripts, Dockerfile, compose file and systemd unit were **not executed**: the development machine is Windows with no WSL/Docker/systemd. They were reviewed by hand only. Run the first install on the VPS while watching output, and use `check.sh`/the docker check step.
* Traefik details (network name, entrypoint, resolver) are auto-detected best-effort and otherwise prompted; confirm they match your setup.
* The Hermes config/skill format follows the public Hermes docs as of this build; your installed version was not available to inspect. Adjustment steps are in `hermes-skill/`.
* The ServiceNow on-call table/field names (`cmn_rota`, `cmn_rota_roster`, `cmn_rota_member`) and the `whoisoncall` response shape come from community documentation, not official docs - confirm on your instance before enabling on-call writes. Approval behaviour (patching `sysapproval_approver`) and valid `close_code` values are also instance-specific. The setup `check` only probes read access; write permissions are never tested automatically (test with a preview, then one real change on a test record).
* ServiceNow roles/ACLs must be created by you; the exact ACL set can vary by instance (see README) — `check` reports `forbidden` per table if something is missing.


## Update: problem management

| # | Found by | Issue | Fix |
|---|---|---|---|
| 24 | requirement | Wanted problem management (originally out of scope) | Added `search_problems`, `get_problem` behind `MCP_ENABLE_PROBLEM_READ` and `create_problem`, `update_problem`, `resolve_problem`, `link_incident_to_problem` behind `MCP_ENABLE_PROBLEM_WRITES` (implies read). Both flags are **off by default**, so an existing deployment's `tools/list` is unchanged after upgrading. Writes use the same preview (`confirm=false`) / confirm pattern, audit lines and write rate limit as the incident tools |
| 25 | design | Problem state values differ between ServiceNow releases (101-107 model vs older 1-4 numbering) | States are names in the tools and raw values only in config: `MCP_PROBLEM_STATES` (default 101-107; partial overrides allowed; unknown names, non-numeric or duplicate values are rejected at startup). The guards (finished-problem checks) and payloads both use the configured values; setup asks for them when a problem feature is enabled |
| 26 | design | Risk of an agent closing or reopening problems unintentionally | `update_problem` can only set assess / root_cause_analysis / fix_in_progress and refuses resolved/closed problems; `resolve_problem` needs real cause and fix notes and refuses finished problems; no tool closes or deletes a problem. Client allowlist adds only (POST, PATCH) on `problem` and GET on `problem`; DELETE/PUT stay impossible (tested) |
| 27 | design | `link_incident_to_problem` writes to the incident table | It can only set `problem_id`, refuses closed/canceled incidents and resolved/closed problems, and refuses an incident already linked to a *different* problem (re-pointing is left to ServiceNow). Linking to the same problem is a no-op (`already_linked`) |
| 28 | design | Team assignment for problems | Problems reuse the administrator's `MCP_ASSIGNMENT_GROUPS` allowlist (also usable without enabling incident assignment); `team` is optional and rejected when no teams are configured. No free-form assignment_group/assigned_to parameters exist |
| 29 | design | The Linux error text for identifiers named only incidents and changes | `validate_identifier` now has an example per record type (adds `PRB0010001`); `incident.problem_id` was added to incident reads so linked problems are visible |
| 30 | tests | Wiring (flags, setup scripts, compose, Hermes include list, skill) could drift from the code again | New `tests/test_wiring.py` checks every feature flag and tool name appears in the example env files, both setup scripts, the compose file, `hermes-instructions.sh` and `SKILL.md` |

Known gaps for this update: the new setup-script prompts were syntax-checked (`bash -n`) and the state-value regex was exercised, but the scripts were not run end to end. Problem field names and the state model follow standard ServiceNow tables and were not verified against your instance - confirm with a preview, then one change on a test problem.

Verified for this update: 346 passed, 1 skipped in 3.61s; `ruff check src tests` clean; live loopback smoke test: `tools/list` returns 3 tools by default, 5 with `MCP_ENABLE_PROBLEM_READ`, 9 with `MCP_ENABLE_PROBLEM_WRITES`, 18 with every feature enabled; unauthenticated call rejected (401); a query-injection identifier (`PRB1^ORDERBYDESC`) rejected by `get_problem`.

## Update: skills for other clients

| # | Found by | Issue | Fix |
|---|---|---|---|
| 31 | requirement | The skill was Hermes-specific (frontmatter, tool prefix, connection steps) | `hermes-skill/` moved to `skills/hermes/`; added `skills/generic` (any `SKILL.md` client) plus Claude Desktop, OpenClaw, Paperclip and ChatGPT Desktop variants. All are generated from one shared body (`skills/_source/body.md`) by `skills/build.py`, and a test fails if a generated file is stale, misses a tool, or contains a secret-looking value |
| 32 | research | Client facts could not all be confirmed | Claude Desktop header auth is a beta available to some orgs; OpenClaw header syntax and ChatGPT bearer-token support are **not verified** (ChatGPT documents OAuth only, and this server has no OAuth). Each client README says what was and wasn't confirmed |
