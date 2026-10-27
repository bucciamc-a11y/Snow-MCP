import functools
import json
import logging
from contextlib import asynccontextmanager

import httpx
import pytest
from conftest import INSTANCE, SN_PASSWORD, SN_USER, TOKEN, base_env

from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import load_settings
from servicenow_mcp.logging_setup import JsonFormatter, quiet_noisy_loggers
from servicenow_mcp.server import create_app

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "X-Forwarded-Proto": "https",
    "Host": "mcp.example.com",
}


def sn_handler(request: httpx.Request):
    if request.url.path.endswith("/incident"):
        return httpx.Response(200, json={"result": [{"number": "INC0010001", "short_description": "VPN"}]},
                              headers={"X-Total-Count": "1"})
    return httpx.Response(404, json={"error": {"message": "nope"}})


@asynccontextmanager
async def open_stack():
    settings = load_settings(base_env())
    sn = ServiceNowClient(INSTANCE, SN_USER, SN_PASSWORD, transport=httpx.MockTransport(sn_handler))
    app, inner, _ = create_app(settings, sn)
    transport = httpx.ASGITransport(app=app)
    async with (
        inner.router.lifespan_context(inner),
        httpx.AsyncClient(transport=transport, base_url="https://mcp.example.com") as c,
    ):
        yield c
    await sn.aclose()


def with_stack(fn):
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        async with open_stack() as stack:
            await fn(stack, *args, **kwargs)

    # keep parametrize marks working while hiding the injected argument from pytest
    import inspect
    sig = inspect.signature(fn)
    wrapper.__signature__ = sig.replace(parameters=list(sig.parameters.values())[1:])
    return wrapper


async def rpc(c, method, params=None, id_=1, headers=None):
    body = {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}
    r = await c.post("/mcp", headers=headers or HEADERS, content=json.dumps(body))
    return r


async def call(c, name, args):
    r = await rpc(c, "tools/call", {"name": name, "arguments": args})
    assert r.status_code == 200, r.text
    return r.json()["result"]


@with_stack
async def test_unauthenticated_mcp_rejected(stack):
    r = await rpc(stack, "tools/list", headers={k: v for k, v in HEADERS.items() if k != "Authorization"})
    assert r.status_code == 401


@with_stack
async def test_tool_list_is_exactly_the_three_read_only_tools(stack):
    r = await rpc(stack, "tools/list")
    tools = {t["name"]: t for t in r.json()["result"]["tools"]}
    assert set(tools) == {"search_incidents", "get_incident", "get_change_request"}
    for t in tools.values():
        assert t["annotations"]["readOnlyHint"] is True and t["annotations"]["destructiveHint"] is False
    assert all(n.startswith(("search_", "get_")) for n in tools)
    banned = ("create", "update", "delete", "approve", "script", "execute", "post", "put")
    assert not any(b in n for n in tools for b in banned)


@with_stack
async def test_search_tool_end_to_end(stack):
    res = await call(stack, "search_incidents", {"state": "new", "limit": 5})
    assert not res.get("isError")
    data = json.loads(res["content"][0]["text"])
    assert data["incidents"][0]["number"] == "INC0010001" and data["next_offset"] is None


@with_stack
async def test_not_found_is_safe_error(stack):
    res = await call(stack, "get_change_request", {"identifier": "CHG0030001"})
    assert res["isError"] and "not found or not accessible" in res["content"][0]["text"]
    assert "nope" not in res["content"][0]["text"]


@pytest.mark.parametrize("name,args", [
    ("get_incident", {"identifier": "INC1^ORactive=true"}),
    ("get_incident", {"identifier": "../../api/now/table/sys_user"}),
    ("search_incidents", {"short_description_contains": "a^ORb"}),
    ("search_incidents", {"limit": 1000}),
    ("search_incidents", {"state": "bogus"}),
])
@with_stack
async def test_invalid_inputs_are_rejected(stack, name, args):
    r = await rpc(stack, "tools/call", {"name": name, "arguments": args})
    assert r.status_code == 200
    assert r.json()["result"]["isError"] is True


@with_stack
async def test_unknown_tool_rejected(stack):
    r = await rpc(stack, "tools/call", {"name": "run_arbitrary_request", "arguments": {"path": "/api/now/table/sys_user"}})
    assert r.json().get("error") or r.json()["result"]["isError"]


@with_stack
async def test_dns_rebinding_protection(stack):
    r = await rpc(stack, "tools/list", headers={**HEADERS, "Host": "evil.example.net"})
    assert r.status_code in (400, 421)


@with_stack
async def test_audit_log_has_no_secrets_or_free_text(stack, caplog):
    caplog.set_level(logging.INFO)
    quiet_noisy_loggers()
    await call(stack, "search_incidents", {"short_description_contains": "confidential-needle", "limit": 3})
    await call(stack, "get_incident", {"identifier": "INC0010001"})
    fmt = JsonFormatter()  # production silences httpx/mcp loggers (see logging_setup)
    lines = [fmt.format(r) for r in caplog.records]
    blob = "\n".join(lines)
    assert "tool_call" in blob and "search_incidents" in blob
    for secret in (SN_PASSWORD, TOKEN, "confidential-needle", "Basic "):
        assert secret not in blob
    entries = [json.loads(line) for line in lines if '"tool_call"' in line]
    assert all(e["outcome"] == "ok" for e in entries)

