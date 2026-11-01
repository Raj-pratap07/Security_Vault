from __future__ import annotations

from app.storage.postgres import PostgresStorageAdapter


class NeonAdapter(PostgresStorageAdapter):
    provider_id = "neon"
