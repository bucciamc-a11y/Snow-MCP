"""Problem management: feature gating, read tools, guarded write tools, state map and client safety."""

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
    build_problem_fields,
    build_problem_resolution,
    build_problem_search,
)

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "X-Forwarded-Proto": "https",
    "Host": "mcp.example.com",
}
PRB = "e" * 32
PRB2 = "f" * 32
INC = "a" * 32
TEAM_NET = "1" * 32
CI = "6" * 32
PROBLEM_READ = {"MCP_ENABLE_PROBLEM_READ": "true"}
PROBLEM_WRITES = {"MCP_ENABLE_PROBLEM_WRITES": "true", "MCP_ASSIGNMENT_GROUPS": f"Network Ops={TEAM_NET}"}
WRITE_TOOLS = ("create_problem", "update_problem", "resolve_problem", "link_incident_to_problem")


class FakeServiceNow:
    """Stateless stand-in for the problem/incident Table API; records every request."""

    def __init__(self, problem_state="102", incident_state="2", incident_problem="", linked_forbidden=False,
                 cause_notes="Faulty gateway firmware"):
        self.calls: list[httpx.Request] = []
        self.problem_state = problem_state
        self.incident_state = incident_state
        self.incident_problem = incident_problem
        self.linked_forbidden = linked_forbidden
        self.cause_notes = cause_notes

    def writes(self):
        return [c for c in self.calls if c.method != "GET"]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        path, q = req.url.path, dict(req.url.params)
        body = json.loads(req.content) if req.content else {}
        raw = q.get("sysparm_display_value") == "false"
        table = path.split("/")[4] if path.startswith("/api/now/table/") else ""
        if req.method == "GET" and table == "problem":
            rec = {"sys_id": PRB, "number": "PRB0010001", "short_description": "Recurring VPN drops",
                   "state": self.problem_state if raw else "Assess", "known_error": "false",
                   "cause_notes": self.cause_notes}
            return httpx.Response(200, json={"result": rec if path.endswith(PRB) else [rec]},
                                  headers={"X-Total-Count": "1"})
        if req.method == "GET" and table == "incident":
            if "problem_id=" in q.get("sysparm_query", ""):
                if self.linked_forbidden:
                    return httpx.Response(403, json={"error": {"message": "no"}})
                return httpx.Response(200, json={"result": [{"number": "INC0010001", "short_description": "VPN"}]},
                                      headers={"X-Total-Count": "1"})
            rec = {"sys_id": INC, "number": "INC0010001", "short_description": "VPN down",
                   "state": self.incident_state if raw else "In Progress", "problem_id": self.incident_problem}
            return httpx.Response(200, json={"result": rec if path.endswith(INC) else [rec]},
                                  headers={"X-Total-Count": "1"})
        if req.method == "POST" and table == "problem":
            return httpx.Response(201, json={"result": {"sys_id": PRB, "number": "PRB0010002", **body}})
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


def with_stack(**env):
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*a, **kw):
            async with open_stack(None, **env) as (c, f):
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
async def test_problem_tools_absent_by_default(c, fake):
    assert set(await tool_names(c)) == {"search_incidents", "get_incident", "get_change_request"}


@with_stack(**PROBLEM_READ)
async def test_read_flag_adds_exactly_two_read_only_tools(c, fake):
    tools = await tool_names(c)
    assert set(tools) == {"search_incidents", "get_incident", "get_change_request", "search_problems", "get_problem"}
    for name in ("search_problems", "get_problem"):
        assert tools[name]["annotations"]["readOnlyHint"] is True
        assert tools[name]["annotations"]["destructiveHint"] is False


