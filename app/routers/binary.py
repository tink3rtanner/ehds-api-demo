"""GET /Binary/{id} — returns the on-demand compiled FHIR document bundle.

binary ids encode the patient + category as 'doc-<patient_id>-<category_code>'.
the actual content (a Bundle.type=document) is materialised on demand by
app.fhir.document.compile_document.
"""
from __future__ import annotations

import json
import re
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response

from app.auth.verify import Principal, require_scope
from app.fhir import document as doc_compile
from app.fhir import store

router = APIRouter()


# patient ids look like "p-001". the category suffix is one of the known set —
# we anchor on the known suffixes to disambiguate the dash-separated id.
# Source of truth: app.fhir.document.CATEGORY_TO_DOC_TYPE keys.
_CATEGORY_ALT = "|".join(re.escape(k) for k in doc_compile.CATEGORY_TO_DOC_TYPE.keys())
_BIN_ID_RE = re.compile(
    rf"^doc-(?P<patient>[A-Za-z0-9._]+(?:-[A-Za-z0-9._]+)*?)-(?P<category>{_CATEGORY_ALT})$"
)


def _oo(code: str, diag: str) -> dict:
    return {"resourceType": "OperationOutcome",
            "issue": [{"severity": "error", "code": code, "diagnostics": diag}]}


@router.get("/Binary/{rid}", name="read_Binary")
async def read_binary(
    rid: str,
    _p: Annotated[Principal, Depends(require_scope("system/Binary.read"))],
):
    m = _BIN_ID_RE.match(rid)
    if not m:
        # also support submitted-bundle binaries stored on disk
        if (bundle := store.read("DocumentReference", rid)) is not None:  # safety net
            return JSONResponse(bundle, media_type="application/fhir+json")
        return JSONResponse(status_code=404, content=_oo("not-found", f"Binary/{rid}"))

    pid = m.group("patient")
    category = m.group("category")
    if store.read("Patient", pid) is None:
        return JSONResponse(status_code=404, content=_oo("not-found", f"Patient/{pid}"))
    try:
        bundle = doc_compile.compile_document(pid, category)
    except doc_compile.UnknownCategory:
        return JSONResponse(status_code=404, content=_oo("not-found", f"unknown category {category}"))
    except doc_compile.MissingResources as e:
        return JSONResponse(status_code=422, content=_oo("not-supported", str(e)))
    return Response(content=json.dumps(bundle), media_type="application/fhir+json")
