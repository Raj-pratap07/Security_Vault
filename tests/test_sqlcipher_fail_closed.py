from __future__ import annotations

import base64
import hashlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.db.repository import SecretRepository
from app.db.sqlcipher import open_sqlcipher_database
from app.services.factory import build_orchestrator
from app.storage.registry import ProviderRegistry
from tests.fakes import FakeStorageAdapter, PROVIDERS, new_repo

KEY = "firefly-demo-key"


def digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_missing_database_fails_closed_and_is_not_created(tmp_path):
    path = tmp_path / "missing.db"

    with pytest.raises(ConfigurationError, match="not found"):
        SecretRepository(str(path), KEY)

    assert not path.exists()


def test_valid_encrypted_database_opens_and_reports_sqlcipher(tmp_path):
    path = tmp_path / "valid.db"
    new_repo(path)

    repository = SecretRepository(str(path), KEY)
    connection = open_sqlcipher_database(str(path), KEY)
    try:
        assert connection.execute("PRAGMA cipher_version;").fetchone()[0]
    finally:
        connection.close()
    assert repository.list_pending_records() == []


def test_wrong_key_fails_without_modifying_or_recreating_the_database(tmp_path):
    path = tmp_path / "wrongkey.db"
    new_repo(path)
    before = digest(path)

    with pytest.raises(ConfigurationError) as excinfo:
        SecretRepository(str(path), "not-the-right-key")

    assert digest(path) == before
    assert "not-the-right-key" not in str(excinfo.value)


def test_unencrypted_sqlite_file_is_rejected(tmp_path):
    path = tmp_path / "plain.db"
    plain = sqlite3.connect(path)
    plain.execute("CREATE TABLE secret_records (record_id TEXT)")
    plain.commit()
    plain.close()
    before = digest(path)

    with pytest.raises(ConfigurationError):
        SecretRepository(str(path), KEY)

    assert digest(path) == before


def test_garbage_file_is_rejected_and_preserved(tmp_path):
    path = tmp_path / "garbage.db"
    path.write_bytes(bytes(range(256)) * 64)
    before = digest(path)

    with pytest.raises(ConfigurationError):
        SecretRepository(str(path), KEY)

    assert digest(path) == before


def test_page_corruption_fails_integrity_check_and_is_preserved(tmp_path):
    path = tmp_path / "corrupt.db"
    new_repo(path)
    data = bytearray(path.read_bytes())
    assert len(data) > 8192
    for offset in range(4096 + 64, 4096 + 128):
        data[offset] ^= 0xFF
    path.write_bytes(bytes(data))
    before = digest(path)

    with pytest.raises(ConfigurationError):
        SecretRepository(str(path), KEY)

    assert digest(path) == before


def test_empty_encrypted_database_without_schema_is_rejected(tmp_path):
    path = tmp_path / "noschema.db"
    open_sqlcipher_database(str(path), KEY, create=True).close()

    with pytest.raises(ConfigurationError, match="schema"):
        SecretRepository(str(path), KEY)


def test_explicit_initialization_creates_the_database_once(tmp_path):
    path = tmp_path / "nested" / "first-time.db"

    repository = SecretRepository.initialize(str(path), KEY)

    assert path.is_file()
    assert repository.list_pending_records() == []
    before = digest(path)
    with pytest.raises(ConfigurationError, match="already exists"):
        SecretRepository.initialize(str(path), KEY)
    assert digest(path) == before


def _settings(path) -> Settings:
    return Settings(
        api_token="startup-token",
        database_path=str(path),
        sqlcipher_key_b64=base64.b64encode(bytes(range(32))).decode(),
    )


def test_build_orchestrator_fails_when_database_is_missing(tmp_path):
    settings = _settings(tmp_path / "absent.db")
    registry = ProviderRegistry(FakeStorageAdapter(pid) for pid in PROVIDERS)

    with pytest.raises(ConfigurationError, match="not found"):
        build_orchestrator(settings, provider_registry=registry)

    assert not (tmp_path / "absent.db").exists()


def test_application_startup_fails_closed_for_missing_database(tmp_path, monkeypatch):
    from app import main as main_module
    from app.services import factory

    settings = _settings(tmp_path / "absent.db")
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        factory,
        "create_provider_registry",
        lambda _settings: ProviderRegistry(FakeStorageAdapter(pid) for pid in PROVIDERS),
    )

    with pytest.raises(ConfigurationError):
        with TestClient(main_module.app):
            pass

    assert not (tmp_path / "absent.db").exists()
