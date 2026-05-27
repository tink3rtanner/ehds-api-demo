"""dev-mode UI: static SPA + in-process JSON endpoints.

these endpoints bypass the SMART bearer check by reading the store directly.
they are mounted under /ui/api/* and only enabled when ENV != prod (so a public
VPS in prod mode does NOT expose them).
"""
from __future__ import annotations

import json
import platform
import subprocess
import time
import uuid
from pathlib import Path

import jwt as _jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from app.auth.jwks import server_kid, server_private_key
from app.config import settings
from app.fhir import document as doc_compile
from app.fhir import store

router = APIRouter(prefix="/ui")

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"
REPO_ROOT = STATIC_DIR.parent

CATEGORIES = ["patient-summary", "laboratory-report", "discharge-report", "imaging-report"]


def _is_prod() -> bool:
    return settings.is_prod


def _gate() -> None:
    if _is_prod():
        raise HTTPException(status_code=404)


@router.get("/", include_in_schema=False)
@router.get("", include_in_schema=False)
async def ui_root():
    _gate()
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/styles.css", include_in_schema=False)
async def ui_styles():
    _gate()
    return FileResponse(STATIC_DIR / "styles.css", media_type="text/css")


@router.get("/main.js", include_in_schema=False)
async def ui_js():
    _gate()
    return FileResponse(STATIC_DIR / "main.js", media_type="application/javascript")


# ---------- API ----------

@router.get("/api/patients")
async def api_patients() -> JSONResponse:
    _gate()
    out = []
    for p in store.list_all("Patient"):
        name = (p.get("name") or [{}])[0]
        ident = (p.get("identifier") or [{}])[0]
        out.append({
            "id": p["id"],
            "family": name.get("family", ""),
            "given": " ".join(name.get("given", [])),
            "birthDate": p.get("birthDate"),
            "gender": p.get("gender"),
            "country": (p.get("address") or [{}])[0].get("country", ""),
            "city": (p.get("address") or [{}])[0].get("city", ""),
            "identifier_system": ident.get("system", ""),
            "identifier_value": ident.get("value", ""),
        })
    out.sort(key=lambda r: r["id"])
    return JSONResponse(out)


@router.get("/api/patients/{pid}")
async def api_patient_detail(pid: str) -> JSONResponse:
    _gate()
    p = store.read("Patient", pid)
    if p is None:
        raise HTTPException(status_code=404)
    # bucket linked resources by type. DocumentReference and Composition are
    # excluded — those are surfaced separately via the four priority-category
    # compiled-document cards, and duplicating them in the resource list was
    # confusing.
    hidden = {"Patient", "DocumentReference", "Composition"}
    buckets: dict[str, list] = {}
    for r in store.all_referenced_resources_for_patient(pid):
        rt = r["resourceType"]
        if rt in hidden:
            continue
        buckets.setdefault(rt, []).append(r)
    return JSONResponse({"patient": p, "buckets": buckets,
                         "documents": [{"category": c, "binary": f"doc-{pid}-{c}"} for c in CATEGORIES]})


@router.get("/api/patients/{pid}/doc/{category}")
async def api_patient_doc(pid: str, category: str) -> JSONResponse:
    _gate()
    if category not in CATEGORIES:
        raise HTTPException(status_code=404, detail="unknown category")
    if store.read("Patient", pid) is None:
        raise HTTPException(status_code=404, detail="patient not found")
    try:
        bundle = doc_compile.compile_document(pid, category)
    except doc_compile.MissingResources as e:
        raise HTTPException(status_code=422, detail=str(e))
    return JSONResponse(bundle)


@router.get("/api/validate/{pid}/{category}")
async def api_validate(pid: str, category: str) -> JSONResponse:
    """fast structural validation via fhir.resources pydantic. used by the
    document viewer to render an inline OK/issues badge."""
    _gate()
    from app.fhir.validate import structural_validate
    if category not in CATEGORIES:
        raise HTTPException(status_code=404)
    if store.read("Patient", pid) is None:
        raise HTTPException(status_code=404)
    try:
        bundle = doc_compile.compile_document(pid, category)
    except doc_compile.MissingResources as e:
        return JSONResponse({"ok": False, "stage": "compile", "issues": [str(e)]})
    ok, problems = structural_validate(bundle)
    return JSONResponse({"ok": ok, "stage": "pydantic", "issues": problems})


@router.get("/api/server-info")
async def api_server_info() -> JSONResponse:
    _gate()
    types = store.SUPPORTED_TYPES
    counts = {t: len(list(store.list_all(t))) for t in types}
    return JSONResponse({
        "base_url": settings.base_url,
        "env": settings.env,
        "total_resources": sum(counts.values()),
        "patients": counts.get("Patient", 0),
        "by_type": counts,
        "categories": CATEGORIES,
    })


