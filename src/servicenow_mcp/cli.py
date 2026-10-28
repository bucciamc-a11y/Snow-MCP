"""Helper commands.

  python -m servicenow_mcp.cli hash-token     read a token on stdin, print its SHA-256
  python -m servicenow_mcp.cli gen-token      print a new random token (and nothing else)
  python -m servicenow_mcp.cli check          verify ServiceNow connectivity/permissions

Secrets are only ever read from stdin or the environment, never argv.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys

from .auth import hash_token
from .client import ServiceNowClient
from .config import ConfigError, _bool, load_servicenow_settings


async def _check() -> int:
    try:
        url, user, password = load_servicenow_settings()
    except ConfigError as exc:
        print(f"FAIL configuration: {exc}")
        return 2
    client = ServiceNowClient(url, user, password, timeout=15.0)
    try:
        host = url.removeprefix("https://")
        print(f"instance: {host}")
        tables = ["incident", "change_request"]
        if _bool(os.environ, "MCP_ENABLE_CHANGE_APPROVAL"):
            tables.append("sysapproval_approver")
        if _bool(os.environ, "MCP_ENABLE_ONCALL_READ") or _bool(os.environ, "MCP_ENABLE_ONCALL_WRITES"):
            tables += ["cmn_rota", "cmn_rota_roster", "cmn_rota_member"]
        if _bool(os.environ, "MCP_ENABLE_PROBLEM_READ") or _bool(os.environ, "MCP_ENABLE_PROBLEM_WRITES"):
            tables.append("problem")
        if _bool(os.environ, "MCP_ENABLE_CATALOG_READ") or _bool(os.environ, "MCP_ENABLE_CATALOG_WRITES"):
            tables += ["sc_cat_item", "sc_category", "item_option_new"]
        results = await client.check_connectivity(tuple(tables))
    finally:
        await client.aclose()
    hints = {
        "auth_failed": "credentials rejected - check username/password and that the account is not locked or web-service-disabled",
        "forbidden": "missing role/ACL - see README 'Required ServiceNow permissions'",
        "tls_error": "TLS verification failed - check the instance URL",
        "network": "cannot reach instance - check DNS/egress firewall",
        "unavailable": "timeout or 5xx - instance may be hibernating or unavailable",
        "bad_response": "not a ServiceNow REST response - check the instance URL",
    }
    print("note: only READ access is probed here; write permissions are never tested by the check.")
    failed = False
    for table, outcome in results.items():
        if outcome == "ok":
            print(f"PASS read {table}")
        else:
            failed = True
            print(f"FAIL read {table}: {outcome} - {hints.get(outcome, 'see server logs')}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    cmd = args[0] if args else ""
    if cmd == "gen-token":
        print(secrets.token_urlsafe(48))
        return 0
    if cmd == "hash-token":
        token = sys.stdin.readline().rstrip("\r\n")
        if len(token) < 32:
            print("token must be at least 32 characters", file=sys.stderr)
            return 2
        print(hash_token(token))
        return 0
    if cmd == "check":
        return asyncio.run(_check())
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
