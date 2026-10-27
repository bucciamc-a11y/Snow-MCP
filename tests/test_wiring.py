"""Every feature flag and tool must be wired into config, setup scripts, compose, Hermes include list and skill."""

import dataclasses
import re
from pathlib import Path

import pytest

from servicenow_mcp.config import Settings

ROOT = Path(__file__).resolve().parent.parent
SERVER = (ROOT / "src" / "servicenow_mcp" / "server.py").read_text(encoding="utf-8")
FLAGS = sorted("MCP_" + f.name.upper() for f in dataclasses.fields(Settings) if f.name.startswith("enable_"))
TOOLS = sorted(set(re.findall(r"@mcp\.tool\([^)]*\)\s+async def (\w+)\(", SERVER)))

FLAG_FILES = [
    "config/config.env.example",
    "docker/.env.example",
    "docker/docker-compose.yml",
    "scripts/configure.sh",
    "scripts/docker-setup.sh",
    "scripts/hermes-instructions.sh",
]
TOOL_FILES = ["scripts/hermes-instructions.sh", "skills/hermes/servicenow/SKILL.md", "README.md"]


def text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_discovery_finds_flags_and_tools():
    assert "MCP_ENABLE_PROBLEM_WRITES" in FLAGS
    assert {"search_problems", "get_problem", "create_problem", "update_problem",
            "resolve_problem", "link_incident_to_problem", "get_incident"} <= set(TOOLS)


@pytest.mark.parametrize("rel", FLAG_FILES)
@pytest.mark.parametrize("flag", FLAGS)
def test_flag_is_wired(rel, flag):
    if flag == "MCP_ENABLE_INCIDENT_ASSIGNMENT" and rel.endswith("hermes-instructions.sh"):
        pytest.skip("assign tools are listed under their own flag name below")
    assert flag in text(rel), f"{flag} missing from {rel}"


@pytest.mark.parametrize("rel", TOOL_FILES)
@pytest.mark.parametrize("tool", TOOLS)
def test_tool_is_documented(rel, tool):
    assert tool in text(rel), f"{tool} missing from {rel}"


def test_problem_state_setting_wired():
    for rel in FLAG_FILES[:5]:
        assert "MCP_PROBLEM_STATES" in text(rel), rel


SKILL_FILES = ["generic/servicenow/SKILL.md", "claude-desktop/servicenow/SKILL.md", "openclaw/servicenow/SKILL.md",
               "paperclip/servicenow/SKILL.md", "hermes/servicenow/SKILL.md", "chatgpt-desktop/instructions.md"]


def test_generated_skills_are_up_to_date():
    import subprocess
    import sys

    r = subprocess.run([sys.executable, str(ROOT / "skills" / "build.py"), "--check"],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("rel", SKILL_FILES)
@pytest.mark.parametrize("tool", TOOLS)
def test_every_skill_mentions_every_tool(rel, tool):
    assert tool in (ROOT / "skills" / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", SKILL_FILES)
def test_skills_hold_no_secrets_or_private_hosts(rel):
    text = (ROOT / "skills" / rel).read_text(encoding="utf-8")
    assert not re.search(r"srv\d{5,}", text)
    assert not re.search(r"Bearer [A-Za-z0-9_\-]{24,}", text)
