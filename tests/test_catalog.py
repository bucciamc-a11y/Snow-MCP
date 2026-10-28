"""Service catalog: feature gating, validation, preview/confirm creation of items and record producers."""

import functools
import inspect
import json
import typing

import httpx
import pytest
from conftest import INSTANCE, SN_PASSWORD, SN_USER, base_env
from test_problems import call, is_error, open_stack, tool_names

from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import ConfigError, load_settings
from servicenow_mcp.server import CatalogVariable
from servicenow_mcp.validation import (
    VARIABLE_TYPES,
    ValidationError,
    build_catalog_item_fields,
    build_catalog_search,
    build_catalog_variables,
    catalog_record_count,
)

ITEM = "c" * 32
CATALOG = "d" * 32
CATEGORY = "e" * 32
CATALOG_READ = {"MCP_ENABLE_CATALOG_READ": "true"}
CATALOG_WRITES = {"MCP_ENABLE_CATALOG_WRITES": "true"}
READ_TOOLS = {"search_catalog_items", "get_catalog_item", "get_catalog_options"}
WRITE_TOOLS = {"create_catalog_item", "create_record_producer", "add_catalog_variables"}
BASE_TOOLS = {"search_incidents", "get_incident", "get_change_request"}
VARS = [
    {"name": "model", "label": "Which laptop?", "type": "select_box", "mandatory": True,
     "choices": [{"label": "Standard"}, {"label": "Developer", "value": "dev"}]},
    {"name": "reason", "label": "Why do you need it?", "type": "multi_line_text"},
]
NEW_ITEM = {"name": "New laptop", "short_description": "Request a laptop", "category": CATEGORY, "catalog": CATALOG}


class FakeCatalog:
    """Stand-in for the catalog tables; records every request."""

    def __init__(self, active="false", sys_class="sc_cat_item", fail_variable_at=None, last_order=None):
        self.calls: list[httpx.Request] = []
        self.active, self.sys_class, self.fail_at, self.last_order = active, sys_class, fail_variable_at, last_order
        self.variables_created = 0

    def posts(self, table=None):
        return [(c.url.path.rsplit("/", 1)[1], json.loads(c.content)) for c in self.calls
                if c.method == "POST" and (table is None or c.url.path.endswith("/" + table))]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        path, q = req.url.path, dict(req.url.params)
        table = path.split("/")[4]
        body = json.loads(req.content) if req.content else {}
        raw = q.get("sysparm_display_value") == "false"
        if req.method == "GET":
            if table == "sc_catalog":
                return httpx.Response(200, json={"result": [{"sys_id": CATALOG, "title": "Service Catalog"}]})
            if table == "sc_category":
                return httpx.Response(200, json={"result": [{"sys_id": CATEGORY, "title": "Hardware"}]})
            if table == "sc_cat_item":
                rec = {"sys_id": ITEM, "active": self.active, "sys_class_name": self.sys_class}
                return httpx.Response(200, json={"result": rec if raw else {**rec, "name": "New laptop"}})
            if table == "item_option_new":
                if "ORDERBYDESCorder" in q["sysparm_query"]:
                    rows = [{"order": str(self.last_order)}] if self.last_order else []
                else:
                    rows = [{"sys_id": "1" * 32, "name": "model", "type": "Select Box", "order": "100"}]
                return httpx.Response(200, json={"result": rows})
            if table == "question_choice":
                return httpx.Response(200, json={"result": [
                    {"question": "1" * 32, "text": "Standard", "value": "standard"},
                    {"question": "1" * 32, "text": "Developer", "value": "dev"}]})
        if req.method == "POST":
            if table == "item_option_new":
                self.variables_created += 1
                if self.fail_at == self.variables_created:
                    return httpx.Response(400, json={"error": {"message": "bad variable"}})
                return httpx.Response(201, json={"result": {"sys_id": f"{self.variables_created:032x}", **body}})
            return httpx.Response(201, json={"result": {"sys_id": ITEM, **body}})
        return httpx.Response(404, json={"error": {"message": "no"}})


def with_catalog(fake_kwargs=None, **env):
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*a, **kw):
            async with open_stack(FakeCatalog(**(fake_kwargs or {})), **env) as (c, fake):
                await fn(c, fake, *a, **kw)

        sig = inspect.signature(fn)
        wrapper.__signature__ = sig.replace(parameters=list(sig.parameters.values())[2:])
        return wrapper

    return deco


# ----------------------------------------------------------------- gating and config
@with_catalog()
async def test_catalog_tools_absent_by_default(c, fake):
    assert set(await tool_names(c)) == BASE_TOOLS


