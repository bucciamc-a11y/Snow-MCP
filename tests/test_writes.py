"""Write-capable tools: gating, preview/confirm, guards, allowlists, client write safety."""

import functools
import inspect
import json
import logging
from contextlib import asynccontextmanager

import httpx
import pytest
from conftest import INSTANCE, SN_PASSWORD, SN_USER, TOKEN, base_env

from servicenow_mcp.client import ServiceNowClient, ServiceNowError
from servicenow_mcp.config import ConfigError, load_settings
from servicenow_mcp.logging_setup import JsonFormatter, quiet_noisy_loggers
from servicenow_mcp.server import create_app
from servicenow_mcp.validation import (
    ValidationError,
    build_incident_fields,
    build_incident_resolution,
    build_oncall_member_fields,
    validate_date,
    validate_text,
)

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "X-Forwarded-Proto": "https",
    "Host": "mcp.example.com",
}
INC = "a" * 32
CHG = "c" * 32
APPR = "d" * 32
TEAM_NET = "1" * 32
TEAM_DESK = "2" * 32
ROSTER = "3" * 32
USER = "4" * 32
MEMBER = "5" * 32
ALL_ON = {
    "MCP_ENABLE_INCIDENT_WRITES": "true",
    "MCP_ENABLE_INCIDENT_ASSIGNMENT": "true",
    "MCP_ASSIGNMENT_GROUPS": f"Network Ops={TEAM_NET};Service Desk={TEAM_DESK}",
    "MCP_ENABLE_CHANGE_APPROVAL": "true",
    "MCP_ENABLE_ONCALL_WRITES": "true",
}


class FakeServiceNow:
    """Small stateful stand-in for the ServiceNow Table API; records every request."""

    def __init__(self, incident_state="2", change_type="Normal", pending=True):
        self.calls: list[httpx.Request] = []
        self.incident_state = incident_state
        self.change_type = change_type
        self.pending = pending

    def writes(self):
        return [c for c in self.calls if c.method != "GET"]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        path, q = req.url.path, dict(req.url.params)
        body = json.loads(req.content) if req.content else {}
        raw = q.get("sysparm_display_value") == "false"
        if path.startswith("/api/now/on_call_rota"):
            return httpx.Response(200, json={"result": [{"user": "Pat Oncall", "group": "Network Ops"}]})
        table = path.split("/")[4] if path.startswith("/api/now/table/") else ""
        if req.method == "GET" and table == "incident":
            rec = {"sys_id": INC, "number": "INC0010001", "short_description": "VPN down",
                   "state": self.incident_state if raw else "In Progress"}
            return httpx.Response(200, json={"result": rec if path.endswith(INC) else [rec]},
                                  headers={"X-Total-Count": "1"})
        if req.method == "GET" and table == "change_request":
            rec = {"sys_id": CHG, "number": "CHG0030001", "type": self.change_type, "state": "Assess",
                   "short_description": "Patch"}
            return httpx.Response(200, json={"result": rec if path.endswith(CHG) else [rec]})
        if req.method == "GET" and table == "sysapproval_approver":
            rows = [{"sys_id": APPR}] if self.pending else []
            return httpx.Response(200, json={"result": rows})
        if req.method == "GET" and table == "cmn_rota":
            return httpx.Response(200, json={"result": [{"sys_id": "9" * 32, "name": "NetOps rota"}]})
        if req.method == "GET" and table == "cmn_rota_roster":
            return httpx.Response(200, json={"result": [{"sys_id": ROSTER, "name": "Primary"}]})
        if req.method == "GET" and table == "cmn_rota_member":
            rec = {"sys_id": MEMBER, "member": "Pat", "order": "100"}
            return httpx.Response(200, json={"result": rec if path.endswith(MEMBER) else [rec]})
        if req.method == "POST" and table == "incident":
            return httpx.Response(201, json={"result": {"sys_id": INC, "number": "INC0010002", **body}})
        if req.method == "PATCH":
            return httpx.Response(200, json={"result": {"sys_id": path.rsplit("/", 1)[1], **body}})
        if req.method == "POST" and table == "cmn_rota_member":
            return httpx.Response(201, json={"result": {"sys_id": MEMBER, **body}})
        return httpx.Response(404, json={"error": {"message": "no"}})


