from __future__ import annotations

from typing import Dict, Iterable

from psycopg.conninfo import make_conninfo

from app.core.config import Settings, get_settings
from app.core.errors import ConfigurationError
from app.storage.base import BaseStorageAdapter
from app.storage.firebase import FirebaseAdapter
from app.storage.neon import NeonAdapter
from app.storage.upstash import UpstashAdapter


class ProviderRegistry:
    def __init__(self, adapters: Iterable[BaseStorageAdapter]):
        self._adapters: Dict[str, BaseStorageAdapter] = {
            adapter.provider_id: adapter for adapter in adapters
        }

    def get(self, provider_id: str) -> BaseStorageAdapter:
        if provider_id not in self._adapters:
            raise KeyError(f"Unknown provider: {provider_id}")
        return self._adapters[provider_id]

    def all(self) -> Iterable[BaseStorageAdapter]:
        return list(self._adapters.values())

    def provider_ids(self) -> list[str]:
        return list(self._adapters.keys())

    def close(self) -> None:
        for adapter in self._adapters.values():
            adapter.close()

    def __contains__(self, provider_id: str) -> bool:
        return provider_id in self._adapters


def create_provider_registry(settings: Settings | None = None) -> ProviderRegistry:
    config = settings or get_settings()
    timeout = config.provider_timeout_seconds
    provider_configuration = {
        "upstash": (config.upstash_rest_url, config.upstash_rest_token),
        "neon": (config.neon_database_url,),
        "firebase": (config.firebase_project_id, config.firestore_collection),
    }
    configured_values = [
        config.akeyless_access_id,
        config.akeyless_access_key,
        *(value for provider_id in config.provider_ids for value in provider_configuration[provider_id]),
        *(config.akeyless_key_refs.get(provider_id, "") for provider_id in config.provider_ids),
    ]
    placeholder_markers = ("replace_me", "replace_with", "your_", "changeme", "<", ">")
    if timeout <= 0 or any(
        not value or any(marker in value.lower() for marker in placeholder_markers)
        for value in configured_values
    ):
        raise ConfigurationError("Required provider configuration is missing or invalid")
    try:
        make_conninfo(config.neon_database_url)
    except Exception:
        raise ConfigurationError("A provider connection configuration is invalid") from None

    adapters = []
    if "upstash" in config.provider_ids:
        adapters.append(
            UpstashAdapter(
                rest_url=config.upstash_rest_url,
                rest_token=config.upstash_rest_token,
                timeout_seconds=timeout,
            )
        )
    if "neon" in config.provider_ids:
        adapters.append(
            NeonAdapter(
                database_url=config.neon_database_url,
                schema=config.neon_schema,
                table=config.neon_table,
                timeout_seconds=timeout,
            )
        )
    if "firebase" in config.provider_ids:
        adapters.append(
            FirebaseAdapter(
                project_id=config.firebase_project_id,
                collection=config.firestore_collection,
                credentials_path=config.firebase_credentials_path,
                timeout_seconds=timeout,
            )
        )
    return ProviderRegistry(adapters)
