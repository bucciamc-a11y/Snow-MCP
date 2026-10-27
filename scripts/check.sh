#!/usr/bin/env bash
# Verify ServiceNow connectivity and read permissions using the real configuration.
# Runs as the unprivileged service user in a sandbox; prints PASS/FAIL only, never secrets.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
exec systemd-run --quiet --pipe --wait --collect \
  -p User=servicenow-mcp -p EnvironmentFile=/etc/servicenow-mcp/config.env \
  -p EnvironmentFile=/etc/servicenow-mcp/secrets.env \
  -p NoNewPrivileges=yes -p PrivateTmp=yes -p ProtectSystem=strict -p ProtectHome=yes \
  /opt/servicenow-mcp/venv/bin/python -I -m servicenow_mcp.cli check
