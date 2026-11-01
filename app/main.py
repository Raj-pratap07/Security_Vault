from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.demo_routes import holder_router, router as demo_router
from app.api.routes import router
from app.core.config import get_settings
from app.core.errors import ConfigurationError, ProviderError, RecordNotFoundError, RecoveryError
from app.services.demo_service import DemoService
from app.services.factory import build_orchestrator


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.require_api_token()
    orchestrator = build_orchestrator(settings)
    logging.getLogger("firefly.runtime").info(
        "Firefly demo profile=%s providers=%s shares=%d threshold=%d metadata_db=%s",
        settings.profile,
        ",".join(settings.provider_ids),
        settings.share_count,
        settings.threshold,
        settings.database_path,
    )
    app.state.orchestrator = orchestrator
    app.state.demo_service = DemoService(orchestrator)
    try:
        orchestrator.reconcile_pending()
        yield
    finally:
        app.state.orchestrator = None
        app.state.demo_service = None
        orchestrator.close()


app = FastAPI(title="Firefly", debug=False, lifespan=lifespan)
app.include_router(router)
app.include_router(demo_router)
app.include_router(holder_router)


@app.exception_handler(RequestValidationError)
def handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI's default body echoes the submitted input, which here may be a secret.
    errors = [{"loc": list(error["loc"]), "type": error["type"]} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.exception_handler(RecordNotFoundError)
def handle_not_found(_request: Request, _exc: RecordNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Record not found"})


@app.exception_handler(RecoveryError)
def handle_recovery_error(_request: Request, _exc: RecoveryError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "Secret recovery is unavailable"})


@app.exception_handler(ProviderError)
def handle_provider_error(_request: Request, _exc: ProviderError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "A required service is unavailable"})


@app.exception_handler(ConfigurationError)
def handle_configuration_error(_request: Request, _exc: ConfigurationError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "Firefly is not correctly configured"})