@asynccontextmanager
async def open_stack(fake=None, **env):
    fake = fake or FakeServiceNow()
    settings = load_settings(base_env(**env))
    sn = ServiceNowClient(INSTANCE, SN_USER, SN_PASSWORD, transport=httpx.MockTransport(fake))
    app, inner, _ = create_app(settings, sn)
    transport = httpx.ASGITransport(app=app)
    async with (
        inner.router.lifespan_context(inner),
        httpx.AsyncClient(transport=transport, base_url="https://mcp.example.com") as c,
    ):
        yield c, fake
    await sn.aclose()


def with_stack(**env):
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*a, **kw):
            fake = kw.pop("_fake", None)
            async with open_stack(fake, **env) as (c, f):
                await fn(c, f, *a, **kw)

        sig = inspect.signature(fn)
        wrapper.__signature__ = sig.replace(parameters=list(sig.parameters.values())[2:])
        return wrapper

    return deco


async def rpc(c, method, params=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return await c.post("/mcp", headers=HEADERS, content=json.dumps(body))


async def call(c, name, args):
    r = await rpc(c, "tools/call", {"name": name, "arguments": args})
    assert r.status_code == 200, r.text
    res = r.json().get("result")
    if res is None:
        return {"isError": True, "text": json.dumps(r.json())}
    text = res["content"][0]["text"]
    if res.get("isError"):
        return {"isError": True, "text": text}
    return json.loads(text)


def is_error(res, *needles):
    assert res.get("isError"), res
    return all(n in res["text"] for n in needles)


# ----------------------------------------------------------------- gating
@with_stack(**ALL_ON, MCP_ENABLE_ONCALL_READ="true")
async def test_all_tools_registered_when_enabled(c, fake):
    r = await rpc(c, "tools/list")
    tools = {t["name"]: t for t in r.json()["result"]["tools"]}
    assert set(tools) == {
        "search_incidents", "get_incident", "get_change_request",
        "create_incident", "update_incident", "resolve_incident",
        "list_assignment_teams", "assign_incident", "approve_change_request",
        "list_oncall_rotas", "get_oncall_roster", "whois_oncall",
        "add_oncall_member", "update_oncall_member",
    }
    # nothing exposes delete or a generic request tool, and no write tool can be read-only flagged
    assert not any(w in n for n in tools for w in ("delete", "remove", "execute", "script", "raw_request"))
    for name in ("create_incident", "update_incident", "resolve_incident", "assign_incident",
                 "approve_change_request", "add_oncall_member", "update_oncall_member"):
        assert tools[name]["annotations"]["readOnlyHint"] is False
        assert "confirm" in tools[name]["inputSchema"]["properties"]
        assert tools[name]["inputSchema"]["properties"]["confirm"].get("default") is False


@with_stack(MCP_ENABLE_INCIDENT_WRITES="true")
async def test_features_are_independent(c, fake):
    r = await rpc(c, "tools/list")
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert "create_incident" in names and "assign_incident" not in names
    assert "approve_change_request" not in names and "add_oncall_member" not in names


@with_stack(MCP_ENABLE_ONCALL_WRITES="true")
async def test_oncall_writes_imply_oncall_read(c, fake):
    names = {t["name"] for t in (await rpc(c, "tools/list")).json()["result"]["tools"]}
    assert {"list_oncall_rotas", "get_oncall_roster", "whois_oncall", "add_oncall_member"} <= names


# ----------------------------------------------------------------- incidents
@with_stack(**ALL_ON)
async def test_create_previews_by_default_then_creates_on_confirm(c, fake):
    args = {"short_description": "Printer on fire", "urgency": 2, "impact": 3}
    pre = await call(c, "create_incident", args)
    assert pre["executed"] is False and pre["confirm_required"] and fake.writes() == []
    assert pre["fields_to_set"] == {"short_description": "Printer on fire", "urgency": "2", "impact": "3"}
    done = await call(c, "create_incident", {**args, "confirm": True})
    assert done["executed"] is True and done["incident"]["number"] == "INC0010002"
    (w,) = fake.writes()
    assert w.method == "POST" and w.url.path == "/api/now/table/incident"
    assert json.loads(w.content) == {"short_description": "Printer on fire", "urgency": "2", "impact": "3"}


@with_stack(**ALL_ON)
async def test_create_rejects_bad_input_and_unlisted_team(c, fake):
    assert is_error(await call(c, "create_incident", {"short_description": "x\u202ey", "confirm": True}), "invalid")
    assert is_error(await call(c, "create_incident", {"short_description": "ok", "team": "Hackers", "confirm": True}),
                    "team must be one of")
    assert is_error(await call(c, "create_incident", {"short_description": "ok", "caller_id": "nope"}))
    assert fake.writes() == []


@with_stack(**ALL_ON)
async def test_update_preview_confirm_and_guards(c, fake):
    args = {"identifier": "INC0010001", "work_note": "Called the user", "urgency": 1}
    pre = await call(c, "update_incident", args)
    assert pre["executed"] is False and "incident" in pre and fake.writes() == []
    done = await call(c, "update_incident", {**args, "confirm": True})
    assert done["executed"] is True
    (w,) = fake.writes()
    assert w.method == "PATCH" and w.url.path == f"/api/now/table/incident/{INC}"
    assert json.loads(w.content) == {"work_notes": "Called the user", "urgency": "1"}
    assert is_error(await call(c, "update_incident", {"identifier": "INC0010001", "confirm": True}), "at least one")


@with_stack(**ALL_ON)
async def test_update_cannot_change_assignment_or_close(c, fake):
    # assignment_group / assigned_to are not parameters: the schema rejects them
    for extra in ({"assignment_group": TEAM_NET}, {"assigned_to": USER}, {"state": "closed"}, {"state": "resolved"}):
        res = await call(c, "update_incident", {"identifier": "INC0010001", "work_note": "x", "confirm": True, **extra})
        assert res.get("isError") or "assignment" not in json.dumps(res)
    assert all("assignment_group" not in (w.content or b"").decode() for w in fake.writes())
    assert all(json.loads(w.content).get("state") not in ("7", "6", "8") for w in fake.writes())


@pytest.mark.parametrize("state", ["7", "8"])
async def test_update_refuses_closed_or_canceled(state):
    async with open_stack(FakeServiceNow(incident_state=state), **ALL_ON) as (c, fake):
        res = await call(c, "update_incident", {"identifier": "INC0010001", "work_note": "x", "confirm": True})
        assert is_error(res, "closed or canceled")
        assert fake.writes() == []


@with_stack(**ALL_ON)
async def test_resolve_flow(c, fake):
    args = {"identifier": "INC0010001", "close_code": "Solution provided", "close_notes": "Rebooted the VPN gateway"}
    pre = await call(c, "resolve_incident", args)
    assert pre["executed"] is False and fake.writes() == []
    assert pre["fields_to_set"]["state"] == "6"
    done = await call(c, "resolve_incident", {**args, "confirm": True})
    assert done["executed"] is True
    (w,) = fake.writes()
    assert json.loads(w.content) == {"state": "6", "close_code": "Solution provided",
                                     "close_notes": "Rebooted the VPN gateway"}
    assert is_error(await call(c, "resolve_incident", {**args, "close_code": "x^ORy", "confirm": True}), "close_code")


@pytest.mark.parametrize("state", ["6", "7", "8"])
async def test_resolve_refuses_already_done(state):
    async with open_stack(FakeServiceNow(incident_state=state), **ALL_ON) as (c, fake):
        res = await call(c, "resolve_incident", {"identifier": "INC0010001", "close_code": "Solution provided",
                                                 "close_notes": "n", "confirm": True})
        assert is_error(res, "already")
        assert fake.writes() == []


# ----------------------------------------------------------------- triage / assignment
@with_stack(**ALL_ON)
async def test_assign_only_to_allowlisted_teams(c, fake):
    teams = await call(c, "list_assignment_teams", {})
    assert [t["name"] for t in teams["teams"]] == ["Network Ops", "Service Desk"]
    for bad in ("Hackers", "6" * 32, "' OR 1=1", ""):
        res = await call(c, "assign_incident", {"identifier": "INC0010001", "team": bad, "confirm": True})
        assert res.get("isError"), bad
    assert fake.writes() == []


@with_stack(**ALL_ON)
async def test_assign_preview_then_confirm(c, fake):
    args = {"identifier": "INC0010001", "team": "network ops", "urgency": 1, "category": "network",
            "work_note": "Triage: looks like a gateway fault"}
    pre = await call(c, "assign_incident", args)
    assert pre["executed"] is False and pre["assign_to_team"] == "Network Ops" and fake.writes() == []
    done = await call(c, "assign_incident", {**args, "confirm": True})
    assert done["executed"] is True and done["assigned_to_team"] == "Network Ops"
    (w,) = fake.writes()
    assert json.loads(w.content) == {"urgency": "1", "category": "network", "assignment_group": TEAM_NET,
                                     "work_notes": "Triage: looks like a gateway fault"}
    # accepted by sys_id only when that sys_id is in the allowlist
    ok = await call(c, "assign_incident", {"identifier": "INC0010001", "team": TEAM_DESK})
    assert ok["assign_to_team"] == "Service Desk"


@pytest.mark.parametrize("state", ["6", "7", "8"])
async def test_assign_refuses_finished_incidents(state):
    async with open_stack(FakeServiceNow(incident_state=state), **ALL_ON) as (c, fake):
        res = await call(c, "assign_incident", {"identifier": "INC0010001", "team": "Network Ops", "confirm": True})
        assert is_error(res, "cannot be assigned")
        assert fake.writes() == []


@with_stack(**ALL_ON)
async def test_triage_queue_filter(c, fake):
    await call(c, "search_incidents", {"active": True, "unassigned": True})
    q = dict(fake.calls[-1].url.params)["sysparm_query"]
    assert q == "active=true^assignment_groupISEMPTY^ORDERBYDESCopened_at"


# ----------------------------------------------------------------- change approval
@with_stack(**ALL_ON)
async def test_approve_preview_then_confirm_only_own_pending_approval(c, fake):
    pre = await call(c, "approve_change_request", {"identifier": "CHG0030001", "comments": "Reviewed"})
    assert pre["executed"] is False and fake.writes() == []
    lookup = [x for x in fake.calls if "sysapproval_approver" in x.url.path][-1]
    assert dict(lookup.url.params)["sysparm_query"] == f"sysapproval={CHG}^approver.user_name={SN_USER}^state=requested"
    done = await call(c, "approve_change_request", {"identifier": "CHG0030001", "comments": "Reviewed", "confirm": True})
    assert done["executed"] is True
    (w,) = fake.writes()
    assert w.method == "PATCH" and w.url.path == f"/api/now/table/sysapproval_approver/{APPR}"
    assert json.loads(w.content) == {"state": "approved", "comments": "Reviewed"}
    assert not any(x.url.path.startswith("/api/now/table/change_request") and x.method != "GET" for x in fake.calls)


async def test_approve_refuses_when_nothing_pending_for_integration_user():
    async with open_stack(FakeServiceNow(pending=False), **ALL_ON) as (c, fake):
        assert is_error(await call(c, "approve_change_request", {"identifier": "CHG0030001", "confirm": True}),
                        "no pending approval")
        assert fake.writes() == []


async def test_approve_refuses_disallowed_change_type():
    async with open_stack(FakeServiceNow(change_type="Emergency"), **ALL_ON) as (c, fake):
        assert is_error(await call(c, "approve_change_request", {"identifier": "CHG0030001", "confirm": True}),
                        "cannot be approved")
        assert fake.writes() == []
    async with open_stack(FakeServiceNow(change_type="Emergency"), **ALL_ON,
                          MCP_APPROVAL_ALLOWED_TYPES="standard,normal,emergency") as (c, fake):
        done = await call(c, "approve_change_request", {"identifier": "CHG0030001", "confirm": True})
        assert done["executed"] is True


# ----------------------------------------------------------------- on-call
@with_stack(**ALL_ON)
async def test_oncall_reads(c, fake):
    assert (await call(c, "list_oncall_rotas", {"active": True}))["rotas"][0]["name"] == "NetOps rota"
    roster = await call(c, "get_oncall_roster", {"rota_id": "9" * 32})
    assert roster["rosters"][0]["name"] == "Primary" and roster["members"][0]["sys_id"] == MEMBER
    who = await call(c, "whois_oncall", {"group_ids": [TEAM_NET]})
    assert who["oncall"][0]["user"] == "Pat Oncall"
    assert dict(fake.calls[-1].url.params) == {"group_ids": TEAM_NET}
    assert is_error(await call(c, "whois_oncall", {"group_ids": ["nope"]}))
    assert fake.writes() == []


@with_stack(**ALL_ON)
async def test_oncall_member_add_and_update_preview_confirm(c, fake):
    add = {"roster_id": ROSTER, "user_id": USER, "order": 200, "from_date": "2026-11-01", "to_date": "2026-11-30"}
    assert (await call(c, "add_oncall_member", add))["executed"] is False and fake.writes() == []
    assert (await call(c, "add_oncall_member", {**add, "confirm": True}))["executed"] is True
    assert json.loads(fake.writes()[-1].content) == {"roster": ROSTER, "member": USER, "order": "200",
                                                     "from": "2026-11-01", "to": "2026-11-30"}
    upd = {"member_record_id": MEMBER, "to_date": "2026-12-15"}
    pre = await call(c, "update_oncall_member", upd)
    assert pre["executed"] is False and pre["member"]["sys_id"] == MEMBER
    assert (await call(c, "update_oncall_member", {**upd, "confirm": True}))["executed"] is True
    w = fake.writes()[-1]
    assert w.method == "PATCH" and w.url.path == f"/api/now/table/cmn_rota_member/{MEMBER}"
    assert json.loads(w.content) == {"to": "2026-12-15"}
    assert is_error(await call(c, "add_oncall_member", {**add, "to_date": "2026-10-01", "confirm": True}), "before")
    assert is_error(await call(c, "update_oncall_member", {"member_record_id": MEMBER, "confirm": True}), "provide")


# ----------------------------------------------------------------- audit / logging
@with_stack(**ALL_ON)
async def test_write_audit_has_no_free_text_or_secrets(c, fake, caplog):
    caplog.set_level(logging.INFO)
    quiet_noisy_loggers()
    await call(c, "update_incident", {"identifier": "INC0010001", "work_note": "secret-note-needle"})
    await call(c, "update_incident", {"identifier": "INC0010001", "work_note": "secret-note-needle", "confirm": True})
    fmt = JsonFormatter()
    blob = "\n".join(fmt.format(r) for r in caplog.records)
    for secret in (SN_PASSWORD, TOKEN, "secret-note-needle", "Basic "):
        assert secret not in blob
    entries = [json.loads(x) for x in blob.splitlines() if '"tool_call"' in x]
    assert [e["executed"] for e in entries] == [False, True] and all(e["write"] for e in entries)


# ----------------------------------------------------------------- client write safety
def make_client(handler, **kw):
    seen = []

    def wrapped(r):
        seen.append(r)
        return handler(r)

    return ServiceNowClient(INSTANCE, SN_USER, SN_PASSWORD, transport=httpx.MockTransport(wrapped), **kw), seen


async def test_writes_are_never_retried_and_report_unknown_outcome():
    for handler in (lambda r: httpx.Response(503), lambda r: httpx.Response(500),
                    lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("t", request=r))):
        c, seen = make_client(handler)
        with pytest.raises(ServiceNowError) as exc:
            await c.create_incident({"short_description": "x"})
        assert exc.value.code == "unknown_outcome" and "UNKNOWN" in str(exc.value)
        assert len(seen) == 1
        await c.aclose()


