"""ServiceNow Table API client with a closed set of operations.

Security properties:
  * every request goes through ``_request``, which checks (method, table) against the
    ``_READ_TABLES`` / ``_WRITES`` allowlists. There is no DELETE anywhere, and PUT is never used.
  * the base URL is fixed at construction (https, validated host); paths are built from
    constants and validated sys_ids, never from caller input
  * redirects are not followed, TLS verification is on, env proxies are ignored
  * request/response sizes, timeouts, concurrency and request rates (separate, lower rate for
    writes) are bounded
  * reads are retried; writes are NEVER retried (a retried POST could create duplicates). A write
    that times out reports an unknown outcome so the caller verifies before trying again.
  * errors are translated to fixed messages; URLs and the Authorization header never appear in
    exceptions or logs. For rejected writes only a short sanitised ServiceNow validation message is passed on.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import httpx

from .validation import (
    CHANGE_NUMBER_RE,
    INCIDENT_NUMBER_RE,
    PROBLEM_NUMBER_RE,
    ChangeSearch,
    IncidentSearch,
    ProblemSearch,
    validate_identifier,
    validate_sys_id,
)

log = logging.getLogger("servicenow_mcp.client")

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
RETRY_STATUSES = {502, 503, 504}
MAX_RETRIES = 2

INCIDENT_FIELDS = (
    "sys_id,number,short_description,description,state,priority,urgency,impact,category,"
    "subcategory,caller_id,assigned_to,assignment_group,business_service,cmdb_ci,active,"
    "opened_at,opened_by,sys_updated_on,resolved_at,closed_at,close_code,problem_id"
)
PROBLEM_FIELDS = (
    "sys_id,number,short_description,description,state,priority,urgency,impact,category,subcategory,"
    "assignment_group,assigned_to,business_service,cmdb_ci,known_error,active,cause_notes,workaround,"
    "fix_notes,resolution_code,related_incidents,first_reported_by_task,opened_at,opened_by,"
    "sys_updated_on,resolved_at,closed_at"
)
CHANGE_FIELDS = (
    "sys_id,number,short_description,description,state,type,priority,risk,impact,category,"
    "requested_by,assigned_to,assignment_group,business_service,cmdb_ci,approval,justification,"
    "implementation_plan,backout_plan,test_plan,start_date,end_date,opened_at,sys_updated_on,closed_at,"
    "close_code,active"
)
APPROVER_FIELDS = "sys_id,approver,state,sysapproval,comments,sys_updated_on"
ROTA_FIELDS = "sys_id,name,group,active,description,schedule,time_zone,sys_updated_on"
ROSTER_FIELDS = "sys_id,name,rota,order,active,schedule,time_zone,sys_updated_on"
MEMBER_FIELDS = "sys_id,member,roster,order,from,to,sys_updated_on"
_TEXT_FIELDS = {"description", "justification", "implementation_plan", "backout_plan", "test_plan",
                "cause_notes", "workaround", "fix_notes"}

_TABLES = {
    "incident": ("/api/now/table/incident", INCIDENT_FIELDS, INCIDENT_NUMBER_RE, "incident"),
    "change_request": (
        "/api/now/table/change_request",
        CHANGE_FIELDS,
        CHANGE_NUMBER_RE,
        "change request",
    ),
    "problem": ("/api/now/table/problem", PROBLEM_FIELDS, PROBLEM_NUMBER_RE, "problem"),
}
_TABLE_PATH = "/api/now/table/"
_WHOISONCALL_PATH = "/api/now/on_call_rota/whoisoncall"

# What may be read, and what may be written. Anything else raises before a request is built.
_READ_TABLES = {"incident", "change_request", "problem", "sysapproval_approver", "cmn_rota",
                "cmn_rota_roster", "cmn_rota_member"}
_WRITES = {
    ("POST", "incident"),
    ("PATCH", "incident"),
    ("POST", "problem"),
    ("PATCH", "problem"),
    ("POST", "change_request"),
    ("PATCH", "change_request"),
    ("PATCH", "sysapproval_approver"),
    ("POST", "cmn_rota_member"),
    ("PATCH", "cmn_rota_member"),
}
_SYS_ID_PATH_RE = re.compile(r"^[0-9a-f]{32}\Z")
_SAFE_MESSAGE_RE = re.compile(r"[^\x20-\x7e]")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._@-]{1,80}\Z")


class ServiceNowError(Exception):
    """Failure with a message that is safe to show to an agent."""

    def __init__(self, message: str, *, code: str = "error", status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


class NotFoundError(ServiceNowError):
    def __init__(self) -> None:
        super().__init__("record not found or not accessible to the integration account",
                         code="not_found", status=404)


_STATUS_MESSAGES = {
    401: (
        "auth_failed",
        (
            "ServiceNow rejected the integration credentials (HTTP 401); "
            "ask an administrator to check the integration account"
        ),
    ),
    403: ("forbidden", "the integration account is not permitted to do this (HTTP 403)"),
    429: ("rate_limited", "ServiceNow is rate limiting requests (HTTP 429); retry later"),
}


class _RateLimiter:
    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic):
        self._per_minute = per_minute
        self._clock = clock
        self._hits: deque[float] = deque()

    def allow(self) -> bool:
        now = self._clock()
        while self._hits and now - self._hits[0] > 60:
            self._hits.popleft()
        if len(self._hits) >= self._per_minute:
            return False
        self._hits.append(now)
        return True


def _clean(record: dict[str, Any], max_text: int) -> dict[str, Any]:
    """Flatten display values and truncate long text."""
    out: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, dict):  # reference field, if links were not excluded
            value = value.get("display_value") or value.get("value") or ""
        if value is None or value == "" or isinstance(value, list):
            continue
        if key in _TEXT_FIELDS and isinstance(value, str) and len(value) > max_text:
            value = value[:max_text] + "…[truncated]"
        out["updated_at" if key == "sys_updated_on" else key] = value
    return out


def _safe_detail(body: bytes) -> str:
    """Short, printable-ASCII excerpt of ServiceNow's validation message (no URLs/headers/bodies)."""
    try:
        data = json.loads(body)
        message = str(data.get("error", {}).get("message", ""))
    except (ValueError, AttributeError):
        return ""
    return _SAFE_MESSAGE_RE.sub("", message)[:200].strip()


