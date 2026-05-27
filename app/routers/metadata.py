"""GET /metadata — CapabilityStatement.

built on the fly from app.fhir.capability so adding new resource handlers
elsewhere automatically surfaces in the capability doc.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.fhir.capability import build_capability_statement

router = APIRouter()


@router.get("/metadata")
def metadata() -> JSONResponse:
    return JSONResponse(build_capability_statement())
