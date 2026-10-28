#!/usr/bin/env bash
# Interactive configuration. Secrets are read from the terminal with echo off and are never
# placed in argv, the environment of other processes, or shell history.
# Re-runnable: press Enter at a prompt to keep the existing value.
set -euo pipefail
umask 077

CONF_DIR=/etc/servicenow-mcp
VENV_PY=/opt/servicenow-mcp/venv/bin/python
CONF="$CONF_DIR/config.env"
SECRETS="$CONF_DIR/secrets.env"

[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
[[ -x $VENV_PY ]] || { echo "Run install.sh first" >&2; exit 1; }
[[ -t 0 ]] || { echo "configure.sh must be run from an interactive terminal" >&2; exit 1; }

# existing_value FILE KEY -> value (quotes stripped) or empty; plain parsing, never sourced.
existing_value() {
  local v; v="$(grep -E "^$2=" "$1" 2>/dev/null | tail -n1 | cut -d= -f2- || true)"
  v="${v#\"}"; v="${v%\"}"; printf '%s' "$v"
}
# dq: escape for a double-quoted systemd EnvironmentFile value.
dq() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }

ask() { # ask "Prompt" default -> REPLY
  local def="$2" ans
  if [[ -n $def ]]; then read -r -p "$1 [$def]: " ans; else read -r -p "$1: " ans; fi
  REPLY="${ans:-$def}"
}
reject_ctl() { if printf '%s' "$1" | LC_ALL=C grep -q '[[:cntrl:]]'; then echo "Control characters are not allowed." >&2; exit 1; fi; }

cur_url="$(existing_value "$CONF" SERVICENOW_INSTANCE_URL)"; [[ $cur_url == *your-instance* ]] && cur_url=""
cur_host="$(existing_value "$CONF" MCP_PUBLIC_HOSTNAME)"; [[ $cur_host == mcp.example.com ]] && cur_host=""

