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

CATEGORIES = ["patient-summary", "laboratory-report", "discharge-report", "imaging-report", "prescription"]


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
    from app.fhir.ids import SLOT_IDENTIFIER_SYSTEM
    out = []
    for p in store.list_all("Patient"):
        name = (p.get("name") or [{}])[0]
        slot = None
        national = {}
        for ident in p.get("identifier") or []:
            if ident.get("system") == SLOT_IDENTIFIER_SYSTEM:
                slot = ident.get("value")
            elif not national:
                national = ident
        out.append({
            "id": p["id"],
            "slot": slot,
            "family": name.get("family", ""),
            "given": " ".join(name.get("given", [])),
            "birthDate": p.get("birthDate"),
            "gender": p.get("gender"),
            "country": (p.get("address") or [{}])[0].get("country", ""),
            "city": (p.get("address") or [{}])[0].get("city", ""),
            "identifier_system": national.get("system", ""),
            "identifier_value": national.get("value", ""),
        })
    out.sort(key=lambda r: r.get("slot") or r["id"])
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
    from app.fhir.ids import SLOT_IDENTIFIER_SYSTEM
    from app.fhir.ids import bundle_id as _bundle_id
    slot = None
    for ident in p.get("identifier") or []:
        if ident.get("system") == SLOT_IDENTIFIER_SYSTEM:
            slot = ident.get("value")
            break
    bundle_key = slot or pid
    return JSONResponse({"patient": p, "buckets": buckets,
                         "documents": [{"category": c, "bundle_id": _bundle_id(bundle_key, c)} for c in CATEGORIES]})


@router.get("/api/patients/{pid}/timeline")
async def api_patient_timeline(pid: str) -> JSONResponse:
    """chronological clinical-event timeline for a patient.

    aggregates events across Conditions, Encounters, Procedures, Observations,
    Immunizations, MedicationRequests, MedicationDispenses, MedicationStatements,
    DiagnosticReports, ImagingStudies, AllergyIntolerances and DocumentReferences.
    sorted by date descending. used by the patient-detail timeline component.
    """
    _gate()
    if store.read("Patient", pid) is None:
        raise HTTPException(status_code=404)
    events: list[dict] = []

    def _add(kind: str, label: str, dt: str | None, resource_type: str, rid: str, detail: str = "", icon: str = ""):
        if not dt:
            return
        events.append({
            "kind": kind, "label": label, "date": dt[:19] if len(dt) >= 19 else dt,
            "resource_type": resource_type, "resource_id": rid, "detail": detail, "icon": icon,
        })

    def _txt(c: dict | None) -> str:
        if not c:
            return ""
        return c.get("text") or (c.get("coding") or [{}])[0].get("display") or ""

    def refs(r): return (r.get("subject") or r.get("patient") or {}).get("reference", "")

    for r in store.list_all("Condition"):
        if refs(r).endswith(f"Patient/{pid}"):
            _add("condition", "Condition recorded", r.get("recordedDate"), "Condition", r["id"], _txt(r.get("code")), "🩺")
    for r in store.list_all("Encounter"):
        if refs(r).endswith(f"Patient/{pid}"):
            start = (r.get("period") or {}).get("start")
            cls = r.get("class", {})
            label = "Inpatient encounter" if cls.get("code") == "IMP" else "Ambulatory visit"
            _add("encounter", label, start, "Encounter", r["id"], _txt(r.get("reasonCode", [{}])[0] if r.get("reasonCode") else None), "🏥")
    for r in store.list_all("Procedure"):
        if refs(r).endswith(f"Patient/{pid}"):
            _add("procedure", "Procedure", r.get("performedDateTime"), "Procedure", r["id"], _txt(r.get("code")), "⚕️")
    for r in store.list_all("Observation"):
        if refs(r).endswith(f"Patient/{pid}"):
            cat = ((r.get("category") or [{}])[0].get("coding") or [{}])[0].get("code", "")
            label = "Lab result" if cat == "laboratory" else "Vital sign"
            value = ""
            if r.get("valueQuantity"):
                vq = r["valueQuantity"]
                value = f"{vq.get('value','')}{vq.get('unit','')}"
            elif r.get("component"):
                parts = []
                for c in r["component"]:
                    v = c.get("valueQuantity", {})
                    parts.append(f"{_txt(c.get('code'))} {v.get('value','')}{v.get('unit','')}")
                value = " / ".join(parts)
            _add("observation", label, r.get("effectiveDateTime"), "Observation", r["id"],
                 f"{_txt(r.get('code'))}: {value}".strip(": "),
                 "🧪" if cat == "laboratory" else "📊")
    for r in store.list_all("Immunization"):
        if refs(r).endswith(f"Patient/{pid}"):
            _add("immunization", "Vaccination", r.get("occurrenceDateTime"), "Immunization", r["id"], _txt(r.get("vaccineCode")), "💉")
    for r in store.list_all("MedicationRequest"):
        if refs(r).endswith(f"Patient/{pid}"):
            med = (r.get("medicationReference") or {}).get("display", "") or _txt(r.get("medicationCodeableConcept"))
            _add("medication", "Prescription", r.get("authoredOn"), "MedicationRequest", r["id"], med, "💊")
    for r in store.list_all("MedicationDispense"):
        if refs(r).endswith(f"Patient/{pid}"):
            med = (r.get("medicationReference") or {}).get("display", "") or _txt(r.get("medicationCodeableConcept"))
            _add("medication", "Dispensed", r.get("whenHandedOver"), "MedicationDispense", r["id"], med, "💊")
    for r in store.list_all("AllergyIntolerance"):
        if refs(r).endswith(f"Patient/{pid}"):
            _add("allergy", "Allergy recorded", r.get("recordedDate") or r.get("onsetDateTime"), "AllergyIntolerance", r["id"], _txt(r.get("code")), "⚠️")
    for r in store.list_all("DiagnosticReport"):
        if refs(r).endswith(f"Patient/{pid}"):
            cat = ((r.get("category") or [{}])[0].get("coding") or [{}])[0].get("code", "")
            label = "Imaging report" if cat in ("RAD", "radiology") else "Lab report"
            _add("report", label, r.get("effectiveDateTime") or r.get("issued"), "DiagnosticReport", r["id"], _txt(r.get("code")),
                 "🩻" if cat in ("RAD", "radiology") else "🧪")
    for r in store.list_all("ImagingStudy"):
        if refs(r).endswith(f"Patient/{pid}"):
            _add("imaging", "Imaging study", r.get("started"), "ImagingStudy", r["id"],
                 ((r.get("modality") or [{}])[0].get("display", "")), "🩻")
    for r in store.list_all("DocumentReference"):
        if refs(r).endswith(f"Patient/{pid}"):
            cat = ((r.get("category") or [{}])[0].get("coding") or [{}])[0].get("code", "")
            _add("document", "Document published", r.get("date"), "DocumentReference", r["id"], cat, "📄")

    events.sort(key=lambda e: e["date"], reverse=True)
    return JSONResponse({"total": len(events), "events": events})


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


