from __future__ import annotations

import os

import sqlcipher3

from app.core.errors import ConfigurationError

REQUIRED_TABLES = ("secret_records", "share_operations")


def _escape_sqlcipher_key(value: str) -> str:
    return value.replace("'", "''")


def open_sqlcipher_database(database_path: str, key: str, *, create: bool = False):
    """Open a SQLCipher database, failing closed.

    Normal startup (create=False) never creates a file: a missing database is an
    error. Only explicit first-time initialization passes create=True.
    """
    if create:
        os.makedirs(os.path.dirname(database_path) or ".", exist_ok=True)
    elif not os.path.isfile(database_path):
        raise ConfigurationError(
            "SQLCipher database not found; run explicit first-time initialization"
        )
    connection = sqlcipher3.connect(database_path)
    try:
        connection.execute(f"PRAGMA key = '{_escape_sqlcipher_key(key)}';")
        version = connection.execute("PRAGMA cipher_version;").fetchone()
        if not version or not version[0]:
            raise ConfigurationError("The SQLite driver is not linked to SQLCipher")
        # The first read fails for a wrong key or a damaged header page.
        connection.execute("SELECT count(*) FROM sqlite_master;").fetchone()
    except ConfigurationError:
        connection.close()
        raise
    except Exception:
        connection.close()
        raise ConfigurationError(
            "SQLCipher database could not be opened with the configured key"
        ) from None
    return connection


def initialize_sqlcipher_schema(
    connection,
    *,
    share_count: int = 3,
    threshold: int = 2,
) -> None:
    if (share_count, threshold) != (3, 2):
        raise ValueError("Firefly metadata requires exactly 3 shares with threshold 2")
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS secret_records (
            record_id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            share_count INTEGER NOT NULL CHECK (share_count = {share_count}),
            threshold INTEGER NOT NULL CHECK (threshold = {threshold}),
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            operation_id TEXT,
            last_error_code TEXT
        );
        """
    )
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS share_operations (
            record_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            share_index INTEGER NOT NULL CHECK (share_index BETWEEN 1 AND {share_count}),
            provider_id TEXT NOT NULL,
            share_id TEXT NOT NULL,
            key_ref TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_attempt_at TEXT,
            last_error_code TEXT,
            PRIMARY KEY (record_id, version, share_index),
            UNIQUE (share_id),
            FOREIGN KEY (record_id) REFERENCES secret_records(record_id)
        );
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_secret_records_owner_status ON secret_records(owner_id, status);"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_share_operations_record_status ON share_operations(record_id, status);"
    )
    connection.commit()


def verify_sqlcipher_schema(connection) -> bool:
    """Return True only if the expected tables exist in the opened database."""
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table';"
    ).fetchall()
    return set(REQUIRED_TABLES).issubset({row[0] for row in rows})


def check_sqlcipher_integrity(connection) -> bool:
    """cipher_integrity_check returns no rows when every page authenticates."""
    try:
        return connection.execute("PRAGMA cipher_integrity_check;").fetchall() == []
    except Exception:
        return False