@router.get("/api/raw/{rtype}/{rid}")
async def api_raw_resource(rtype: str, rid: str) -> JSONResponse:
    """fetch a single resource as-is (same shape FHIR REST returns, without auth)."""
    _gate()
    if rtype not in store.SUPPORTED_TYPES:
        raise HTTPException(status_code=404, detail="unknown type")
    res = store.read(rtype, rid)
    if res is None:
        raise HTTPException(status_code=404)
    return JSONResponse(res)


@router.post("/api/dev-token")
async def api_dev_token() -> JSONResponse:
    """mint a real bearer for the viewer to use against /Patient/* etc.

    dev-only — same JWT format as /token, signed by the server key, accepted by
    verify_bearer just like a normal SMART-issued token. lets the UI demonstrate
    the actual REST endpoints behind the scenes.
    """
    _gate()
    now = int(time.time())
    bearer = _jwt.encode(
        {
            "iss": settings.issuer,
            "sub": "ui-viewer",
            "aud": settings.base_url,
            "iat": now,
            "exp": now + settings.token_ttl_seconds,
            "jti": str(uuid.uuid4()),
            "scope": "system/*.read system/Bundle.write",
            "client_id": "ui-viewer",
        },
        server_private_key(),
        algorithm="RS256",
        headers={"kid": server_kid()},
    )
    return JSONResponse({
        "access_token": bearer,
        "token_type": "bearer",
        "expires_in": settings.token_ttl_seconds,
        "scope": "system/*.read system/Bundle.write",
    })


def _git_short_sha() -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        return r.stdout.strip() or None
    except Exception:
        return None


@router.get("/api/build-info")
async def api_build_info() -> JSONResponse:
    """random useful demo facts: validator status, deps, git sha, IG packs cloned."""
    _gate()
    try:
        import fastapi
        fastapi_v = fastapi.__version__
    except Exception:
        fastapi_v = "?"
    try:
        import fhir.resources as _fr
        fhir_v = getattr(_fr, "__version__", "?")
    except Exception:
        fhir_v = "?"
    validator_jar = settings.validator_jar
    ig_dir = REPO_ROOT / "ig"
    igs = []
    if ig_dir.exists():
        for d in sorted(ig_dir.iterdir()):
            if d.is_dir():
                igs.append({"name": d.name, "path": str(d.relative_to(REPO_ROOT))})
    return JSONResponse({
        "git_sha": _git_short_sha(),
        "python": platform.python_version(),
        "fastapi": fastapi_v,
        "fhir_resources": fhir_v,
        "validator_jar_present": validator_jar.exists(),
        "validator_jar_size_mb": round(validator_jar.stat().st_size / 1_048_576, 1) if validator_jar.exists() else None,
        "ig_packages": igs,
        "data_dir": str(settings.data_dir),
        "base_url": settings.base_url,
        "issuer": settings.issuer,
        "token_endpoint": settings.token_endpoint,
        "token_ttl_seconds": settings.token_ttl_seconds,
        "rate_limit_per_min": settings.rate_limit_per_min,
        "body_max_bytes": settings.body_max_bytes,
    })


