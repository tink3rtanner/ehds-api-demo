"""DocumentReference search + read (ITI-67).

each DocumentReference points (via content.attachment.url) at a Binary id of
the form 'doc-<patient>-<category>'. resolving the Binary triggers an
on-demand compile.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.auth.verify import Principal, require_scope
from app.fhir import store

router = APIRouter()


def _oo(severity: str, code: str, diag: str) -> dict:
    return {"resourceType": "OperationOutcome",
            "issue": [{"severity": severity, "code": code, "diagnostics": diag}]}


@router.get("/DocumentReference/{rid}", name="read_DocumentReference")
async def read_docref(
    rid: str,
    _p: Annotated[Principal, Depends(require_scope("system/DocumentReference.read"))],
) -> JSONResponse:
    res = store.read("DocumentReference", rid)
    if res is None:
        return JSONResponse(status_code=404, content=_oo("error", "not-found", f"DocumentReference/{rid}"))
    return JSONResponse(res, media_type="application/fhir+json")


@router.get("/DocumentReference", name="search_DocumentReference")
async def search_docref(
    request: Request,
    _p: Annotated[Principal, Depends(require_scope("system/DocumentReference.read"))],
) -> JSONResponse:
    norm: dict[str, list[str]] = {}
    for k, v in request.query_params.multi_items():
        norm.setdefault(k, []).append(v)

    results = list(store.list_all("DocumentReference"))

    if (v := norm.get("_id")):
        results = [r for r in results if r.get("id") == v[0]]
    # chained search: ?patient.identifier=system|value
    if (v := norm.get("patient.identifier")):
        matches = store.find_patient_ids_by_identifier(v[0])
        if not matches:
            results = []
        else:
            results = [r for r in results
                       if any((r.get("subject", {}).get("reference", "")).endswith(f"Patient/{m}")
                              for m in matches)]
    elif (v := norm.get("patient")):
        canonical = store.resolve_patient_ref(v[0])
        if canonical is None:
            results = []
        else:
            results = [r for r in results
                       if (r.get("subject", {}).get("reference", "")).endswith(f"Patient/{canonical}")]
    if (v := norm.get("status")):
        results = [r for r in results if r.get("status") == v[0]]
    if (v := norm.get("category")):
        wanted = v[0].split("|")[-1]
        def cat_match(r):
            for c in (r.get("category") or []):
                for code in (c.get("coding") or []):
                    if code.get("code") == wanted:
                        return True
            return False
        results = [r for r in results if cat_match(r)]
    if (v := norm.get("type")):
        wanted = v[0].split("|")[-1]
        def type_match(r):
            for code in (r.get("type", {}).get("coding") or []):
                if code.get("code") == wanted:
                    return True
            return False
        results = [r for r in results if type_match(r)]

    return JSONResponse(store.bundle_searchset("DocumentReference", results),
                        media_type="application/fhir+json")
