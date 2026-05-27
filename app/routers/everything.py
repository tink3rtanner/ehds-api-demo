"""Patient/{id}/$everything — bundle of every resource in the patient's compartment."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.auth.verify import Principal, require_scope
from app.config import settings
from app.fhir import store

router = APIRouter()


@router.get("/Patient/{rid}/$everything", name="patient_everything")
async def patient_everything(
    rid: str,
    _p: Annotated[Principal, Depends(require_scope("system/Patient.read"))],
) -> JSONResponse:
    if store.read("Patient", rid) is None:
        return JSONResponse(
            status_code=404,
            content={"resourceType": "OperationOutcome",
                     "issue": [{"severity": "error", "code": "not-found", "diagnostics": f"Patient/{rid}"}]},
        )
    resources = store.all_referenced_resources_for_patient(rid)
    entries = [
        {"fullUrl": f"{settings.base_url}/{r['resourceType']}/{r['id']}", "resource": r}
        for r in resources
    ]
    return JSONResponse({
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(entries),
        "entry": entries,
    }, media_type="application/fhir+json")