@with_stack(**PROBLEM_WRITES)
async def test_write_flag_implies_read_and_adds_confirm_gated_tools(c, fake):
    tools = await tool_names(c)
    assert {"search_problems", "get_problem", *WRITE_TOOLS} <= set(tools)
    for name in WRITE_TOOLS:
        assert tools[name]["annotations"]["readOnlyHint"] is False
        assert tools[name]["inputSchema"]["properties"]["confirm"].get("default") is False
    assert not any(w in n for n in tools for w in ("delete", "remove", "close", "execute", "script", "raw_request"))


@with_stack(MCP_ENABLE_INCIDENT_WRITES="true", MCP_ENABLE_ONCALL_WRITES="true")
async def test_problem_features_independent_of_other_flags(c, fake):
    names = set(await tool_names(c))
    assert not names & {"search_problems", "get_problem", *WRITE_TOOLS}


# ----------------------------------------------------------------- reads
@with_stack(**PROBLEM_READ)
async def test_search_builds_exact_validated_query(c, fake):
    res = await call(c, "search_problems", {
        "state": "root_cause_analysis", "active": True, "known_error": False, "priority": 2, "category": "network",
        "assignment_group": TEAM_NET.upper(), "cmdb_ci": CI, "short_description_contains": "VPN drops",
        "opened_after": "2026-10-01", "order_by": "priority", "descending": False, "limit": 5,
    })
    assert res["problems"][0]["number"] == "PRB0010001" and res["next_offset"] is None
    q = dict(fake.calls[-1].url.params)
    assert q["sysparm_query"] == (
        f"state=103^active=true^known_error=false^priority=2^category=network^assignment_group={TEAM_NET}"
        f"^cmdb_ci={CI}^short_descriptionLIKEVPN drops^opened_at>=2026-10-01 00:00:00^ORDERBYpriority"
    )
    assert q["sysparm_limit"] == "5" and "cause_notes" in q["sysparm_fields"]


@pytest.mark.parametrize("args", [
    {"state": "bogus"}, {"state": "canceled"}, {"short_description_contains": "a^ORb"}, {"limit": 1000},
    {"assignment_group": "nope"}, {"cmdb_ci": "x" * 32 + "^ORactive=true"}, {"category": "a^b"},
    {"opened_after": "yesterday"}, {"order_by": "short_description"}, {"priority": 9},
])
@with_stack(**PROBLEM_READ)
async def test_search_rejects_invalid_input_without_calling_servicenow(c, fake, args):
    assert (await call(c, "search_problems", args)).get("isError")
    assert fake.calls == []


@pytest.mark.parametrize("identifier", ["INC0010001", "PRB1^ORactive=true", "../../api/now/table/sys_user", "", "prb"])
@with_stack(**PROBLEM_READ)
async def test_get_problem_rejects_bad_identifiers(c, fake, identifier):
    assert (await call(c, "get_problem", {"identifier": identifier})).get("isError")
    assert fake.calls == []


@with_stack(**PROBLEM_READ)
async def test_get_problem_includes_linked_incidents(c, fake):
    res = await call(c, "get_problem", {"identifier": "prb0010001"})
    assert res["problem"]["number"] == "PRB0010001"
    assert res["linked_incidents"]["incidents"][0]["number"] == "INC0010001"
    lookup = [x for x in fake.calls if "problem_id=" in dict(x.url.params).get("sysparm_query", "")][-1]
    assert dict(lookup.url.params)["sysparm_query"] == f"problem_id={PRB}^ORDERBYDESCopened_at"
    assert fake.writes() == []


async def test_get_problem_survives_unreadable_incident_list():
    async with open_stack(FakeServiceNow(linked_forbidden=True), **PROBLEM_READ) as (c, _fake):
        res = await call(c, "get_problem", {"identifier": "PRB0010001"})
        assert res["problem"]["number"] == "PRB0010001" and res["linked_incidents"] == {"error": "forbidden"}


async def test_long_text_fields_are_truncated():
    async with open_stack(FakeServiceNow(cause_notes="x" * 500), MCP_MAX_TEXT_CHARS="200", **PROBLEM_READ) as (c, _):
        res = await call(c, "get_problem", {"identifier": "PRB0010001"})
        assert res["problem"]["cause_notes"].endswith("…[truncated]") and len(res["problem"]["cause_notes"]) < 230


