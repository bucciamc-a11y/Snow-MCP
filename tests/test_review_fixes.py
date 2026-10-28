"""Regression tests for defects found in the code review (see FIXES.md)."""

import re
from pathlib import Path

import httpx
import pytest
from conftest import INSTANCE, SN_PASSWORD, SN_USER, base_env

from servicenow_mcp import client as client_mod
from servicenow_mcp.client import ServiceNowClient, ServiceNowError
from servicenow_mcp.config import load_settings
from servicenow_mcp.logging_setup import JsonFormatter
from servicenow_mcp.validation import (
    ValidationError,
    build_change_fields,
    build_incident_fields,
    build_problem_fields,
    validate_text,
)

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def fast(_):
        return None

    monkeypatch.setattr(client_mod.asyncio, "sleep", fast)


def _client(handler):
    seen = []

    def wrapped(request: httpx.Request):
        seen.append(request)
        return handler(request)

    return ServiceNowClient(INSTANCE, SN_USER, SN_PASSWORD, transport=httpx.MockTransport(wrapped)), seen


# --- 1. a connection that drops mid-write must be reported as an UNKNOWN outcome -----------------------
@pytest.mark.parametrize("exc_type", [httpx.ReadError, httpx.WriteError, httpx.CloseError])
async def test_write_network_error_reports_unknown_outcome_and_is_not_retried(exc_type):
    def boom(request):
        raise exc_type("connection reset", request=request)

    c, seen = _client(boom)
    with pytest.raises(ServiceNowError) as exc:
        await c.create_incident({"short_description": "x"})
    assert exc.value.code == "unknown_outcome" and "UNKNOWN" in str(exc.value)
    assert len(seen) == 1  # a retried POST could create a duplicate
    await c.aclose()


async def test_read_network_error_is_retried():
    calls = []

    def flaky(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadError("connection reset", request=request)
        return httpx.Response(200, json={"result": [{"number": "INC0010001"}]})

    c, _ = _client(flaky)
    rows, _ = await c._list("incident", "active=true", "number", 1)
    assert rows == [{"number": "INC0010001"}] and len(calls) == 2
    await c.aclose()


# --- 2. a lone carriage return must not slip past the single-line check ----------------------------------
@pytest.mark.parametrize("builder", [build_incident_fields, build_problem_fields, build_change_fields])
def test_lone_carriage_return_is_not_allowed_in_short_description(builder):
    kwargs = {"state_map": {}} if builder is build_problem_fields else {}
    with pytest.raises(ValidationError):
        builder(short_description="first line\rsecond line", **kwargs)


def test_validate_text_normalises_line_endings():
    assert validate_text("a\r\nb\rc", "x", 100) == "a\nb\nc"


# --- 3. docker-compose must not hand the server an empty value the config deliberately rejects ------------
def _compose_environment(overrides):
    """Evaluate the compose `environment:` block the way compose substitutes ${VAR:-default}."""
    out = {}
    pattern = re.compile(r"^ {6}([A-Z][A-Z0-9_]*): (\S+?)(?:\s+#.*)?$")
    for line in (ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8").splitlines():
        m = pattern.match(line)
        if not m:
            continue
        name, raw = m.group(1), m.group(2).strip('"')
        sub = re.fullmatch(r"\$\{([A-Z0-9_]+)(?::-(.*))?\}", raw)
        if sub:
            raw = overrides.get(sub.group(1), sub.group(2) or "")
        out[name] = raw
    return out


def test_compose_defaults_load_when_all_non_assignment_features_are_enabled():
    flags = {f"MCP_ENABLE_{n}": "true" for n in (
        "INCIDENT_WRITES", "CHANGE_APPROVAL", "ONCALL_READ", "ONCALL_WRITES", "PROBLEM_READ", "PROBLEM_WRITES",
        "CHANGE_READ", "CHANGE_WRITES")}
    env = _compose_environment({**flags, "SERVICENOW_INSTANCE_URL": INSTANCE, "MCP_PUBLIC_HOSTNAME": "mcp.example.com"})
    # secrets arrive as *_FILE paths in the container; supply the equivalents directly for this check
    secrets = base_env()
    env = {k: v for k, v in env.items() if not k.endswith("_FILE")}
    env.update({k: secrets[k] for k in ("SERVICENOW_USERNAME", "SERVICENOW_PASSWORD", "MCP_AUTH_TOKEN_SHA256")})
    settings = load_settings(env)  # raised ConfigError for an empty MCP_CHANGE_CREATE_TYPES before the fix
    assert settings.change_create_types == ("normal", "standard")
    assert settings.enable_change_writes and settings.enable_problem_writes


# --- 4. the JSON log formatter tolerates a missing exception type -----------------------------------------
def test_json_formatter_handles_exc_info_without_type():
    import logging

    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "msg", None, None)
    record.exc_info = (None, None, None)
    assert '"msg": "msg"' in JsonFormatter().format(record)


# --- 5. the unprivileged systemd service cannot bind ports below 1024 ----------------------------------------
def test_configure_script_never_assigns_a_privileged_port():
    unit = (ROOT / "deploy" / "servicenow-mcp.service").read_text(encoding="utf-8")
    assert "CapabilityBoundingSet=\n" in unit and "AmbientCapabilities=\n" in unit  # no CAP_NET_BIND_SERVICE
    script = (ROOT / "scripts" / "configure.sh").read_text(encoding="utf-8")
    for value in re.findall(r"^\s*PORT=(\d+)\s*$", script, flags=re.MULTILINE):
        assert int(value) >= 1024, f"configure.sh sets PORT={value}, which the service cannot bind"
    assert "PORT=443" not in script


# --- 6. compose splits a comma-separated tmpfs entry into separate (invalid) mounts --------------------------
def test_compose_tmpfs_entries_are_single_absolute_paths():
    text = (ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
    entries = re.findall(r"^\s*tmpfs:\s*\[(.*)\]\s*$", text, flags=re.MULTILINE)
    assert entries, "expected a tmpfs line"
    for entry in entries:
        assert "," not in entry, f"tmpfs entry {entry!r} would be split into several mounts by docker compose"
        assert entry.strip().startswith("/")
