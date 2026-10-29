"""get_journal and search_cis: gating, exact queries, input validation, truncation, and the close_notes field."""

import httpx
import pytest
from conftest import INSTANCE, SN_PASSWORD, SN_USER, base_env
from test_problems import call, is_error, open_stack, tool_names, with_stack

from servicenow_mcp.client import (
    _READ_TABLES,
    _WRITES,
    CHANGE_FIELDS,
    INCIDENT_FIELDS,
    ServiceNowClient,
)
from servicenow_mcp.config import load_settings
from servicenow_mcp.validation import ValidationError, build_ci_search

INC = "a" * 32
JOURNAL = {"MCP_ENABLE_JOURNAL_READ": "true"}
CMDB = {"MCP_ENABLE_CMDB_READ": "true"}


def fake_sn(journal_text="Rebooted the gateway"):
    """Minimal stand-in: an incident record, its journal and a CI list. Every request is recorded."""
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        table = req.url.path.split("/")[4]
        if table in ("incident", "change_request"):
            return httpx.Response(200, json={"result": [{"sys_id": INC, "number": "INC0010001"}]},
                                  headers={"X-Total-Count": "1"})
        if table == "sys_journal_field":
            rows = [{"sys_created_on": "2026-10-01 10:00:00", "sys_created_by": "jdoe",
                     "element": "work_notes", "value": journal_text}]
            return httpx.Response(200, json={"result": rows}, headers={"X-Total-Count": "1"})
        if table.startswith("cmdb_ci"):
            if req.url.params.get("sysparm_fields") == "sys_id,sys_class_name":  # first pass: ids + real class
                rows = [{"sys_id": "6" * 32, "sys_class_name": "cmdb_ci_linux_server"}]
            else:  # full record, only valid from the CI's own class table
                assert table == "cmdb_ci_linux_server" and req.url.params["sysparm_query"] == "sys_idIN" + "6" * 32
                rows = [{"sys_id": "6" * 32, "name": "web-01", "sys_class_name": "Linux Server",
                         "ip_address": "10.0.0.5", "os": "Linux", "comments": ""}]
            return httpx.Response(200, json={"result": rows}, headers={"X-Total-Count": "1"})
        return httpx.Response(404, json={"error": {"message": "no"}})

    handler.calls = calls
    return handler


# ---------------------------------------------------------------- gating
@with_stack()
async def test_tools_absent_by_default(c, fake):
    assert not set(await tool_names(c)) & {"get_journal", "search_cis"}


@with_stack(**JOURNAL, **CMDB)
async def test_flags_add_exactly_two_read_only_tools(c, fake):
    tools = await tool_names(c)
    for name in ("get_journal", "search_cis"):
        assert tools[name]["annotations"]["readOnlyHint"] is True
        assert tools[name]["annotations"]["destructiveHint"] is False


def test_new_tables_are_read_only():
    assert {"sys_journal_field", "cmdb_ci"} <= _READ_TABLES
    assert not {t for _, t in _WRITES} & {"sys_journal_field", "cmdb_ci"}


def test_close_notes_are_returned_for_incidents_and_changes():
    assert "close_notes" in INCIDENT_FIELDS.split(",") and "close_notes" in CHANGE_FIELDS.split(",")


# ---------------------------------------------------------------- client behaviour
def make_client(handler, **kw):
    return ServiceNowClient(INSTANCE, SN_USER, SN_PASSWORD, transport=httpx.MockTransport(handler), **kw)


async def test_journal_query_is_exact_and_oldest_first():
    h = fake_sn()
    client = make_client(h)
    out = await client.get_journal("inc0010001", "both", 50, 0)
    assert out["entries"][0]["value"] == "Rebooted the gateway" and out["next_offset"] is None
    q = dict(h.calls[-1].url.params)
    assert q["sysparm_query"] == (f"name=incident^element_id={INC}^elementINwork_notes,comments"
                                  "^ORDERBYsys_created_on")
    await client.get_journal("CHG0030001", "comments", 5, 0)
    assert "name=change_request^" in dict(h.calls[-1].url.params)["sysparm_query"]
    assert dict(h.calls[-1].url.params)["sysparm_query"].count("elementINcomments") == 1
    await client.aclose()