echo "== ServiceNow =="
ask "Instance URL (https://<name>.service-now.com)" "$cur_url"; URL="${REPLY%/}"
[[ $URL =~ ^https://[A-Za-z0-9.-]+$ ]] || { echo "URL must look like https://name.service-now.com" >&2; exit 1; }
ALLOW_CUSTOM=false
if [[ ! $URL =~ \.(service-now\.com|servicenowservices\.com)$ ]]; then
  read -r -p "Host is not *.service-now.com; allow this custom domain? [y/N]: " yn
  [[ $yn =~ ^[Yy]$ ]] && ALLOW_CUSTOM=true || { echo "Aborted." >&2; exit 1; }
fi

echo "Integration account username (the dedicated least-privilege user; see README)."
cur_user="$(existing_value "$SECRETS" SERVICENOW_USERNAME)"
read -r -p "Username [${cur_user:+keep existing}]: " SN_USER; SN_USER="${SN_USER:-$cur_user}"
[[ -n $SN_USER ]] || { echo "Username required" >&2; exit 1; }; reject_ctl "$SN_USER"
read -r -s -p "Password [Enter keeps existing]: " SN_PASS; echo
if [[ -z $SN_PASS ]]; then SN_PASS="$(existing_value "$SECRETS" SERVICENOW_PASSWORD)"; SN_PASS_ESCAPED=1; else SN_PASS_ESCAPED=0; fi
[[ -n $SN_PASS ]] || { echo "Password required" >&2; exit 1; }; reject_ctl "$SN_PASS"

echo; echo "== MCP endpoint =="
echo "Host name of THIS server as Hermes reaches it (e.g. mcp.yourcompany.ca); a pasted https://host/mcp URL is also accepted."
ask "MCP server host name" "$cur_host"; HOSTNAME_PUB="$REPLY"
[[ -n $HOSTNAME_PUB ]] || { echo "Host name required" >&2; exit 1; }
HOSTNAME_PUB="${HOSTNAME_PUB#*://}"; HOSTNAME_PUB="${HOSTNAME_PUB%%/*}"; HOSTNAME_PUB="${HOSTNAME_PUB%%:*}"
[[ $HOSTNAME_PUB =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$ ]] \
  || { echo "Invalid host name" >&2; exit 1; }
HOSTNAME_PUB="$(printf '%s' "$HOSTNAME_PUB" | tr '[:upper:]' '[:lower:]')"

cur_hash="$(existing_value "$SECRETS" MCP_AUTH_TOKEN_SHA256)"
NEW_TOKEN=""
prev_hash=""
if [[ -n $cur_hash ]]; then
  read -r -p "An MCP token already exists. Replace it? (old one stays valid until you remove it) [y/N]: " yn
  if [[ $yn =~ ^[Yy]$ ]]; then prev_hash="$cur_hash"; cur_hash=""; fi
fi
if [[ -z $cur_hash ]]; then
  read -r -s -p "MCP bearer token (min 32 chars) [Enter generates a random one]: " TOKEN_IN; echo
  if [[ -z $TOKEN_IN ]]; then NEW_TOKEN="$("$VENV_PY" -I -m servicenow_mcp.cli gen-token)"; TOKEN_IN="$NEW_TOKEN"; fi
  reject_ctl "$TOKEN_IN"
  # printf is a shell builtin: the token is passed on stdin only, never via argv.
  cur_hash="$(printf '%s\n' "$TOKEN_IN" | "$VENV_PY" -I -m servicenow_mcp.cli hash-token)"
  unset TOKEN_IN
fi
[[ $cur_hash =~ ^[0-9a-f]{64}$ ]] || { echo "Failed to derive token digest" >&2; exit 1; }

# TLS mode
cur_cert="$(existing_value "$CONF" MCP_TLS_CERT_FILE)"; cur_key="$(existing_value "$CONF" MCP_TLS_KEY_FILE)"
TLS_LINES=""
BEHIND=true
echo; echo "== TLS =="
read -r -p "TLS is terminated by a reverse proxy on this host, e.g. Traefik (recommended)? [Y/n]: " yn
if [[ $yn =~ ^[Nn]$ ]]; then
  ask "Path to TLS certificate (PEM, readable by servicenow-mcp)" "$cur_cert"; CERT="$REPLY"
  ask "Path to TLS private key (PEM, readable by servicenow-mcp)" "$cur_key"; KEY="$REPLY"
  [[ -r $CERT && -r $KEY ]] || { echo "Certificate/key not readable" >&2; exit 1; }
  [[ $CERT =~ ^/[A-Za-z0-9._/-]+$ && $KEY =~ ^/[A-Za-z0-9._/-]+$ ]] || { echo "Unsupported characters in path" >&2; exit 1; }
  TLS_LINES=$'MCP_TLS_CERT_FILE='"$CERT"$'\nMCP_TLS_KEY_FILE='"$KEY"
  BEHIND=false
  read -r -p "Bind address [0.0.0.0]: " BIND; BIND="${BIND:-0.0.0.0}"
  # The service runs unprivileged with no capabilities (see deploy/servicenow-mcp.service), so it cannot bind
  # ports below 1024; 443 would fail at start-up. Use a high port (or forward 443 to it with your firewall).
  read -r -p "TCP port to listen on (1024-65535) [8443]: " PORT; PORT="${PORT:-8443}"
  [[ $PORT =~ ^[0-9]+$ ]] && ((PORT >= 1024 && PORT <= 65535)) \
    || { echo "Port must be a number between 1024 and 65535 (the service cannot bind privileged ports)." >&2; exit 1; }
else
  PORT=8765
  echo
  echo "Which address will the reverse proxy (Traefik) use to reach this service?"
  echo "  1) 127.0.0.1  - proxy runs on the host network (or is a host process)"
  echo "  2) a Docker bridge gateway - Traefik runs in a container (the usual setup)"
  echo "The service is never bound to a public address; plain HTTP stays on a private interface."
  cands=()
  if command -v docker >/dev/null 2>&1; then
    while read -r name gw; do
      [[ -n ${gw:-} ]] && cands+=("$gw  (docker network: $name)")
    done < <(docker network ls --filter driver=bridge -q 2>/dev/null \
             | xargs -r docker network inspect -f '{{.Name}} {{range .IPAM.Config}}{{.Gateway}}{{end}}' 2>/dev/null || true)
  fi
  if ((${#cands[@]})); then printf '  detected: %s\n' "${cands[@]}"; fi
  read -r -p "Choose 1, 2, or type an address [1]: " choice; choice="${choice:-1}"
  case "$choice" in
    1) BIND=127.0.0.1 ;;
    2) BIND=172.17.0.1
       if ((${#cands[@]})); then ask "Gateway address of Traefik's docker network" "${cands[0]%% *}"; BIND="$REPLY"; fi ;;
    *) BIND="$choice" ;;
  esac
  if [[ $BIND != 127.0.0.1 ]]; then
    echo
    echo "NOTE: if a host firewall (nftables/ufw) is active it may block the container -> $BIND:$PORT."
    echo "      Example (adjust the subnet): ufw allow from 172.16.0.0/12 to $BIND port $PORT proto tcp"
    echo "      Debian 13 uses nftables by default; with plain nftables add an equivalent accept rule on the docker bridge interface."
    echo "      Do NOT open port $PORT to the internet."
  fi
fi
[[ $BIND =~ ^[0-9a-fA-F:.]+$ ]] || { echo "Invalid bind address" >&2; exit 1; }

# --- optional write capabilities (all OFF unless enabled here) -------------------------
echo; echo "== Capabilities =="
echo "Read tools are always on. Each WRITE feature below is off unless you enable it, and every write"
echo "tool previews first and only applies after the agent calls it again with confirm=true."
echo "Each needs matching ServiceNow roles/ACLs on the integration account (see README)."
yesno() { # yesno "Question" CONFIG_KEY -> REPLY=true|false (default = current setting, else no)
  local cur def ans; cur="$(existing_value "$CONF" "$2")"
  if [[ $cur == true ]]; then def=Y; else def=N; fi
  read -r -p "$1 [$([[ $def == Y ]] && echo Y/n || echo y/N)]: " ans; ans="${ans:-$def}"
  if [[ $ans =~ ^[Yy]$ ]]; then REPLY=true; else REPLY=false; fi
}
yesno "Allow create / update / resolve incidents?" MCP_ENABLE_INCIDENT_WRITES; EN_INC=$REPLY
yesno "Allow triage + assigning incidents to teams you list?" MCP_ENABLE_INCIDENT_ASSIGNMENT; EN_ASSIGN=$REPLY
TEAMS=""
if [[ $EN_ASSIGN == true ]]; then
  TEAMS="$(existing_value "$CONF" MCP_ASSIGNMENT_GROUPS)"
  if [[ -n $TEAMS ]]; then
    echo "Current teams: $TEAMS"
    read -r -p "Keep these teams? [Y/n]: " yn; [[ $yn =~ ^[Nn]$ ]] && TEAMS=""
  fi
  if [[ -z $TEAMS ]]; then
    echo "Enter the teams incidents may be assigned to. Find a group's sys_id in ServiceNow:"
    echo "  User Administration > Groups > open the group > right-click header > Copy sys_id."
    while :; do
      read -r -p "Team name (blank to finish): " tname; [[ -z $tname ]] && break
      [[ $tname =~ ^[A-Za-z0-9][A-Za-z0-9\ _.\&-]{0,59}$ ]] || { echo "  Use letters, digits, spaces, . _ & - (max 60)."; continue; }
      read -r -p "  sys_id for '$tname' (32 hex chars): " tid; tid="$(printf '%s' "$tid" | tr 'A-F' 'a-f')"
      [[ $tid =~ ^[0-9a-f]{32}$ ]] || { echo "  Not a 32-character hex sys_id."; continue; }
      TEAMS="${TEAMS:+$TEAMS;}$tname=$tid"
    done
  fi
  [[ -n $TEAMS ]] || { echo "Assignment needs at least one team." >&2; exit 1; }
fi
yesno "Allow approving change requests (only approvals assigned to the integration user itself)?" MCP_ENABLE_CHANGE_APPROVAL; EN_APPR=$REPLY
APPR_TYPES="$(existing_value "$CONF" MCP_APPROVAL_ALLOWED_TYPES | grep . || echo standard,normal)"
if [[ $EN_APPR == true ]]; then
  ask "Change types that may be approved (comma list; emergency is excluded by default)" "$APPR_TYPES"; APPR_TYPES="$REPLY"
  [[ $APPR_TYPES =~ ^[a-z][a-z,\ _-]*$ ]] || { echo "Use lowercase names such as standard,normal" >&2; exit 1; }
fi
yesno "Allow reading the on-call schedule (rotas, rosters, who is on call)?" MCP_ENABLE_ONCALL_READ; EN_OCR=$REPLY
yesno "Allow editing on-call roster members (add member, change order/valid dates)?" MCP_ENABLE_ONCALL_WRITES; EN_OCW=$REPLY
yesno "Allow reading work notes and comments (get_journal)?" MCP_ENABLE_JOURNAL_READ; EN_JRN=$REPLY
yesno "Allow looking up configuration items (search_cis)?" MCP_ENABLE_CMDB_READ; EN_CMDB=$REPLY
yesno "Allow reading problem records (search_problems, get_problem)?" MCP_ENABLE_PROBLEM_READ; EN_PRBR=$REPLY
yesno "Allow problem management writes (create / update / resolve problems, link incidents to a problem)?" MCP_ENABLE_PROBLEM_WRITES; EN_PRBW=$REPLY
PRB_STATES="$(existing_value "$CONF" MCP_PROBLEM_STATES | grep . || echo "new=101;assess=102;root_cause_analysis=103;fix_in_progress=104;resolved=106;closed=107")"
if [[ $EN_PRBR == true || $EN_PRBW == true ]]; then
  echo "Problem state values differ between ServiceNow releases (current default: 101-107)."
  ask "Problem state values (name=value;...)" "$PRB_STATES"; PRB_STATES="$REPLY"
  prb_re='^[a-z_]+=[0-9]{1,4}(;[a-z_]+=[0-9]{1,4})*$'
  [[ $PRB_STATES =~ $prb_re ]] || { echo "Use the form resolved=106;closed=107" >&2; exit 1; }
fi
if [[ $EN_PRBW == true && -z ${TEAMS:-} ]]; then
  echo "Note: without MCP_ASSIGNMENT_GROUPS (teams) the agent cannot assign problems to a team."
fi
yesno "Allow reading change requests (search_change_requests)?" MCP_ENABLE_CHANGE_READ; EN_CHGR=$REPLY
yesno "Allow change management writes (create / edit changes, move them through phases, close or cancel)?" MCP_ENABLE_CHANGE_WRITES; EN_CHGW=$REPLY
CHG_STATES="$(existing_value "$CONF" MCP_CHANGE_STATES | grep . || echo "new=-5;assess=-4;authorize=-3;scheduled=-2;implement=-1;review=0;closed=3;canceled=4")"
CHG_TYPES="$(existing_value "$CONF" MCP_CHANGE_CREATE_TYPES | grep . || echo "normal,standard")"
if [[ $EN_CHGR == true || $EN_CHGW == true ]]; then
  echo "Change phase state values differ between ServiceNow releases (current default: new=-5 ... review=0, closed=3, canceled=4)."
  ask "Change state values (name=value;...)" "$CHG_STATES"; CHG_STATES="$REPLY"
  chg_re='^[a-z_]+=-?[0-9]{1,4}(;[a-z_]+=-?[0-9]{1,4})*$'
  [[ $CHG_STATES =~ $chg_re ]] || { echo "Use the form closed=3;canceled=4" >&2; exit 1; }
fi
if [[ $EN_CHGW == true ]]; then
  ask "Change types the agent may create (comma list; emergency is excluded by default)" "$CHG_TYPES"; CHG_TYPES="$REPLY"
  chgt_re='^[a-z]+(,[a-z]+)*$'
  [[ $CHG_TYPES =~ $chgt_re ]] || { echo "Use a comma list such as normal,standard" >&2; exit 1; }
  if [[ -z ${TEAMS:-} ]]; then
    echo "Note: without MCP_ASSIGNMENT_GROUPS (teams) the agent cannot assign changes to a team."
  fi
fi
# --- write files atomically, owner root, restrictive modes -----------------------
tmp_conf="$(mktemp "$CONF_DIR/.config.XXXXXX")"
cat > "$tmp_conf" <<EOF
# Managed by configure.sh (non-secret). See config.env.example for all options.
SERVICENOW_INSTANCE_URL=$URL
SERVICENOW_ALLOW_CUSTOM_DOMAIN=$ALLOW_CUSTOM
MCP_PUBLIC_HOSTNAME=$HOSTNAME_PUB
MCP_BIND_HOST=$BIND
MCP_BIND_PORT=$PORT
MCP_BEHIND_TLS_PROXY=$BEHIND
${TLS_LINES}
MCP_REQUEST_TIMEOUT=$(existing_value "$CONF" MCP_REQUEST_TIMEOUT | grep . || echo 15)
MCP_MAX_PAGE_SIZE=$(existing_value "$CONF" MCP_MAX_PAGE_SIZE | grep . || echo 50)
MCP_RATE_LIMIT_PER_MINUTE=$(existing_value "$CONF" MCP_RATE_LIMIT_PER_MINUTE | grep . || echo 60)
MCP_MAX_TEXT_CHARS=$(existing_value "$CONF" MCP_MAX_TEXT_CHARS | grep . || echo 4000)
MCP_LOG_LEVEL=$(existing_value "$CONF" MCP_LOG_LEVEL | grep . || echo INFO)
MCP_ENABLE_INCIDENT_WRITES=$EN_INC
MCP_ENABLE_INCIDENT_ASSIGNMENT=$EN_ASSIGN
MCP_ASSIGNMENT_GROUPS="$TEAMS"
MCP_ENABLE_CHANGE_APPROVAL=$EN_APPR
MCP_APPROVAL_ALLOWED_TYPES=$APPR_TYPES
MCP_ENABLE_ONCALL_READ=$EN_OCR
MCP_ENABLE_ONCALL_WRITES=$EN_OCW
MCP_ENABLE_JOURNAL_READ=$EN_JRN
MCP_ENABLE_CMDB_READ=$EN_CMDB
MCP_ENABLE_PROBLEM_READ=$EN_PRBR
MCP_ENABLE_PROBLEM_WRITES=$EN_PRBW
MCP_PROBLEM_STATES="$PRB_STATES"
MCP_ENABLE_CHANGE_READ=$EN_CHGR
MCP_ENABLE_CHANGE_WRITES=$EN_CHGW
MCP_CHANGE_STATES="$CHG_STATES"
MCP_CHANGE_CREATE_TYPES="$CHG_TYPES"
EOF
chown root:root "$tmp_conf"; chmod 0644 "$tmp_conf"

tmp_sec="$(mktemp "$CONF_DIR/.secrets.XXXXXX")"
{
  echo "# Managed by configure.sh. root:root 0600. Contains secrets - do not copy or commit."
  echo "SERVICENOW_USERNAME=\"$(dq "$SN_USER")\""
  if [[ $SN_PASS_ESCAPED == 1 ]]; then echo "SERVICENOW_PASSWORD=\"$SN_PASS\""; else echo "SERVICENOW_PASSWORD=\"$(dq "$SN_PASS")\""; fi
  echo "MCP_AUTH_TOKEN_SHA256=\"$cur_hash\""
  if [[ -n $prev_hash ]]; then echo "MCP_AUTH_TOKEN_SHA256_PREVIOUS=\"$prev_hash\""; fi
} > "$tmp_sec"
unset SN_PASS
chown root:root "$tmp_sec"; chmod 0600 "$tmp_sec"
mv -f "$tmp_conf" "$CONF"; mv -f "$tmp_sec" "$SECRETS"

# Validate with the real loader (prints only non-secret error text).
if ! systemd-run --quiet --pipe --wait --collect -p User=servicenow-mcp \
     -p EnvironmentFile=$CONF -p EnvironmentFile=$SECRETS \
     "$VENV_PY" -I -c 'from servicenow_mcp.config import load_settings; load_settings(); print("configuration valid")'; then
  echo "Configuration did not validate; fix the message above and re-run." >&2; exit 1
fi

if [[ -n $NEW_TOKEN ]]; then
  cat <<EOF

=========================================================================
Generated MCP bearer token (shown ONCE; the server stores only its hash):

  $NEW_TOKEN

Put it in Hermes' ~/.hermes/.env as SERVICENOW_MCP_TOKEN (see README), then
clear this terminal's scrollback. It cannot be recovered - rotate if lost.
=========================================================================
EOF
  unset NEW_TOKEN
fi
echo
echo "Saved."
if [[ $BEHIND == true ]]; then
  echo "Add the Traefik route (see /etc/servicenow-mcp/examples/traefik-dynamic.yml.example), pointing at http://$BIND:$PORT."
  echo "Hermes URL: https://$HOSTNAME_PUB/mcp"
else
  echo "Native TLS on port $PORT. Hermes URL: https://$HOSTNAME_PUB:$PORT/mcp"
fi
