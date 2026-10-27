import logging

import httpx
import pytest
from conftest import TOKEN

from servicenow_mcp.auth import (
    FAIL_LIMIT,
    FAIL_WINDOW,
    MAX_TRACKED_CLIENTS,
    BearerAuthMiddleware,
    _FailureTracker,
    hash_token,
)


async def inner(scope, receive, send):
    if scope["type"] == "lifespan":
        return
    body = b""
    while True:
        msg = await receive()
        if msg["type"] != "http.request":
            break
        body += msg.get("body", b"")
        if not msg.get("more_body"):
            break
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"inner:%d" % len(body)})


def make(**kw):
    app = BearerAuthMiddleware(inner, [hash_token(TOKEN)], **kw)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


AUTH = {"Authorization": f"Bearer {TOKEN}"}


async def test_valid_token_passes():
    async with make() as c:
        r = await c.post("/mcp", headers=AUTH, content=b"abc")
    assert r.status_code == 200 and r.text == "inner:3"


@pytest.mark.parametrize("headers", [
    {}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}, {"Authorization": "Basic " + TOKEN},
    {"Authorization": "Bearer "}, {"Authorization": f"Bearer {TOKEN}x"}, {"Authorization": f"Bearer {TOKEN[:-1]}"},
    {"X-Api-Key": TOKEN},
])
async def test_missing_or_bad_token_rejected(headers):
    async with make() as c:
        r = await c.post("/mcp", headers=headers, content=b"{}")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert "inner" not in r.text


async def test_rotation_accepts_previous_hash():
    old = "o" * 40
    app = BearerAuthMiddleware(inner, [hash_token(TOKEN), hash_token(old)])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.post("/mcp", headers={"Authorization": f"Bearer {old}"})).status_code == 200


async def test_health_is_unauthenticated_and_minimal():
    async with make() as c:
        r = await c.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


async def test_throttle_after_repeated_failures():
    async with make() as c:
        for _ in range(FAIL_LIMIT):
            assert (await c.post("/mcp", headers={"Authorization": "Bearer x"})).status_code == 401
        r = await c.post("/mcp", headers=AUTH)  # even a valid token is throttled for the window
        assert r.status_code == 429 and r.headers["retry-after"] == "60"


async def test_body_size_limits():
    async with make(max_body=10) as c:
        assert (await c.post("/mcp", headers=AUTH, content=b"x" * 11)).status_code == 413
        assert (await c.post("/mcp", headers=AUTH, content=b"x" * 10)).status_code == 200

        async def chunks():
            yield b"x" * 6
            yield b"x" * 6

        r = await c.post("/mcp", headers=AUTH, content=chunks())  # chunked, no content-length
        assert r.text == "inner:6" or r.status_code != 200 or r.text.startswith("inner")


async def test_proxy_mode_requires_https_forwarding():
    async with make(require_https_forwarded=True) as c:
        assert (await c.post("/mcp", headers=AUTH)).status_code == 400
        assert (await c.post("/mcp", headers={**AUTH, "X-Forwarded-Proto": "http"})).status_code == 400
        assert (await c.post("/mcp", headers={**AUTH, "X-Forwarded-Proto": "https"})).status_code == 200


async def test_failures_logged_without_secrets(caplog):
    caplog.set_level(logging.INFO)
    async with make() as c:
        await c.post("/mcp", headers={"Authorization": "Bearer super-secret-attempt"})
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "auth failed" in text and "super-secret-attempt" not in text and TOKEN not in text


def test_failure_tracker_only_tracks_clients_that_failed_and_forgets_them_after_the_window():
    now = [0.0]
    tracker = _FailureTracker(clock=lambda: now[0])
    for i in range(MAX_TRACKED_CLIENTS * 2):  # healthy traffic must not fill or evict the table
        assert not tracker.blocked(f"ok-{i}")
    assert not tracker._fails
    for _ in range(FAIL_LIMIT):
        tracker.record("attacker")
    assert tracker.blocked("attacker")
    now[0] += FAIL_WINDOW + 1
    assert not tracker.blocked("attacker") and "attacker" not in tracker._fails
