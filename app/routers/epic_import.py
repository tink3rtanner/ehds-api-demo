"""HTTP endpoint: POST /Epic/$import?patient=<epicPatientId>

Ingests one Epic patient compartment into the local store and (optionally)
returns the compiled IPS Patient Summary bundle.

Requires scope ``system/Patient.write`` so it's gated behind the same
backend-services auth as ITI-105 submissions. In dev mode the auth layer
short-circuits and any bearer works.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.auth.verify import Principal, require_scope
from app.fhir.document import compile_document
from app.sources.epic_client import EpicAuthError, EpicClient, EpicConfigError
from app.sources.epic_ingest import ingest_patient

router = APIRouter()


@router.post("/Epic/$import", name="epic_import")
async def epic_import(
    patient: Annotated[str, Query(description="Epic Patient.id")],
    _p: Annotated[Principal, Depends(require_scope("system/Patient.write"))],
    bundle: bool = Query(True, description="also compile and return the IPS Bundle"),
) -> JSONResponse:
    try:
        client = EpicClient()
    except EpicConfigError as e:
        return JSONResponse(
            status_code=503,
            content={"resourceType": "OperationOutcome", "issue": [{
                "severity": "error", "code": "not-supported",
                "diagnostics": f"Epic source not configured: {e}",
            }]},
        )

    try:
        summary = ingest_patient(client, patient)
    except EpicAuthError as e:
        return JSONResponse(
            status_code=502,
            content={"resourceType": "OperationOutcome", "issue": [{
                "severity": "error", "code": "security",
                "diagnostics": f"Epic auth failed: {e}",
            }]},
        )

    body: dict = {
        "epicPatientId": summary.epic_patient_id,
        "patientId": summary.patient_id,
        "counts": summary.counts,
        "skipped": summary.skipped,
    }
    if bundle:
        body["bundle"] = compile_document(summary.patient_id, "patient-summary")
    return JSONResponse(body, media_type="application/fhir+json")
