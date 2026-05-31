"""generic /{Type}/{id} and /{Type}?... router for first-class resource types.

Patient + DocumentReference + Binary + Bundle have their own routers with more
specific behaviour; this one handles everything else.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.auth.verify import Principal, require_scope
from app.fhir import store

router = APIRouter()

# types served by this generic router (excludes ones that have dedicated routers)
GENERIC_TYPES = tuple(t for t in store.SUPPORTED_TYPES if t not in {"Patient", "DocumentReference"})


def _operation_outcome(severity: str, code: str, diagnostics: str) -> dict:
    return {
        "resourceType": "OperationOutcome",
        "issue": [{"severity": severity, "code": code, "diagnostics": diagnostics}],
    }


def _attach(rtype: str) -> None:
    # NOTE: f-strings inside Annotated break under `from __future__ import
    # annotations` because get_type_hints can't see closure cells. so we use
    # the default-value Depends form, which is eagerly bound.
    scope_dep = Depends(require_scope(f"system/{rtype}.read"))

    @router.get(f"/{rtype}/{{rid}}", name=f"read_{rtype}")
    async def _read(rid: str, _p: Principal = scope_dep) -> JSONResponse:
        if "/" in rid or ".." in rid:
            return JSONResponse(status_code=400, content=_operation_outcome("error", "invalid", "bad id"))
        res = store.read(rtype, rid)
        if res is None:
            return JSONResponse(status_code=404, content=_operation_outcome("error", "not-found", f"{rtype}/{rid}"))
        return JSONResponse(res, media_type="application/fhir+json")

    @router.get(f"/{rtype}", name=f"search_{rtype}")
    async def _search(request: Request, _p: Principal = scope_dep) -> JSONResponse:
        norm: dict[str, list[str]] = {}
        for k, v in request.query_params.multi_items():
            norm.setdefault(k, []).append(v)
        results = store.search(rtype, norm)
        return JSONResponse(store.bundle_searchset(rtype, results, self_link=str(request.url)),
                            media_type="application/fhir+json")


for _rtype in GENERIC_TYPES:
    _attach(_rtype)