@router.get("/api/qr")
async def api_qr(text: str) -> Response:
    """SVG QR code for arbitrary text — used by the /ui#/qr sharing page.

    cached for a year per-text since QR encoding is deterministic and
    the inputs are short, public URLs.
    """
    _gate()
    if not text or len(text) > 1024:
        raise HTTPException(status_code=400, detail="text must be 1..1024 chars")
    import io

    import qrcode
    import qrcode.image.svg
    buf = io.BytesIO()
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgImage,
                      border=2, box_size=10)
    img.save(buf)
    return Response(
        content=buf.getvalue(),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/api/proxy")
async def api_proxy(path: str, request: Request) -> Response:
    """proxy a GET against the live FHIR REST surface so a browser click
    can show the actual JSON without the user having to mint a bearer.

    used by the URL chips in the UI: chip displays e.g. 'GET /Patient/p-001',
    click goes to /ui/api/proxy?path=/Patient/p-001 which fetches the FHIR
    endpoint with an internally-minted dev token and streams the response back.
    pretty-prints JSON for browser readability.
    """
    _gate()
    if not path.startswith("/"):
        raise HTTPException(status_code=400, detail="path must start with /")
    # only proxy our own server; never an external URL
    if path.startswith("//") or "://" in path:
        raise HTTPException(status_code=400, detail="absolute URLs are not proxied")
    # mint an internal dev bearer
    now = int(time.time())
    bearer = _jwt.encode(
        {
            "iss": settings.issuer,
            "sub": "ui-proxy",
            "aud": settings.base_url,
            "iat": now,
            "exp": now + 120,
            "jti": str(uuid.uuid4()),
            "scope": "system/*.read",
            "client_id": "ui-proxy",
        },
        server_private_key(),
        algorithm="RS256",
        headers={"kid": server_kid()},
    )
    # dispatch in-process via the ASGI app — no socket hop required (httpx 0.28+)
    import httpx
    transport = httpx.ASGITransport(app=request.app)
    base_url = str(request.base_url).rstrip("/")
    async with httpx.AsyncClient(transport=transport, base_url=base_url, timeout=10.0) as client:
        r = await client.get(path, headers={"Authorization": f"Bearer {bearer}", "Accept": "application/fhir+json"})
    ctype = r.headers.get("content-type", "application/json")
    if "json" in ctype:
        try:
            pretty = json.dumps(r.json(), indent=2)
            return Response(content=pretty, media_type="application/json", status_code=r.status_code)
        except Exception:
            pass
    return Response(content=r.content, media_type=ctype, status_code=r.status_code)


@router.get("/api/endpoints")
async def api_endpoints() -> JSONResponse:
    """catalogue of demo-relevant endpoints + curl snippets."""
    _gate()
    base = settings.base_url
    return JSONResponse([
        {"label": "Health check",        "method": "GET",  "path": "/healthz",
         "auth": "none", "curl": f"curl -s {base}/healthz"},
        {"label": "Capability statement","method": "GET",  "path": "/metadata",
         "auth": "none", "curl": f"curl -s {base}/metadata | jq ."},
        {"label": "SMART configuration", "method": "GET",  "path": "/.well-known/smart-configuration",
         "auth": "none", "curl": f"curl -s {base}/.well-known/smart-configuration | jq ."},
        {"label": "Server JWKS",         "method": "GET",  "path": "/.well-known/jwks.json",
         "auth": "none", "curl": f"curl -s {base}/.well-known/jwks.json | jq ."},
        {"label": "Mint token (SMART backend services)", "method": "POST", "path": "/token",
         "auth": "client_assertion",
         "curl": (
             f"# mint a JWT client assertion, then:\n"
             f"curl -s -X POST {base}/token \\\n"
             f"  -d grant_type=client_credentials \\\n"
             f"  -d client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer \\\n"
             f"  -d client_assertion=$ASSERTION \\\n"
             f"  -d 'scope=system/*.read'"
         )},
        {"label": "Read Patient",        "method": "GET", "path": "/Patient/{id}",
         "auth": "bearer", "curl": f"curl -s -H \"Authorization: Bearer $TOKEN\" {base}/Patient/p-001"},
        {"label": "PDQm search Patient", "method": "GET", "path": "/Patient?family={f}&birthdate={d}",
         "auth": "bearer",
         "curl": f"curl -s -H \"Authorization: Bearer $TOKEN\" '{base}/Patient?family=Rossi&birthdate=1981-11-02'"},
        {"label": "Patient $match",      "method": "POST", "path": "/Patient/$match",
         "auth": "bearer",
         "curl": (
             f"curl -s -X POST -H \"Authorization: Bearer $TOKEN\" \\\n"
             f"  -H 'Content-Type: application/fhir+json' \\\n"
             f"  -d @match-query.json {base}/Patient/\\$match"
         )},
        {"label": "Patient $everything", "method": "GET", "path": "/Patient/{id}/$everything",
         "auth": "bearer",
         "curl": f"curl -s -H \"Authorization: Bearer $TOKEN\" {base}/Patient/p-001/\\$everything"},
        {"label": "ITI-67 DocumentReference search", "method": "GET", "path": "/DocumentReference?patient={id}",
         "auth": "bearer",
         "curl": f"curl -s -H \"Authorization: Bearer $TOKEN\" '{base}/DocumentReference?patient=p-001'"},
        {"label": "ITI-68 Binary retrieve (on-demand compiled Bundle)", "method": "GET",
         "path": "/Binary/doc-{patient}-{category}",
         "auth": "bearer",
         "curl": f"curl -s -H \"Authorization: Bearer $TOKEN\" {base}/Binary/doc-p-001-patient-summary"},
        {"label": "ITI-105 submit Bundle",          "method": "POST", "path": "/",
         "auth": "bearer (system/Bundle.write)",
         "curl": (
             f"curl -s -X POST -H \"Authorization: Bearer $TOKEN\" \\\n"
             f"  -H 'Content-Type: application/fhir+json' \\\n"
             f"  -d @submission-bundle.json {base}/"
         )},
        {"label": "Resource access (IPA-style)", "method": "GET",
         "path": "/{ResourceType}?patient={id}",
         "auth": "bearer",
         "curl": (
             f"curl -s -H \"Authorization: Bearer $TOKEN\" \\\n"
             f"  '{base}/Observation?patient=p-001&category=laboratory'"
         )},
    ])