@with_catalog(**CATALOG_READ)
async def test_read_flag_adds_only_read_only_tools(c, fake):
    tools = await tool_names(c)
    assert set(tools) == BASE_TOOLS | READ_TOOLS
    assert all(tools[n]["annotations"]["readOnlyHint"] for n in READ_TOOLS)


@with_catalog(**CATALOG_WRITES)
async def test_write_flag_implies_read_and_every_write_is_preview_by_default(c, fake):
    tools = await tool_names(c)
    assert set(tools) == BASE_TOOLS | READ_TOOLS | WRITE_TOOLS
    for name in WRITE_TOOLS:
        assert tools[name]["inputSchema"]["properties"]["confirm"]["default"] is False
    assert not any(w in n for n in tools for w in ("delete", "activate", "script", "update_catalog"))


def test_producer_target_tables_config():
    assert load_settings(base_env()).producer_target_tables == ("incident",)
    s = load_settings(base_env(MCP_ENABLE_CATALOG_WRITES="true", MCP_PRODUCER_TARGET_TABLES="Incident, sc_request"))
    assert s.producer_target_tables == ("incident", "sc_request")
    for bad in ("bad table!", "", ",", "1abc"):
        with pytest.raises(ConfigError):
            load_settings(base_env(MCP_ENABLE_CATALOG_WRITES="true", MCP_PRODUCER_TARGET_TABLES=bad))


def test_model_type_literal_matches_the_validated_vocabulary():
    assert set(typing.get_args(CatalogVariable.model_fields["type"].annotation)) == set(VARIABLE_TYPES)


