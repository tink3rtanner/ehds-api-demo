"""dev-mode UI: static SPA + in-process JSON endpoints.

these endpoints bypass the SMART bearer check by reading the store directly.
they are mounted under /ui/api/* and only enabled when ENV != prod (so a public
VPS in prod mode does NOT expose them).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from app.config import settings
from app.fhir import document as doc_compile
from app.fhir import store

router = APIRouter(prefix="/ui")

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"

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
        out.append({
            "id": p["id"],
            "family": name.get("family", ""),
            "given": " ".join(name.get("given", [])),
            "birthDate": p.get("birthDate"),
            "gender": p.get("gender"),
            "country": (p.get("address") or [{}])[0].get("country", ""),
            "city": (p.get("address") or [{}])[0].get("city", ""),
        })
    out.sort(key=lambda r: r["id"])
    return JSONResponse(out)


@router.get("/api/patients/{pid}")
async def api_patient_detail(pid: str) -> JSONResponse:
    _gate()
    p = store.read("Patient", pid)
    if p is None:
        raise HTTPException(status_code=404)
    # bucket all linked resources by type
    buckets: dict[str, list] = {}
    for r in store.all_referenced_resources_for_patient(pid):
        if r["resourceType"] == "Patient":
            continue
        buckets.setdefault(r["resourceType"], []).append(r)
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
