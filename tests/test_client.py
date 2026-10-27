import json

import httpx
import pytest
from conftest import INSTANCE, SN_PASSWORD, SN_USER

from servicenow_mcp import client as client_mod
from servicenow_mcp.client import NotFoundError, ServiceNowClient, ServiceNowError
from servicenow_mcp.validation import ValidationError, build_incident_search

SYS_ID = "b" * 32


def make(handler, **kw):
    seen = []

    def wrapped(request: httpx.Request):
        seen.append(request)
        return handler(request)

    c = ServiceNowClient(INSTANCE, SN_USER, SN_PASSWORD, transport=httpx.MockTransport(wrapped), **kw)
    return c, seen


def ok(payload, headers=None):
    return httpx.Response(200, json=payload, headers=headers or {})


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def fast(_):
        return None

    monkeypatch.setattr(client_mod.asyncio, "sleep", fast)


async def test_search_builds_safe_request_and_paginates():
    rows = [{"sys_id": SYS_ID, "number": "INC0010001", "short_description": "VPN", "state": "New",
             "sys_updated_on": "2024-01-01 00:00:00", "assigned_to": "", "description": "d" * 5000}]
    c, seen = make(lambda r: ok({"result": rows}, {"X-Total-Count": "25"}))
    out = await c.search_incidents(build_incident_search(state="new", limit=1, offset=0))
    req = seen[0]
    assert req.method == "GET" and req.url.host == "dev12345.service-now.com"
    assert req.url.path == "/api/now/table/incident"
    q = dict(req.url.params)
    assert q["sysparm_query"] == "state=1^ORDERBYDESCopened_at"
    assert q["sysparm_limit"] == "1" and q["sysparm_exclude_reference_link"] == "true"
    assert "work_notes" not in q["sysparm_fields"] and "comments" not in q["sysparm_fields"]
    assert out["total_count"] == 25 and out["next_offset"] == 1 and out["count"] == 1
    rec = out["incidents"][0]
    assert rec["updated_at"] == "2024-01-01 00:00:00" and "assigned_to" not in rec
    assert rec["description"].endswith("[truncated]") and len(rec["description"]) < 4100
    await c.aclose()


async def test_last_page_has_no_next_offset():
    c, _ = make(lambda r: ok({"result": [{"number": "INC0010001"}]}, {"X-Total-Count": "1"}))
    out = await c.search_incidents(build_incident_search(limit=10))
    assert out["next_offset"] is None
    await c.aclose()


async def test_authorization_header_sent_but_not_in_url():
    c, seen = make(lambda r: ok({"result": []}))
    await c.search_incidents(build_incident_search())
    assert seen[0].headers["authorization"].startswith("Basic ")
    assert SN_PASSWORD not in str(seen[0].url)
    await c.aclose()


async def test_empty_and_404_list_is_empty():
    c, _ = make(lambda r: httpx.Response(404, json={"error": {"message": "No Record found"}}))
    out = await c.search_incidents(build_incident_search())
    assert out["count"] == 0 and out["incidents"] == []
    await c.aclose()


async def test_get_by_number_uses_query_not_path():
    c, seen = make(lambda r: ok({"result": [{"number": "INC0010001", "sys_id": SYS_ID}]}))
    rec = await c.get_record("incident", "inc0010001")
    q = dict(seen[0].url.params)
    assert seen[0].url.path == "/api/now/table/incident"
    assert q["sysparm_query"] == "number=INC0010001" and q["sysparm_limit"] == "1"
    assert rec["number"] == "INC0010001"
    await c.aclose()


async def test_get_by_sys_id_uses_path():
    c, seen = make(lambda r: ok({"result": {"number": "CHG0030001", "sys_id": SYS_ID, "state": "Scheduled"}}))
    rec = await c.get_record("change_request", SYS_ID.upper())
    assert seen[0].url.path == f"/api/now/table/change_request/{SYS_ID}"
    assert rec["state"] == "Scheduled"
    await c.aclose()


async def test_not_found_variants_are_uniform():
    for handler in (
        lambda r: httpx.Response(404, json={"error": {"message": "secret detail"}}),
        lambda r: ok({"result": []}),
        lambda r: ok({"result": {}}),
    ):
        c, _ = make(handler)
        with pytest.raises(NotFoundError) as exc:
            await c.get_record("incident", "INC0010001")
        assert "secret detail" not in str(exc.value)
        await c.aclose()


