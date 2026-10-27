# ServiceNow MCP server (read tools + optional guarded write tools)

A small [Model Context Protocol](https://modelcontextprotocol.io) server that lets an agent (Hermes) work with
ServiceNow incidents, problems, change approvals and the on-call schedule. It speaks MCP over **Streamable HTTP**, is
protected by a **bearer token**, and talks to ServiceNow with a dedicated least-privilege account using
**Basic Authentication over HTTPS**.

**Read tools are always on. Every write capability is OFF by default** and is only registered when you enable it
during setup, so `tools/list` always shows exactly what the integration can do.

| Tool | Enabled by | Purpose |
|---|---|---|
| `search_incidents` | always | Filter/page incidents (incl. `unassigned=true` for the triage queue) |
| `get_incident`, `get_change_request` | always | One record by number or sys_id |
| `create_incident`, `update_incident`, `resolve_incident` | `MCP_ENABLE_INCIDENT_WRITES` | Create, edit (not assignment/close), resolve incidents |
| `list_assignment_teams`, `assign_incident` | `MCP_ENABLE_INCIDENT_ASSIGNMENT` | Triage and assign to **teams you list** |
| `approve_change_request` | `MCP_ENABLE_CHANGE_APPROVAL` | Approve changes whose approval is pending for the integration user |
| `list_oncall_rotas`, `get_oncall_roster`, `whois_oncall` | `MCP_ENABLE_ONCALL_READ` | Review on-call rotas/rosters, who is on call |
| `add_oncall_member`, `update_oncall_member` | `MCP_ENABLE_ONCALL_WRITES` | Add a roster member; change order/valid dates |
| `search_problems`, `get_problem` | `MCP_ENABLE_PROBLEM_READ` | Filter problems (state, known errors, CI, group...); one problem with cause/workaround/fix notes and its linked incidents |
| `create_problem`, `update_problem`, `resolve_problem`, `link_incident_to_problem` | `MCP_ENABLE_PROBLEM_WRITES` (implies the problem read tools) | Raise a problem, record root cause / workaround / known error, move it through assess -> RCA -> fix in progress, resolve it, link incidents to it |

Never available: delete, closing/cancelling incidents, closing problems, rejecting changes, editing other tables, scripts, or any
generic "call this ServiceNow API" tool.

### How writes are kept safe

* **Preview, then confirm.** Every write tool defaults to `confirm=false`: it validates, reads current state and
  returns a preview of exactly what would be sent, changing nothing. The agent must call again with `confirm=true`
  after the user approves. Keep Hermes' own tool-approval prompts on for write tools as the human gate.
* **Allowlists, not free input.** Assignment only to teams in `MCP_ASSIGNMENT_GROUPS` (name=sys_id pairs you set);
  incident edits cannot touch assignment or close/cancel; approvals only for records pending on the integration
  user themself and only for types in `MCP_APPROVAL_ALLOWED_TYPES` (default `standard,normal`, not `emergency`);
  on-call edits only touch roster-member order/valid dates; nothing is ever deleted. Problem edits can move a
  problem only to assess / root-cause-analysis / fix-in-progress (resolving is its own tool, closing does not
  exist), only to teams in `MCP_ASSIGNMENT_GROUPS`, never touch resolved/closed problems, and linking never moves
  an incident that already belongs to a different problem.
* **Guards on state:** closed/canceled incidents cannot be edited; resolved/closed/canceled cannot be resolved or
  assigned (checked on raw state values, not localised labels).
* **Prompt-injection resistance:** text fields are plain text only (control and bidi-override characters rejected),
  sys_ids/dates/codes use strict patterns, and the client can only issue a fixed list of (method, table) pairs.
* **No retries on writes** (a retried POST could duplicate). A timeout/5xx on a write reports "outcome UNKNOWN" so the
  agent re-reads before trying again. Writes have their own lower rate limit (20/min).
* **Audit log:** one JSON line per call: tool, parameter *names*, record number, `write`, `executed`, outcome,
  duration - never field values, notes or secrets.

## Design

```
Hermes ──HTTPS + Authorization: Bearer <MCP token>──▶ Traefik (TLS, your cert) ──▶ MCP server ──HTTPS + Basic──▶ ServiceNow
                                                       mcp.example.com/mcp   (container or systemd)      <instance>.service-now.com
```

* **Transport:** MCP Streamable HTTP, stateless, JSON responses, single endpoint `/mcp` (Hermes' native
  HTTP MCP client supports this). Python SDK: `mcp` 1.x (`FastMCP`), `httpx`, `uvicorn`.
* **Hermes → MCP server authentication:** a random bearer token (≥32 chars). The server stores only its
  **SHA-256 digest**; the token itself lives only in Hermes' `~/.hermes/.env`. ServiceNow Basic Auth is a
  completely separate credential and never authenticates Hermes. Failed attempts are throttled per client.
* **MCP server → ServiceNow:** Basic Auth header from the integration account, TLS verification on,
  redirects off, host restricted to `*.service-now.com` / `*.servicenowservices.com` (a custom domain needs an
  explicit opt-in), a fixed allowlist of (method, table) pairs - reads on the tables for the features you enabled, and POST/PATCH
  only on the tables a write feature you enabled needs. No DELETE or PUT exists in the client.
* **TLS:** terminated by your existing **Traefik** (certificate from Traefik's resolver). The app never
  serves plain HTTP on a public address: it refuses to start unless (a) it has its own cert/key, or (b) it
  is bound to loopback/private address (or in a container with no published port) *and* you confirmed a TLS
  proxy (`MCP_BEHIND_TLS_PROXY=true`). In proxy mode every request must carry `X-Forwarded-Proto: https`
  (Traefik sets it) or it is rejected. Host/Origin headers are validated (DNS-rebinding protection).

### Compatibility assumptions (please check)

* **MCP SDK:** `mcp>=1.27,<2`. PyPI also has 2.x, which renames the server API (`MCPServer`); this project
  deliberately stays on the maintained 1.x line. Bump only after testing.
* **Problem state model:** the tools use names (new, assess, root_cause_analysis, fix_in_progress, resolved, closed)
  that map to raw values through `MCP_PROBLEM_STATES`. The default is the 101-107 model of current releases
  (`new=101;assess=102;root_cause_analysis=103;fix_in_progress=104;resolved=106;closed=107`); an older instance
  needs its own numbers, e.g. `resolved=4;closed=7`. Setup asks for them when you enable a problem feature; check
  them against the State column of your Problems list (show raw values) before enabling problem writes. Field
  names (`cause_notes`, `fix_notes`, `workaround`, `known_error`, `resolution_code`, `problem_id`) follow the
  standard problem/incident tables and were not verified against your instance.
* **Hermes:** documented config (as of late 2026) is `mcp_servers:` in `~/.hermes/config.yaml` with
  `url`, `headers`, `timeout`, `tools.include`; `${VAR}` placeholders resolved from `~/.hermes/.env`;
  tools named `mcp_<server>_<tool>`. The Hermes format was written from its public documentation and not checked against every version; if yours differs use
  `hermes mcp add` / `hermes mcp test` (see `skills/hermes/`, "Adjusting for your version").
* **ServiceNow:** Table API (`/api/now/table/...`), `sysparm_*` parameters, `X-Total-Count` header. Any
  supported release. Basic Auth must be allowed for REST for the integration user (default).
* **Traefik:** v2/v3, HTTPS entrypoint with a certificate resolver. In Docker mode the container joins
  Traefik's Docker network and is routed via labels.
* **Debian 13 (trixie) target:** system Python 3.13 (systemd mode installs `python3-venv` via apt if missing),
  systemd 257, nftables firewall by default, Docker image `python:3.13-slim-trixie`. For Docker mode install
  Docker Engine + `docker-compose-plugin` from Docker's official Debian repository
  (<https://docs.docker.com/engine/install/debian/>); Debian's own `docker.io` package lags behind.
  All scripts assume root and use only POSIX tools present on a default Debian install.
* **Test status:** the Python code has an automated suite (see Development). The shell scripts, Dockerfile and
  systemd unit are syntax-checked and reviewed, but a fresh install is the real test: watch the output of the
  first run and use the verification steps in [INSTALL.md](INSTALL.md).

## Documentation

| Document | What it covers |
|---|---|
| [INSTALL.md](INSTALL.md) | Quick start, the ServiceNow account and permissions you must create, Docker and systemd install, verification, operations, troubleshooting, uninstall |
| [docs/TOOLS.md](docs/TOOLS.md) | Per-tool inputs and validation rules, fields returned |
| [skills/](skills/README.md) | Skills for Claude Desktop, OpenClaw, Paperclip, ChatGPT Desktop, Hermes and a generic one for any client |
| [fixes.md](fixes.md) | Issues found while building and testing, how each was fixed, known gaps |

## Quick start

On a Debian 13 server with Docker (or systemd) and Traefik, as root:

```bash
bash setup.sh
```

Setup asks for everything it needs (ServiceNow URL and integration account, the host name Hermes will connect
to, an MCP token, which write features to enable) and nothing is pre-filled from anyone's environment. Full
steps and the ServiceNow-side prerequisites are in [INSTALL.md](INSTALL.md).

## Repository layout

```
setup.sh                 single entry point (Docker or systemd)
scripts/                 install, configure, docker-setup, check, hermes-instructions, uninstall
src/servicenow_mcp/      server, config, validation, ServiceNow client, auth, CLI
tests/                   pytest suite (ServiceNow is mocked)
config/  docker/  deploy/  example config, Docker Compose, systemd unit
skills/                  agent skills per client (generated from skills/_source) + build.py
docs/TOOLS.md            tool reference
```

## Development / testing

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
pytest -q        # 346 tests; ServiceNow is mocked, no credentials needed
ruff check src tests
```
Tests cover validation/injection attempts, config and TLS rules, the ServiceNow client (mock transport),
bearer auth/throttling/body limits, MCP end-to-end over ASGI, tool-set read-only guard and log redaction.
