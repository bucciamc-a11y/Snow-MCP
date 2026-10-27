# Installing the ServiceNow MCP server

Target: a Debian 13 server running Traefik, reached by Hermes over HTTPS. For what the server does and how it is
kept safe, see [README.md](README.md). Tool reference: [docs/TOOLS.md](docs/TOOLS.md).

## Prerequisites

* A Debian 13 server you can log in to as root, with Docker Engine + the compose plugin (Docker mode) or
  systemd (host mode), and Traefik terminating TLS for a DNS name that points at the server.
* A ServiceNow integration account with the permissions in the section below (you create this in ServiceNow).
* Hermes (or another MCP client) that can send a bearer token to an HTTPS MCP endpoint.

## Quick start

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
   time with `bash scripts/hermes-instructions.sh`. Details: `skills/hermes/servicenow/SETUP.md`.

Setup asks which write features to enable (default: none) and, for assignment, which teams are allowed.
Hermes endpoint: **`https://mcp.example.com/mcp`** (do not strip the `/mcp` prefix in Traefik).

## ServiceNow: required account and permissions

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

Tool inputs, validation rules and the fields each tool returns: [docs/TOOLS.md](docs/TOOLS.md).