async def test_invalid_identifier_never_hits_network():
    c, seen = make(lambda r: ok({"result": []}))
    with pytest.raises(ValidationError):
        await c.get_record("incident", "INC1; drop")
    assert seen == []
    await c.aclose()


@pytest.mark.parametrize("status,code", [(401, "auth_failed"), (403, "forbidden"), (429, "rate_limited"),
                                         (400, "http_error"), (302, "redirect")])
async def test_status_mapping_hides_body(status, code):
    body = {"error": {"message": f"leak {SN_PASSWORD}", "detail": "Authorization: Basic abc"}}
    c, _ = make(lambda r: httpx.Response(status, json=body, headers={"Location": "https://evil.example"}))
    with pytest.raises(ServiceNowError) as exc:
        await c.get_record("incident", "INC0010001")
    assert exc.value.code == code
    text = str(exc.value)
    assert SN_PASSWORD not in text and "Authorization" not in text and "evil" not in text
    await c.aclose()


async def test_retry_then_success():
    calls = {"n": 0}

    def handler(r):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else ok({"result": []})

    c, _ = make(handler)
    await c.search_incidents(build_incident_search())
    assert calls["n"] == 3
    await c.aclose()


async def test_retries_exhausted_and_timeout():
    c, _ = make(lambda r: httpx.Response(503))
    with pytest.raises(ServiceNowError) as exc:
        await c.get_record("incident", "INC0010001")
    assert exc.value.code == "unavailable"
    await c.aclose()

    def boom(r):
        raise httpx.ReadTimeout("timeout", request=r)

    c, _ = make(boom)
    with pytest.raises(ServiceNowError) as exc:
        await c.get_record("incident", "INC0010001")
    assert exc.value.code == "unavailable"
    await c.aclose()


async def test_tls_failure_message_is_generic():
    def boom(r):
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] for host dev12345", request=r)

    c, _ = make(boom)
    with pytest.raises(ServiceNowError) as exc:
        await c.get_record("incident", "INC0010001")
    assert exc.value.code == "tls_error" and "dev12345" not in str(exc.value)
    await c.aclose()


async def test_bad_json_and_shape():
    c, _ = make(lambda r: httpx.Response(200, content=b"<html>login</html>"))
    with pytest.raises(ServiceNowError) as exc:
        await c.get_record("incident", "INC0010001")
    assert exc.value.code == "bad_response"
    await c.aclose()
    c, _ = make(lambda r: ok({"nope": 1}))
    with pytest.raises(ServiceNowError):
        await c.get_record("incident", "INC0010001")
    await c.aclose()


async def test_response_size_limit(monkeypatch):
    monkeypatch.setattr(client_mod, "MAX_RESPONSE_BYTES", 100)
    c, _ = make(lambda r: httpx.Response(200, content=json.dumps({"result": "x" * 500}).encode()))
    with pytest.raises(ServiceNowError) as exc:
        await c.get_record("incident", "INC0010001")
    assert exc.value.code == "too_large"
    await c.aclose()


async def test_server_side_rate_limit():
    c, _ = make(lambda r: ok({"result": []}), rate_limit_per_minute=2)
    await c.search_incidents(build_incident_search())
    await c.search_incidents(build_incident_search())
    with pytest.raises(ServiceNowError) as exc:
        await c.search_incidents(build_incident_search())
    assert exc.value.code == "rate_limited"
    await c.aclose()


async def test_http_base_url_refused():
    with pytest.raises(ValueError):
        ServiceNowClient("http://dev1.service-now.com", "u", "p")


async def test_connectivity_check_reports_codes_only():
    def handler(r):
        return ok({"result": []}) if r.url.path.endswith("/incident") else httpx.Response(403, text=SN_PASSWORD)

    c, _ = make(handler)
    res = await c.check_connectivity()
    assert res == {"incident": "ok", "change_request": "forbidden"}
    await c.aclose()


async def test_only_get_requests_to_allowed_tables():
    c, seen = make(lambda r: ok({"result": []}))
    await c.search_incidents(build_incident_search())
    await c.check_connectivity()
    with pytest.raises(NotFoundError):
        await c.get_record("change_request", "CHG0030001")
    assert {r.method for r in seen} == {"GET"}
    assert {r.url.path for r in seen} <= {"/api/now/table/incident", "/api/now/table/change_request"}
    await c.aclose()
