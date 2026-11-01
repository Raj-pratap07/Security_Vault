from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from app.api.dependencies import get_orchestrator, require_auth
from app.api.schemas import SecretCreateRequest, SecretCreateResponse, SecretRecoverResponse, SecretStatusResponse
from app.core.config import get_settings
from app.services.orchestrator import StorageOrchestrator

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/secrets", response_model=SecretCreateResponse, status_code=status.HTTP_201_CREATED)
def create_secret(
    request: SecretCreateRequest,
    _token: str = Depends(require_auth),
    orchestrator: StorageOrchestrator = Depends(get_orchestrator),
) -> SecretCreateResponse:
    settings = get_settings()
    if len(request.secret.encode("utf-8")) > settings.max_secret_bytes:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Secret is too large")
    result = orchestrator.store_secret("demo-owner", request.secret)
    return SecretCreateResponse(
        record_id=result["record_id"],
        status=result["status"],
        share_count=result["share_count"],
        threshold=result["threshold"],
        created_at=result["created_at"],
    )


@router.post("/secrets/{record_id}/recover", response_model=SecretRecoverResponse)
def recover_secret(
    record_id: str,
    _token: str = Depends(require_auth),
    orchestrator: StorageOrchestrator = Depends(get_orchestrator),
) -> JSONResponse:
    secret = orchestrator.recover_secret(record_id, "demo-owner")
    return JSONResponse(
        content={"record_id": record_id, "secret": secret},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.get("/secrets/{record_id}/status", response_model=SecretStatusResponse)
def get_status(
    record_id: str,
    _token: str = Depends(require_auth),
    orchestrator: StorageOrchestrator = Depends(get_orchestrator),
) -> SecretStatusResponse:
    record = orchestrator.get_status(record_id, "demo-owner")
    return SecretStatusResponse(
        record_id=record["record_id"],
        version=record["version"],
        status=record["status"],
        threshold=record["threshold"],
        share_count=record["share_count"],
        confirmed_shares=record["confirmed_shares"],
        created_at=record["created_at"],
        updated_at=record["updated_at"],
        operation_id=record.get("operation_id"),
    )