async def test_rejected_write_passes_only_sanitised_detail():
    body = {"error": {"message": "Data Policy Exception: Close code is mandatory\x00\n" + "x" * 500, "detail": "d"}}
    c, _ = make_client(lambda r: httpx.Response(400, json=body))
    with pytest.raises(ServiceNowError) as exc:
        await c.update_incident(INC, {"state": "6"})
    msg = str(exc.value)
    assert exc.value.code == "rejected" and "Close code is mandatory" in msg
    assert "\x00" not in msg and len(msg) < 300
    await c.aclose()


async def test_client_refuses_unlisted_methods_and_tables():
    c, seen = make_client(lambda r: httpx.Response(200, json={"result": {}}))
    for method, table in (("DELETE", "incident"), ("PUT", "incident"), ("DELETE", "change_request"),
                          ("PUT", "change_request"), ("PATCH", "sys_user"), ("POST", "sysapproval_approver"),
                          ("POST", "sys_user")):
        with pytest.raises(ValueError):
            await c._request(method, table, sys_id=INC, body={"a": "b"})
    with pytest.raises(ValueError):
        await c._request("GET", "sys_user")
    with pytest.raises(ValueError):
        await c._get("/api/now/table/sys_user", {})
    with pytest.raises(ValueError):
        await c._get("/api/now/v1/anything", {})
    with pytest.raises(ValueError):
        ServiceNowClient._table_path("incident", "../../sys_user")
    assert seen == []
    await c.aclose()


