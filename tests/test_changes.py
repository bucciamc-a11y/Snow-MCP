"""Change management: feature gating, search, create/update, phase transitions, close/cancel, state map, safety."""

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
    build_change_cancel,
    build_change_close,
    build_change_fields,
    build_change_search,
    build_change_transition,
)

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "X-Forwarded-Proto": "https",
    "Host": "mcp.example.com",
}
CHG = "c" * 32
TEAM_NET = "1" * 32
CI = "6" * 32
CHANGE_READ = {"MCP_ENABLE_CHANGE_READ": "true"}
CHANGE_WRITES = {"MCP_ENABLE_CHANGE_WRITES": "true", "MCP_ASSIGNMENT_GROUPS": f"Network Ops={TEAM_NET}"}
WRITE_TOOLS = ("create_change_request", "update_change_request", "transition_change_request",
               "close_change_request", "cancel_change_request")
# default raw state values
NEW, ASSESS, AUTHORIZE, SCHEDULED, IMPLEMENT, REVIEW, CLOSED, CANCELED = "-5", "-4", "-3", "-2", "-1", "0", "3", "4"


class FakeServiceNow:
    """Stand-in for the change_request Table API; records every request."""

    def __init__(self, state=ASSESS, ctype="normal"):
        self.calls: list[httpx.Request] = []
        self.state = state
        self.ctype = ctype

    def writes(self):
        return [c for c in self.calls if c.method != "GET"]

    def bodies(self):
        return [json.loads(c.content) for c in self.writes()]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        path, q = req.url.path, dict(req.url.params)
        body = json.loads(req.content) if req.content else {}
        raw = q.get("sysparm_display_value") == "false"
        table = path.split("/")[4] if path.startswith("/api/now/table/") else ""
        if req.method == "GET" and table == "change_request":
            rec = {"sys_id": CHG, "number": "CHG0030001", "short_description": "Patch core switch",
                   "state": self.state if raw else "Assess", "type": self.ctype, "approval": "not requested"}
            return httpx.Response(200, json={"result": rec if path.endswith(CHG) else [rec]},
                                  headers={"X-Total-Count": "1"})
        if req.method == "POST" and table == "change_request":
            return httpx.Response(201, json={"result": {"sys_id": CHG, "number": "CHG0030002", **body}})
        if req.method == "PATCH":
            return httpx.Response(200, json={"result": {"sys_id": path.rsplit("/", 1)[1], **body}})
        return httpx.Response(404, json={"error": {"message": "no"}})


@asynccontextmanager
async def open_stack(fake=None, **env):
    fake = fake or FakeServiceNow()
    settings = load_settings(base_env(**env))
    sn = ServiceNowClient(INSTANCE, SN_USER, SN_PASSWORD, transport=httpx.MockTransport(fake),
                          max_text_chars=settings.max_text_chars)
    app, inner, _ = create_app(settings, sn)
    transport = httpx.ASGITransport(app=app)
    async with (
        inner.router.lifespan_context(inner),
        httpx.AsyncClient(transport=transport, base_url="https://mcp.example.com") as c,
    ):
        yield c, fake
    await sn.aclose()


def with_stack(state=ASSESS, ctype="normal", **env):
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*a, **kw):
            async with open_stack(FakeServiceNow(state, ctype), **env) as (c, f):
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


async def tool_names(c):
    return {t["name"]: t for t in (await rpc(c, "tools/list")).json()["result"]["tools"]}


# ----------------------------------------------------------------- gating
@with_stack()
async def test_change_tools_absent_by_default(c, fake):
    assert set(await tool_names(c)) == {"search_incidents", "get_incident", "get_change_request"}


@with_stack(**CHANGE_READ)
async def test_read_flag_adds_one_read_only_tool(c, fake):
    tools = await tool_names(c)
    assert set(tools) == {"search_incidents", "get_incident", "get_change_request", "search_change_requests"}
    assert tools["search_change_requests"]["annotations"]["readOnlyHint"] is True


@with_stack(**CHANGE_WRITES)
async def test_write_flag_adds_search_and_five_write_tools(c, fake):
    tools = await tool_names(c)
    assert set(WRITE_TOOLS) <= set(tools) and "search_change_requests" in tools
    assert len(tools) == 3 + 1 + 5
    for name in WRITE_TOOLS:
        assert tools[name]["annotations"]["readOnlyHint"] is False
        assert "confirm" in tools[name]["inputSchema"]["properties"]


