"""Rehearsal server: the real FastAPI app, with in-memory clouds and a SIMULATED Akeyless.

    uvicorn scripts.demo_server:create_app --factory --port 8000
    python -m scripts.demo --rehearsal

Needs no credentials and touches no cloud. Akeyless is faked (Base64 only), so this is
for practising the flow, not evidence of real encryption. For the real thing run
`uvicorn app.main:app` with FIREFLY_DEMO_ENDPOINTS=true and your .env.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import FastAPI

from app.services.orchestrator import StorageOrchestrator
from app.services.sss_service import SSSService
from app.storage.registry import ProviderRegistry
from tests.fakes import FakeStorageAdapter, fake_akeyless_service, new_repo

REHEARSAL_TOKEN = "rehearsal-token"
PROVIDERS = ("upstash", "neon", "firebase")


def build_rehearsal_orchestrator(workdir: Path | None = None) -> StorageOrchestrator:
    folder = workdir or Path(tempfile.mkdtemp(prefix="firefly-rehearsal-"))
    return StorageOrchestrator(
        repository=new_repo(folder / "rehearsal-metadata.db"),
        sss_service=SSSService(),
        akeyless_service=fake_akeyless_service(),
        provider_registry=ProviderRegistry(FakeStorageAdapter(pid) for pid in PROVIDERS),
    )


def create_app() -> FastAPI:
    os.environ["FIREFLY_API_TOKEN"] = REHEARSAL_TOKEN
    os.environ["FIREFLY_DEMO_ENDPOINTS"] = "true"
    os.environ["FIREFLY_ENV"] = "development"
    from app import main as main_module

    main_module.build_orchestrator = lambda _settings: build_rehearsal_orchestrator()
    return main_module.app
