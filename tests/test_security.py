"""security middleware: body cap, rate limit, secret scrubbing, prod-mode."""
from __future__ import annotations

import logging

import pytest

pytestmark = pytest.mark.asyncio


async def test_body_size_cap(client, auth_headers):
    # 6MB payload, default cap is 5MB
    payload = "x" * (6 * 1024 * 1024)
    r = await client.post("/", headers={**auth_headers, "Content-Type": "text/plain"},
                          content=payload)
    assert r.status_code == 413


async def test_rate_limit_kicks_in(app, auth_headers):
    """drive rate limit directly with a small per-minute cap so we hit it fast."""
    from app.security import RateLimitMiddleware
    # find our installed RateLimitMiddleware instance
    middleware = app.user_middleware
    for m in middleware:
        if m.cls is RateLimitMiddleware:
            # build a fresh middleware with tight cap and exercise its logic
            pass
    # simpler: instantiate independently and call dispatch a bunch of times
    from starlette.requests import Request
    rl_app = RateLimitMiddleware(lambda *_a, **_k: None, per_minute=5)
    # forge a Request-like
    scope = {"type": "http", "headers": [(b"authorization", auth_headers["Authorization"].encode())],
             "path": "/Patient", "method": "GET", "client": ("127.0.0.1", 1234)}
    req = Request(scope)
    key = rl_app._key(req)
    import time as _time
    now = _time.monotonic()
    rl_app._hits[key].extend([now] * 5)
    # next attempt should exceed
    from starlette.responses import Response as _Resp
    async def _next(_r):
        return _Resp("ok")
    resp = await rl_app.dispatch(req, _next)
    assert resp.status_code == 429


async def test_logs_do_not_leak_authorization(client, auth_headers, caplog):
    caplog.set_level(logging.INFO, logger="ehds.http")
    await client.get("/Patient/p-001", headers=auth_headers)
    blob = "\n".join(r.message for r in caplog.records)
    assert "Authorization" not in blob
    assert "Bearer " not in blob
    assert auth_headers["Authorization"].split(" ", 1)[1] not in blob
