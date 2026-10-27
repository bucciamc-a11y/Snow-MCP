#!/usr/bin/env bash
# Prepare a VPS: service account, isolated venv, protected config dir, systemd unit.
# Run as root from the project directory:  sudo ./scripts/install.sh
# Idempotent. Does NOT touch firewall rules, does NOT start the service.
set -euo pipefail

SVC_USER=servicenow-mcp
APP_DIR=/opt/servicenow-mcp
CONF_DIR=/etc/servicenow-mcp
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

[[ $EUID -eq 0 ]] || { echo 'Run as root (e.g. "su -" then bash scripts/install.sh)' >&2; exit 1; }
[[ -f "$SRC_DIR/pyproject.toml" ]] || { echo "Run from the project directory" >&2; exit 1; }
command -v systemctl >/dev/null || { echo "systemd is required" >&2; exit 1; }

# --- Python >= 3.11 -----------------------------------------------------------
find_python() {
  for c in python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
      command -v "$c"; return 0
    fi
  done
  return 1
}
PY="$(find_python || true)"
if [[ -z "$PY" ]]; then
  echo "Python 3.11+ not found. Install it (e.g. 'apt install python3.12 python3.12-venv' on Ubuntu 22.04+/Debian 12+) and re-run." >&2
  exit 1
fi
if ! "$PY" -c 'import venv, ensurepip' 2>/dev/null; then
  if command -v apt-get >/dev/null; then
    echo "Installing the python venv package via apt..."
    apt-get update -qq
    apt-get install -y "$(basename "$PY")-venv" || apt-get install -y python3-venv
  else
    echo "Python venv/ensurepip module missing; install your distro's python3-venv package." >&2
    exit 1
  fi
fi
echo "Using $PY ($("$PY" --version))"

# --- unprivileged service account ----------------------------------------------
if ! id "$SVC_USER" >/dev/null 2>&1; then
  useradd --system --user-group --no-create-home --home-dir /nonexistent \
          --shell /usr/sbin/nologin --comment "ServiceNow MCP server" "$SVC_USER"
  echo "Created system user $SVC_USER"
fi

# --- application + venv (root-owned, read-only to the service) ------------------
install -d -m 0755 -o root -g root "$APP_DIR" "$APP_DIR/app"
for item in src pyproject.toml README.md; do
  rm -rf "$APP_DIR/app/$item"
  cp -a "$SRC_DIR/$item" "$APP_DIR/app/$item"
done
if [[ ! -x "$APP_DIR/venv/bin/python" ]]; then
  "$PY" -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/python" -m pip install --quiet --upgrade pip
"$APP_DIR/venv/bin/python" -m pip install --quiet --upgrade "$APP_DIR/app"
chown -R root:root "$APP_DIR"
chmod -R go-w "$APP_DIR"

# --- protected configuration ----------------------------------------------------
install -d -m 0755 -o root -g root "$CONF_DIR"
if [[ ! -e "$CONF_DIR/config.env" ]]; then
  install -m 0644 -o root -g root "$SRC_DIR/config/config.env.example" "$CONF_DIR/config.env"
fi
if [[ ! -e "$CONF_DIR/secrets.env" ]]; then
  ( umask 077; : > "$CONF_DIR/secrets.env" )
fi
chown root:root "$CONF_DIR/secrets.env"; chmod 0600 "$CONF_DIR/secrets.env"
install -d -m 0755 -o root -g root "$CONF_DIR/examples"
install -m 0644 "$SRC_DIR"/config/Caddyfile.example "$SRC_DIR"/config/nginx.conf.example "$SRC_DIR"/config/traefik-dynamic.yml.example "$CONF_DIR/examples/"
install -d -m 0750 -o root -g root "$APP_DIR/scripts"
install -m 0750 "$SRC_DIR"/scripts/*.sh "$APP_DIR/scripts/"

# --- systemd --------------------------------------------------------------------
install -m 0644 -o root -g root "$SRC_DIR/deploy/servicenow-mcp.service" /etc/systemd/system/servicenow-mcp.service
systemctl daemon-reload
systemctl disable servicenow-mcp.service >/dev/null 2>&1 || true   # enabled explicitly after configuration

cat <<EOF

Install complete. The service is NOT enabled or started yet.
Next:
  $APP_DIR/scripts/configure.sh     # prompts for URL, ServiceNow account, MCP token
  $APP_DIR/scripts/check.sh         # verify ServiceNow connectivity/permissions
  systemctl enable --now servicenow-mcp
Then put a TLS reverse proxy in front (see $CONF_DIR/examples and README.md).
EOF
