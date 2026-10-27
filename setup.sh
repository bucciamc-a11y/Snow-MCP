#!/usr/bin/env bash
# One entry point. Copy this project to the server, then:   sudo bash setup.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ $EUID -eq 0 ]] || { echo "Run as root: sudo bash setup.sh" >&2; exit 1; }

echo "ServiceNow MCP server setup"
echo "  1) Docker container behind your existing Traefik (recommended, portable)"
echo "  2) systemd service on the host (Traefik routes to a private address)"
read -r -p "Choose [1]: " mode; mode="${mode:-1}"
case "$mode" in
  1) exec bash "$HERE/scripts/docker-setup.sh" ;;
  2) bash "$HERE/scripts/install.sh"
     bash /opt/servicenow-mcp/scripts/configure.sh
     bash /opt/servicenow-mcp/scripts/check.sh || echo "!! check failed; see README"
     read -r -p "Enable and start the service now? [Y/n]: " yn
     if [[ ! $yn =~ ^[Nn]$ ]]; then systemctl enable --now servicenow-mcp; systemctl --no-pager status servicenow-mcp | head -n 8; fi
     bash "$HERE/scripts/hermes-instructions.sh" ;;
  *) echo "Invalid choice" >&2; exit 1 ;;
esac