@router.get("/api/bundle-id/{pid}/{category}")
async def api_bundle_id(pid: str, category: str) -> JSONResponse:
    """resolve (patient, category) to the deterministic Bundle uuid.

    Accepts either a Patient.id (uuid) or a slot identifier (``p-001`` etc.).
    """
    _gate()
    if category not in CATEGORIES:
        raise HTTPException(status_code=404, detail="unknown category")
    from app.fhir.ids import SLOT_IDENTIFIER_SYSTEM
    from app.fhir.ids import bundle_id as _bid
    p = store.read("Patient", pid)
    if p is not None:
        slot = next((i["value"] for i in (p.get("identifier") or []) if i.get("system") == SLOT_IDENTIFIER_SYSTEM), pid)
    else:
        match = [pp for pp in store.list_all("Patient")
                 if any(i.get("system") == SLOT_IDENTIFIER_SYSTEM and i.get("value") == pid
                        for i in (pp.get("identifier") or []))]
        if not match:
            raise HTTPException(status_code=404, detail="patient not found")
        slot = pid
    bid = _bid(slot, category)
    return JSONResponse({"bundle_id": bid, "path": f"/Bundle/{bid}"})


@router.get("/api/documents")
async def api_documents() -> JSONResponse:
    """list every document on the server.

    sources two streams:
      1. DocumentReference resources (the FHIR-side registry of documents)
      2. all compiled-on-demand Bundles (10 patients × 5 priority categories)

    used by the /ui#/documents page to render a sortable table of every
    document the server can produce.
    """
    _gate()
    out = []
    # 1. registered DocumentReferences
    for r in store.list_all("DocumentReference"):
        cat = ((r.get("category") or [{}])[0].get("coding") or [{}])[0]
        typ = ((r.get("type") or {}).get("coding") or [{}])[0]
        subj = (r.get("subject") or {}).get("reference", "")
        att = ((r.get("content") or [{}])[0].get("attachment") or {})
        out.append({
            "source": "DocumentReference",
            "id": r["id"],
            "fhir_path": f"/DocumentReference/{r['id']}",
            "binary_url": att.get("url", ""),
            "patient": subj.split("/", 1)[-1] if subj else "",
            "category_code": cat.get("code", ""),
            "category_display": cat.get("display", ""),
            "type_code": typ.get("code", ""),
            "type_display": typ.get("display", ""),
            "date": r.get("date", ""),
            "description": r.get("description", ""),
        })
    # 2. on-demand compiled Bundles (one per patient × category).
    # served via GET /Binary/{id} per IHE MHD convention — the Binary URL is
    # the routing path, the response is a Bundle.type=document.
    from app.fhir.ids import SLOT_IDENTIFIER_SYSTEM
    from app.fhir.ids import bundle_id as _bid
    patients = sorted(store.list_all("Patient"), key=lambda p: p["id"])
    for p in patients:
        slot = next((i["value"] for i in (p.get("identifier") or []) if i.get("system") == SLOT_IDENTIFIER_SYSTEM), None)
        key = slot or p["id"]
        for cat in CATEGORIES:
            bid = _bid(key, cat)
            out.append({
                "source": "Compiled Bundle",
                "id": bid,
                "fhir_path": f"/Bundle/{bid}",
                "binary_url": f"Bundle/{bid}",
                "patient": p["id"],
                "patient_slot": slot,
                "category_code": cat,
                "category_display": cat.replace("-", " ").title(),
                "type_code": "",
                "type_display": "",
                "date": "",
                "description": "on-demand FHIR Bundle.type=document compiled from atomic resources, served at /Bundle/{id}",
            })
    return JSONResponse({"total": len(out), "documents": out})


