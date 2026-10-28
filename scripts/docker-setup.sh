#!/usr/bin/env bash
# Docker deployment: prompts for settings/credentials, writes docker/.env and docker/secrets/*,
# builds the image and starts the container on Traefik's docker network.
# Run as root from anywhere:  sudo bash scripts/docker-setup.sh
# Secrets are read with echo off, written to 0400 files (owner = container uid), never put in argv.
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DK="$ROOT/docker"
SEC="$DK/secrets"
ENVF="$DK/.env"
IMAGE=servicenow-mcp:2.0.0
COMPOSE=(docker compose -f "$DK/docker-compose.yml" --env-file "$ENVF")

[[ $EUID -eq 0 ]] || { echo "Run as root (needed to set secret-file ownership)" >&2; exit 1; }
[[ -t 0 ]] || { echo "Run from an interactive terminal" >&2; exit 1; }
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. On Debian 13 (trixie) install Docker Engine + the compose plugin from Docker's" >&2
  echo "apt repository (docker-ce, docker-ce-cli, containerd.io, docker-compose-plugin):" >&2
  echo "  https://docs.docker.com/engine/install/debian/   then re-run this script." >&2
  exit 1
fi
docker compose version >/dev/null 2>&1 || { echo "'docker compose' plugin missing (apt-get install docker-compose-plugin from Docker's Debian repo)" >&2; exit 1; }

existing() { local v; v="$(grep -E "^$1=" "$ENVF" 2>/dev/null | tail -n1 | cut -d= -f2- || true)"; v="${v#\"}"; v="${v%\"}"; printf '%s' "$v"; }
ask() { local def="$2" ans; if [[ -n $def ]]; then read -r -p "$1 [$def]: " ans; else read -r -p "$1: " ans; fi; REPLY="${ans:-$def}"; }
reject_ctl() { if printf '%s' "$1" | LC_ALL=C grep -q '[[:cntrl:]]'; then echo "Control characters are not allowed." >&2; exit 1; fi; }

# --- detect Traefik defaults (best effort) ----------------------------------------
tr_container="$(docker ps --format '{{.Names}} {{.Image}}' | awk 'tolower($0) ~ /traefik/ {print $1; exit}' || true)"
def_net="$(existing TRAEFIK_NETWORK)"; def_res="$(existing TRAEFIK_CERTRESOLVER)"
if [[ -n $tr_container ]]; then
  echo "Found Traefik container: $tr_container"
  [[ -z $def_net ]] && def_net="$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$tr_container" | awk '{print $1}')"
  [[ -z $def_res ]] && def_res="$(docker inspect -f '{{join .Args " "}} {{join .Config.Cmd " "}}' "$tr_container" 2>/dev/null \
      | grep -o 'certificatesresolvers\.[A-Za-z0-9_-]*' | head -n1 | cut -d. -f2 || true)"
else
  echo "No running Traefik container detected; you will be asked for its network and resolver names."
fi

