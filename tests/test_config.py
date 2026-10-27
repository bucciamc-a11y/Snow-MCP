import pytest
from conftest import SN_PASSWORD, base_env

from servicenow_mcp.config import ConfigError, load_settings, validate_instance_url


def test_valid_settings(env):
    s = load_settings(env)
    assert s.instance_url == "https://dev12345.service-now.com"
    assert s.bind_host == "127.0.0.1" and s.behind_tls_proxy
    assert SN_PASSWORD not in repr(s)
    assert len(s.auth_token_hashes) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://dev1.service-now.com",
        "https://user:pw@dev1.service-now.com",
        "https://dev1.service-now.com/api",
        "https://dev1.service-now.com?x=1",
        "https://dev1.service-now.com:8443",
        "https://evil.example.com",
        "https://service-now.com.evil.com",
        "https://127.0.0.1",
        "https://localhost",
        "ftp://dev1.service-now.com",
        "",
        "dev1.service-now.com",
    ],
)
def test_instance_url_rejected(url):
    with pytest.raises(ConfigError):
        validate_instance_url(url)


def test_instance_url_normalised_and_custom_domain():
    assert validate_instance_url("https://DEV1.Service-Now.com/") == "https://dev1.service-now.com"
    with pytest.raises(ConfigError):
        validate_instance_url("https://sn.corp.example.com")
    assert validate_instance_url("https://sn.corp.example.com", True) == "https://sn.corp.example.com"


def test_tls_required_for_public_bind():
    with pytest.raises(ConfigError, match="public or wildcard"):
        load_settings(base_env(MCP_BIND_HOST="0.0.0.0"))
    with pytest.raises(ConfigError, match="no TLS"):
        load_settings(base_env(MCP_BEHIND_TLS_PROXY="false"))


def test_native_tls_allows_public_bind():
    s = load_settings(base_env(MCP_BIND_HOST="0.0.0.0", MCP_BEHIND_TLS_PROXY=None,
                               MCP_TLS_CERT_FILE="/c.pem", MCP_TLS_KEY_FILE="/k.pem"))
    assert s.native_tls


def test_tls_files_must_pair():
    with pytest.raises(ConfigError):
        load_settings(base_env(MCP_TLS_CERT_FILE="/c.pem"))


@pytest.mark.parametrize("key,val", [
    ("MCP_AUTH_TOKEN_SHA256", "plaintext-token"), ("MCP_AUTH_TOKEN_SHA256", None),
    ("MCP_AUTH_TOKEN_SHA256_PREVIOUS", "zz"), ("SERVICENOW_USERNAME", None),
    ("SERVICENOW_PASSWORD", None), ("MCP_PUBLIC_HOSTNAME", "bad host"),
    ("MCP_BIND_PORT", "99999"), ("MCP_MAX_PAGE_SIZE", "0"), ("MCP_LOG_LEVEL", "TRACE"),
    ("MCP_REQUEST_TIMEOUT", "abc"),
])
def test_invalid_values(key, val):
    with pytest.raises(ConfigError) as exc:
        load_settings(base_env(**{key: val}))
    assert SN_PASSWORD not in str(exc.value)


def test_previous_hash_accepted():
    s = load_settings(base_env(MCP_AUTH_TOKEN_SHA256_PREVIOUS="b" * 64))
    assert len(s.auth_token_hashes) == 2
