from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from app.api.dependencies import get_demo_service, require_auth, require_demo_enabled
from app.api.schemas import (
    DemoCleanupResponse,
    DemoCollectResponse,
    DemoFetchRequest,
    DemoFetchResponse,
    DemoRebuildResponse,
    DemoShareCard,
    DemoShareInfo,
)
from app.services.demo_service import DemoService

# Demo-only. The dependencies run in order: the router is invisible (404) unless enabled,
# then a valid bearer token is required. Every response is non-cacheable.
router = APIRouter(
    prefix="/demo",
    tags=["demo"],
    dependencies=[Depends(require_demo_enabled), Depends(require_auth)],
)

# Share holders have no bearer token: presenting genuine shares is their credential. The service
# checks every card against the real stored share, so an invalid card unlocks nothing.
holder_router = APIRouter(prefix="/demo", tags=["demo"], dependencies=[Depends(require_demo_enabled)])

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
OWNER = "demo-owner"


@router.get("/secrets/{record_id}/shares", response_model=List[DemoShareInfo])
def list_shares(record_id: str, demo: DemoService = Depends(get_demo_service)) -> JSONResponse:
    shares = [DemoShareInfo(**share).model_dump() for share in demo.list_shares(record_id, OWNER)]
    return JSONResponse(content=shares, headers=NO_STORE)


@router.post("/secrets/{record_id}/collect/{share_index}", response_model=DemoCollectResponse)
def collect_share(record_id: str, share_index: int, demo: DemoService = Depends(get_demo_service)) -> JSONResponse:
    result = DemoCollectResponse(**demo.collect(record_id, OWNER, share_index))
    return JSONResponse(content=result.model_dump(), headers=NO_STORE)


@router.get("/secrets/{record_id}/export/{share_index}", response_model=DemoShareCard)
def export_share(record_id: str, share_index: int, demo: DemoService = Depends(get_demo_service)) -> JSONResponse:
    """One decrypted share as a hand-out card. Demo only: it deliberately reveals a share."""
    card = DemoShareCard(**demo.export_share(record_id, OWNER, share_index))
    return JSONResponse(content=card.model_dump(), headers=NO_STORE)


@router.post("/secrets/{record_id}/rebuild", response_model=DemoRebuildResponse)
def rebuild_secret(record_id: str, demo: DemoService = Depends(get_demo_service)) -> JSONResponse:
    """200 with the secret when enough shares are held; 409 with an explanation when not."""
    result = DemoRebuildResponse(**demo.rebuild(record_id, OWNER))
    return JSONResponse(
        content=result.model_dump(exclude_none=True),
        status_code=status.HTTP_200_OK if result.rebuilt else status.HTTP_409_CONFLICT,
        headers=NO_STORE,
    )


@router.delete("/secrets/{record_id}/collected", status_code=status.HTTP_204_NO_CONTENT)
def reset_collected(record_id: str, demo: DemoService = Depends(get_demo_service)) -> JSONResponse:
    demo.reset(record_id, OWNER)
    return JSONResponse(content=None, status_code=status.HTTP_204_NO_CONTENT, headers=NO_STORE)


@router.delete("/secrets/{record_id}", response_model=DemoCleanupResponse)
def cleanup_secret(record_id: str, demo: DemoService = Depends(get_demo_service)) -> JSONResponse:
    result = DemoCleanupResponse(**demo.cleanup(record_id, OWNER))
    return JSONResponse(content=result.model_dump(), headers=NO_STORE)


@holder_router.post("/secrets/{record_id}/fetch", response_model=DemoFetchResponse)
def fetch_secret(record_id: str, body: DemoFetchRequest, demo: DemoService = Depends(get_demo_service)) -> JSONResponse:
    """200 with the secret when enough genuine shares are presented; 409 with a reason when not."""
    result = DemoFetchResponse(**demo.fetch_with_shares(record_id, OWNER, [card.model_dump() for card in body.cards]))
    return JSONResponse(
        content=result.model_dump(exclude_none=True),
        status_code=status.HTTP_200_OK if result.rebuilt else status.HTTP_409_CONFLICT,
        headers=NO_STORE,
    )
