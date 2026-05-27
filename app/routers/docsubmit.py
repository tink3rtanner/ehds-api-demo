"""ITI-105 document submission.

accepts POST / (root) with Content-Type: application/fhir+json carrying a
Bundle.type=transaction whose entries contain a DocumentReference + Binary
(and any supporting Composition + clinical resources).

we:
  1. validate the bundle structurally via fhir.resources
  2. assign ids where missing
  3. persist the entire bundle to data/inbox/<bundle-id>.json
  4. mirror constituent resources into the store so they're queryable
  5. return the bundle (server-assigned ids) per FHIR transaction response semantics
"""
from __future__ import annotations

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.auth.verify import Principal, require_scope
from app.config import settings
from app.fhir import store
from app.fhir.validate import structural_validate

router = APIRouter()


def _oo(severity: str, code: str, diag: str) -> dict:
    return {"resourceType": "OperationOutcome",
            "issue": [{"severity": severity, "code": code, "diagnostics": diag}]}


@router.post("/", name="iti_105_submit")
async def submit_bundle(
    _p: Annotated[Principal, Depends(require_scope("system/Bundle.write"))],
    body: dict = Body(...),
) -> JSONResponse:
    if body.get("resourceType") != "Bundle":
        return JSONResponse(status_code=400, content=_oo("error", "invalid", "expected Bundle"))
    btype = body.get("type")
    if btype not in ("transaction", "document"):
        return JSONResponse(status_code=400, content=_oo("error", "invalid", "Bundle.type must be transaction or document"))

    # structural validation
    ok, problems = structural_validate(body)
    if not ok:
        return JSONResponse(status_code=400, content={"resourceType": "OperationOutcome",
            "issue": [{"severity": "error", "code": "structure", "diagnostics": p} for p in problems]})

    # ensure id
    body.setdefault("id", str(uuid.uuid4()))
    inbox_dir = settings.data_dir / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    (inbox_dir / f"{body['id']}.json").write_text(json.dumps(body, indent=2, sort_keys=True))

    # mirror into store
    written: list[str] = []
    for ent in body.get("entry", []) or []:
        res = ent.get("resource")
        if not isinstance(res, dict):
            continue
        rt = res.get("resourceType")
        if rt not in store.SUPPORTED_TYPES:
            continue
        res.setdefault("id", str(uuid.uuid4()))
        store.write(res)
        written.append(f"{rt}/{res['id']}")

    return JSONResponse(
        status_code=201,
        content={
            "resourceType": "Bundle",
            "id": body["id"],
            "type": "transaction-response",
            "entry": [{"response": {"status": "201 Created", "location": ref}} for ref in written],
        },
        headers={"Location": f"{settings.base_url}/Bundle/{body['id']}"},
    )
