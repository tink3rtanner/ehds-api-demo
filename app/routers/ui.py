"""The human-facing UI: static ES-module SPA + the few JSON endpoints it needs
that are *not* FHIR.

Design rule (docs/ui-design.md, goal 2): every piece of FHIR content on screen
is fetched through the public API with a visible bearer, exactly as any client
would. So this router deliberately serves **no** FHIR resources. What is here:

* ``POST /ui/api/viewer-token`` — a read-only bearer for the page (the same
  JWT shape ``/token`` issues, signed by the server key)
* ``/ui/api/examples`` — live example ids/URLs (shared with smart-configuration)
* ``/ui/api/coverage``, ``/ui/api/submissions[...]`` — the Coverage map
* ``/ui/api/validation/...`` — EU-profile validation badges (async queue)
* ``/ui/api/audit``, ``/ui/api/audit/stats`` — the Activity page
* ``/ui/api/server-info``, ``/ui/api/build-info``, ``/ui/api/clients``, ``/ui/api/qr``

The static files themselves are mounted by app.main at ``/ui`` (after this
router, so ``/ui/api/*`` wins). Everything is disabled when ``ENV=prod``.
"""
from __future__ import annotations

import platform
import subprocess
import time
import uuid
from pathlib import Path

import jwt as _jwt
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from app import audit_log
from app.auth.jwks import load_clients, server_kid, server_private_key
from app.config import settings
from app.fhir import coverage as cov
from app.fhir import store
from app.fhir import validation_queue as vq
from app.fhir.document import CATEGORY_TO_DOC_TYPE
from app.fhir.examples import live_examples
from app.fhir.origin import origin_of

router = APIRouter(prefix="/ui/api")

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"
REPO_ROOT = STATIC_DIR.parent
CATEGORIES = tuple(CATEGORY_TO_DOC_TYPE.keys())

VIEWER_CLIENT_ID = "ui-viewer"
VIEWER_SCOPE = "system/*.read"


def _gate() -> None:
    if settings.is_prod:
        raise HTTPException(status_code=404)


# ---------- token ----------

@router.post("/viewer-token")
async def viewer_token() -> JSONResponse:
    """A read-only bearer for the UI. Same JWT shape as ``/token`` issues,
    verified by the same code path, so what the page does is what any
    read-only client could do. Never carries a write scope."""
    _gate()
    now = int(time.time())
    bearer = _jwt.encode(
        {
            "iss": settings.issuer, "sub": VIEWER_CLIENT_ID, "aud": settings.base_url,
            "iat": now, "exp": now + settings.token_ttl_seconds, "jti": str(uuid.uuid4()),
            "scope": VIEWER_SCOPE, "client_id": VIEWER_CLIENT_ID,
        },
        server_private_key(), algorithm="RS256", headers={"kid": server_kid()},
    )
    return JSONResponse({"access_token": bearer, "token_type": "bearer",
                         "expires_in": settings.token_ttl_seconds, "scope": VIEWER_SCOPE,
                         "client_id": VIEWER_CLIENT_ID})


# ---------- facts ----------

@router.get("/examples")
async def examples() -> JSONResponse:
    _gate()
    return JSONResponse(live_examples())


@router.get("/server-info")
async def server_info() -> JSONResponse:
    _gate()
    counts = {t: 0 for t in store.SUPPORTED_TYPES}
    origins = {"reference": 0, "community": 0, "unknown": 0}
    for t in store.SUPPORTED_TYPES:
        for r in store.list_all(t):
            counts[t] += 1
            if t == "Patient":
                origins[origin_of(r)["kind"]] += 1
    return JSONResponse({
        "base_url": settings.base_url,
        "issuer": settings.issuer,
        "token_endpoint": settings.token_endpoint,
        "env": settings.env,
        "total_resources": sum(counts.values()),
        "patients": counts.get("Patient", 0),
        "patients_by_origin": origins,
        "by_type": counts,
        "categories": list(CATEGORIES),
        "token_ttl_seconds": settings.token_ttl_seconds,
        "rate_limit_per_min": settings.rate_limit_per_min,
        "body_max_bytes": settings.body_max_bytes,
    })


def _git_short_sha() -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=2)
        return r.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


@router.get("/build-info")
async def build_info() -> JSONResponse:
    _gate()
    try:
        import fastapi
        fastapi_v = fastapi.__version__
    except Exception:  # noqa: BLE001
        fastapi_v = "?"
    try:
        import fhir.resources as _fr
        fhir_v = getattr(_fr, "__version__", "?")
    except Exception:  # noqa: BLE001
        fhir_v = "?"
    jar = settings.validator_jar
    pkgs = sorted(p.name for p in settings.eu_packages_dir.glob("*.tgz")) if settings.eu_packages_dir.exists() else []
    ok, reason = vq._validator_available()
    return JSONResponse({
        "git_sha": _git_short_sha(),
        "python": platform.python_version(),
        "fastapi": fastapi_v,
        "fhir_resources": fhir_v,
        "validator": {"jar_present": jar.exists(),
                      "jar_size_mb": round(jar.stat().st_size / 1_048_576, 1) if jar.exists() else None,
                      "eu_packages": pkgs, "available": ok, "reason": reason or None},
        "data_dir": str(settings.data_dir),
    })