@router.get("/api/clients")
async def api_list_clients() -> JSONResponse:
    """list registered clients (public JWKS only, no private material)."""
    _gate()
    from app.auth.jwks import load_clients
    clients = load_clients()
    out = []
    for c in clients.values():
        out.append({
            "client_id": c.client_id,
            "scopes": list(c.scopes),
            "kids": [k.get("kid") for k in c.jwks.get("keys", []) if k.get("kid")],
            "key_count": len(c.jwks.get("keys", [])),
        })
    return JSONResponse({"clients": sorted(out, key=lambda r: r["client_id"])})


# Allowed registration scopes. Must stay in sync with the top-level
# /register-client (app/routers/discovery.py) — keep both authoritative.
# The UI re-imports the canonical list so a single allowlist exists.
from app.routers.discovery import _ALLOWED_REG_SCOPES
_CLIENT_ID_RE = __import__("re").compile(r"^[a-z0-9][a-z0-9\-_]{1,62}[a-z0-9]$")


@router.post("/api/register-client")
async def api_register_client(payload: dict) -> JSONResponse:
    """register or update a SMART backend services client.

    body shape (one of jwk / public_key_pem required):
      {
        "client_id": "my-app",
        "scopes": ["system/*.read"],
        "jwk": { ... single JWK ... }    # OR
        "public_key_pem": "-----BEGIN PUBLIC KEY-----\\n..."
      }

    returns the registered client, including the kid the server will accept
    in the JWT client assertion header. takes effect on the very next /token
    call (no restart needed; load_clients reads the registry on each request).
    """
    _gate()
    from app.auth.jwks import load_clients, upsert_client

    cid = (payload.get("client_id") or "").strip()
    if not _CLIENT_ID_RE.match(cid):
        raise HTTPException(status_code=400, detail="client_id must match [a-z0-9][a-z0-9-_]{1,62}[a-z0-9]")
    scopes = payload.get("scopes") or ["system/*.read"]
    if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
        raise HTTPException(status_code=400, detail="scopes must be a list of strings")
    bad = [s for s in scopes if s not in _ALLOWED_REG_SCOPES]
    if bad:
        raise HTTPException(status_code=400, detail=f"scopes not in allowed set: {bad}. allowed: {list(_ALLOWED_REG_SCOPES)}")

    # build a JWKS
    if payload.get("jwk"):
        jwk = dict(payload["jwk"])
        if not jwk.get("kty"):
            raise HTTPException(status_code=400, detail="jwk.kty missing")
        jwk.setdefault("use", "sig")
        jwk.setdefault("alg", "RS256")
        jwk.setdefault("kid", f"{cid}-key-1")
        jwks = {"keys": [jwk]}
    elif payload.get("public_key_pem"):
        try:
            from cryptography.hazmat.primitives import serialization
            from jwt.algorithms import RSAAlgorithm
            pub = serialization.load_pem_public_key(payload["public_key_pem"].encode())
            jwk = json.loads(RSAAlgorithm.to_jwk(pub))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"bad PEM: {e}")
        jwk["kid"] = f"{cid}-key-1"
        jwk["use"] = "sig"
        jwk["alg"] = "RS256"
        jwks = {"keys": [jwk]}
    else:
        raise HTTPException(status_code=400, detail="provide either jwk or public_key_pem")

    try:
        upsert_client(cid, jwks, scopes)
    except (OSError, PermissionError) as e:
        raise HTTPException(status_code=500, detail=f"could not persist client: {e}")

    # verify it round-trips
    loaded = load_clients().get(cid)
    return JSONResponse({
        "client_id": cid,
        "scopes": list(scopes),
        "jwks": jwks,
        "registered": bool(loaded),
        "next_steps": {
            "token_endpoint": settings.token_endpoint,
            "client_assertion_kid": jwks["keys"][0]["kid"],
            "audience": settings.token_endpoint,
            "algorithm": "RS256",
            "expires_in_seconds": settings.token_ttl_seconds,
        },
    })