@with_stack(**CHANGE_WRITES)
async def test_no_write_tool_can_set_state_or_type_directly(c, fake):
    tools = await tool_names(c)
    props = tools["update_change_request"]["inputSchema"]["properties"]
    assert "state" not in props and "type" not in props and "approval" not in props
    assert set(tools["transition_change_request"]["inputSchema"]["properties"]["to_phase"]["enum"]) == {
        "assess", "authorize", "scheduled", "implement", "review"}


# ----------------------------------------------------------------- search
@with_stack(**CHANGE_READ)
async def test_search_builds_exact_closed_vocabulary_query(c, fake):
    res = await call(c, "search_change_requests", {"state": "implement", "active": True, "type": "standard",
                                                   "order_by": "start_date", "descending": False})
    assert res["count"] == 1 and res["change_requests"][0]["number"] == "CHG0030001"
    q = fake.calls[-1].url.params["sysparm_query"]
    assert q == "state=-1^active=true^type=standard^ORDERBYstart_date"
    assert fake.writes() == []


@pytest.mark.parametrize("args", [
    {"short_description_contains": "a^ORDERBYDESCnumber"},
    {"short_description_contains": "x\ny"},
    {"assignment_group": "nothex"},
    {"opened_after": "yesterday"},
    {"limit": 0},
])
@with_stack(**CHANGE_READ)
async def test_search_rejects_unsafe_input(c, fake, args):
    res = await call(c, "search_change_requests", args)
    assert res.get("isError") and fake.calls == []


@with_stack(**CHANGE_READ)
async def test_search_rejects_unknown_state_and_type_at_schema(c, fake):
    assert (await call(c, "search_change_requests", {"state": "approved"})).get("isError")
    assert (await call(c, "search_change_requests", {"type": "weird"})).get("isError")
    assert fake.calls == []


# ----------------------------------------------------------------- create
@with_stack(**CHANGE_WRITES)
async def test_create_previews_then_creates(c, fake):
    args = {"short_description": "Patch core switch", "justification": "CVE fix", "backout_plan": "Reboot old image",
            "start_date": "2026-11-01 02:00:00", "end_date": "2026-11-01 04:00:00", "impact": 2,
            "team": "Network Ops", "cmdb_ci": CI}
    pre = await call(c, "create_change_request", args)
    assert pre["executed"] is False and pre["confirm_required"] is True
    assert fake.writes() == []
    assert pre["fields_to_set"]["assignment_group"] == TEAM_NET
    assert pre["fields_to_set"]["type"] == "normal"
    done = await call(c, "create_change_request", {**args, "confirm": True})
    assert done["executed"] is True and done["change_request"]["number"] == "CHG0030002"
    (body,) = fake.bodies()
    assert body["short_description"] == "Patch core switch" and body["type"] == "normal"
    assert body["start_date"] == "2026-11-01 02:00:00" and body["impact"] == "2"
    assert "state" not in body  # created in the instance's own initial phase


@with_stack(**CHANGE_WRITES)
async def test_create_emergency_is_refused_unless_configured(c, fake):
    res = await call(c, "create_change_request", {"short_description": "x", "type": "emergency", "confirm": True})
    assert is_error(res, "only lets agents create")
    assert fake.writes() == []


@with_stack(MCP_CHANGE_CREATE_TYPES="standard", **CHANGE_WRITES)
async def test_create_types_follow_configuration(c, fake):
    assert is_error(await call(c, "create_change_request", {"short_description": "x", "confirm": True}), "standard")
    ok = await call(c, "create_change_request", {"short_description": "x", "type": "standard", "confirm": True})
    assert ok["executed"] is True


@with_stack(MCP_ASSIGNMENT_GROUPS="", MCP_ENABLE_CHANGE_WRITES="true")
async def test_create_team_needs_configured_teams(c, fake):
    res = await call(c, "create_change_request", {"short_description": "x", "team": "Network Ops"})
    assert is_error(res, "no assignment teams")