echo "== ServiceNow =="
ask "Instance URL (https://<name>.service-now.com)" "$(existing SERVICENOW_INSTANCE_URL | grep -v your-instance || true)"; URL="${REPLY%/}"
[[ $URL =~ ^https://[A-Za-z0-9.-]+$ ]] || { echo "URL must look like https://name.service-now.com" >&2; exit 1; }
ALLOW_CUSTOM=false
if [[ ! $URL =~ \.(service-now\.com|servicenowservices\.com)$ ]]; then
  read -r -p "Host is not *.service-now.com; allow this custom domain? [y/N]: " yn
  [[ $yn =~ ^[Yy]$ ]] && ALLOW_CUSTOM=true || { echo "Aborted." >&2; exit 1; }
fi
cur_user=""; [[ -f $SEC/servicenow_username ]] && cur_user="$(cat "$SEC/servicenow_username")"
read -r -p "Integration username [${cur_user:+keep existing}]: " SN_USER; SN_USER="${SN_USER:-$cur_user}"
[[ -n $SN_USER ]] || { echo "Username required" >&2; exit 1; }; reject_ctl "$SN_USER"
read -r -s -p "Integration password [Enter keeps existing]: " SN_PASS; echo
if [[ -z $SN_PASS && -f $SEC/servicenow_password ]]; then SN_PASS="$(cat "$SEC/servicenow_password")"; fi
[[ -n $SN_PASS ]] || { echo "Password required" >&2; exit 1; }; reject_ctl "$SN_PASS"

echo; echo "== MCP endpoint / Traefik =="
echo "Host name of THIS server as Hermes reaches it (e.g. mcp.yourcompany.ca); a pasted https://host/mcp URL is also accepted."
cur_host="$(existing MCP_PUBLIC_HOSTNAME | grep . || true)"; [[ $cur_host == mcp.example.com ]] && cur_host=""
ask "MCP server host name" "$cur_host"; HOST_PUB="$REPLY"
[[ -n $HOST_PUB ]] || { echo "Host name required" >&2; exit 1; }
HOST_PUB="${HOST_PUB#*://}"; HOST_PUB="${HOST_PUB%%/*}"; HOST_PUB="${HOST_PUB%%:*}"
[[ $HOST_PUB =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$ ]] || { echo "Invalid host name" >&2; exit 1; }
HOST_PUB="$(printf '%s' "$HOST_PUB" | tr '[:upper:]' '[:lower:]')"
ask "Traefik docker network name" "${def_net:-traefik}"; TR_NET="$REPLY"
ask "Traefik HTTPS entrypoint name" "$(existing TRAEFIK_ENTRYPOINT | grep . || echo websecure)"; TR_EP="$REPLY"
ask "Traefik certificate resolver name" "${def_res:-letsencrypt}"; TR_RES="$REPLY"
for v in "$TR_NET" "$TR_EP" "$TR_RES"; do [[ $v =~ ^[A-Za-z0-9._@-]+$ ]] || { echo "Invalid Traefik name: $v" >&2; exit 1; }; done
docker network inspect "$TR_NET" >/dev/null 2>&1 || { echo "Docker network '$TR_NET' does not exist (it must be Traefik's network)." >&2; exit 1; }

# --- optional write capabilities (all OFF unless enabled here) -------------------------
echo; echo "== Capabilities =="
echo "Read tools are always on. Each WRITE feature below is off unless you enable it, and every write"
echo "tool previews first and only applies after the agent calls it again with confirm=true."
echo "Each needs matching ServiceNow roles/ACLs on the integration account (see README)."
yesno() { # yesno "Question" CONFIG_KEY -> REPLY=true|false (default = current setting, else no)
  local cur def ans; cur="$(existing "$2")"
  if [[ $cur == true ]]; then def=Y; else def=N; fi
  read -r -p "$1 [$([[ $def == Y ]] && echo Y/n || echo y/N)]: " ans; ans="${ans:-$def}"
  if [[ $ans =~ ^[Yy]$ ]]; then REPLY=true; else REPLY=false; fi
}
yesno "Allow create / update / resolve incidents?" MCP_ENABLE_INCIDENT_WRITES; EN_INC=$REPLY
yesno "Allow triage + assigning incidents to teams you list?" MCP_ENABLE_INCIDENT_ASSIGNMENT; EN_ASSIGN=$REPLY
TEAMS=""
if [[ $EN_ASSIGN == true ]]; then
  TEAMS="$(existing MCP_ASSIGNMENT_GROUPS)"
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
APPR_TYPES="$(existing MCP_APPROVAL_ALLOWED_TYPES | grep . || echo standard,normal)"
if [[ $EN_APPR == true ]]; then
  ask "Change types that may be approved (comma list; emergency is excluded by default)" "$APPR_TYPES"; APPR_TYPES="$REPLY"
  [[ $APPR_TYPES =~ ^[a-z][a-z,\ _-]*$ ]] || { echo "Use lowercase names such as standard,normal" >&2; exit 1; }
fi
yesno "Allow reading the on-call schedule (rotas, rosters, who is on call)?" MCP_ENABLE_ONCALL_READ; EN_OCR=$REPLY
yesno "Allow editing on-call roster members (add member, change order/valid dates)?" MCP_ENABLE_ONCALL_WRITES; EN_OCW=$REPLY
yesno "Allow reading problem records (search_problems, get_problem)?" MCP_ENABLE_PROBLEM_READ; EN_PRBR=$REPLY
yesno "Allow problem management writes (create / update / resolve problems, link incidents to a problem)?" MCP_ENABLE_PROBLEM_WRITES; EN_PRBW=$REPLY
PRB_STATES="$(existing MCP_PROBLEM_STATES | grep . || echo "new=101;assess=102;root_cause_analysis=103;fix_in_progress=104;resolved=106;closed=107")"
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
CHG_STATES="$(existing MCP_CHANGE_STATES | grep . || echo "new=-5;assess=-4;authorize=-3;scheduled=-2;implement=-1;review=0;closed=3;canceled=4")"
CHG_TYPES="$(existing MCP_CHANGE_CREATE_TYPES | grep . || echo "normal,standard")"
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
# --- write env + secrets --------------------------------------------------------------
cat > "$ENVF" <<EOF
# Non-secret compose settings, generated by docker-setup.sh
SERVICENOW_INSTANCE_URL=$URL
SERVICENOW_ALLOW_CUSTOM_DOMAIN=$ALLOW_CUSTOM
MCP_PUBLIC_HOSTNAME=$HOST_PUB
MCP_LOG_LEVEL=INFO
TRAEFIK_NETWORK=$TR_NET
TRAEFIK_ENTRYPOINT=$TR_EP
TRAEFIK_CERTRESOLVER=$TR_RES
MCP_ENABLE_INCIDENT_WRITES=$EN_INC
MCP_ENABLE_INCIDENT_ASSIGNMENT=$EN_ASSIGN
MCP_ASSIGNMENT_GROUPS="$TEAMS"
MCP_ENABLE_CHANGE_APPROVAL=$EN_APPR
MCP_APPROVAL_ALLOWED_TYPES=$APPR_TYPES
MCP_ENABLE_ONCALL_READ=$EN_OCR
MCP_ENABLE_ONCALL_WRITES=$EN_OCW
MCP_ENABLE_PROBLEM_READ=$EN_PRBR
MCP_ENABLE_PROBLEM_WRITES=$EN_PRBW
MCP_PROBLEM_STATES="$PRB_STATES"
MCP_ENABLE_CHANGE_READ=$EN_CHGR
MCP_ENABLE_CHANGE_WRITES=$EN_CHGW
MCP_CHANGE_STATES="$CHG_STATES"
MCP_CHANGE_CREATE_TYPES="$CHG_TYPES"
EOF
chmod 0644 "$ENVF"
install -d -m 0700 -o root -g root "$SEC"
write_secret() { printf '%s' "$2" > "$SEC/$1.tmp"; chown 10001:10001 "$SEC/$1.tmp"; chmod 0400 "$SEC/$1.tmp"; mv -f "$SEC/$1.tmp" "$SEC/$1"; }
write_secret servicenow_username "$SN_USER"
write_secret servicenow_password "$SN_PASS"
unset SN_PASS

echo; echo "Building image..."
"${COMPOSE[@]}" build --quiet

# --- MCP bearer token (only the SHA-256 is stored) ---------------------------------------
NEW_TOKEN=""
if [[ -f $SEC/mcp_auth_token_sha256 ]]; then
  read -r -p "An MCP token already exists. Generate a new one (Hermes must be updated)? [y/N]: " yn
  [[ $yn =~ ^[Yy]$ ]] || KEEP_TOKEN=1
fi
if [[ -z ${KEEP_TOKEN:-} ]]; then
  read -r -s -p "MCP bearer token (min 32 chars) [Enter generates a random one]: " TOKEN_IN; echo
  if [[ -z $TOKEN_IN ]]; then NEW_TOKEN="$(docker run --rm --network none "$IMAGE" python -I -m servicenow_mcp.cli gen-token)"; TOKEN_IN="$NEW_TOKEN"; fi
  reject_ctl "$TOKEN_IN"
  HASH="$(printf '%s\n' "$TOKEN_IN" | docker run --rm -i --network none "$IMAGE" python -I -m servicenow_mcp.cli hash-token)"
  unset TOKEN_IN
  [[ $HASH =~ ^[0-9a-f]{64}$ ]] || { echo "Failed to derive token digest" >&2; exit 1; }
  write_secret mcp_auth_token_sha256 "$HASH"
fi

echo; echo "Verifying ServiceNow connectivity/permissions..."
"${COMPOSE[@]}" run --rm --no-deps --entrypoint "" servicenow-mcp python -I -m servicenow_mcp.cli check || \
  echo "!! Check failed - see README 'Required ServiceNow permissions'. The container will still start; fix and 'docker compose restart'."

"${COMPOSE[@]}" up -d
echo
echo "Container started. Hermes URL: https://$HOST_PUB/mcp"
if [[ -n $NEW_TOKEN ]]; then
  cat <<EOF

=========================================================================
Generated MCP bearer token (shown ONCE; only its hash is stored):

  $NEW_TOKEN

Put it in Hermes' ~/.hermes/.env as SERVICENOW_MCP_TOKEN, then clear this
terminal's scrollback. It cannot be recovered - rerun this script to rotate.
=========================================================================
EOF
fi

bash "$ROOT/scripts/hermes-instructions.sh" "$HOST_PUB"