async def test_write_rate_limit():
    c, _ = make_client(lambda r: httpx.Response(201, json={"result": {"sys_id": INC}}), write_rate_limit_per_minute=2)
    await c.create_incident({"short_description": "a"})
    await c.create_incident({"short_description": "b"})
    with pytest.raises(ServiceNowError) as exc:
        await c.create_incident({"short_description": "c"})
    assert exc.value.code == "rate_limited"
    await c.aclose()


async def test_approval_lookup_rejects_unsafe_username():
    c, seen = make_client(lambda r: httpx.Response(200, json={"result": []}))
    with pytest.raises(ValueError):
        await c.find_approval_ids(CHG, "bob^ORapprover=x")
    assert seen == []
    await c.aclose()


# ----------------------------------------------------------------- config
def test_config_flags_default_off():
    s = load_settings(base_env())
    assert not (s.enable_incident_writes or s.enable_incident_assignment or s.enable_change_approval
                or s.enable_oncall_read or s.enable_oncall_writes)
    assert s.assignment_teams == ()


def test_config_assignment_requires_valid_team_list():
    with pytest.raises(ConfigError):
        load_settings(base_env(MCP_ENABLE_INCIDENT_ASSIGNMENT="true"))
    for bad in ("Net=zz", "Net", f"=({TEAM_NET})", f"A={TEAM_NET};a={TEAM_DESK}", f"Bad^Name={TEAM_NET}"):
        with pytest.raises(ConfigError):
            load_settings(base_env(MCP_ENABLE_INCIDENT_ASSIGNMENT="true", MCP_ASSIGNMENT_GROUPS=bad))
    s = load_settings(base_env(MCP_ENABLE_INCIDENT_ASSIGNMENT="true",
                               MCP_ASSIGNMENT_GROUPS=f"Network Ops={TEAM_NET.upper()}; Service Desk={TEAM_DESK}"))
    assert s.assignment_teams == (("Network Ops", TEAM_NET), ("Service Desk", TEAM_DESK))