@pytest.mark.parametrize("args", [
    {"short_description": ""},
    {"short_description": "a\nb"},
    {"short_description": "x", "team": "Not A Team"},
    {"short_description": "x", "start_date": "2026-11-02", "end_date": "2026-11-01"},
    {"short_description": "x", "cmdb_ci": "nothex"},
    {"short_description": "x", "category": "a^b"},
    {"short_description": "x", "description": "bad \u202e text"},
    {"short_description": "x", "impact": 4},
])
@with_stack(**CHANGE_WRITES)
async def test_create_rejects_invalid_input_without_any_request(c, fake, args):
    res = await call(c, "create_change_request", {**args, "confirm": True})
    assert res.get("isError") and fake.calls == []


# ----------------------------------------------------------------- update
@with_stack(**CHANGE_WRITES)
async def test_update_edits_open_change_only_with_confirm(c, fake):
    args = {"identifier": "CHG0030001", "implementation_plan": "Do it", "work_note": "scheduling", "team": "Network Ops"}
    pre = await call(c, "update_change_request", args)
    assert pre["executed"] is False and fake.writes() == []
    assert set(pre["fields_to_set"]) == {"implementation_plan", "work_notes", "assignment_group"}
    done = await call(c, "update_change_request", {**args, "confirm": True})
    assert done["executed"] is True
    assert fake.bodies() == [{"implementation_plan": "Do it", "work_notes": "scheduling",
                              "assignment_group": TEAM_NET}]
    assert fake.writes()[0].url.path.endswith(f"/change_request/{CHG}")


@pytest.mark.parametrize("state", [CLOSED, CANCELED])
async def test_update_refuses_finished_changes(state):
    async with open_stack(FakeServiceNow(state), **CHANGE_WRITES) as (c, fake):
        res = await call(c, "update_change_request", {"identifier": "CHG0030001", "description": "x", "confirm": True})
        assert is_error(res, "closed or canceled") and fake.writes() == []


@with_stack(**CHANGE_WRITES)
async def test_update_requires_a_field_and_ignores_forbidden_arguments(c, fake):
    assert is_error(await call(c, "update_change_request", {"identifier": "CHG0030001"}), "at least one")
    # unknown arguments are dropped by the schema: nothing but the work note may be written
    await call(c, "update_change_request", {"identifier": "CHG0030001", "work_note": "x", "state": "3",
                                            "type": "emergency", "confirm": True})
    assert fake.bodies() == [{"work_notes": "x"}]


# ----------------------------------------------------------------- transitions
@pytest.mark.parametrize(("current", "target", "raw"), [
    (NEW, "assess", ASSESS), (ASSESS, "authorize", AUTHORIZE), (AUTHORIZE, "scheduled", SCHEDULED),
    (SCHEDULED, "implement", IMPLEMENT), (IMPLEMENT, "review", REVIEW), (NEW, "implement", IMPLEMENT),
])
async def test_transition_moves_forward_with_preview_then_confirm(current, target, raw):
    async with open_stack(FakeServiceNow(current), **CHANGE_WRITES) as (c, fake):
        args = {"identifier": "CHG0030001", "to_phase": target, "work_note": "approved by CAB"}
        pre = await call(c, "transition_change_request", args)
        assert pre["executed"] is False and pre["to_phase"] == target and fake.writes() == []
        assert pre["fields_to_set"] == {"state": raw, "work_notes": "approved by CAB"}
        done = await call(c, "transition_change_request", {**args, "confirm": True})
        assert done["executed"] is True and done["to_phase"] == target
        assert fake.bodies() == [{"state": raw, "work_notes": "approved by CAB"}]


@pytest.mark.parametrize(("current", "target"), [
    (ASSESS, "assess"), (SCHEDULED, "authorize"), (REVIEW, "implement"), (IMPLEMENT, "assess"),
])
async def test_transition_never_goes_backwards_or_sideways(current, target):
    async with open_stack(FakeServiceNow(current), **CHANGE_WRITES) as (c, fake):
        res = await call(c, "transition_change_request",
                         {"identifier": "CHG0030001", "to_phase": target, "confirm": True})
        assert is_error(res, "forward") and fake.writes() == []


@pytest.mark.parametrize("state", [CLOSED, CANCELED])
async def test_transition_refuses_finished_changes(state):
    async with open_stack(FakeServiceNow(state), **CHANGE_WRITES) as (c, fake):
        res = await call(c, "transition_change_request",
                         {"identifier": "CHG0030001", "to_phase": "review", "confirm": True})
        assert is_error(res, "closed or canceled") and fake.writes() == []


