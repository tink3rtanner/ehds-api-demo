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


def _extract_client_id(auth_header: str) -> str | None:
    """decode the bearer (unverified — we just want to surface who claimed to call)."""
    if not auth_header.lower().startswith("bearer "):
        return None
    try:
        import jwt as _jwt
        payload = _jwt.decode(auth_header.split(" ", 1)[1], options={"verify_signature": False})
        return payload.get("client_id") or payload.get("sub")
    except Exception:
        return None


class StructuredLogMiddleware(BaseHTTPMiddleware):
    """Logs every request as one JSON line, both to the python logger (->
    systemd journal) and to a dated JSONL file under settings.audit_log_dir.

    The file is the source of truth for the /ui/#/audit page; the journal
    is the operational tail. Secret-bearing headers (Authorization, etc.) are
    never written, but the JWT *claimed* client_id is parsed and persisted.
    """

    def __init__(self, app) -> None:
        super().__init__(app)
        self._cached_path = None
        self._cached_day = None

    def _todays_file(self):
        from datetime import UTC, date, datetime
        today = date.today().isoformat()
        if self._cached_day == today and self._cached_path is not None:
            return self._cached_path
        d = settings.audit_log_dir
        d.mkdir(parents=True, exist_ok=True)
        self._cached_path = d / f"audit-{today}.jsonl"
        self._cached_day = today
        return self._cached_path

    async def dispatch(self, request: Request, call_next):
        from datetime import UTC, datetime
        started = time.perf_counter()
        ts = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        try:
            response: Response = await call_next(request)
        except Exception:
            log.exception("exception path=%s method=%s", request.url.path, request.method)
            raise
        dur_ms = int((time.perf_counter() - started) * 1000)
        client_id = _extract_client_id(request.headers.get("authorization", ""))
        try:
            req_bytes = int(request.headers.get("content-length") or 0)
        except ValueError:
            req_bytes = 0
        try:
            resp_bytes = int(response.headers.get("content-length") or 0)
        except (ValueError, AttributeError):
            resp_bytes = 0
        entry = {
            "ts": ts,
            "method": request.method,
            "path": request.url.path,
            "query": request.url.query or None,
            "status": response.status_code,
            "dur_ms": dur_ms,
            "client_id": client_id,
            "ip": request.client.host if request.client else None,
            "ua": request.headers.get("user-agent"),
            "req_bytes": req_bytes,
            "resp_bytes": resp_bytes,
        }
        line = json.dumps(entry, separators=(",", ":"))
        log.info(line)
        # persistent file: don't crash the request if the disk is full / unwritable
        try:
            with open(self._todays_file(), "a", encoding="utf-8") as f:
                f.write(line)
                f.write("\n")
        except OSError as e:
            log.warning("audit log write failed: %s", e)
        return response


def install(app: FastAPI) -> None:
    app.add_middleware(StructuredLogMiddleware)
    app.add_middleware(RateLimitMiddleware, per_minute=settings.rate_limit_per_min)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.body_max_bytes)
