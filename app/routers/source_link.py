"""`GET /{Type}/{id}/$source` — resolve a resource's back-link to its origin.

This is the "open at source" affordance. A naturalized resource carries
`meta.source` = the absolute URL it was pulled from (e.g. an Epic FHIR REST
resource URL). This operation reads that and 307-redirects to it, so a viewer
can render a clickable link that lands on the live source resource.

Note: the link is resolvable at the SOURCE's FHIR REST granularity. For Epic
there is no MHD/ITI-67-68 document query endpoint, so the back-link is always a
per-resource FHIR URL, never a document query. If the source has drifted or the
resource is gone, following the link is what surfaces that — we don't promise
the bytes still match, only the path back.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, RedirectResponse

from app.auth.verify import Principal, require_scope
from app.fhir import store

router = APIRouter()


def _oo(severity: str, code: str, diagnostics: str) -> dict:
    return {"resourceType": "OperationOutcome",
            "issue": [{"severity": severity, "code": code, "diagnostics": diagnostics}]}


def _attach(rtype: str) -> None:
    scope_dep = Depends(require_scope(f"system/{rtype}.read"))

    @router.get(f"/{rtype}/{{rid}}/$source", name=f"source_link_{rtype}")
    async def _source(rid: str, _p: Principal = scope_dep):
        if "/" in rid or ".." in rid:
            return JSONResponse(status_code=400, content=_oo("error", "invalid", "bad id"))
        res = store.read(rtype, rid)
        if res is None:
            return JSONResponse(status_code=404, content=_oo("error", "not-found", f"{rtype}/{rid}"))
        src = (res.get("meta") or {}).get("source")
        if not src:
            return JSONResponse(
                status_code=404,
                content=_oo("information", "not-found",
                            f"no source back-link recorded for {rtype}/{rid}"),
            )
        # 307 keeps it a redirect without implying the source is a permanent home
        return RedirectResponse(src, status_code=307)


for _rtype in store.SUPPORTED_TYPES:
    _attach(_rtype)