@with_stack(state="99", **CHANGE_WRITES)
async def test_transition_refuses_unrecognised_state(c, fake):
    res = await call(c, "transition_change_request",
                     {"identifier": "CHG0030001", "to_phase": "review", "confirm": True})
    assert is_error(res, "does not recognise") and fake.writes() == []


@pytest.mark.parametrize("target", ["closed", "canceled", "new", "approved", ""])
@with_stack(**CHANGE_WRITES)
async def test_transition_cannot_target_finished_or_unknown_phases(c, fake, target):
    res = await call(c, "transition_change_request", {"identifier": "CHG0030001", "to_phase": target, "confirm": True})
    assert res.get("isError") and fake.calls == []


async def test_servicenow_rejection_is_reported_and_not_retried():
    seen = []
    ok = FakeServiceNow(ASSESS)

    def reject(req):
        seen.append(req)
        if req.method == "PATCH":
            return httpx.Response(403, json={"error": {"message": "no"}})
        return ok(req)

    settings = load_settings(base_env(**CHANGE_WRITES))
    sn = ServiceNowClient(INSTANCE, SN_USER, SN_PASSWORD, transport=httpx.MockTransport(reject))
    app, inner, _ = create_app(settings, sn)
    async with (
        inner.router.lifespan_context(inner),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://mcp.example.com") as c,
    ):
        res = await call(c, "transition_change_request",
                         {"identifier": "CHG0030001", "to_phase": "authorize", "confirm": True})
        assert is_error(res, "not permitted")
    await sn.aclose()
    assert len([x for x in seen if x.method == "PATCH"]) == 1


# ----------------------------------------------------------------- close
@pytest.mark.parametrize("state", [IMPLEMENT, REVIEW])
async def test_close_from_implement_or_review(state):
    async with open_stack(FakeServiceNow(state), **CHANGE_WRITES) as (c, fake):
        args = {"identifier": "CHG0030001", "close_code": "successful", "close_notes": "Completed, no issues"}
        pre = await call(c, "close_change_request", args)
        assert pre["executed"] is False and fake.writes() == []
        assert pre["fields_to_set"] == {"state": CLOSED, "close_code": "successful",
                                        "close_notes": "Completed, no issues"}
        done = await call(c, "close_change_request", {**args, "confirm": True})
        assert done["executed"] is True
        assert fake.bodies() == [pre["fields_to_set"]]


@pytest.mark.parametrize("state", [NEW, ASSESS, AUTHORIZE, SCHEDULED, CLOSED, CANCELED])
async def test_close_refused_before_implementation_and_when_finished(state):
    async with open_stack(FakeServiceNow(state), **CHANGE_WRITES) as (c, fake):
        res = await call(c, "close_change_request", {"identifier": "CHG0030001", "close_code": "successful",
                                                     "close_notes": "x", "confirm": True})
        assert res.get("isError") and fake.writes() == []


@pytest.mark.parametrize("args", [
    {"close_code": "bad;code", "close_notes": "x"},
    {"close_code": "successful", "close_notes": ""},
    {"close_code": "", "close_notes": "x"},
])
@with_stack(state=REVIEW, **CHANGE_WRITES)
async def test_close_rejects_invalid_input(c, fake, args):
    res = await call(c, "close_change_request", {"identifier": "CHG0030001", "confirm": True, **args})
    assert res.get("isError") and fake.writes() == []


# ----------------------------------------------------------------- cancel
@pytest.mark.parametrize("state", [NEW, ASSESS, AUTHORIZE, SCHEDULED, IMPLEMENT, REVIEW])
async def test_cancel_open_change_records_reason(state):
    async with open_stack(FakeServiceNow(state), **CHANGE_WRITES) as (c, fake):
        args = {"identifier": "CHG0030001", "reason": "Window missed; superseded by CHG0030009"}
        pre = await call(c, "cancel_change_request", args)
        assert pre["executed"] is False and fake.writes() == []
        done = await call(c, "cancel_change_request", {**args, "confirm": True})
        assert done["executed"] is True
        assert fake.bodies() == [{"state": CANCELED, "work_notes": "Window missed; superseded by CHG0030009"}]


