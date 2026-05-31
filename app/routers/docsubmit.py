"""ITI-105 document submission.

accepts POST / (root) with Content-Type: application/fhir+json carrying a
Bundle.type=transaction whose entries contain a DocumentReference + Binary
(and any supporting Composition + clinical resources).

we:
  1. validate the bundle structurally via fhir.resources
  2. persist the as-submitted bundle to data/inbox/<bundle-id>.json (evidence)
  3. NATURALIZE the constituent resources into this server's local identity
     space — local uuid5 ids, rewritten references, original ids preserved as
     `urn:ehds-demo:source-id` identifiers, and `meta.source` back-links — then
     mirror them into the store so they're queryable
  4. return the bundle with the server-assigned (local) ids per FHIR
     transaction-response semantics

Naturalizing on the way in (rather than trusting foreign ids verbatim) keeps
the store in one consistent identity space and stops external submissions from
polluting the panel with foreign-id'd, dangling-reference resources. See
`app/fhir/naturalize.py` and `docs/resource-identity.md`.
"""
from __future__ import annotations

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from app.auth.verify import Principal, require_scope
from app.config import settings
from app.fhir import store
from app.fhir.naturalize import naturalize_bundle
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

    # ensure id, then persist the AS-SUBMITTED bundle as evidence (foreign ids
    # intact) before we naturalize — the inbox is the original-of-record.
    body.setdefault("id", str(uuid.uuid4()))
    inbox_dir = settings.data_dir / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    (inbox_dir / f"{body['id']}.json").write_text(json.dumps(body, indent=2, sort_keys=True))

    # naturalize into local identity (local ids + rewritten refs + source
    # back-links) and mirror into the store
    written: list[str] = []
    for res in naturalize_bundle(body):
        store.write(res)
        written.append(f"{res['resourceType']}/{res['id']}")

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
