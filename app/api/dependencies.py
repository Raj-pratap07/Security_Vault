from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.core.errors import ConfigurationError
from app.services.demo_service import DemoService
from app.services.orchestrator import StorageOrchestrator

bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    """Demo-only single-token auth. Fails closed if the token is unset or blank."""
    expected = get_settings().api_token
    if not expected.strip() or credentials is None or not credentials.credentials:
        raise _unauthorized()
    if not hmac.compare_digest(
        credentials.credentials.encode("utf-8"),
        expected.encode("utf-8"),
    ):
        raise _unauthorized()
    return credentials.credentials


def get_orchestrator(request: Request) -> StorageOrchestrator:
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        raise ConfigurationError("Firefly services are not initialized")
    return orchestrator


def require_demo_enabled() -> None:
    """The /demo endpoints expose decrypted shares, so they do not exist unless switched on."""
    if not get_settings().demo_endpoints:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


def get_demo_service(request: Request) -> DemoService:
    demo_service = getattr(request.app.state, "demo_service", None)
    if demo_service is None:
        raise ConfigurationError("Firefly services are not initialized")
    return demo_service