async def test_journal_text_is_truncated():
    client = make_client(fake_sn("x" * 500), max_text_chars=200)
    out = await client.get_journal("INC0010001", "work_notes", 10, 0)
    assert out["entries"][0]["value"].endswith("…[truncated]") and len(out["entries"][0]["value"]) < 300
    await client.aclose()


@pytest.mark.parametrize("identifier", ["", "PRB1^ORactive=true", "sys_user", INC, "RITM0010001", "INC1; drop"])
async def test_journal_rejects_bad_identifiers_without_a_journal_call(identifier):
    h = fake_sn()
    client = make_client(h)
    with pytest.raises(ValidationError):
        await client.get_journal(identifier, "both", 10, 0)
    assert not any("sys_journal_field" in str(r.url) for r in h.calls)
    await client.aclose()


async def test_journal_rejects_unknown_field():
    client = make_client(fake_sn())
    with pytest.raises(ValidationError):
        await client.get_journal("INC0010001", "sys_id^ORactive=true", 10, 0)
    await client.aclose()


# ---------------------------------------------------------------- CI search validation
def test_ci_search_query():
    s = build_ci_search(name_contains="web-01", ci_class="cmdb_ci_server", limit=5)
    assert s.query == "nameLIKEweb-01^ORDERBYname" and s.limit == 5
    assert s.table == "cmdb_ci_server"
    assert build_ci_search(name_contains="web-01").table == "cmdb_ci"


@pytest.mark.parametrize("kwargs", [
    {}, {"ci_class": "cmdb_ci"}, {"name_contains": "a^ORb"}, {"ci_class": "cmdb_ci^ORactive=true"}, {"ci_class": "Server"}, {"ci_class": "incident"}, {"ci_class": "sys_user"},
    {"name_contains": "web", "limit": 1000},
])
def test_ci_search_rejects_bad_input(kwargs):
    with pytest.raises(ValidationError):
        build_ci_search(**kwargs)


# ---------------------------------------------------------------- through the MCP stack
async def test_tools_end_to_end():
    h = fake_sn()
    async with open_stack(h, **JOURNAL, **CMDB) as (c, _):
        res = await call(c, "get_journal", {"identifier": "INC0010001", "field": "work_notes"})
        assert res["record"] == "INC0010001" and res["entries"][0]["element"] == "work_notes"
        res = await call(c, "search_cis", {"name_contains": "web", "ci_class": "cmdb_ci_server"})
        assert res["cis"][0]["name"] == "web-01"
        # all CI data: class-specific fields come back, empty ones are dropped
        assert res["cis"][0]["ip_address"] == "10.0.0.5" and "comments" not in res["cis"][0]
        first, last = h.calls[-2], h.calls[-1]
        assert first.url.path.split("/")[4] == "cmdb_ci_server"  # class table covers its subclasses
        assert dict(first.url.params)["sysparm_query"] == "nameLIKEweb^ORDERBYname"
        assert last.url.path.split("/")[4] == "cmdb_ci_linux_server"  # re-read from the CI's own class
        assert "sysparm_fields" not in dict(last.url.params)  # no field list -> every column
        await call(c, "search_cis", {"ci_class": "cmdb_ci_server"})  # class alone is enough: all servers
        assert h.calls[-2].url.path.split("/")[4] == "cmdb_ci_server"
        assert dict(h.calls[-2].url.params)["sysparm_query"] == "ORDERBYname"
        # invalid input is rejected before any further ServiceNow call
        before = len(h.calls)
        assert is_error(await call(c, "get_journal", {"identifier": "bogus"}), "invalid input")
        assert is_error(await call(c, "search_cis", {}), "name_contains or ci_class")
        assert is_error(await call(c, "search_cis", {"ci_class": "x^y"}), "ci_class")
        assert len(h.calls) == before


def test_settings_flags():
    s = load_settings(base_env())
    assert not s.enable_journal_read and not s.enable_cmdb_read
    s = load_settings(base_env(**JOURNAL, **CMDB))
    assert s.enable_journal_read and s.enable_cmdb_read