@router.get("/clients")
async def clients() -> JSONResponse:
    """Recently registered clients — id and time only. Proof that self-registration
    works, without collecting or showing anything about who is behind an id."""
    _gate()
    out = []
    for c in load_clients().values():
        out.append({"client_id": c.client_id, "registered_at": c.registered_at,
                    "scope_count": len(c.scopes),
                    "can_write": any(s.endswith(".write") for s in c.scopes)})
    out.sort(key=lambda r: (r["registered_at"] or "", r["client_id"]), reverse=True)
    return JSONResponse({"total": len(out), "clients": out[:20]})


# ---------- coverage + submissions ----------

@router.get("/coverage")
async def coverage() -> JSONResponse:
    _gate()
    return JSONResponse(cov.coverage())


@router.get("/submissions")
async def submissions() -> JSONResponse:
    _gate()
    subs = cov.submissions()
    return JSONResponse({"total": len(subs), "submissions": subs})


@router.get("/submissions/{inbox_id}")
async def submission_detail(inbox_id: str) -> JSONResponse:
    _gate()
    bundle = cov.load_submission(inbox_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="unknown submission")
    fp = settings.data_dir / "inbox" / f"{inbox_id}.json"
    summary = cov.summarise_submission(inbox_id, bundle, mtime=fp.stat().st_mtime,
                                       validation=vq.status(inbox_id))
    summary["resources"] = cov.submission_resources(inbox_id, bundle)
    summary["validation_record"] = vq.status(inbox_id)
    return JSONResponse(summary)


@router.post("/submissions/{inbox_id}/validate")
async def submission_validate(inbox_id: str) -> JSONResponse:
    """(Re-)queue EU-profile validation for one submission."""
    _gate()
    if cov.load_submission(inbox_id) is None:
        raise HTTPException(status_code=404, detail="unknown submission")
    return JSONResponse(vq.enqueue_submission(inbox_id), status_code=202)


# ---------- validation records ----------

@router.get("/validation/reference/{pid}/{category}")
async def reference_validation(pid: str, category: str) -> JSONResponse:
    _gate()
    if category not in CATEGORIES:
        raise HTTPException(status_code=404, detail="unknown category")
    rec = vq.status(vq.reference_key(pid, category))
    return JSONResponse(rec or {"state": "none", "kind": "reference", "patient": pid, "category": category})


@router.post("/validation/reference/{pid}/{category}")
async def reference_validate(pid: str, category: str) -> JSONResponse:
    _gate()
    if category not in CATEGORIES:
        raise HTTPException(status_code=404, detail="unknown category")
    if store.read("Patient", pid) is None:
        raise HTTPException(status_code=404, detail="unknown patient")
    return JSONResponse(vq.enqueue_reference(pid, category), status_code=202)


@router.get("/validation/{key}")
async def validation_record(key: str) -> JSONResponse:
    _gate()
    try:
        rec = vq.status(key)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad key")
    if rec is None:
        raise HTTPException(status_code=404, detail="no validation record")
    return JSONResponse(rec)


# ---------- activity ----------

@router.get("/audit")
async def audit(
    limit: int = Query(100, ge=1, le=1000),
    days: int = Query(7, ge=1, le=60),
    scope: str = Query("fhir", pattern="^(fhir|ui|noise|all)$"),
    method: str | None = None,
    path_prefix: str | None = None,
    status_min: int = 100,
    status_max: int = 599,
    client_id: str | None = None,
    patient: str | None = None,
    run: str | None = None,
    since: str | None = None,
) -> JSONResponse:
    _gate()
    return JSONResponse(audit_log.query(
        limit=limit, days=days, scope=scope, method=method, path_prefix=path_prefix,
        status_min=status_min, status_max=status_max, client_id=client_id,
        patient=patient, run=run, since=since))


@router.get("/audit/stats")
async def audit_stats(days: int = Query(1, ge=1, le=60),
                      scope: str = Query("fhir", pattern="^(fhir|ui|noise|all)$")) -> JSONResponse:
    _gate()
    return JSONResponse(audit_log.stats(days=days, scope=scope))


# ---------- QR ----------

@router.get("/qr")
async def qr(text: str) -> Response:
    """SVG QR code for one of *our* URLs (the slide, a patient, a document)."""
    _gate()
    if not text or len(text) > 1024:
        raise HTTPException(status_code=400, detail="text must be 1..1024 chars")
    if not (text.startswith(settings.base_url) or text.startswith("/")):
        raise HTTPException(status_code=400, detail="only URLs on this server are encoded")
    if text.startswith("/"):
        text = settings.base_url + text
    import io

    import qrcode
    import qrcode.image.svg
    buf = io.BytesIO()
    qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, border=1, box_size=10).save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})
