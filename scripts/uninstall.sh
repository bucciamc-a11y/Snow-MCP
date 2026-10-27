#!/usr/bin/env bash
# Remove the service. Pass --purge to also delete configuration/secrets and the service user.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
PURGE=false; [[ ${1:-} == "--purge" ]] && PURGE=true

systemctl disable --now servicenow-mcp.service 2>/dev/null || true
rm -f /etc/systemd/system/servicenow-mcp.service
systemctl daemon-reload
rm -rf /opt/servicenow-mcp
echo "Removed service and application files."

if $PURGE; then
  # Overwrite best-effort before unlinking (not a guarantee on journaling/SSD filesystems).
  if [[ -f /etc/servicenow-mcp/secrets.env ]]; then shred -u /etc/servicenow-mcp/secrets.env 2>/dev/null || rm -f /etc/servicenow-mcp/secrets.env; fi
  rm -rf /etc/servicenow-mcp
  id servicenow-mcp >/dev/null 2>&1 && userdel servicenow-mcp 2>/dev/null || true
  echo "Purged configuration, secrets and service user."
  echo "REMINDER: disable/delete the ServiceNow integration user and discard the Hermes token."
else
  echo "Configuration kept in /etc/servicenow-mcp (use --purge to delete it)."
fi