@pytest.mark.parametrize("state", [CLOSED, CANCELED])
async def test_cancel_refused_when_already_finished(state):
    async with open_stack(FakeServiceNow(state), **CHANGE_WRITES) as (c, fake):
        res = await call(c, "cancel_change_request", {"identifier": "CHG0030001", "reason": "x", "confirm": True})
        assert is_error(res, "closed or canceled") and fake.writes() == []


@with_stack(**CHANGE_WRITES)
async def test_cancel_needs_a_reason(c, fake):
    res = await call(c, "cancel_change_request", {"identifier": "CHG0030001", "reason": " ", "confirm": True})
    assert res.get("isError") and fake.calls == []


# ----------------------------------------------------------------- identifiers
@pytest.mark.parametrize("ident", ["INC0010001", "CHG1", "CHG0030001^active=true", "x" * 32, ""])
@with_stack(**CHANGE_WRITES)
async def test_bad_identifiers_never_reach_servicenow(c, fake, ident):
    res = await call(c, "cancel_change_request", {"identifier": ident, "reason": "x", "confirm": True})
    assert res.get("isError") and fake.calls == []


# ----------------------------------------------------------------- state map override
async def test_state_map_override_is_used_for_guards_and_payloads():
    env = {**CHANGE_WRITES, "MCP_CHANGE_STATES": "implement=5;review=6;closed=8;canceled=9"}
    async with open_stack(FakeServiceNow("6"), **env) as (c, fake):
        done = await call(c, "close_change_request", {"identifier": "CHG0030001", "close_code": "successful",
                                                      "close_notes": "ok", "confirm": True})
        assert done["executed"] is True and fake.bodies()[0]["state"] == "8"
    async with open_stack(FakeServiceNow("8"), **env) as (c, fake):
        res = await call(c, "cancel_change_request", {"identifier": "CHG0030001", "reason": "x", "confirm": True})
        assert is_error(res, "closed or canceled")
    async with open_stack(FakeServiceNow("4"), **env) as (c, fake):  # 4 is a plain unknown value here
        res = await call(c, "transition_change_request",
                         {"identifier": "CHG0030001", "to_phase": "review", "confirm": True})
        assert is_error(res, "does not recognise")


# ----------------------------------------------------------------- audit / logging
@with_stack(**CHANGE_WRITES)
async def test_change_audit_has_no_free_text_or_secrets(c, fake, caplog):
    caplog.set_level(logging.INFO)
    quiet_noisy_loggers()
    args = {"identifier": "CHG0030001", "reason": "secret-reason-needle"}
    await call(c, "cancel_change_request", args)
    await call(c, "cancel_change_request", {**args, "confirm": True})
    await call(c, "search_change_requests", {"short_description_contains": "secret-search-needle"})
    blob = "\n".join(JsonFormatter().format(r) for r in caplog.records)
    for secret in (SN_PASSWORD, TOKEN, "secret-reason-needle", "secret-search-needle", "Basic "):
        assert secret not in blob
    entries = [json.loads(x) for x in blob.splitlines() if '"tool_call"' in x]
    cancels = [e for e in entries if e["tool"] == "cancel_change_request"]
    assert [e["executed"] for e in cancels] == [False, True] and all(e["write"] for e in cancels)


# ----------------------------------------------------------------- client allowlist
def make_client(handler):
    seen = []

    def wrapped(req):
        seen.append(req)
        return handler(req)

    return ServiceNowClient(INSTANCE, SN_USER, SN_PASSWORD, transport=httpx.MockTransport(wrapped)), seen


async def test_client_allows_only_post_and_patch_on_change_request():
    c, seen = make_client(lambda r: httpx.Response(200, json={"result": {"sys_id": CHG}}))
    await c.update_change(CHG, {"work_notes": "x"})
    assert [r.method for r in seen] == ["PATCH"]
    for method in ("DELETE", "PUT"):
        with pytest.raises(ValueError):
            await c._request(method, "change_request", sys_id=CHG)
    with pytest.raises(ValueError):
        await c._request("POST", "change_task", body={"a": "b"})
    assert len(seen) == 1
    await c.aclose()


async def test_change_writes_are_never_retried_and_report_unknown_outcome():
    c, seen = make_client(lambda r: httpx.Response(503))
    with pytest.raises(ServiceNowError) as exc:
        await c.create_change({"short_description": "x"})
    assert exc.value.code == "unknown_outcome" and len(seen) == 1
    await c.aclose()


