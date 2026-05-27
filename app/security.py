"""HTTP middleware: body size cap, per-client rate limiting, structured logging,
secret scrubbing.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings

log = logging.getLogger("ehds.http")


def _oo(severity: str, code: str, diag: str) -> dict:
    return {"resourceType": "OperationOutcome",
            "issue": [{"severity": severity, "code": code, "diagnostics": diag}]}


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > self.max_bytes:
                    return JSONResponse(status_code=413, content=_oo("error", "too-costly",
                        f"body exceeds {self.max_bytes} bytes"))
            except ValueError:
                return JSONResponse(status_code=400, content=_oo("error", "invalid", "bad content-length"))
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """token-bucket-ish per-client rate limit. keyed by Bearer client_id (sub claim) or remote IP."""

    def __init__(self, app, per_minute: int) -> None:
        super().__init__(app)
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _key(self, request: Request) -> str:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            # decode without verification: rate-limit even bad tokens by their claimed sub
            import jwt as _jwt
            try:
                payload = _jwt.decode(auth.split(" ", 1)[1], options={"verify_signature": False})
                return f"client:{payload.get('client_id') or payload.get('sub') or 'unknown'}"
            except Exception:
                pass
        return f"ip:{request.client.host if request.client else 'unknown'}"

    async def dispatch(self, request: Request, call_next):
        if request.url.path in ("/healthz", "/.well-known/smart-configuration", "/.well-known/jwks.json"):
            return await call_next(request)
        k = self._key(request)
        now = time.monotonic()
        cutoff = now - 60.0
        q = self._hits[k]
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= self.per_minute:
            return JSONResponse(status_code=429, content=_oo("error", "throttled", "rate limit exceeded"),
                                headers={"Retry-After": "30"})
        q.append(now)
        return await call_next(request)


_SECRET_RE = re.compile(r"(?i)(authorization|client_assertion|access_token|password)")


class StructuredLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        try:
            response: Response = await call_next(request)
        except Exception:
            log.exception("exception path=%s method=%s", request.url.path, request.method)
            raise
        dur_ms = int((time.perf_counter() - started) * 1000)
        # log a single line of structured JSON, never including secret-bearing headers
        safe_headers = {k: v for k, v in request.headers.items() if not _SECRET_RE.search(k)}
        log.info(json.dumps({
            "evt": "http",
            "path": request.url.path,
            "method": request.method,
            "status": response.status_code,
            "dur_ms": dur_ms,
            "client": request.client.host if request.client else None,
            "ua": safe_headers.get("user-agent"),
        }))
        return response


def install(app: FastAPI) -> None:
    app.add_middleware(StructuredLogMiddleware)
    app.add_middleware(RateLimitMiddleware, per_minute=settings.rate_limit_per_min)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.body_max_bytes)
