from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from app.core.errors import ConfigurationError, RecordNotFoundError
from app.db.sqlcipher import (
    check_sqlcipher_integrity,
    initialize_sqlcipher_schema,
    open_sqlcipher_database,
    verify_sqlcipher_schema,
)

RECORD_COLUMNS = (
    "record_id",
    "owner_id",
    "version",
    "share_count",
    "threshold",
    "status",
    "created_at",
    "updated_at",
    "operation_id",
    "last_error_code",
)
SHARE_COLUMNS = (
    "record_id",
    "version",
    "share_index",
    "provider_id",
    "share_id",
    "key_ref",
    "status",
    "attempt_count",
    "last_attempt_at",
    "last_error_code",
)
_RECORD_SELECT = ", ".join(RECORD_COLUMNS)
_SHARE_SELECT = ", ".join(SHARE_COLUMNS)


class SecretRepository:
    """SQLCipher metadata store.

    Construction opens an existing, verified database and never creates one.
    First-time setup is the separate, explicit `SecretRepository.initialize`.
    """

    def __init__(
        self,
        database_path: str,
        sqlcipher_key: str,
        *,
        share_count: int = 3,
        threshold: int = 2,
    ):
        self.database_path = database_path
        self.sqlcipher_key = sqlcipher_key
        if (share_count, threshold) != (3, 2):
            raise ValueError("Firefly metadata requires exactly 3 shares with threshold 2")
        self.share_count = share_count
        self.threshold = threshold
        self._verify_existing_database()

    @classmethod
    def initialize(
        cls,
        database_path: str,
        sqlcipher_key: str,
        *,
        share_count: int = 3,
        threshold: int = 2,
    ) -> "SecretRepository":
        """Explicit first-time setup. Refuses to touch an existing file."""
        if os.path.exists(database_path):
            raise ConfigurationError("SQLCipher database already exists; refusing to overwrite it")
        connection = open_sqlcipher_database(database_path, sqlcipher_key, create=True)
        try:
            initialize_sqlcipher_schema(
                connection,
                share_count=share_count,
                threshold=threshold,
            )
        finally:
            connection.close()
        return cls(
            database_path,
            sqlcipher_key,
            share_count=share_count,
            threshold=threshold,
        )

    @contextmanager
    def _session(self) -> Iterator[Any]:
        """One short transaction on a fresh connection; always closed."""
        connection = open_sqlcipher_database(self.database_path, self.sqlcipher_key)
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _verify_existing_database(self) -> None:
        connection = open_sqlcipher_database(self.database_path, self.sqlcipher_key)
        try:
            if not verify_sqlcipher_schema(connection):
                raise ConfigurationError("SQLCipher database schema is missing or incomplete")
            if not check_sqlcipher_integrity(connection):
                raise ConfigurationError("SQLCipher integrity check failed")
        finally:
            connection.close()

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _record_dict(row: Any) -> Dict[str, Any]:
        return dict(zip(RECORD_COLUMNS, row))

    @staticmethod
    def _share_dict(row: Any) -> Dict[str, Any]:
        return dict(zip(SHARE_COLUMNS, row))

    @staticmethod
    def _insert_share_rows(connection: Any, record_id: str, version: int, share_config: List[Dict[str, Any]]) -> None:
        for row in share_config:
            connection.execute(
                """
                INSERT INTO share_operations (
                    record_id, version, share_index, provider_id, share_id, key_ref,
                    status, attempt_count, last_attempt_at, last_error_code
                ) VALUES (?, ?, ?, ?, ?, ?, 'NOT_STARTED', 0, NULL, NULL)
                """,
                (
                    record_id,
                    version,
                    row["share_index"],
                    row["provider_id"],
                    row["share_id"],
                    row["key_ref"],
                ),
            )

    def create_record(
        self,
        *,
        record_id: str,
        owner_id: str,
        status: str = "PENDING",
        operation_id: Optional[str] = None,
        share_count: int = 3,
        threshold: int = 2,
        version: int = 1,
    ) -> Dict[str, Any]:
        now = self._utc_now_iso()
        with self._session() as connection:
            connection.execute(
                """
                INSERT INTO secret_records (
                    record_id, owner_id, version, share_count, threshold, status,
                    created_at, updated_at, operation_id, last_error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (record_id, owner_id, version, share_count, threshold, status, now, now, operation_id),
            )
        return self.get_record(record_id)

    def create_pending_operation(
        self,
        *,
        record_id: str,
        owner_id: str,
        version: int,
        share_config: List[Dict[str, Any]],
        share_count: int = 3,
        threshold: int = 2,
    ) -> Dict[str, Any]:
        if len(share_config) != share_count or share_count < threshold:
            raise ConfigurationError("Storage configuration has an invalid share count")
        if share_count != self.share_count or threshold != self.threshold:
            raise ConfigurationError("Storage configuration does not match the metadata database")
        now = self._utc_now_iso()
        with self._session() as connection:
            connection.execute(
                """
                INSERT INTO secret_records (
                    record_id, owner_id, version, share_count, threshold, status,
                    created_at, updated_at, operation_id, last_error_code
                ) VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?, NULL, NULL)
                """,
                (record_id, owner_id, version, share_count, threshold, now, now),
            )
            self._insert_share_rows(connection, record_id, version, share_config)
        return self.get_record(record_id)

    def create_share_rows(
        self,
        *,
        record_id: str,
        version: int,
        share_config: List[Dict[str, Any]],
    ) -> None:
        with self._session() as connection:
            self._insert_share_rows(connection, record_id, version, share_config)

    def get_record(self, record_id: str) -> Dict[str, Any]:
        with self._session() as connection:
            row = connection.execute(
                f"SELECT {_RECORD_SELECT} FROM secret_records WHERE record_id = ?",
                (record_id,),
            ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Record {record_id} not found")
        return self._record_dict(row)

    def get_record_for_owner(self, record_id: str, owner_id: str) -> Dict[str, Any]:
        with self._session() as connection:
            row = connection.execute(
                f"SELECT {_RECORD_SELECT} FROM secret_records WHERE record_id = ? AND owner_id = ?",
                (record_id, owner_id),
            ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Record {record_id} for owner {owner_id} not found")
        return self._record_dict(row)

    def get_share_records_for_record(self, record_id: str, version: int = 1) -> List[Dict[str, Any]]:
        with self._session() as connection:
            rows = connection.execute(
                f"""
                SELECT {_SHARE_SELECT}
                FROM share_operations
                WHERE record_id = ? AND version = ?
                ORDER BY share_index ASC
                """,
                (record_id, version),
            ).fetchall()
        return [self._share_dict(row) for row in rows]

    def record_write_attempt(self, *, record_id: str, version: int, share_index: int) -> None:
        """Count one real provider write attempt (not every status change)."""
        now = self._utc_now_iso()
        with self._session() as connection:
            connection.execute(
                """
                UPDATE share_operations
                SET attempt_count = attempt_count + 1, last_attempt_at = ?
                WHERE record_id = ? AND version = ? AND share_index = ?
                """,
                (now, record_id, version, share_index),
            )

    def update_share_status(
        self,
        *,
        record_id: str,
        version: int,
        share_index: int,
        status: str,
        last_error_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = self._utc_now_iso()
        with self._session() as connection:
            connection.execute(
                """
                UPDATE share_operations
                SET status = ?, last_error_code = ?
                WHERE record_id = ? AND version = ? AND share_index = ?
                """,
                (status, last_error_code, record_id, version, share_index),
            )
            connection.execute(
                "UPDATE secret_records SET updated_at = ? WHERE record_id = ?",
                (now, record_id),
            )
        return self.get_share_record(record_id, version, share_index)

    def get_share_record(self, record_id: str, version: int, share_index: int) -> Dict[str, Any]:
        with self._session() as connection:
            row = connection.execute(
                f"""
                SELECT {_SHARE_SELECT}
                FROM share_operations
                WHERE record_id = ? AND version = ? AND share_index = ?
                """,
                (record_id, version, share_index),
            ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Share {share_index} for record {record_id} not found")
        return self._share_dict(row)

    def set_record_status(
        self,
        *,
        record_id: str,
        status: str,
        last_error_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = self._utc_now_iso()
        with self._session() as connection:
            if status == "AVAILABLE":
                record = connection.execute(
                    "SELECT share_count, version FROM secret_records WHERE record_id = ?",
                    (record_id,),
                ).fetchone()
                if record is None:
                    raise RecordNotFoundError(f"Record {record_id} not found")
                stored_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM share_operations
                    WHERE record_id = ? AND version = ? AND status = 'STORED'
                    """,
                    (record_id, record[1]),
                ).fetchone()[0]
                if stored_count != record[0]:
                    raise ConfigurationError(
                        "A record is AVAILABLE only after all configured writes are confirmed"
                    )
            connection.execute(
                "UPDATE secret_records SET status = ?, updated_at = ?, last_error_code = ? WHERE record_id = ?",
                (status, now, last_error_code, record_id),
            )
        return self.get_record(record_id)

    def get_status(self, record_id: str) -> Dict[str, Any]:
        return self.get_record(record_id)

    def list_pending_records(self) -> List[Dict[str, Any]]:
        """Records whose storage or cleanup did not finish (record-level statuses only)."""
        with self._session() as connection:
            rows = connection.execute(
                f"""
                SELECT {_RECORD_SELECT} FROM secret_records
                WHERE status IN ('PENDING', 'CLEANUP_REQUIRED')
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [self._record_dict(row) for row in rows]