async def test_client_has_no_catalog_update_or_delete():
    c = ServiceNowClient(INSTANCE, SN_USER, SN_PASSWORD, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    for method in ("PATCH", "PUT", "DELETE"):
        with pytest.raises(ValueError):
            await c._request(method, "sc_cat_item", sys_id=ITEM, body={})
    await c.aclose()


# ----------------------------------------------------------------- validation
def test_items_are_always_inactive_and_validated():
    f = build_catalog_item_fields(**{k: v for k, v in NEW_ITEM.items()})
    assert f["active"] == "false" and f["sc_catalogs"] == CATALOG and f["category"] == CATEGORY
    for bad in ({"name": "two\nlines"}, {"name": ""}, {"category": "nope"}, {"catalog": "x" * 32}):
        with pytest.raises(ValidationError):
            build_catalog_item_fields(**{**NEW_ITEM, **bad})


def test_producer_target_table_must_be_allowlisted():
    ok = build_catalog_item_fields(**NEW_ITEM, target_table="Incident", allowed_target_tables=("incident",))
    assert ok["table_name"] == "incident"
    for table in ("sys_user", "incident; drop", ""):
        with pytest.raises(ValidationError):
            build_catalog_item_fields(**NEW_ITEM, target_table=table, allowed_target_tables=("incident",))


def test_variables_are_translated_and_ordered():
    builds = build_catalog_variables(VARS, start_order=300)
    assert [b.fields["order"] for b in builds] == ["400", "500"]
    assert builds[0].fields["type"] == "5" and builds[0].fields["mandatory"] == "true"
    assert builds[0].choices == ({"text": "Standard", "value": "standard", "order": "100"},
                                 {"text": "Developer", "value": "dev", "order": "200"})
    assert catalog_record_count(builds) == 4


@pytest.mark.parametrize("bad", [
    {"name": "Bad Name", "label": "q", "type": "date"},
    {"name": "a", "label": "q", "type": "script"},
    {"name": "a", "label": "q", "type": "date", "onchange": "alert(1)"},
    {"name": "a", "label": "q\nr", "type": "date"},
    {"name": "a", "label": "q", "type": "select_box"},
    {"name": "a", "label": "q", "type": "select_box", "choices": []},
    {"name": "a", "label": "q", "type": "date", "choices": [{"label": "x"}]},
    {"name": "a", "label": "q", "type": "reference"},
    {"name": "a", "label": "q", "type": "date", "reference_table": "sys_user"},
    {"name": "a", "label": "q", "type": "date", "mandatory": "yes"},
    {"name": "a", "label": "q", "type": "date", "maps_to_field": "short_description"},  # not a producer
])
def test_bad_variables_are_rejected(bad):
    with pytest.raises(ValidationError):
        build_catalog_variables([bad])


def test_variable_lists_reject_duplicates_and_empty_input():
    one = {"name": "a", "label": "q", "type": "date"}
    for bad in ([], None, [one, one], [{**one, "maps_to_field": "x"}, {**one, "name": "b", "maps_to_field": "x"}]):
        with pytest.raises(ValidationError):
            build_catalog_variables(bad, producer=True)
    dup = {"name": "a", "label": "q", "type": "select_box", "choices": [{"label": "A"}, {"label": "a"}]}
    with pytest.raises(ValidationError):
        build_catalog_variables([dup])


def test_producer_mapping_and_reference_fields():
    b = build_catalog_variables([
        {"name": "who", "label": "Who?", "type": "reference", "reference_table": "sys_user", "maps_to_field": "caller_id"},
    ], producer=True)[0]
    assert b.fields["reference"] == "sys_user" and b.fields["map_to_field"] == "true" and b.fields["field"] == "caller_id"


def test_catalog_search_query_is_exact():
    s = build_catalog_search(name_contains="laptop", kind="record_producer", active=False, category=CATEGORY,
                             catalog=CATALOG)
    assert s.query == (f"nameLIKElaptop^sys_class_name=sc_cat_item_producer^active=false^category={CATEGORY}"
                       f"^sc_catalogsLIKE{CATALOG}^ORDERBYname")
    for bad in ({"name_contains": "a^b"}, {"kind": "workflow"}, {"category": "x"}, {"order_by": "price"}):
        with pytest.raises(ValidationError):
            build_catalog_search(**bad)


# ----------------------------------------------------------------- reads
@with_catalog(**CATALOG_WRITES)
async def test_options_list_places_types_and_target_tables(c, fake):
    res = await call(c, "get_catalog_options", {})
    assert res["catalogs"][0]["sys_id"] == CATALOG and res["categories"][0]["sys_id"] == CATEGORY
    assert set(res["variable_types"]) == set(VARIABLE_TYPES) and res["producer_target_tables"] == ["incident"]


@with_catalog(**CATALOG_READ)
async def test_options_hide_target_tables_without_write_access(c, fake):
    assert (await call(c, "get_catalog_options", {}))["producer_target_tables"] == []


@with_catalog(**CATALOG_READ)
async def test_get_item_returns_variables_with_their_choices(c, fake):
    res = await call(c, "get_catalog_item", {"item_id": ITEM})
    assert res["variable_count"] == 1
    assert [ch["value"] for ch in res["variables"][0]["choices"]] == ["standard", "dev"]
    assert not fake.posts()


# ----------------------------------------------------------------- create item
@with_catalog(**CATALOG_WRITES)
async def test_create_item_preview_changes_nothing(c, fake):
    res = await call(c, "create_catalog_item", {**NEW_ITEM, "variables": VARS})
    assert res["executed"] is False and res["confirm_required"] is True and res["records_to_create"] == 5
    assert res["fields_to_set"]["active"] == "false"
    assert res["variables"][0]["choices"] == ["Standard", "Developer"]
    assert not fake.posts()


@with_catalog(**CATALOG_WRITES)
async def test_create_item_confirm_creates_inactive_item_variables_and_choices(c, fake):
    res = await call(c, "create_catalog_item", {**NEW_ITEM, "variables": VARS, "confirm": True})
    assert res["executed"] is True and res["variables_created"] == 2 and "INACTIVE" in res["message"]
    tables = [t for t, _ in fake.posts()]
    assert tables == ["sc_cat_item", "item_option_new", "question_choice", "question_choice", "item_option_new"]
    item = fake.posts("sc_cat_item")[0][1]
    assert item["active"] == "false" and item["sc_catalogs"] == CATALOG
    var1 = fake.posts("item_option_new")[0][1]
    assert var1["cat_item"] == ITEM and var1["type"] == "5" and var1["order"] == "100"
    assert {ch["question"] for _, ch in fake.posts("question_choice")} == {"0" * 31 + "1"}


@with_catalog(**CATALOG_WRITES)
async def test_item_without_variables_is_allowed(c, fake):
    res = await call(c, "create_catalog_item", {**NEW_ITEM, "confirm": True})
    assert res["executed"] is True and [t for t, _ in fake.posts()] == ["sc_cat_item"]


@with_catalog(**CATALOG_WRITES)
async def test_invalid_input_never_reaches_servicenow(c, fake):
    bad_var = [{"name": "x", "label": "q", "type": "script"}]
    assert is_error(await call(c, "create_catalog_item", {**NEW_ITEM, "variables": bad_var, "confirm": True}))
    assert is_error(await call(c, "create_catalog_item", {**NEW_ITEM, "category": "nope", "confirm": True}))
    assert not fake.posts()


@with_catalog(**CATALOG_WRITES, MCP_WRITE_RATE_LIMIT_PER_MINUTE="4")
async def test_oversized_build_is_refused_before_anything_is_created(c, fake):
    res = await call(c, "create_catalog_item", {**NEW_ITEM, "variables": VARS, "confirm": True})
    assert is_error(res, "5 records", "at most 4", "add_catalog_variables")
    assert not fake.posts()


@with_catalog({"fail_variable_at": 2}, **CATALOG_WRITES)
async def test_partial_build_names_the_draft_and_the_next_step(c, fake):
    res = await call(c, "create_catalog_item", {**NEW_ITEM, "variables": VARS, "confirm": True})
    assert is_error(res, ITEM, "stopped after 1 of 2", "add_catalog_variables")


# ----------------------------------------------------------------- record producers
PRODUCER_VARS = [
    {"name": "summary", "label": "What is wrong?", "type": "single_line_text", "mandatory": True,
     "maps_to_field": "short_description"},
    {"name": "details", "label": "Details", "type": "multi_line_text"},
]
PRODUCER = {**NEW_ITEM, "name": "Report a problem", "target_table": "incident", "variables": PRODUCER_VARS}


@with_catalog(**CATALOG_WRITES)
async def test_record_producer_is_created_inactive_with_declarative_mapping(c, fake):
    preview = await call(c, "create_record_producer", PRODUCER)
    assert preview["executed"] is False and preview["fields_to_set"]["table_name"] == "incident"
    assert preview["variables"][0]["maps_to_field"] == "short_description" and not fake.posts()
    res = await call(c, "create_record_producer", {**PRODUCER, "confirm": True})
    assert res["executed"] is True
    prod = fake.posts("sc_cat_item_producer")[0][1]
    assert prod["active"] == "false" and prod["table_name"] == "incident" and "script" not in prod
    first = fake.posts("item_option_new")[0][1]
    assert first["map_to_field"] == "true" and first["field"] == "short_description"
    assert not any("script" in body for _, body in fake.posts())  # no field named script anywhere


@with_catalog(**CATALOG_WRITES, MCP_PRODUCER_TARGET_TABLES="incident,sc_request")
async def test_producer_target_must_be_on_the_administrators_list(c, fake):
    refused = await call(c, "create_record_producer", {**PRODUCER, "target_table": "sys_user", "confirm": True})
    assert is_error(refused, "may only create records in")
    assert not fake.posts()
    ok = await call(c, "create_record_producer", {**PRODUCER, "target_table": "sc_request", "confirm": True})
    assert ok["executed"] is True


@with_catalog(**CATALOG_WRITES)
async def test_maps_to_field_is_refused_on_a_plain_item(c, fake):
    res = await call(c, "create_catalog_item", {**NEW_ITEM, "variables": PRODUCER_VARS, "confirm": True})
    assert is_error(res, "maps_to_field")
    assert not fake.posts()


# ----------------------------------------------------------------- add variables to a draft
@with_catalog({"last_order": 300}, **CATALOG_WRITES)
async def test_add_variables_continues_the_order_and_creates_records(c, fake):
    args = {"item_id": ITEM, "variables": [{"name": "when", "label": "When?", "type": "date"}]}
    preview = await call(c, "add_catalog_variables", args)
    assert preview["executed"] is False and not fake.posts()
    res = await call(c, "add_catalog_variables", {**args, "confirm": True})
    assert res["executed"] is True and res["variables_created"] == 1
    assert fake.posts("item_option_new")[0][1]["order"] == "400"


@with_catalog({"active": "true"}, **CATALOG_WRITES)
async def test_live_items_cannot_be_extended(c, fake):
    args = {"item_id": ITEM, "variables": [{"name": "when", "label": "When?", "type": "date"}], "confirm": True}
    assert is_error(await call(c, "add_catalog_variables", args), "live")
    assert not fake.posts()


@with_catalog({"sys_class": "sc_cat_item_guide"}, **CATALOG_WRITES)
async def test_only_plain_items_and_producers_can_be_extended(c, fake):
    args = {"item_id": ITEM, "variables": [{"name": "when", "label": "When?", "type": "date"}], "confirm": True}
    assert is_error(await call(c, "add_catalog_variables", args), "not a plain catalog item")
    assert not fake.posts()


@with_catalog({"sys_class": "sc_cat_item_producer"}, **CATALOG_WRITES)
async def test_producer_drafts_accept_mapped_variables(c, fake):
    args = {"item_id": ITEM, "confirm": True, "variables": [
        {"name": "sev", "label": "Impact", "type": "single_line_text", "maps_to_field": "description"}]}
    assert (await call(c, "add_catalog_variables", args))["executed"] is True
    assert fake.posts("item_option_new")[0][1]["field"] == "description"
