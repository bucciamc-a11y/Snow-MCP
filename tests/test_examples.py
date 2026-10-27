"""The shipped example env files must be loadable by systemd and by the server."""

import re
from pathlib import Path

import pytest
from conftest import base_env

from servicenow_mcp.config import load_settings

ROOT = Path(__file__).resolve().parent.parent
FILES = [ROOT / "config" / "config.env.example", ROOT / "config" / "secrets.env.example",
         ROOT / "docker" / ".env.example"]


def parse(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        assert re.match(r'^[A-Z][A-Z0-9_]*=("[^"]*"|[^\s#"]*)$', line), f"{path.name}: bad line {line!r}"
        key, _, value = line.partition("=")
        out[key] = value.strip('"')
    return out


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_example_lines_have_no_trailing_comments(path):
    assert parse(path)


def test_example_config_defaults_are_read_only_and_loadable():
    cfg = parse(ROOT / "config" / "config.env.example")
    env = base_env(**{k: v for k, v in cfg.items() if k.startswith("MCP_") and k not in ("MCP_PUBLIC_HOSTNAME",)})
    s = load_settings(env)
    assert not (s.enable_incident_writes or s.enable_incident_assignment or s.enable_change_approval
                or s.enable_oncall_read or s.enable_oncall_writes)
    assert s.assignment_teams == ()


def test_no_files_start_with_a_bom():
    for path in [*ROOT.glob("scripts/*.sh"), ROOT / "setup.sh", ROOT / "deploy" / "servicenow-mcp.service", *FILES]:
        assert not path.read_bytes().startswith(b"\xef\xbb\xbf"), path.name
        assert b"\r" not in path.read_bytes(), f"{path.name} has CRLF line endings"
