# Code review fix log

Review date: 2026-10-26. Scope: the whole repository (`src/`, `tests/`, `scripts/`, `deploy/`, `docker/`, `config/`,
`skills/`, docs). Baseline before changes: 632 tests passing, `ruff` clean on its default rules.

Method: ran the test suite, `ruff` (default and a stricter rule set), `mypy`, `shellcheck` and `bash -n`; read every
module in `src/servicenow_mcp`; read all deployment scripts, the systemd unit, Dockerfile and compose file; and
cross-checked environment variables, tool names and documentation against the code. Fixes 1-4 each have a regression
test in `tests/test_review_fixes.py` that was confirmed to fail before the fix and pass after it. Fixes 5-10 are
shell-quoting, style, typing and documentation corrections with no behaviour change worth a test.

## Fixed

| # | Severity | Where | Problem | Fix |
|---|---|---|---|---|
| 1 | High | `src/servicenow_mcp/client.py` (`_send_once`) | A connection reset or broken pipe during a write (`httpx.ReadError`, `WriteError`, `CloseError`) was reported as `network` / "could not communicate with ServiceNow", and the audit log recorded `executed=false`. The request may already have been applied, so an agent could retry and create a duplicate incident, problem or change. This contradicted the documented guarantee that a failed write reports "outcome UNKNOWN". Reads also were not retried on these errors. | Catch `httpx.NetworkError` (the parent of `ConnectError`, `ReadError`, `WriteError`, `CloseError`) alongside timeouts. Writes now report `unknown_outcome` and are never retried; reads are retried. The certificate-error check on `ConnectError` is unchanged. |
| 2 | Medium | `src/servicenow_mcp/validation.py` (`validate_text`) | Only `\r\n` was normalised, so a lone `\r` slipped past the "short_description must be a single line" check in incident, problem and change builders. | Normalise every line ending (`\r\n` and `\r`) to `\n` before validating. |
| 3 | Medium | `docker/docker-compose.yml` | `MCP_CHANGE_CREATE_TYPES: ${MCP_CHANGE_CREATE_TYPES:-}` hands the container an empty string when the variable is absent from `docker/.env` (for example an `.env` written before change tools existed, or edited by hand). With `MCP_ENABLE_CHANGE_WRITES=true` the server then refuses to start with a `ConfigError`. The sibling variable `MCP_APPROVAL_ALLOWED_TYPES` already had a non-empty default. | Default is now `normal,standard`. The config loader is intentionally left strict: an empty list is rejected on purpose (see "Reviewed, not changed"). |
| 4 | Medium | `scripts/configure.sh` | Native-TLS mode set the listen port to 443, but the systemd unit drops all capabilities (`CapabilityBoundingSet=` empty), so the unprivileged service cannot bind a port below 1024 and fails at start-up. | Prompt for a port (default 8443), reject anything below 1024, and print the resulting Hermes URL including the port. |
| 5 | Low | `scripts/install.sh` | The "Run as root" message used nested double quotes, so the quotes around `su -` were silently dropped. | Use single quotes around the message. |
| 6 | Low | `setup.sh` | With `set -o pipefail`, `systemctl status \| head -n 8` can return 141 (SIGPIPE) and abort the script before the Hermes connection instructions are printed. | Added `\|\| true` to the status display. |
| 7 | Low | `scripts/configure.sh`, `scripts/docker-setup.sh` | `tr 'A-Z' 'a-z'` is locale dependent (shellcheck SC2018/SC2019). | Use `tr '[:upper:]' '[:lower:]'`. |
| 8 | Low | `src/servicenow_mcp/logging_setup.py` | `entry` was inferred as `dict[str, str]` although `exc_type` can be `None` and extra fields can be any type (mypy error). | Annotated as `dict[str, Any]`. |
| 9 | Low | `src/servicenow_mcp/validation.py`, `src/servicenow_mcp/config.py` | Loop and local variables reused one name for different types (`val` for ints then strings; `key` for a secret name then the TLS key path), which produced 10 mypy errors and invited mistakes. | Renamed to distinct, descriptive names. No behaviour change. |
| 10 | Low | `README.md` | Stated a fixed test count (346) that had drifted far from reality (643). | Removed the number so it cannot go stale. |