# ----------------------------------------------------------------- create
@with_stack(**PROBLEM_WRITES)
async def test_create_previews_then_creates_on_confirm(c, fake):
    args = {"short_description": "Recurring VPN drops", "urgency": 2, "impact": 2, "category": "network",
            "cmdb_ci": CI, "team": "network ops"}
    pre = await call(c, "create_problem", args)
    assert pre["executed"] is False and pre["confirm_required"] and fake.writes() == []
    expected = {"short_description": "Recurring VPN drops", "urgency": "2", "impact": "2", "category": "network",
                "cmdb_ci": CI, "assignment_group": TEAM_NET}
    assert pre["fields_to_set"] == expected
    done = await call(c, "create_problem", {**args, "confirm": True})
    assert done["executed"] is True and done["problem"]["number"] == "PRB0010002"
    (w,) = fake.writes()
    assert w.method == "POST" and w.url.path == "/api/now/table/problem" and json.loads(w.content) == expected


@with_stack(**PROBLEM_WRITES)
async def test_create_rejects_bad_input_and_unlisted_team(c, fake):
    assert is_error(await call(c, "create_problem", {"short_description": "x\u202ey", "confirm": True}), "invalid")
    assert is_error(await call(c, "create_problem", {"short_description": "ok", "team": "Hackers", "confirm": True}),
                    "team must be one of")
    assert is_error(await call(c, "create_problem", {"short_description": "ok", "cmdb_ci": "nope"}))
    assert (await call(c, "create_problem", {"short_description": ""})).get("isError")
    assert fake.writes() == []


@with_stack(MCP_ENABLE_PROBLEM_WRITES="true")
async def test_team_needs_configured_allowlist(c, fake):
    assert is_error(await call(c, "create_problem", {"short_description": "ok", "team": "Network Ops"}), "no assignment teams")
    assert fake.writes() == []


# ----------------------------------------------------------------- update
@with_stack(**PROBLEM_WRITES)
async def test_update_preview_confirm_and_state_translation(c, fake):
    args = {"identifier": "PRB0010001", "state": "root_cause_analysis", "cause_notes": "Firmware bug in gateway",
            "workaround": "Reboot the gateway nightly", "known_error": True, "work_note": "RCA started",
            "team": "Network Ops"}
    pre = await call(c, "update_problem", args)
    assert pre["executed"] is False and "problem" in pre and fake.writes() == []
    done = await call(c, "update_problem", {**args, "confirm": True})
    assert done["executed"] is True
    (w,) = fake.writes()
    assert w.method == "PATCH" and w.url.path == f"/api/now/table/problem/{PRB}"
    assert json.loads(w.content) == {
        "state": "103", "cause_notes": "Firmware bug in gateway", "workaround": "Reboot the gateway nightly",
        "known_error": "true", "work_notes": "RCA started", "assignment_group": TEAM_NET,
    }


@with_stack(**PROBLEM_WRITES)
async def test_update_cannot_resolve_close_or_assign_directly(c, fake):
    # state values outside assess/RCA/fix-in-progress are rejected by the schema; assignment_group /
    # assigned_to are not parameters at all, so they never reach the request body
    for extra in ({"state": "resolved"}, {"state": "closed"}, {"state": "new"}):
        assert (await call(c, "update_problem", {"identifier": "PRB0010001", "work_note": "x", "confirm": True,
                                                 **extra})).get("isError")
    assert fake.writes() == []
    for extra in ({"assignment_group": TEAM_NET}, {"assigned_to": "9" * 32}):
        await call(c, "update_problem", {"identifier": "PRB0010001", "work_note": "x", "confirm": True, **extra})
    bodies = [json.loads(w.content) for w in fake.writes()]
    assert bodies and all(b == {"work_notes": "x"} for b in bodies)


