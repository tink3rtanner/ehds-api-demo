"""FastAPI entry point."""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth.smart import router as smart_router
from app.config import settings
from app.security import install as install_security

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # re-queue EU-profile validations a previous process left `pending`
    from app.fhir.validation_queue import resume_pending
    try:
        n = resume_pending()
        if n:
            logging.getLogger("ehds").info("re-queued %d stale validation job(s)", n)
    except Exception:  # noqa: BLE001 — never block startup on a bookkeeping pass
        logging.getLogger("ehds").exception("resume_pending failed")
    yield


def _build_app() -> FastAPI:
    app = FastAPI(
        title="EU Health Data API — reference implementation",
        version="0.2.0",
        docs_url=None if settings.is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_prod else "/openapi.json",
        lifespan=_lifespan,
    )
    install_security(app)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(smart_router)

    from app.routers import binary as binary_router
    from app.routers import bundle as bundle_router
    from app.routers import discovery as discovery_router
    from app.routers import docref as docref_router
    from app.routers import docsubmit as docsubmit_router
    from app.routers import epic_import as epic_import_router
    from app.routers import everything as everything_router
    from app.routers import metadata as metadata_router
    from app.routers import patient as patient_router
    from app.routers import publish as publish_router
    from app.routers import resource as resource_router
    from app.routers import root as root_router
    from app.routers import source_link as source_link_router

    app.include_router(root_router.router)  # GET / discovery document + /llms.txt
    app.include_router(metadata_router.router)
    app.include_router(patient_router.router)
    app.include_router(everything_router.router)
    app.include_router(publish_router.router)  # POST /DocumentReference (ITI-105)
    app.include_router(docref_router.router)
    app.include_router(bundle_router.router)
    app.include_router(binary_router.router)  # legacy 301 -> /Bundle/{id}
    app.include_router(source_link_router.router)  # /{Type}/{id}/$source back-link
    app.include_router(resource_router.router)
    app.include_router(docsubmit_router.router)  # POST / (Bundle convenience, not ITI-105)
    app.include_router(discovery_router.router)  # /register-client + /spec/*
    app.include_router(epic_import_router.router)  # /Epic/$import

    # the human-facing UI: JSON helpers first (so /ui/api/* wins), then the
    # static ES-module app. both disabled in prod.
    if not settings.is_prod:
        from app.routers import ui as ui_router
        app.include_router(ui_router.router)
        if STATIC_DIR.exists():
            app.mount("/ui", StaticFiles(directory=str(STATIC_DIR), html=True), name="ui")

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("ehds")
app = _build_app()


_OO_CODE = {400: "invalid", 401: "login", 403: "forbidden", 404: "not-found", 405: "not-supported",
            413: "too-costly", 422: "processing", 429: "throttled"}


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """OAuth errors stay OAuth-shaped ({"error", "error_description"}, RFC 6749
    5.2); everything else is a FHIR OperationOutcome."""
    headers = getattr(exc, "headers", None)
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        return JSONResponse(status_code=exc.status_code, content=detail, headers=headers)
    text = detail if isinstance(detail, str) else json.dumps(detail)
    return JSONResponse(
        status_code=exc.status_code, headers=headers, media_type="application/fhir+json",
        content={"resourceType": "OperationOutcome", "issue": [{
            "severity": "error", "code": _OO_CODE.get(exc.status_code, "processing"), "diagnostics": text}]},
    )


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    if request.url.path == "/token":
        missing = ", ".join(str(e.get("loc", ["", "?"])[-1]) for e in exc.errors())
        return JSONResponse(status_code=400, content={"error": "invalid_request",
                                                      "error_description": f"missing or invalid form fields: {missing}"})
    issues = [{"severity": "error", "code": "invalid",
               "diagnostics": f"{'.'.join(str(x) for x in e.get('loc', []))}: {e.get('msg')}"} for e in exc.errors()]
    return JSONResponse(status_code=400, media_type="application/fhir+json",
                        content={"resourceType": "OperationOutcome", "issue": issues})


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