## Reviewed, not changed

* **Blank `MCP_CHANGE_CREATE_TYPES` / `MCP_BIND_HOST` stay rejected by the config loader.** I first made the loader fall
  back to defaults for blank values, then reverted it: two existing tests deliberately require rejection. An empty
  `MCP_BIND_HOST` is treated by uvicorn as "all interfaces", and an empty type list may mean "allow none", so
  silently defaulting would fail open. The deployment file was the actual defect (fix 3).
* **Query-injection defences** (`validation.py`): `^`, quotes and control characters cannot reach `sysparm_query`; all
  identifiers use anchored `\Z` patterns. No issue found.
* **Authentication** (`auth.py`): constant-work digest comparison, per-client throttling, body-size limits and
  `/healthz` ordering are correct. No issue found.
* **Change tools reuse `resolve_problem_team`** in `server.py` for team lookup. It works (it is the shared allowlist),
  but the name is misleading; left alone to keep this change set focused on defects.
* **Remaining `ruff` style-only findings** under the stricter rule set (too many positional arguments on MCP tool
  functions, loop-variable overwrite in `client._clean`, one deferred import) and shellcheck info-level notes
  (SC2015 on `a && b || c` where `b` cannot fail) are intentional or harmless.
* **Docs vs code**: every environment variable and tool name in the code is documented, and the generated skills are
  in sync (`python3 skills/build.py --check`). `docs/TOOLS.md` documents read tools only, by design.

## Verification after the fixes

* `pytest`: 643 passed, 1 skipped (was 632 passed, 1 skipped); 11 new regression tests.
* `ruff check .`: clean. `mypy src`: clean (was 10 errors). `shellcheck -S warning` and `bash -n` on all scripts: clean.
* Not verifiable here: a real install (systemd/Docker) and live ServiceNow calls. The README already recommends
  watching the first run; the port change in `configure.sh` in particular should be exercised once on a real host.

---

# Simplification pass (2026-10-27)

Behaviour-preserving cleanup; the full test suite is the safety net (644 passing, `ruff` and `mypy` clean).
About 100 fewer lines (+210 / -308).

| Where | Change |
|---|---|
| `client.py` | The three near-identical `get_incident_raw` / `get_problem_raw` / `get_change_raw` bodies are now one `_get_raw`. The three `search_*` bodies are one `_search`. Eight create/update bodies are one `_write`. The "is there a next page" calculation, repeated five times, is one `_next_offset`. Public method names are unchanged. |
| `client.py` | `get_roster` fetches the members of all rosters concurrently (`asyncio.gather`) instead of one roster at a time; member order is unchanged. |
| `validation.py` | `IncidentSearch`, `ProblemSearch` and `ChangeSearch` were three identical dataclasses; now a single `Search`. The three search builders share small helpers (`_state_value`, `_bool_clause`, `_rating_clauses`, `_category`, `_sys_id_clauses`, `_text_and_date_clauses`, `_finish_search`) instead of repeating the same validation blocks. The `short_description` single-line check and `category` check, copy-pasted into the write builders, are now `_short_description` and `_category`. Clause order and error messages are unchanged. |
| `auth.py` | The failed-login tracker created an entry and reshuffled its LRU on every request, including healthy ones, so ordinary traffic could push a real offender out of the table. It now tracks only clients that have failed, and forgets them once their window expires. |
| `server.py` | `resolve_problem_team` renamed `resolve_configured_team`: problems and changes both use it. |

Deliberately not done, to avoid over-engineering: no framework or decorator layer for the MCP tool definitions
(the repeated `params = {...}` audit dictionaries are explicit and easy to read), and the config helpers `_int` and
`_float` stay separate.