@pytest.mark.parametrize("state", ["106", "107"])
async def test_update_refuses_resolved_or_closed_problem(state):
    async with open_stack(FakeServiceNow(problem_state=state), **PROBLEM_WRITES) as (c, fake):
        assert is_error(await call(c, "update_problem", {"identifier": "PRB0010001", "work_note": "x", "confirm": True}),
                        "resolved or closed")
        assert fake.writes() == []


@with_stack(**PROBLEM_WRITES)
async def test_update_needs_a_field(c, fake):
    assert is_error(await call(c, "update_problem", {"identifier": "PRB0010001", "confirm": True}), "at least one")
    assert fake.writes() == []


# ----------------------------------------------------------------- resolve
@with_stack(**PROBLEM_WRITES)
async def test_resolve_flow(c, fake):
    args = {"identifier": "PRB0010001", "cause_notes": "Gateway firmware bug", "fix_notes": "Upgraded to 9.2.1",
            "resolution_code": "fix_applied"}
    pre = await call(c, "resolve_problem", args)
    assert pre["executed"] is False and fake.writes() == [] and pre["fields_to_set"]["state"] == "106"
    done = await call(c, "resolve_problem", {**args, "confirm": True})
    assert done["executed"] is True
    (w,) = fake.writes()
    assert json.loads(w.content) == {"state": "106", "cause_notes": "Gateway firmware bug",
                                     "fix_notes": "Upgraded to 9.2.1", "resolution_code": "fix_applied"}


@with_stack(**PROBLEM_WRITES)
async def test_resolve_requires_notes_and_valid_code(c, fake):
    base = {"identifier": "PRB0010001", "cause_notes": "c", "fix_notes": "f", "confirm": True}
    assert (await call(c, "resolve_problem", {"identifier": "PRB0010001", "confirm": True})).get("isError")
    assert (await call(c, "resolve_problem", {**base, "fix_notes": ""})).get("isError")
    assert is_error(await call(c, "resolve_problem", {**base, "resolution_code": "x^ORy"}), "resolution_code")
    assert fake.writes() == []


@pytest.mark.parametrize("state", ["106", "107"])
async def test_resolve_refuses_already_finished(state):
    async with open_stack(FakeServiceNow(problem_state=state), **PROBLEM_WRITES) as (c, fake):
        res = await call(c, "resolve_problem", {"identifier": "PRB0010001", "cause_notes": "c", "fix_notes": "f",
                                                "confirm": True})
        assert is_error(res, "already")
        assert fake.writes() == []


# ----------------------------------------------------------------- link incident to problem
@with_stack(**PROBLEM_WRITES)
async def test_link_preview_then_confirm_writes_only_the_incident(c, fake):
    args = {"problem": "PRB0010001", "incident": "INC0010001"}
    pre = await call(c, "link_incident_to_problem", args)
    assert pre["executed"] is False and pre["fields_to_set"] == {"problem_id": PRB} and fake.writes() == []
    assert pre["problem"]["number"] == "PRB0010001" and pre["incident"]["number"] == "INC0010001"
    done = await call(c, "link_incident_to_problem", {**args, "confirm": True})
    assert done["executed"] is True
    (w,) = fake.writes()
    assert w.method == "PATCH" and w.url.path == f"/api/now/table/incident/{INC}"
    assert json.loads(w.content) == {"problem_id": PRB}


@pytest.mark.parametrize("kwargs,needle", [
    ({"problem_state": "106"}, "resolved or closed"),
    ({"problem_state": "107"}, "resolved or closed"),
    ({"incident_state": "7"}, "closed or canceled"),
    ({"incident_state": "8"}, "closed or canceled"),
    ({"incident_problem": PRB2}, "different problem"),
])
async def test_link_guards(kwargs, needle):
    async with open_stack(FakeServiceNow(**kwargs), **PROBLEM_WRITES) as (c, fake):
        res = await call(c, "link_incident_to_problem", {"problem": "PRB0010001", "incident": "INC0010001",
                                                         "confirm": True})
        assert is_error(res, needle)
        assert fake.writes() == []


