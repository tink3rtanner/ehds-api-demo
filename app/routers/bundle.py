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
from app.fhir.ids import SLOT_IDENTIFIER_SYSTEM, bundle_id

router = APIRouter()


def _oo(code: str, diag: str) -> dict:
    return {"resourceType": "OperationOutcome",
            "issue": [{"severity": "error", "code": code, "diagnostics": diag}]}


def _slot_of(patient: dict) -> str | None:
    """extract the ehds-demo slot identifier (p-001 etc.) if present."""
    for ident in patient.get("identifier", []) or []:
        if ident.get("system") == SLOT_IDENTIFIER_SYSTEM:
            return ident.get("value")
    return None


def _build_reverse_index() -> dict[str, tuple[str, str]]:
    """build bundle_uuid -> (patient_uuid, category) map.

    bundle ids are minted from the patient slot label (so they're stable
    even before a Patient resource has been read). The reverse map keys
    on bundle_uuid and yields (patient.id, category) for compile_document.
    """
    out: dict[str, tuple[str, str]] = {}
    for p in store.list_all("Patient"):
        slot = _slot_of(p)
        if not slot:
            continue
        for cat in doc_compile.CATEGORY_TO_DOC_TYPE:
            out[bundle_id(slot, cat)] = (p["id"], cat)
    return out


@router.get("/Bundle/{rid}", name="read_Bundle")
async def read_bundle(
    rid: str,
    _p: Annotated[Principal, Depends(require_scope("system/Binary.read"))],
):
    index = _build_reverse_index()
    if rid not in index:
        # fall back to any persisted Bundle (e.g. submitted via ITI-105)
        persisted = store.read("Bundle", rid) if "Bundle" in store.SUPPORTED_TYPES else None
        if persisted is not None:
            return JSONResponse(persisted, media_type="application/fhir+json")
        return JSONResponse(status_code=404, content=_oo("not-found", f"Bundle/{rid}"))
    pid, category = index[rid]
    if store.read("Patient", pid) is None:
        return JSONResponse(status_code=404, content=_oo("not-found", f"Patient/{pid}"))
    try:
        bundle = doc_compile.compile_document(pid, category)
    except doc_compile.UnknownCategory:
        return JSONResponse(status_code=404, content=_oo("not-found", f"unknown category {category}"))
    except doc_compile.MissingResources as e:
        return JSONResponse(status_code=422, content=_oo("not-supported", str(e)))
    return Response(content=json.dumps(bundle), media_type="application/fhir+json")
