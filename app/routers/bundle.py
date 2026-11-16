"""GET /Bundle/{id} — returns the on-demand compiled FHIR document Bundle.

Bundle ids are deterministic uuid5(NAMESPACE, "Bundle/{patient}/{category}")
per app.fhir.ids. We compute the full forward map at startup (10 patients ×
5 categories = 50 entries) and use it for reverse lookup at request time.
"""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response

from app.auth.verify import Principal, require_scope
from app.fhir import document as doc_compile
from app.fhir import store
from app.fhir.ids import bundle_id, owned_slot
from app.routers.publish import document_path

router = APIRouter()


def _oo(code: str, diag: str) -> dict:
    return {"resourceType": "OperationOutcome",
            "issue": [{"severity": "error", "code": code, "diagnostics": diag}]}


def _build_reverse_index() -> dict[str, tuple[str, str]]:
    """build bundle_uuid -> (patient_uuid, category) map.

    bundle ids are minted from the patient slot label (so they're stable
    even before a Patient resource has been read). The reverse map keys
    on bundle_uuid and yields (patient.id, category) for compile_document.

    Only the Patient whose id derives from its slot owns that slot. An upload
    can store a copy of a reference Patient, slot identifier included; that
    copy must not take over the reference Patient's documents.
    """
    out: dict[str, tuple[str, str]] = {}
    for p in store.list_all("Patient"):
        slot = owned_slot(p)
        if slot is None:
            continue
        for cat in doc_compile.CATEGORY_TO_DOC_TYPE:
            out[bundle_id(slot, cat)] = (p["id"], cat)
    return out


def bundle_exists(rid: str) -> bool:
    """True when GET /Bundle/{rid} has something to serve: a published
    document, a compiled document id, or a persisted Bundle."""
    if document_path(rid) is not None or rid in _build_reverse_index():
        return True
    return "Bundle" in store.SUPPORTED_TYPES and store.read("Bundle", rid) is not None


@router.get("/Bundle/{rid}", name="read_Bundle")
async def read_bundle(
    rid: str,
    _p: Annotated[Principal, Depends(require_scope("system/Binary.read"))],
):
    # a document published via ITI-105: return the exact bytes we were given
    if (fp := document_path(rid)) is not None:
        dr = store.read("DocumentReference", rid) or {}
        ctype = (((dr.get("content") or [{}])[0]).get("attachment") or {}).get("contentType") or "application/fhir+json"
        return Response(content=fp.read_bytes(), media_type=ctype)
    index = _build_reverse_index()
    if rid not in index:
        # fall back to any persisted Bundle (e.g. submitted via ITI-105)
        persisted = store.read("Bundle", rid) if "Bundle" in store.SUPPORTED_TYPES else None
        if persisted is not None:
            return JSONResponse(persisted, media_type="application/fhir+json")
        return JSONResponse(status_code=404, media_type="application/fhir+json", content=_oo("not-found", f"Bundle/{rid}"))
    pid, category = index[rid]
    if store.read("Patient", pid) is None:
        return JSONResponse(status_code=404, media_type="application/fhir+json", content=_oo("not-found", f"Patient/{pid}"))
    try:
        bundle = doc_compile.compile_document(pid, category)
    except doc_compile.UnknownCategory:
        return JSONResponse(status_code=404, media_type="application/fhir+json", content=_oo("not-found", f"unknown category {category}"))
    except doc_compile.MissingResources as e:
        return JSONResponse(status_code=422, media_type="application/fhir+json", content=_oo("not-supported", str(e)))
    return Response(content=json.dumps(bundle), media_type="application/fhir+json")