async def test_link_is_a_noop_when_already_linked_here():
    async with open_stack(FakeServiceNow(incident_problem=PRB), **PROBLEM_WRITES) as (c, fake):
        res = await call(c, "link_incident_to_problem", {"problem": "PRB0010001", "incident": "INC0010001",
                                                         "confirm": True})
        assert res["executed"] is False and res["already_linked"] is True
        assert fake.writes() == []


@with_stack(**PROBLEM_WRITES)
async def test_link_rejects_malformed_identifiers(c, fake):
    for args in ({"problem": "INC0010001", "incident": "INC0010001"}, {"problem": "PRB0010001", "incident": "PRB0010001"},
                 {"problem": "PRB1^x", "incident": "INC0010001"}):
        assert (await call(c, "link_incident_to_problem", {**args, "confirm": True})).get("isError")
    assert fake.writes() == []


# ----------------------------------------------------------------- configurable state model
LEGACY = {"MCP_PROBLEM_STATES": "resolved=4;closed=7"}


@with_stack(**PROBLEM_WRITES, **LEGACY)
async def test_state_map_override_drives_payloads_and_search(c, fake):
    await call(c, "search_problems", {"state": "resolved"})
    assert dict(fake.calls[-1].url.params)["sysparm_query"].startswith("state=4^")
    await call(c, "resolve_problem", {"identifier": "PRB0010001", "cause_notes": "c", "fix_notes": "f", "confirm": True})
    assert json.loads(fake.writes()[-1].content)["state"] == "4"
    await call(c, "update_problem", {"identifier": "PRB0010001", "state": "assess", "confirm": True})
    assert json.loads(fake.writes()[-1].content)["state"] == "102"  # not overridden -> default


async def test_state_map_override_drives_guards():
    async with open_stack(FakeServiceNow(problem_state="4"), **PROBLEM_WRITES, **LEGACY) as (c, fake):
        assert is_error(await call(c, "update_problem", {"identifier": "PRB0010001", "work_note": "x", "confirm": True}),
                        "resolved or closed")
        assert fake.writes() == []


# ----------------------------------------------------------------- audit / logging
@with_stack(**PROBLEM_WRITES)
async def test_problem_audit_has_no_free_text_or_secrets(c, fake, caplog):
    caplog.set_level(logging.INFO)
    quiet_noisy_loggers()
    args = {"identifier": "PRB0010001", "cause_notes": "secret-cause-needle", "work_note": "secret-note-needle"}
    await call(c, "update_problem", args)
    await call(c, "update_problem", {**args, "confirm": True})
    await call(c, "search_problems", {"short_description_contains": "secret-search-needle"})
    fmt = JsonFormatter()
    blob = "\n".join(fmt.format(r) for r in caplog.records)
    for secret in (SN_PASSWORD, TOKEN, "secret-cause-needle", "secret-note-needle", "secret-search-needle", "Basic "):
        assert secret not in blob
    entries = [json.loads(x) for x in blob.splitlines() if '"tool_call"' in x]
    updates = [e for e in entries if e["tool"] == "update_problem"]
    assert [e["executed"] for e in updates] == [False, True] and all(e["write"] for e in updates)


# ----------------------------------------------------------------- client allowlist
def make_client(handler, **kw):
    seen = []

    def wrapped(r):
        seen.append(r)
        return handler(r)

    return ServiceNowClient(INSTANCE, SN_USER, SN_PASSWORD, transport=httpx.MockTransport(wrapped), **kw), seen


