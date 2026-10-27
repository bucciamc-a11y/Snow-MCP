"""ASGI middleware protecting the MCP endpoint.

Hermes authenticates to this server with ``Authorization: Bearer <token>``. The
server stores only the SHA-256 digest of the token, so reading the server's
secrets file does not reveal the token Hermes holds. This is entirely separate
from the ServiceNow Basic-Auth credentials.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Iterable, MutableMapping
from typing import Any

log = logging.getLogger("servicenow_mcp.access")

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

MAX_BODY_BYTES = 64 * 1024
FAIL_LIMIT = 10  # failed auth attempts per client per window
FAIL_WINDOW = 60.0
MAX_TRACKED_CLIENTS = 1024


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class _FailureTracker:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._fails: OrderedDict[str, deque[float]] = OrderedDict()

    def _prune(self, key: str) -> deque[float]:
        now = self._clock()
        q = self._fails.setdefault(key, deque())
        self._fails.move_to_end(key)
        while q and now - q[0] > FAIL_WINDOW:
            q.popleft()
        while len(self._fails) > MAX_TRACKED_CLIENTS:
            self._fails.popitem(last=False)
        return q

    def blocked(self, key: str) -> bool:
        return len(self._prune(key)) >= FAIL_LIMIT

    def record(self, key: str) -> None:
        self._prune(key).append(self._clock())


async def _respond(send: Send, status: int, body: dict[str, str], extra: Iterable[tuple[bytes, bytes]] = ()) -> None:
    payload = json.dumps(body).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode()),
        (b"cache-control", b"no-store"),
        (b"x-content-type-options", b"nosniff"),
        *extra,
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


class BearerAuthMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        token_hashes: Iterable[str],
        *,
        require_https_forwarded: bool = False,
        max_body: int = MAX_BODY_BYTES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app = app
        self._hashes = [h.encode() for h in token_hashes]
        self._require_https = require_https_forwarded
        self._max_body = max_body
        self._failures = _FailureTracker(clock)

    @staticmethod
    def _header(scope: Scope, name: bytes) -> str:
        for k, v in scope.get("headers", []):
            if k == name:
                return v.decode("latin-1")
        return ""

    def _client_key(self, scope: Scope) -> str:
        if self._require_https:  # behind our own proxy: last X-Forwarded-For hop is the peer it saw
            xff = self._header(scope, b"x-forwarded-for")
            if xff:
                return xff.split(",")[-1].strip()[:64]
        client = scope.get("client")
        return client[0] if client else "unknown"

    def _token_ok(self, scope: Scope) -> bool:
        value = self._header(scope, b"authorization")
        scheme, _, token = value.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return False
        digest = hash_token(token.strip()).encode()
        ok = False
        for expected in self._hashes:  # no early exit: constant work per attempt
            ok |= hmac.compare_digest(digest, expected)
        return ok

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            return  # websockets etc. are not served

        path = scope.get("path", "")
        if path == "/healthz" and scope["method"] == "GET":
            await _respond(send, 200, {"status": "ok"})
            return

        if self._require_https and self._header(scope, b"x-forwarded-proto").lower() != "https":
            log.warning("rejected request without https forwarding", extra={"event": "tls_required"})
            await _respond(send, 400, {"error": "TLS required"})
            return

        key = self._client_key(scope)
        if self._failures.blocked(key):
            log.warning("auth throttled", extra={"event": "auth_throttled", "client": key})
            await _respond(send, 429, {"error": "too many failed attempts"}, [(b"retry-after", b"60")])
            return

        if not self._token_ok(scope):
            self._failures.record(key)
            log.warning("auth failed", extra={"event": "auth_failed", "client": key, "path": path[:64]})
            await _respond(send, 401, {"error": "unauthorized"}, [(b"www-authenticate", b"Bearer")])
            return

        declared = self._header(scope, b"content-length")
        if declared.isdigit() and int(declared) > self._max_body:
            await _respond(send, 413, {"error": "request too large"})
            return

        received = 0
        too_big = False

        async def limited_receive() -> MutableMapping[str, Any]:
            nonlocal received, too_big
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_body:
                    too_big = True
                    return {"type": "http.disconnect"}
            return message

        await self.app(scope, limited_receive, send)
        if too_big:
            log.warning("request body too large", extra={"event": "body_too_large", "client": key})