class _Retryable(Exception):
    pass


class ServiceNowClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        timeout: float = 15.0,
        rate_limit_per_minute: int = 60,
        write_rate_limit_per_minute: int = 20,
        max_text_chars: int = 4000,
        transport: httpx.AsyncBaseTransport | None = None,
        max_concurrency: int = 4,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("ServiceNow base URL must be https")
        self._max_text = max_text_chars
        self._limiter = _RateLimiter(rate_limit_per_minute)
        self._write_limiter = _RateLimiter(write_rate_limit_per_minute)
        self._sem = asyncio.Semaphore(max_concurrency)
        self._http = httpx.AsyncClient(
            base_url=base_url,
            auth=httpx.BasicAuth(username, password),
            headers={"Accept": "application/json", "User-Agent": "servicenow-mcp/2.0"},
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)),
            follow_redirects=False,
            verify=True,
            trust_env=False,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- low level ---------------------------------------------------------
    @staticmethod
    def _table_path(table: str, sys_id: str | None = None) -> str:
        if sys_id is None:
            return f"{_TABLE_PATH}{table}"
        if not _SYS_ID_PATH_RE.match(sys_id):
            raise ValueError("internal: sys_id in path must be 32 hex characters")
        return f"{_TABLE_PATH}{table}/{sys_id}"

    async def _request(
        self,
        method: str,
        table: str,
        *,
        sys_id: str | None = None,
        params: dict[str, str] | None = None,
        body: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], int | None]:
        write = method != "GET"
        if write and (method, table) not in _WRITES:
            raise ValueError(f"internal: {method} on {table} is not permitted")
        if not write and table not in _READ_TABLES:
            raise ValueError(f"internal: reading {table} is not permitted")
        return await self._send(method, self._table_path(table, sys_id), params or {}, body)

    async def _get(self, path: str, params: dict[str, str]) -> tuple[dict[str, Any], int | None]:
        """GET of a fixed, constant path (table list/record paths or the on-call API)."""
        if not (path.startswith(_TABLE_PATH) or path == _WHOISONCALL_PATH):
            raise ValueError("internal: path not permitted")
        if path.startswith(_TABLE_PATH):
            table = path[len(_TABLE_PATH):].split("/")[0]
            if table not in _READ_TABLES:
                raise ValueError(f"internal: reading {table} is not permitted")
        return await self._send("GET", path, params, None)

    async def _send(
        self, method: str, path: str, params: dict[str, str], body: dict[str, str] | None
    ) -> tuple[dict[str, Any], int | None]:
        write = method != "GET"
        limiter = self._write_limiter if write else self._limiter
        if not limiter.allow():
            raise ServiceNowError("server-side request rate limit reached; retry shortly",
                                  code="rate_limited")
        async with self._sem:
            attempts = 1 if write else MAX_RETRIES + 1
            for attempt in range(attempts):
                try:
                    return await self._send_once(method, path, params, body)
                except _Retryable:
                    if write:
                        raise ServiceNowError(
                            "ServiceNow did not confirm the change (timeout or server error); the "
                            "outcome is UNKNOWN - verify the record in ServiceNow before retrying",
                            code="unknown_outcome",
                        ) from None
                    if attempt == attempts - 1:
                        break
                    await asyncio.sleep(0.5 * (2**attempt))
        raise ServiceNowError("ServiceNow is unavailable or timed out; retry later", code="unavailable")

    async def _send_once(
        self, method: str, path: str, params: dict[str, str], body: dict[str, str] | None
    ) -> tuple[dict[str, Any], int | None]:
        write = method != "GET"
        try:
            async with self._http.stream(method, path, params=params, json=body) as resp:
                status = resp.status_code
                if status in RETRY_STATUSES or (write and status >= 500):
                    raise _Retryable
                payload = bytearray()
                async for chunk in resp.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > MAX_RESPONSE_BYTES:
                        raise ServiceNowError("ServiceNow response exceeded the size limit",
                                              code="too_large")
                total = resp.headers.get("X-Total-Count")
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            if isinstance(exc, httpx.ConnectError) and "CERTIFICATE" in str(exc).upper():
                raise ServiceNowError("TLS certificate verification for ServiceNow failed",
                                      code="tls_error") from None
            raise _Retryable from None
        except httpx.HTTPError:
            raise ServiceNowError("could not communicate with ServiceNow", code="network") from None

        if status == 404:
            raise NotFoundError
        if status in _STATUS_MESSAGES:
            code, msg = _STATUS_MESSAGES[status]
            raise ServiceNowError(msg, code=code, status=status)
        if 300 <= status < 400:
            raise ServiceNowError("unexpected redirect from ServiceNow; request refused",
                                  code="redirect", status=status)
        if status == 400 and write:
            detail = _safe_detail(bytes(payload))
            raise ServiceNowError(
                "ServiceNow rejected the data (HTTP 400)" + (f": {detail}" if detail else ""),
                code="rejected", status=400,
            )
        if status not in (200, 201):
            raise ServiceNowError(f"ServiceNow returned HTTP {status}", code="http_error", status=status)
        try:
            data = json.loads(bytes(payload))
        except ValueError:
            raise ServiceNowError("ServiceNow returned a non-JSON response; check the instance URL",
                                  code="bad_response") from None
        if not isinstance(data, dict) or "result" not in data:
            raise ServiceNowError("unexpected response shape from ServiceNow", code="bad_response")
        return data, int(total) if total and total.isdigit() else None

    @staticmethod
    def _display_params(fields: str) -> dict[str, str]:
        return {
            "sysparm_fields": fields,
            "sysparm_display_value": "true",
            "sysparm_exclude_reference_link": "true",
        }

    def _one(self, data: dict[str, Any]) -> dict[str, Any]:
        result = data["result"]
        if not isinstance(result, dict) or not result:
            raise NotFoundError
        return _clean(result, self._max_text)

    async def _list(self, table: str, query: str, fields: str, limit: int, offset: int = 0
                    ) -> tuple[list[dict[str, Any]], int | None]:
        params = self._display_params(fields)
        params.update({"sysparm_query": query, "sysparm_limit": str(limit), "sysparm_offset": str(offset)})
        try:
            data, total = await self._request("GET", table, params=params)
        except NotFoundError:  # some API versions return 404 for an empty list
            return [], 0
        rows = data["result"]
        if rows == {}:  # some endpoints answer an empty match with an empty object
            rows = []
        if not isinstance(rows, list):
            raise ServiceNowError("unexpected response shape from ServiceNow", code="bad_response")
        return [_clean(r, self._max_text) for r in rows if isinstance(r, dict)], total

    # -- incident / change reads -----------------------------------------------
    async def search_incidents(self, search: IncidentSearch) -> dict[str, Any]:
        records, total = await self._list("incident", search.query, INCIDENT_FIELDS, search.limit, search.offset)
        next_offset = None
        more = search.offset + len(records) < total if total is not None else len(records) == search.limit
        if more:
            next_offset = search.offset + len(records)
        return {
            "count": len(records),
            "total_count": total,
            "offset": search.offset,
            "next_offset": next_offset,
            "incidents": records,
        }

    async def get_record(self, table: str, identifier: str) -> dict[str, Any]:
        _, fields, number_re, label = _TABLES[table]
        kind, value = validate_identifier(identifier, number_re, label.split()[0])
        if kind == "sys_id":
            data, _ = await self._request("GET", table, sys_id=value, params=self._display_params(fields))
            return self._one(data)
        rows, _ = await self._list(table, f"number={value}", fields, 1)
        if not rows:
            raise NotFoundError
        return rows[0]

    async def get_incident_raw(self, identifier: str) -> dict[str, str]:
        """Raw (non-display) sys_id/number/state, used for write guards that must not depend on locale."""
        kind, value = validate_identifier(identifier, INCIDENT_NUMBER_RE, "incident")
        params = {"sysparm_fields": "sys_id,number,state,assignment_group,problem_id",
                  "sysparm_display_value": "false",
                  "sysparm_exclude_reference_link": "true"}
        if kind == "sys_id":
            data, _ = await self._request("GET", "incident", sys_id=value, params=params)
            result = data["result"]
        else:
            params.update({"sysparm_query": f"number={value}", "sysparm_limit": "1"})
            data, _ = await self._request("GET", "incident", params=params)
            rows = data["result"]
            result = rows[0] if isinstance(rows, list) and rows else None
        if not isinstance(result, dict) or not result.get("sys_id"):
            raise NotFoundError
        flat = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in result.items()}
        return {k: str(v) for k, v in flat.items() if v is not None}

    # -- problem reads ---------------------------------------------------------
    async def search_problems(self, search: ProblemSearch) -> dict[str, Any]:
        records, total = await self._list("problem", search.query, PROBLEM_FIELDS, search.limit, search.offset)
        more = search.offset + len(records) < total if total is not None else len(records) == search.limit
        return {
            "count": len(records),
            "total_count": total,
            "offset": search.offset,
            "next_offset": search.offset + len(records) if more else None,
            "problems": records,
        }

    async def get_problem_raw(self, identifier: str) -> dict[str, str]:
        """Raw (non-display) sys_id/number/state, used for write guards that must not depend on locale."""
        kind, value = validate_identifier(identifier, PROBLEM_NUMBER_RE, "problem")
        params = {"sysparm_fields": "sys_id,number,state,assignment_group", "sysparm_display_value": "false",
                  "sysparm_exclude_reference_link": "true"}
        if kind == "sys_id":
            data, _ = await self._request("GET", "problem", sys_id=value, params=params)
            result = data["result"]
        else:
            params.update({"sysparm_query": f"number={value}", "sysparm_limit": "1"})
            data, _ = await self._request("GET", "problem", params=params)
            rows = data["result"]
            result = rows[0] if isinstance(rows, list) and rows else None
        if not isinstance(result, dict) or not result.get("sys_id"):
            raise NotFoundError
        flat = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in result.items()}
        return {k: str(v) for k, v in flat.items() if v is not None}

    async def linked_incidents(self, problem_sys_id: str, limit: int = 25) -> dict[str, Any]:
        """Incidents whose ``problem_id`` points at this problem (newest first)."""
        validate_sys_id(problem_sys_id, "problem sys_id")
        rows, total = await self._list(
            "incident", f"problem_id={problem_sys_id}^ORDERBYDESCopened_at", INCIDENT_FIELDS, limit
        )
        return {"count": len(rows), "total_count": total, "incidents": rows}

    # -- problem writes --------------------------------------------------------
    async def create_problem(self, fields: dict[str, str]) -> dict[str, Any]:
        data, _ = await self._request("POST", "problem", params=self._display_params(PROBLEM_FIELDS), body=fields)
        return self._one(data)

    async def update_problem(self, sys_id: str, fields: dict[str, str]) -> dict[str, Any]:
        validate_sys_id(sys_id, "sys_id")
        data, _ = await self._request(
            "PATCH", "problem", sys_id=sys_id, params=self._display_params(PROBLEM_FIELDS), body=fields
        )
        return self._one(data)

    # -- change reads / writes ---------------------------------------------------
    async def search_change_requests(self, search: ChangeSearch) -> dict[str, Any]:
        records, total = await self._list("change_request", search.query, CHANGE_FIELDS, search.limit, search.offset)
        more = search.offset + len(records) < total if total is not None else len(records) == search.limit
        return {
            "count": len(records),
            "total_count": total,
            "offset": search.offset,
            "next_offset": search.offset + len(records) if more else None,
            "change_requests": records,
        }

    async def get_change_raw(self, identifier: str) -> dict[str, str]:
        """Raw (non-display) sys_id/number/state/type, used for write guards that must not depend on locale."""
        kind, value = validate_identifier(identifier, CHANGE_NUMBER_RE, "change")
        params = {"sysparm_fields": "sys_id,number,state,type,approval", "sysparm_display_value": "false",
                  "sysparm_exclude_reference_link": "true"}
        if kind == "sys_id":
            data, _ = await self._request("GET", "change_request", sys_id=value, params=params)
            result = data["result"]
        else:
            params.update({"sysparm_query": f"number={value}", "sysparm_limit": "1"})
            data, _ = await self._request("GET", "change_request", params=params)
            rows = data["result"]
            result = rows[0] if isinstance(rows, list) and rows else None
        if not isinstance(result, dict) or not result.get("sys_id"):
            raise NotFoundError
        flat = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in result.items()}
        return {k: str(v) for k, v in flat.items() if v is not None}

    async def create_change(self, fields: dict[str, str]) -> dict[str, Any]:
        data, _ = await self._request("POST", "change_request", params=self._display_params(CHANGE_FIELDS),
                                      body=fields)
        return self._one(data)

    async def update_change(self, sys_id: str, fields: dict[str, str]) -> dict[str, Any]:
        validate_sys_id(sys_id, "sys_id")
        data, _ = await self._request(
            "PATCH", "change_request", sys_id=sys_id, params=self._display_params(CHANGE_FIELDS), body=fields
        )
        return self._one(data)

    # -- incident writes -------------------------------------------------------
    async def create_incident(self, fields: dict[str, str]) -> dict[str, Any]:
        data, _ = await self._request("POST", "incident", params=self._display_params(INCIDENT_FIELDS), body=fields)
        return self._one(data)

    async def update_incident(self, sys_id: str, fields: dict[str, str]) -> dict[str, Any]:
        validate_sys_id(sys_id, "sys_id")
        data, _ = await self._request(
            "PATCH", "incident", sys_id=sys_id, params=self._display_params(INCIDENT_FIELDS), body=fields
        )
        return self._one(data)

    # -- change approval ---------------------------------------------------------
    async def find_approval_ids(self, change_sys_id: str, username: str) -> list[str]:
        """Raw sys_ids of the pending approval records (display lookups hide them behind labels)."""
        validate_sys_id(change_sys_id, "change sys_id")
        if not _USERNAME_RE.match(username):
            raise ValueError("internal: invalid user name for approval lookup")
        params = {
            "sysparm_query": f"sysapproval={change_sys_id}^approver.user_name={username}^state=requested",
            "sysparm_fields": "sys_id", "sysparm_limit": "3", "sysparm_display_value": "false",
        }
        try:
            data, _ = await self._request("GET", "sysapproval_approver", params=params)
        except NotFoundError:
            return []
        rows = data["result"]
        return [r["sys_id"] for r in rows if isinstance(r, dict) and r.get("sys_id")] if isinstance(rows, list) else []

    async def approve(self, approval_sys_id: str, comments: str | None) -> dict[str, Any]:
        validate_sys_id(approval_sys_id, "approval sys_id")
        body = {"state": "approved"}
        if comments:
            body["comments"] = comments
        data, _ = await self._request(
            "PATCH", "sysapproval_approver", sys_id=approval_sys_id,
            params=self._display_params(APPROVER_FIELDS), body=body,
        )
        return self._one(data)

    # -- on-call -----------------------------------------------------------------
    async def list_rotas(self, query: str, limit: int, offset: int) -> dict[str, Any]:
        rows, total = await self._list("cmn_rota", query, ROTA_FIELDS, limit, offset)
        more = offset + len(rows) < total if total is not None else len(rows) == limit
        return {"count": len(rows), "total_count": total, "next_offset": offset + len(rows) if more else None,
                "rotas": rows}

    async def get_roster(self, rota_sys_id: str) -> dict[str, Any]:
        validate_sys_id(rota_sys_id, "rota_id")
        rosters, _ = await self._list("cmn_rota_roster", f"rota={rota_sys_id}^ORDERBYorder", ROSTER_FIELDS, 50)
        members: list[dict[str, Any]] = []
        for roster in rosters:
            rid = roster.get("sys_id")
            if rid:
                rows, _ = await self._list("cmn_rota_member", f"roster={rid}^ORDERBYorder", MEMBER_FIELDS, 100)
                members.extend(rows)
        return {"rosters": rosters, "members": members, "count": len(members)}

    async def get_member(self, member_sys_id: str) -> dict[str, Any]:
        validate_sys_id(member_sys_id, "member_record_id")
        data, _ = await self._request("GET", "cmn_rota_member", sys_id=member_sys_id,
                                      params=self._display_params(MEMBER_FIELDS))
        return self._one(data)

    async def whois_oncall(self, group_ids: list[str]) -> dict[str, Any]:
        ids = [validate_sys_id(g, "group_id") for g in group_ids]
        data, _ = await self._get(_WHOISONCALL_PATH, {"group_ids": ",".join(ids)})
        result = data["result"]
        items = result if isinstance(result, list) else [result]
        return {"oncall": [_clean(i, self._max_text) for i in items if isinstance(i, dict)]}

    async def create_member(self, fields: dict[str, str]) -> dict[str, Any]:
        data, _ = await self._request("POST", "cmn_rota_member", params=self._display_params(MEMBER_FIELDS), body=fields)
        return self._one(data)

    async def update_member(self, member_sys_id: str, fields: dict[str, str]) -> dict[str, Any]:
        validate_sys_id(member_sys_id, "member_record_id")
        data, _ = await self._request("PATCH", "cmn_rota_member", sys_id=member_sys_id,
                                      params=self._display_params(MEMBER_FIELDS), body=fields)
        return self._one(data)

    # -- connectivity check ------------------------------------------------------
    async def check_connectivity(self, tables: tuple[str, ...] = ("incident", "change_request")) -> dict[str, str]:
        """Probe read access to each table with a one-row query (used by the check CLI).

        Returns table -> "ok" or a short error code; never any response content.
        """
        out = {}
        for table in tables:
            try:
                await self._request("GET", table, params={"sysparm_limit": "1", "sysparm_fields": "sys_id"})
                out[table] = "ok"
            except NotFoundError:
                out[table] = "ok"  # table readable; query matched nothing
            except ServiceNowError as exc:
                out[table] = exc.code
        return out
