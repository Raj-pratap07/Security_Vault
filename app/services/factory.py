from __future__ import annotations

from app.core.config import Settings, get_settings
from app.db.repository import SecretRepository
from app.services.akeyless_service import AkeylessService
from app.services.orchestrator import StorageOrchestrator
from app.services.sss_service import SSSService
from app.storage.registry import ProviderRegistry, create_provider_registry


def build_orchestrator(
    settings: Settings | None = None,
    *,
    provider_registry: ProviderRegistry | None = None,
    akeyless_service: AkeylessService | None = None,
) -> StorageOrchestrator:
    """Construct the long-lived services once. Fails closed on any bad config/database."""
    config = settings or get_settings()
    registry = provider_registry or create_provider_registry(config)
    repository = SecretRepository(
        config.database_path,
        config.sqlcipher_key,
        share_count=config.share_count,
        threshold=config.threshold,
    )
    return StorageOrchestrator(
        repository=repository,
        sss_service=SSSService(share_count=config.share_count, threshold=config.threshold),
        akeyless_service=akeyless_service or AkeylessService(settings=config),
        provider_registry=registry,
    )