# ----------------------------------------------------------------- config
def test_change_flags_default_off_and_default_state_model():
    s = load_settings(base_env())
    assert not (s.enable_change_read or s.enable_change_writes)
    assert s.change_state_map == {"new": "-5", "assess": "-4", "authorize": "-3", "scheduled": "-2",
                                  "implement": "-1", "review": "0", "closed": "3", "canceled": "4"}
    assert s.change_create_types == ("normal", "standard")
    assert load_settings(base_env(MCP_ENABLE_CHANGE_WRITES="true")).enable_change_read is True


@pytest.mark.parametrize("raw", ["closed", "closed=", "closed=abc", "closed=12345", "bogus=1", "closed=3;closed=5",
                                 "closed=4", "review=3", "new=--5"])
def test_change_states_rejects_bad_overrides(raw):
    with pytest.raises(ConfigError):
        load_settings(base_env(MCP_CHANGE_STATES=raw))


def test_change_states_accepts_partial_override_and_negative_values():
    s = load_settings(base_env(MCP_CHANGE_STATES=" Closed = 7 ; new=-10 "))
    assert s.change_state_map["closed"] == "7" and s.change_state_map["new"] == "-10"
    assert s.change_state_map["assess"] == "-4"


@pytest.mark.parametrize("raw", ["", ",", "Normal;standard", "nor mal!"])
def test_change_create_types_must_be_a_comma_list_when_writes_enabled(raw):
    with pytest.raises(ConfigError):
        load_settings(base_env(MCP_ENABLE_CHANGE_WRITES="true", MCP_CHANGE_CREATE_TYPES=raw))


# ----------------------------------------------------------------- validation
SM = {"new": "-5", "assess": "-4", "authorize": "-3", "scheduled": "-2", "implement": "-1", "review": "0",
      "closed": "3", "canceled": "4"}


def test_change_fields_translation_and_limits():
    f = build_change_fields(allowed_types=("normal",), short_description=" Hi ", change_type="Normal", impact=2,
                            start_date="2026-11-01", end_date="2026-11-01 05:00:00", work_note="n", comment="c")
    assert f == {"short_description": "Hi", "type": "normal", "impact": "2", "start_date": "2026-11-01 00:00:00",
                 "end_date": "2026-11-01 05:00:00", "work_notes": "n", "comments": "c"}
    for kw in ({"change_type": "emergency"}, {"change_type": "bogus"}, {"impact": 0}, {"cmdb_ci": "x"},
               {"short_description": "a\nb"}, {"short_description": "x" * 161}, {"category": "a^b"},
               {"start_date": "tomorrow"}, {"work_note": "a\u202eb"}, {"comment": ""},
               {"start_date": "2026-11-02", "end_date": "2026-11-01"}):
        with pytest.raises(ValidationError):
            build_change_fields(allowed_types=("normal",), **kw)
    with pytest.raises(ValidationError):
        build_change_fields(require_short_description=True)


def test_change_transition_close_cancel_builders():
    assert build_change_transition("Authorize", SM) == {"state": "-3"}
    assert build_change_transition("review", SM, "note") == {"state": "0", "work_notes": "note"}
    for bad in ("new", "closed", "canceled", "", "approved"):
        with pytest.raises(ValidationError):
            build_change_transition(bad, SM)
    assert build_change_close(" successful ", "done", "3") == {"state": "3", "close_code": "successful",
                                                               "close_notes": "done"}
    for args in (("bad;code", "x"), ("", "x"), ("ok", " ")):
        with pytest.raises(ValidationError):
            build_change_close(*args, "3")
    assert build_change_cancel("why", "4") == {"state": "4", "work_notes": "why"}
    with pytest.raises(ValidationError):
        build_change_cancel("  ", "4")


def test_change_search_builder_query_and_limits():
    s = build_change_search(state_map=SM, state="review", active=True, change_type="normal", priority=2)
    assert s.query == "state=0^active=true^type=normal^priority=2^ORDERBYDESCopened_at"
    for kw in ({"state": "approved"}, {"change_type": "x"}, {"priority": 6}, {"order_by": "type"},
               {"short_description_contains": "a^b"}, {"limit": 51}):
        with pytest.raises(ValidationError):
            build_change_search(state_map=SM, max_limit=50, **kw)
