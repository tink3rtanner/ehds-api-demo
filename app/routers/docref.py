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
    # patient param supports (MHD-canonical + FHIR-canonical variants):
    #   ?patient=<uuid|slot|Patient/x>      direct reference
    #   ?patient.identifier=<system|value>  MHD ITI-67 chained search
    #   ?patient:identifier=<system|value>  FHIR ':identifier' modifier
    #   ?patient=<system>|<value>           identifier-token shorthand
    pat_ident = (norm.get("patient.identifier") or norm.get("patient:identifier") or [None])[0]
    pat_direct = (norm.get("patient") or [None])[0]
    if pat_ident is None and pat_direct and "|" in pat_direct:
        pat_ident, pat_direct = pat_direct, None
    if pat_ident:
        matches = store.find_patient_ids_by_identifier(pat_ident)
        if not matches:
            results = []
        else:
            results = [r for r in results
                       if any((r.get("subject", {}).get("reference", "")).endswith(f"Patient/{m}")
                              for m in matches)]
    elif pat_direct:
        canonical = store.resolve_patient_ref(pat_direct)
        if canonical is None:
            results = []
        else:
            results = [r for r in results
                       if (r.get("subject", {}).get("reference", "")).endswith(f"Patient/{canonical}")]
    # identifier token search (`system|value` preferred) — keeps DocumentReference
    # in sync with the generic store.search, so an origin/source id is findable here too.
    if (v := norm.get("identifier")):
        ident = v[0]
        if "|" in ident:
            wanted_system, wanted_value = ident.split("|", 1)
        else:
            wanted_system, wanted_value = None, ident
        def has_ident(r):
            for i in r.get("identifier", []) or []:
                if i.get("value") != wanted_value:
                    continue
                if wanted_system and i.get("system") != wanted_system:
                    continue
                return True
            return False
        results = [r for r in results if has_ident(r)]
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

    return JSONResponse(store.bundle_searchset("DocumentReference", results, self_link=str(request.url)),
                        media_type="application/fhir+json")