def test_config_approval_needs_safe_username_and_types():
    with pytest.raises(ConfigError):
        load_settings(base_env(MCP_ENABLE_CHANGE_APPROVAL="true", SERVICENOW_USERNAME="bad^user"))
    with pytest.raises(ConfigError):
        load_settings(base_env(MCP_ENABLE_CHANGE_APPROVAL="true", MCP_APPROVAL_ALLOWED_TYPES="norm^al"))
    assert load_settings(base_env(MCP_ENABLE_CHANGE_APPROVAL="true")).approval_allowed_types == ("standard", "normal")


# ----------------------------------------------------------------- validation
def test_text_validation():
    assert validate_text("line1\r\nline2", "x", 50) == "line1\nline2"
    for bad in ("a\x00b", "a\u202eb", "a\x1bb", "x" * 51):
        with pytest.raises(ValidationError):
            validate_text(bad, "x", 50)
    with pytest.raises(ValidationError):
        validate_text("   ", "x", 50, required=True)


def test_incident_fields_translation_and_limits():
    f = build_incident_fields(short_description=" Hi ", urgency=3, state="in_progress", work_note="n", comment="c")
    assert f == {"short_description": "Hi", "urgency": "3", "state": "2", "work_notes": "n", "comments": "c"}
    for kw in ({"state": "resolved"}, {"state": "closed"}, {"urgency": 4}, {"impact": 0}, {"caller_id": "x"},
               {"short_description": "a\nb"}, {"short_description": "x" * 161}, {"category": "a^b"},
               {"work_note": ""}):
        with pytest.raises(ValidationError):
            build_incident_fields(**kw)
    with pytest.raises(ValidationError):
        build_incident_fields(require_short_description=True)


def test_resolution_and_dates():
    assert build_incident_resolution("Solution provided", "done")["state"] == "6"
    for code in ("", "x^y", "a" * 61, "bad;code"):
        with pytest.raises(ValidationError):
            build_incident_resolution(code, "n")
    with pytest.raises(ValidationError):
        build_incident_resolution("Solution provided", " ")
    assert validate_date("2026-02-28", "d") == "2026-02-28"
    for bad in ("2026-02-30", "2026/02/01", "tomorrow", "2026-02-01T00:00"):
        with pytest.raises(ValidationError):
            validate_date(bad, "d")
    assert build_oncall_member_fields(order=5)["order"] == "5"
    with pytest.raises(ValidationError):
        build_oncall_member_fields(order=1001)