@router.get("/api/audit")
async def api_audit(
    limit: int = 200,
    method: str | None = None,
    path_prefix: str | None = None,
    status_min: int = 100,
    status_max: int = 599,
    client_id: str | None = None,
    days: int = 7,
) -> JSONResponse:
    """tail of the persistent transaction log.

    reads up to the last `days` JSONL files and returns the most recent
    `limit` matching entries (newest first). filters are AND.
    """
    _gate()
    from datetime import date, timedelta
    out: list[dict] = []
    today = date.today()
    files: list = []
    for back in range(days):
        f = settings.audit_log_dir / f"audit-{(today - timedelta(days=back)).isoformat()}.jsonl"
        if f.exists():
            files.append(f)
    # newest file first; within a file, last lines are newest -> read in reverse
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if method and e.get("method") != method.upper():
                continue
            if path_prefix and not (e.get("path") or "").startswith(path_prefix):
                continue
            s = e.get("status", 0)
            if not (status_min <= s <= status_max):
                continue
            if client_id and e.get("client_id") != client_id:
                continue
            out.append(e)
            if len(out) >= limit:
                return JSONResponse({"total": len(out), "entries": out, "truncated": True})
    return JSONResponse({"total": len(out), "entries": out, "truncated": False})


@router.get("/api/audit/stats")
async def api_audit_stats(days: int = 1) -> JSONResponse:
    """aggregate stats over the last `days` JSONL files."""
    _gate()
    from collections import Counter
    from datetime import date, timedelta
    total = 0
    by_status_class: Counter = Counter()
    by_method: Counter = Counter()
    by_path: Counter = Counter()
    by_client: Counter = Counter()
    latencies: list[int] = []
    today = date.today()
    for back in range(days):
        f = settings.audit_log_dir / f"audit-{(today - timedelta(days=back)).isoformat()}.jsonl"
        if not f.exists():
            continue
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            s = e.get("status", 0)
            by_status_class[f"{s // 100}xx"] += 1
            by_method[e.get("method", "?")] += 1
            # collapse pathParam-ish bits so /Patient/p-001 and /Patient/p-002 group
            p = e.get("path", "")
            if p.startswith("/Patient/") and p.count("/") == 2:
                p = "/Patient/{id}"
            elif p.startswith("/Bundle/") and p.count("/") == 2:
                p = "/Bundle/{id}"
            elif p.startswith("/DocumentReference/") and p.count("/") == 2:
                p = "/DocumentReference/{id}"
            by_path[p] += 1
            cid = e.get("client_id")
            if cid:
                by_client[cid] += 1
            d = e.get("dur_ms")
            if isinstance(d, int):
                latencies.append(d)
    latencies.sort()

    def _pct(p: float) -> int | None:
        if not latencies:
            return None
        idx = min(int(len(latencies) * p / 100), len(latencies) - 1)
        return latencies[idx]

    return JSONResponse({
        "total": total,
        "days": days,
        "by_status_class": dict(by_status_class),
        "by_method": dict(by_method),
        "top_paths": by_path.most_common(10),
        "top_clients": by_client.most_common(10),
        "latency_ms": {
            "p50": _pct(50),
            "p95": _pct(95),
            "p99": _pct(99),
            "max": latencies[-1] if latencies else None,
        },
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
        {"label": "ITI-68 Bundle retrieve (on-demand compiled document)", "method": "GET",
         "path": "/Bundle/{uuid}",
         "auth": "bearer",
         "curl": (
             f"# uuid is deterministic per (patient, category) — look it up via /ui/api/documents\n"
             f"curl -s -H \"Authorization: Bearer $TOKEN\" {base}/Bundle/$BUNDLE_UUID"
         )},
        {"label": "IPS Patient Summary ($summary)", "method": "GET",
         "path": "/Patient/{id}/$summary",
         "auth": "bearer",
         "curl": f"curl -s -H \"Authorization: Bearer $TOKEN\" {base}/Patient/p-001/\\$summary"},
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