async def test_client_problem_table_allows_only_post_and_patch():
    c, seen = make_client(lambda r: httpx.Response(200, json={"result": {}}))
    for method in ("DELETE", "PUT"):
        with pytest.raises(ValueError):
            await c._request(method, "problem", sys_id=PRB, body={"a": "b"})
    with pytest.raises(ValueError):
        await c._request("POST", "problem_task", body={"a": "b"})
    with pytest.raises(ValueError):
        await c._get("/api/now/table/problem_task", {})
    assert seen == []
    await c.aclose()


async def test_problem_writes_are_never_retried_and_report_unknown_outcome():
    c, seen = make_client(lambda r: httpx.Response(503))
    with pytest.raises(ServiceNowError) as exc:
        await c.create_problem({"short_description": "x"})
    assert exc.value.code == "unknown_outcome" and len(seen) == 1
    await c.aclose()


# ----------------------------------------------------------------- config
def test_problem_flags_default_off_and_default_state_model():
    s = load_settings(base_env())
    assert not (s.enable_problem_read or s.enable_problem_writes)
    assert s.problem_state_map == {"new": "101", "assess": "102", "root_cause_analysis": "103",
                                   "fix_in_progress": "104", "resolved": "106", "closed": "107"}
    assert load_settings(base_env(MCP_ENABLE_PROBLEM_WRITES="true")).enable_problem_read is True


@pytest.mark.parametrize("raw", ["resolved", "resolved=", "resolved=abc", "resolved=12345", "bogus=1",
                                 "resolved=4;resolved=5", "resolved=107", "Closed=106;resolved=106"])
def test_problem_states_rejects_bad_overrides(raw):
    with pytest.raises(ConfigError):
        load_settings(base_env(MCP_PROBLEM_STATES=raw))


def test_problem_states_accepts_partial_override_and_whitespace():
    s = load_settings(base_env(MCP_PROBLEM_STATES=" Resolved = 4 ; closed=7 "))
    assert s.problem_state_map["resolved"] == "4" and s.problem_state_map["closed"] == "7"
    assert s.problem_state_map["new"] == "101"


# ----------------------------------------------------------------- validation
SM = {"new": "101", "assess": "102", "root_cause_analysis": "103", "fix_in_progress": "104",
      "resolved": "106", "closed": "107"}


def test_problem_fields_translation_and_limits():
    f = build_problem_fields(state_map=SM, short_description=" Hi ", urgency=3, state="fix_in_progress",
                             known_error=False, work_note="n", comment="c", workaround="w")
    assert f == {"short_description": "Hi", "urgency": "3", "state": "104", "workaround": "w",
                 "known_error": "false", "work_notes": "n", "comments": "c"}
    for kw in ({"state": "resolved"}, {"state": "closed"}, {"state": "new"}, {"urgency": 4}, {"impact": 0},
               {"cmdb_ci": "x"}, {"short_description": "a\nb"}, {"short_description": "x" * 161},
               {"category": "a^b"}, {"cause_notes": ""}, {"known_error": "yes"}, {"work_note": "a\u202eb"}):
        with pytest.raises(ValidationError):
            build_problem_fields(state_map=SM, **kw)
    with pytest.raises(ValidationError):
        build_problem_fields(state_map=SM, require_short_description=True)


def test_problem_resolution_and_search_builders():
    r = build_problem_resolution("cause", "fix", "106", "fix_applied")
    assert r == {"state": "106", "cause_notes": "cause", "fix_notes": "fix", "resolution_code": "fix_applied"}
    for args in (("", "fix"), ("cause", " ")):
        with pytest.raises(ValidationError):
            build_problem_resolution(*args, "106")
    with pytest.raises(ValidationError):
        build_problem_resolution("c", "f", "106", "bad;code")
    s = build_problem_search(state_map=SM, active=True, known_error=True, unassigned=True)
    assert s.query == "active=true^known_error=true^assignment_groupISEMPTY^ORDERBYDESCopened_at"
    with pytest.raises(ValidationError):
        build_problem_search(state_map=SM, limit=51, max_limit=50)
