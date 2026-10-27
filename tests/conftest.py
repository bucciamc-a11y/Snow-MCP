import hashlib

import pytest

TOKEN = "t" * 20 + "-test-bearer-token-not-real-" + "x" * 10
SN_PASSWORD = "sn-pass-NOT-REAL-12345"
SN_USER = "svc_test_user"
INSTANCE = "https://dev12345.service-now.com"


def base_env(**overrides):
    env = {
        "SERVICENOW_INSTANCE_URL": INSTANCE,
        "SERVICENOW_USERNAME": SN_USER,
        "SERVICENOW_PASSWORD": SN_PASSWORD,
        "MCP_AUTH_TOKEN_SHA256": hashlib.sha256(TOKEN.encode()).hexdigest(),
        "MCP_PUBLIC_HOSTNAME": "mcp.example.com",
        "MCP_BEHIND_TLS_PROXY": "true",
    }
    env.update(overrides)
    return {k: v for k, v in env.items() if v is not None}


@pytest.fixture
def env():
    return base_env()
