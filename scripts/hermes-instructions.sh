#!/usr/bin/env bash
# Print step-by-step instructions for connecting Hermes to this MCP server.
# Usage: hermes-instructions.sh [public-hostname]   (never prints or needs any secret)
set -euo pipefail
HOST="${1:-}"
if [[ -z $HOST && -r /etc/servicenow-mcp/config.env ]]; then
  HOST="$(grep -E '^MCP_PUBLIC_HOSTNAME=' /etc/servicenow-mcp/config.env | tail -n1 | cut -d= -f2-)"
fi
if [[ -z $HOST && -r "$(dirname "${BASH_SOURCE[0]}")/../docker/.env" ]]; then
  HOST="$(grep -E '^MCP_PUBLIC_HOSTNAME=' "$(dirname "${BASH_SOURCE[0]}")/../docker/.env" | tail -n1 | cut -d= -f2-)"
fi
HOST="${HOST:-<your-mcp-host>}"
# Which tools are enabled? Read the non-secret config the installer wrote.
CFG=/etc/servicenow-mcp/config.env
[[ -r $CFG ]] || CFG="$(dirname "${BASH_SOURCE[0]}")/../docker/.env"
flag() { [[ -r $CFG ]] && grep -qE "^$1=true" "$CFG"; }
INCLUDE="search_incidents, get_incident, get_change_request"
WRITES=""
if flag MCP_ENABLE_INCIDENT_WRITES; then INCLUDE+=", create_incident, update_incident, resolve_incident"; WRITES=1; fi
if flag MCP_ENABLE_INCIDENT_ASSIGNMENT; then INCLUDE+=", list_assignment_teams, assign_incident"; WRITES=1; fi
if flag MCP_ENABLE_CHANGE_APPROVAL; then INCLUDE+=", approve_change_request"; WRITES=1; fi
if flag MCP_ENABLE_ONCALL_READ || flag MCP_ENABLE_ONCALL_WRITES; then INCLUDE+=", list_oncall_rotas, get_oncall_roster, whois_oncall"; fi
if flag MCP_ENABLE_ONCALL_WRITES; then INCLUDE+=", add_oncall_member, update_oncall_member"; WRITES=1; fi
if flag MCP_ENABLE_PROBLEM_READ || flag MCP_ENABLE_PROBLEM_WRITES; then INCLUDE+=", search_problems, get_problem"; fi
if flag MCP_ENABLE_PROBLEM_WRITES; then
  INCLUDE+=", create_problem, update_problem, resolve_problem, link_incident_to_problem"; WRITES=1
fi
if flag MCP_ENABLE_CHANGE_READ || flag MCP_ENABLE_CHANGE_WRITES; then INCLUDE+=", search_change_requests"; fi
if flag MCP_ENABLE_CHANGE_WRITES; then
  INCLUDE+=", create_change_request, update_change_request, transition_change_request, close_change_request, cancel_change_request"; WRITES=1
fi
if flag MCP_ENABLE_CATALOG_READ || flag MCP_ENABLE_CATALOG_WRITES; then
  INCLUDE+=", search_catalog_items, get_catalog_item, get_catalog_options"
fi
if flag MCP_ENABLE_CATALOG_WRITES; then
  INCLUDE+=", create_catalog_item, create_record_producer, add_catalog_variables"; WRITES=1
fi
WRITE_NOTE=""
[[ -n $WRITES ]] && WRITE_NOTE=" Write tools preview first; they change nothing until called again with confirm=true.
    Keep Hermes' own tool-approval prompts ON for the write tools so a human approves each change."
SKILL_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)/skills/hermes/servicenow"

cat <<EOF

================================================================================
 CONNECT HERMES TO THE SERVICENOW MCP SERVER
================================================================================
 MCP endpoint : https://$HOST/mcp
 Auth         : Authorization: Bearer <the MCP token shown once during setup>
 (If you lost the token, rerun setup to generate a new one; it cannot be recovered.)

Do these on the machine where Hermes runs:

 1. Store the token (outside config.yaml, readable only by you):
        umask 077
        read -r -s -p "MCP token: " T; echo
        printf 'SERVICENOW_MCP_TOKEN=%s\n' "\$T" >> ~/.hermes/.env; unset T
    (the token is typed hidden, so it does not land in shell history)

 2. Add the server to ~/.hermes/config.yaml (merge into an existing mcp_servers: block):
        mcp_servers:
          servicenow:
            url: "https://$HOST/mcp"
            headers:
              Authorization: "Bearer \${SERVICENOW_MCP_TOKEN}"
            timeout: 60
            connect_timeout: 30
            tools:
              include: [$INCLUDE]

 3. Install the skill (copy skills/hermes/servicenow from this project to that machine):
        mkdir -p ~/.hermes/skills/devops
        cp -r servicenow ~/.hermes/skills/devops/
    ${SKILL_SRC:+(skill source on this server: $SKILL_SRC)}

 4. Test, then restart Hermes / start a new session:
        hermes mcp test servicenow
        hermes skills list | grep servicenow
    Expect the tools listed in step 2. Then ask Hermes: "Look up incident INC0010001".$WRITE_NOTE

 Quick check from anywhere (should print 401, proving the endpoint demands the token):
        curl -s -o /dev/null -w '%{http_code}\n' -X POST https://$HOST/mcp

 If it does not work:
   401 -> token mismatch (rerun setup to rotate, update ~/.hermes/.env)
   400 / 421 -> Traefik route or host name mismatch (host must be $HOST, path /mcp not stripped)
   hermes can't expand \${VAR} -> use: hermes mcp add servicenow --url https://$HOST/mcp
                                   and enter the Authorization header when prompted
   Full details: README.md and skills/hermes/servicenow/SKILL.md
================================================================================
EOF
