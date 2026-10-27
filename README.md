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
  explicit opt-in), fixed API paths for two tables only, GET only.
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
  tools named `mcp_<server>_<tool>`. I could not inspect your installed Hermes; if yours differs use
  `hermes mcp add` / `hermes mcp test` (see `hermes-skill/`, "Adjusting for your version").
* **ServiceNow:** Table API (`/api/now/table/...`), `sysparm_*` parameters, `X-Total-Count` header. Any
  supported release. Basic Auth must be allowed for REST for the integration user (default).
* **Traefik:** v2/v3, HTTPS entrypoint with a certificate resolver. In Docker mode the container joins
  Traefik's Docker network and is routed via labels.
* **Debian 13 (trixie) target:** system Python 3.13 (systemd mode installs `python3-venv` via apt if missing),
  systemd 257, nftables firewall by default, Docker image `python:3.13-slim-trixie`. For Docker mode install
  Docker Engine + `docker-compose-plugin` from Docker's official Debian repository
  (<https://docs.docker.com/engine/install/debian/>); Debian's own `docker.io` package lags behind.
  All scripts assume root and use only POSIX tools present on a default Debian install.
* Not tested here: the shell scripts, Dockerfile and systemd unit were written on Windows without a Linux
  host and have **not been executed**; the Python code is tested (see Testing). Run the first install
  with the service stopped/unexposed and use the verification steps below.

## Quick start (Debian 13)

1. Copy the whole project directory to the server, e.g. from your PC:
   ```bash
   scp -r servicenow-mcp root@<your-server>:/root/servicenow-mcp
   ```
2. On the server run the single setup script as **root** (Debian 13 minimal installs often have no `sudo`;
   `su -` or log in as root; it needs an interactive terminal for the prompts):
   ```bash
   cd /root/servicenow-mcp && bash setup.sh
   ```
   Choose **1 (Docker, recommended)** or **2 (systemd)**. It prompts for the ServiceNow URL, integration
   username/password (hidden), the host name of the server (asked explicitly; no default) and the MCP token
   (Enter generates one and shows it **once**). Nothing secret is passed in arguments or stored in history.
3. When setup finishes it prints **step-by-step Hermes connection instructions** (token into
   `~/.hermes/.env`, `mcp_servers` block for `config.yaml`, skill install, `hermes mcp test`). Reprint them any
   time with `bash scripts/hermes-instructions.sh`. Details: `hermes-skill/README.md`.

Setup asks which write features to enable (default: none) and, for assignment, which teams are allowed.
Hermes endpoint: **`https://mcp.example.com/mcp`** (do not strip the `/mcp` prefix in Traefik).

## ServiceNow: required account and permissions (reads)

Create these in ServiceNow yourself (nothing here creates or changes accounts for you):

1. **User** (System Security → Users): e.g. `svc_mcp_integration`, strong random password, **"Web service
   access only" = true** (blocks UI login and typically exempts it from MFA), not a member of admin groups.
2. **Role:** create `x_hermes_mcp_reader` (no other roles; do **not** grant `admin`, `itil`, `itil_admin`,
   `rest_api_explorer`, `snc_read_only` or anything with write access).
3. **ACLs** (System Security → Access Control), operation **read**, role `x_hermes_mcp_reader`:
   * table `incident` and `change_request` (and `problem` if you enable the problem tools) (record-level; optionally field-level `*`/listed fields below),
   * read on the *display fields* of referenced tables so names resolve instead of appearing blank:
     `sys_user` (name), `sys_user_group` (name), `cmdb_ci` / `cmdb_ci_service` (name).
   Scope the record ACLs with a condition (e.g. specific assignment groups) if you want less than "all".
4. ServiceNow's Table API additionally requires the role to satisfy the table's **Table API** access
   restrictions: if "Allow access to this table via web services" is off for a table, enable it for those two.


### Extra permissions for each write feature you enable

Grant these only to the same integration role/user for the features you turned on; **none are needed for read-only use**.
Adjust to your instance (verify in the REST API Explorer); the setup `check` only tests *read* access.

| Feature | ServiceNow permission needed |
|---|---|
| Incident writes / assignment | `create` + `write` ACLs on `incident` for role `x_hermes_mcp_reader` (rename to e.g. `x_hermes_mcp`), including the fields: short_description, description, category, subcategory, urgency, impact, caller_id, business_service, cmdb_ci, assignment_group, state, work_notes, comments, close_code, close_notes. Also read on `sys_user_group` for the teams |
| Change approval | The integration user must be an **approver on the change** (a `sysapproval_approver` record in state *Requested* assigned to it - e.g. via group approval membership), plus `read` + `write` ACL on `sysapproval_approver` (state, comments). `approver.user_name` is dot-walked, so read on `sys_user.user_name` is needed |
| Problem read | `read` on `problem` (the fields listed below, including `cause_notes`, `workaround`, `fix_notes`, `known_error`, `resolution_code`); incident reads (already granted) are used for the linked-incident list via `incident.problem_id` |
| Problem writes | `create` + `write` ACLs on `problem` for: short_description, description, category, subcategory, urgency, impact, cmdb_ci, business_service, assignment_group, state, cause_notes, workaround, fix_notes, known_error, resolution_code, work_notes, comments. Also `write` on `incident.problem_id` (needed by `link_incident_to_problem`) and read on `sys_user_group` for the teams. Your instance's problem state model, mandatory fields (e.g. cause/fix notes, resolution code) and data policies decide what ServiceNow accepts |
| On-call read | `read` on `cmn_rota`, `cmn_rota_roster`, `cmn_rota_member`; the *Who is on call* REST API (`/api/now/on_call_rota/whoisoncall`) requires the On-Call Scheduling plugin and its API role |
| On-call writes | `create` + `write` on `cmn_rota_member` (fields roster, member, order, from, to) |

**On-call data-model assumption:** rota -> roster (`cmn_rota_roster`) -> member records (`cmn_rota_member` with
`member`, `roster`, `order`, `from`, `to`). Shift timing lives in schedules, which this server does not edit. Table and field
names come from public ServiceNow community documentation; confirm them on your instance before enabling on-call writes.
The whoisoncall response shape was not verifiable offline: the server returns whatever scalar fields it sends back.

Setting the incident `state` to *resolved* uses `close_code`/`close_notes`; the allowed close codes are defined on your instance
and a wrong one is returned to the agent as "ServiceNow rejected the data (HTTP 400): ...".
Fields returned — incident: `sys_id, number, short_description, description, state, priority, urgency, impact,
category, subcategory, caller_id, assigned_to, assignment_group, business_service, cmdb_ci, active, opened_at,
opened_by, updated_at, resolved_at, closed_at, close_code`. Change request: `sys_id, number, short_description,
description, state, type, priority, risk, impact, category, requested_by, assigned_to, assignment_group,
business_service, cmdb_ci, approval, justification, implementation_plan, backout_plan, start_date, end_date,
opened_at, updated_at, closed_at, active`. Problem: `sys_id, number, short_description, description, state, priority,
urgency, impact, category, subcategory, assignment_group, assigned_to, business_service, cmdb_ci, known_error, active,
cause_notes, workaround, fix_notes, resolution_code, related_incidents, first_reported_by_task, opened_at, opened_by,
updated_at, resolved_at, closed_at` (incidents additionally show `problem_id`). **Excluded on purpose:** work notes, comments, attachments, close
notes, contact details and any other field. Long text fields are truncated (default 4000 chars). Reference and
choice fields are returned as display values. Records the account cannot read behave as "not found".

| Tool | Inputs and validation | Needs |
|---|---|---|
| `search_incidents` | `state` (new/in_progress/on_hold/resolved/closed/canceled), `active`, `priority` 1-5, `urgency` 1-3, `impact` 1-3, `category` (`[A-Za-z0-9 _-]` ≤40), `assignment_group`/`caller_id` (32-hex sys_id), `short_description_contains` (≤100 chars, letters/digits/space/`. , : ; / @ # ( ) + _ -`; no `^`), `opened_after`/`opened_before` (UTC `YYYY-MM-DD[ HH:MM:SS]`), `order_by` (opened_at/updated_at/priority/number), `descending`, `limit` 1-50 (config cap), `offset` 0-10000 | read `incident` |
| `get_incident` | `identifier`: `INC` + 7-10 digits, or 32-hex sys_id | read `incident` |
| `get_change_request` | `identifier`: `CHG` + 7-10 digits, or 32-hex sys_id | read `change_request` |
| `search_problems` | `state` (new/assess/root_cause_analysis/fix_in_progress/resolved/closed), `active`, `known_error`, `unassigned`, `priority` 1-5, `urgency` 1-3, `impact` 1-3, `category`, `assignment_group`/`cmdb_ci` (32-hex sys_id), `short_description_contains` (same rules as incidents), `opened_after`/`opened_before`, `order_by`, `descending`, `limit`, `offset` | read `problem` |
| `get_problem` | `identifier`: `PRB` + 7-10 digits, or 32-hex sys_id | read `problem`, `incident` |

Queries are built only from closed vocabularies/strict patterns and sent as properly URL-encoded
parameters; callers can never supply a path, table, field list or raw encoded query.

## Option 1 — Docker (portable)

`setup.sh` → option 1 runs `scripts/docker-setup.sh`, which detects your Traefik container/network/resolver,
writes `docker/.env` (non-secret) and `docker/secrets/*` (0400, owned by the container uid), builds the
image, runs the connectivity check and starts the container. The container is non-root, read-only,
`cap_drop: ALL`, `no-new-privileges`, memory/pid limited, publishes **no ports** and receives secrets as
Docker secret files (`*_FILE` variables). Manage:
```bash
docker compose -f docker/docker-compose.yml --env-file docker/.env logs -f
docker compose -f docker/docker-compose.yml --env-file docker/.env restart
docker compose -f docker/docker-compose.yml --env-file docker/.env down
```
To move to another host, copy the project and run `setup.sh` there (secrets are re-entered; they are
deliberately not part of the image). Docker secrets/`.env` are git- and docker-ignored.

## Option 2 — systemd on the host

`setup.sh` → option 2 runs `scripts/install.sh` (creates the `servicenow-mcp` system user with no shell/home,
`/opt/servicenow-mcp` root-owned venv, `/etc/servicenow-mcp`, installs the unit; uses apt only to add
`python3-venv` if missing), then `scripts/configure.sh`, then `scripts/check.sh`.

* `/etc/servicenow-mcp/config.env` — non-secret (0644). `/etc/servicenow-mcp/secrets.env` — **root:root 0600**
  (systemd reads it as root; the service account cannot).
* Unit: runs as the unprivileged user with `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`,
  `PrivateTmp/Devices`, empty capability set, syscall filter, `MemoryDenyWriteExecute`, address families
  restricted to IP/UNIX, memory/task limits.
* Traefik (in Docker) must reach the host service: choose the Docker bridge gateway as bind address in
  `configure.sh` and use `config/traefik-dynamic.yml.example` with Traefik's file provider. If a firewall is
  active allow only that bridge subnet to that port (the script prints an example). Never open the port publicly.
```bash
sudo systemctl status servicenow-mcp        sudo journalctl -u servicenow-mcp -f
sudo systemctl restart servicenow-mcp       sudo /opt/servicenow-mcp/scripts/check.sh
```

## Verify (no secrets are printed)

1. **ServiceNow permissions/connectivity:** `check.sh` (systemd) or the check step of `docker-setup.sh`;
   output is `PASS read incident` / `FAIL read change_request: forbidden - ...` only.
2. **Endpoint auth:** `curl -s -o /dev/null -w '%{http_code}\n' -X POST https://mcp.example.com/mcp`
   → `401`. `curl https://mcp.example.com/healthz` → `{"status": "ok"}` (only if you route it).
3. **From Hermes:** `hermes mcp test servicenow` then ask it to look up a known incident.
4. Check logs contain `tool_call` lines (tool, parameter *names*, outcome, duration) and no secrets.

## Operations

* **Rotate the MCP token:** systemd: rerun `configure.sh`, answer `y` to replacing the token (the old one keeps
  working via `MCP_AUTH_TOKEN_SHA256_PREVIOUS` until you rerun and remove it). Docker: rerun
  `docker-setup.sh`, answer `y`. Update `SERVICENOW_MCP_TOKEN` in Hermes, restart Hermes, then remove the old digest.
* **Rotate the ServiceNow password:** change it in ServiceNow, rerun the configure/setup script (Enter keeps the
  other values), restart.
* **Backup:** `/etc/servicenow-mcp/config.env` is safe to back up. Treat `secrets.env`/`docker/secrets` as
  secrets (encrypted backup only) — or just re-run setup; the token cannot be recovered, only replaced.
* **Logs:** JSON lines on stdout (journald / `docker logs`); httpx/uvicorn access logging is off because it would
  record query text. Auth failures log the client address and path only.
* **Limits:** request body 64 KB, 15 s ServiceNow timeout (2 retries on 502/503/504), 2 MB response cap,
  4 concurrent ServiceNow calls, 60 ServiceNow calls/min, page size ≤50, 10 failed auths/min/client.
* **Egress firewall (recommended):** allow outbound 443 only to your instance if your policy permits.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Server exits "configuration error: ..." | Message names the setting; fix and restart (never includes secret values) |
| `401` from Hermes | Token mismatch: rotate the token and update `~/.hermes/.env` |
| `400 TLS required` | Proxy not sending `X-Forwarded-Proto: https` (Traefik does by default on an HTTPS entrypoint) |
| `421`/`400` Invalid Host | Traefik rule/host differs from `MCP_PUBLIC_HOSTNAME`; they must match |
| `404` at `/mcp` | Traefik router rule/path; don't strip the prefix |
| Tool says "credentials rejected (401)" | Wrong password, locked user, or Basic Auth blocked for that user |
| "not permitted (403)" | Missing role/ACL, see permissions section |
| Fields blank (assigned_to etc.) | Missing read ACL on `sys_user`/`sys_user_group`/`cmdb_ci` display fields |
| "TLS certificate verification failed" | Wrong instance URL or an intercepting proxy |
| Container can't reach the instance | Docker DNS/egress; try `docker run --rm curlimages/curl -sI https://<instance>` |

## Uninstall

* Docker: `docker compose -f docker/docker-compose.yml --env-file docker/.env down --rmi local`, then delete `docker/secrets` and `docker/.env`.
* systemd: `sudo bash /opt/servicenow-mcp/scripts/uninstall.sh --purge`.
* Then disable the ServiceNow integration user and remove the Hermes token/config/skill.

## Development / testing

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
pytest -q        # 346 tests; ServiceNow is mocked, no credentials needed
ruff check src tests
```
Tests cover validation/injection attempts, config and TLS rules, the ServiceNow client (mock transport),
bearer auth/throttling/body limits, MCP end-to-end over ASGI, tool-set read-only guard and log redaction.
