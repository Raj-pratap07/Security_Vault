from __future__ import annotations

import re
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app.storage.base import BaseStorageAdapter, DeleteResult, HealthResult, PutResult, ShareEnvelope


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class PostgresStorageAdapter(BaseStorageAdapter):
    database_url: str
    schema: str
    table: str

    def __init__(
        self,
        *,
        database_url: str,
        schema: str,
        table: str,
        timeout_seconds: float = 10.0,
    ):
        self.database_url = database_url
        self.schema = self._validate_identifier(schema)
        self.table = self._validate_identifier(table)
        self.timeout_seconds = max(1, int(timeout_seconds))
        self._table_name = sql.SQL(".").join(
            (sql.Identifier(self.schema), sql.Identifier(self.table))
        )

    @staticmethod
    def _validate_identifier(value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("PostgreSQL schema and table names must be simple SQL identifiers")
        return value

    def _connect(self):
        if not self.database_url:
            raise ValueError("PostgreSQL connection is not configured")
        conninfo = make_conninfo(
            self.database_url,
            connect_timeout=self.timeout_seconds,
            sslmode="require",
        )
        return psycopg.connect(conninfo)

    @staticmethod
    def _envelope_values(share_id: str, envelope: ShareEnvelope) -> tuple[Any, ...]:
        return (
            share_id,
            envelope.record_id,
            envelope.version,
            envelope.share_index,
            envelope.provider_id,
            envelope.key_ref,
            envelope.format_version,
            envelope.ciphertext,
        )

    def put_share(self, share_id: str, envelope: ShareEnvelope) -> PutResult:
        statement = sql.SQL(
            """
            INSERT INTO {} (
                share_id, record_id, version, share_index, provider_id, key_ref,
                format_version, ciphertext
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (share_id) DO UPDATE SET
                record_id = EXCLUDED.record_id,
                version = EXCLUDED.version,
                share_index = EXCLUDED.share_index,
                provider_id = EXCLUDED.provider_id,
                key_ref = EXCLUDED.key_ref,
                format_version = EXCLUDED.format_version,
                ciphertext = EXCLUDED.ciphertext
            """
        ).format(self._table_name)
        try:
            with self._connect() as connection:
                connection.execute(statement, self._envelope_values(share_id, envelope))
            return PutResult(self.provider_id, share_id, "STORED", True)
        except psycopg.OperationalError:
            return PutResult(self.provider_id, share_id, "UNKNOWN", False)
        except Exception:
            return PutResult(self.provider_id, share_id, "WRITE_FAILED", False)

    def get_share(self, share_id: str) -> ShareEnvelope | None:
        statement = sql.SQL(
            """
            SELECT record_id, version, share_index, provider_id, key_ref,
                   format_version, ciphertext
            FROM {}
            WHERE share_id = %s
            """
        ).format(self._table_name)
        try:
            with self._connect() as connection:
                row = connection.execute(statement, (share_id,)).fetchone()
        except Exception:
            raise RuntimeError("PostgreSQL share lookup failed") from None
        if row is None:
            return None
        return ShareEnvelope(
            record_id=row[0],
            version=row[1],
            share_index=row[2],
            provider_id=row[3],
            key_ref=row[4],
            format_version=row[5],
            ciphertext=row[6],
        )

    def delete_share(self, share_id: str) -> DeleteResult:
        statement = sql.SQL("DELETE FROM {} WHERE share_id = %s").format(self._table_name)
        try:
            with self._connect() as connection:
                result = connection.execute(statement, (share_id,))
                existed = result.rowcount > 0
            return DeleteResult(
                self.provider_id,
                share_id,
                "DELETE_CONFIRMED" if existed else "CONFIRMED_ABSENT",
                deleted=existed,
                existed=existed,
            )
        except psycopg.OperationalError:
            return DeleteResult(self.provider_id, share_id, "UNKNOWN", False, False)
        except Exception:
            return DeleteResult(self.provider_id, share_id, "DELETE_FAILED", False, False)

    def health_check(self) -> HealthResult:
        statement = sql.SQL("SELECT 1 FROM {} LIMIT 0").format(self._table_name)
        try:
            with self._connect() as connection:
                connection.execute(statement)
            return HealthResult(self.provider_id, "ok", True)
        except Exception:
            return HealthResult(self.provider_id, "unavailable", False)
