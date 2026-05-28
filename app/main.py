"""FastAPI entry point."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.auth.smart import router as smart_router
from app.config import settings
from app.security import install as install_security


def _build_app() -> FastAPI:
    app = FastAPI(
        title="EHDS Demo FHIR Server",
        version="0.1.0",
        docs_url=None if settings.is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_prod else "/openapi.json",
    )
    install_security(app)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # dev convenience: GET / -> /ui (root POST is reserved for ITI-105 submission)
    if not settings.is_prod:
        from fastapi.responses import RedirectResponse

        @app.get("/", include_in_schema=False)
        def _root_redirect() -> RedirectResponse:
            return RedirectResponse(url="/ui", status_code=307)

    app.include_router(smart_router)

    from app.routers import metadata as metadata_router
    from app.routers import patient as patient_router
    from app.routers import resource as resource_router
    from app.routers import docref as docref_router
    from app.routers import binary as binary_router
    from app.routers import bundle as bundle_router
    from app.routers import everything as everything_router
    from app.routers import docsubmit as docsubmit_router

    app.include_router(metadata_router.router)
    app.include_router(patient_router.router)
    app.include_router(everything_router.router)
    app.include_router(docref_router.router)
    app.include_router(bundle_router.router)
    app.include_router(binary_router.router)  # legacy 301 -> /Bundle/{id}
    app.include_router(resource_router.router)
    app.include_router(docsubmit_router.router)

    # dev-only UI (gated on ENV != prod inside the router)
    if not settings.is_prod:
        from app.routers import ui as ui_router
        app.include_router(ui_router.router)

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("ehds")
app = _build_app()


@app.exception_handler(Exception)
async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "resourceType": "OperationOutcome",
            "issue": [{"severity": "error", "code": "exception", "diagnostics": "internal server error"}],
        },
    )
