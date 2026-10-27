"""Config rules that guard the deployment modes (systemd/Traefik and Docker)."""

import pytest
from conftest import SN_PASSWORD, TOKEN, base_env

from servicenow_mcp.auth import hash_token
from servicenow_mcp.config import ConfigError, load_settings


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "172.17.0.1", "172.18.0.1", "10.0.0.5", "192.168.1.2"])
def test_plain_http_allowed_on_private_interfaces_behind_proxy(host):
    assert load_settings(base_env(MCP_BIND_HOST=host)).bind_host == host


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "8.8.8.8", "1.1.1.1", "mcp.example.com", ""])
def test_plain_http_refused_on_public_or_wildcard(host):
    with pytest.raises(ConfigError):
        load_settings(base_env(MCP_BIND_HOST=host))


def test_container_wildcard_needs_explicit_flag_and_proxy_ack():
    ok = load_settings(base_env(MCP_BIND_HOST="0.0.0.0", MCP_BIND_ALL_IN_CONTAINER="true"))
    assert ok.bind_host == "0.0.0.0"
    with pytest.raises(ConfigError):  # flag alone, without the TLS-proxy acknowledgement
        load_settings(base_env(MCP_BIND_HOST="0.0.0.0", MCP_BIND_ALL_IN_CONTAINER="true", MCP_BEHIND_TLS_PROXY="false"))
    with pytest.raises(ConfigError):  # flag does not legalise a specific public address
        load_settings(base_env(MCP_BIND_HOST="1.1.1.1", MCP_BIND_ALL_IN_CONTAINER="true"))


def test_file_based_secrets(tmp_path):
    files = {
        "SERVICENOW_USERNAME": "svc_file_user",
        "SERVICENOW_PASSWORD": SN_PASSWORD,
        "MCP_AUTH_TOKEN_SHA256": hash_token(TOKEN) + "\n",
    }
    env = base_env(SERVICENOW_USERNAME=None, SERVICENOW_PASSWORD=None, MCP_AUTH_TOKEN_SHA256=None)
    for key, value in files.items():
        path = tmp_path / key
        path.write_text(value)
        env[f"{key}_FILE"] = str(path)
    s = load_settings(env)
    assert s.username == "svc_file_user" and s.password == SN_PASSWORD
    assert s.auth_token_hashes == (hash_token(TOKEN),)


def test_unreadable_secret_file_error_has_no_path_contents(tmp_path):
    env = base_env(SERVICENOW_PASSWORD=None, SERVICENOW_PASSWORD_FILE=str(tmp_path / "missing"))
    with pytest.raises(ConfigError) as exc:
        load_settings(env)
    assert "missing" not in str(exc.value)
