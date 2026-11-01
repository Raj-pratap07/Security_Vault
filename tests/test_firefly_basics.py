from __future__ import annotations

import logging
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import Settings, profile_database_path
from app.core.errors import ConfigurationError, RecoveryError
from app.services.orchestrator import StorageOrchestrator
from app.services.sss_service import SSSService
from app.storage.base import ShareEnvelope, build_share_id
from app.storage.registry import ProviderRegistry
from app.storage.neon import NeonAdapter
from app.storage.upstash import UpstashAdapter
from scripts import check_services
from tests.fakes import (
    FailingStorageAdapter,
    FakeStorageAdapter,
    FakeUpstashSession,
    MismatchingReadbackAdapter,
    MissingReadbackAdapter,
    ReadbackOnceErrorAdapter,
    UnknownAfterWriteAdapter,
    fake_akeyless_service,
    new_repo,
)


@pytest.fixture
def repo(tmp_path):
    db_path = tmp_path / "firefly.db"
    return new_repo(str(db_path))


@pytest.fixture
def provider_registry():
    return ProviderRegistry(
        FakeStorageAdapter(provider_id)
        for provider_id in ("upstash", "neon", "firebase")
    )


def test_sss_round_trip_all_two_share_combinations():
    service = SSSService(share_count=3, threshold=2)
    secret = "hello-firefly"
    shares = service.split_secret(secret)
    assert len(shares) == 3

    for combo in combinations(shares, 2):
        recovered = service.recover_secret(combo)
        assert recovered.decode("utf-8") == secret


def test_sss_rejects_less_than_threshold():
    service = SSSService()
    shares = service.split_secret("abc")
    with pytest.raises(ValueError):
        service.recover_secret(shares[:1])


def test_sss_rejects_unsupported_configuration():
    with pytest.raises(ValueError, match="exactly 3 shares with a threshold of 2"):
        SSSService(share_count=5, threshold=3)


def test_sss_recovers_from_any_two_shares():
    service = SSSService()
    secret = b"\x00three-provider-secret"
    shares = service.split_secret(secret)

    assert len(shares) == 3
    for combo in combinations(shares, 2):
        assert service.recover_secret(combo) == secret
    with pytest.raises(ValueError, match="at least 2"):
        service.recover_secret(shares[:1])


def test_three_provider_is_the_only_supported_profile():
    assert Settings().profile == "three_provider"
    assert Settings().provider_ids == ("upstash", "neon", "firebase")
    assert Settings().share_count == 3
    assert Settings().threshold == 2
    with pytest.raises(ValueError, match="Only FIREFLY_PROFILE=three_provider"):
        Settings(profile="full")
    with pytest.raises(ValueError, match="does not support production"):
        Settings(profile="three_provider", environment="production")


def test_permanent_three_provider_profile_uses_isolated_metadata_database():
    configured_path = Path("./data/firefly.db")

    assert profile_database_path(configured_path) == str(
        configured_path.with_name("firefly.three-provider.db")
    )
    assert profile_database_path(Path("./data/firefly.three-provider.db")) == str(
        Path("./data/firefly.three-provider.db")
    )


def test_sss_preserves_binary_zero_bytes_and_rejects_duplicate_indexes():
    service = SSSService()
    secret = b"\x00\x00\xff" + b"binary-secret-data" * 2
    shares = service.split_secret(secret)

    assert service.recover_secret(shares[1:3]) == secret
    with pytest.raises(ValueError, match="distinct"):
        service.recover_secret([shares[0], shares[0], shares[2]])


def test_akeyless_round_trip_and_context_validation():
    service = fake_akeyless_service()
    record_id = "rec-123"
    version = 1
    share_index = 2
    provider_id = "neon"
    key_ref = service.key_refs[provider_id]
    share_bytes = b"\x00\x01\xff\x10secret-shard"

    ciphertext = service.encrypt_share(
        share_bytes,
        record_id=record_id,
        version=version,
        share_index=share_index,
        provider_id=provider_id,
        key_ref=key_ref,
    )

    recovered = service.decrypt_share(
        ciphertext,
        record_id=record_id,
        version=version,
        share_index=share_index,
        provider_id=provider_id,
        key_ref=key_ref,
    )
    assert recovered == share_bytes

    with pytest.raises(Exception):
        service.decrypt_share(
            ciphertext,
            record_id=record_id,
            version=version,
            share_index=share_index + 1,
            provider_id=provider_id,
            key_ref=key_ref,
        )


def test_repository_persists_pending_and_availability_state(repo):
    record_id = "record-1"
    repo.create_record(record_id=record_id, owner_id="owner-1", status="PENDING")
    repo.create_share_rows(
        record_id=record_id,
        version=1,
        share_config=[
            {"share_index": 1, "provider_id": "upstash", "share_id": "id-1", "key_ref": "ref-1"},
            {"share_index": 2, "provider_id": "neon", "share_id": "id-2", "key_ref": "ref-2"},
            {"share_index": 3, "provider_id": "firebase", "share_id": "id-3", "key_ref": "ref-3"},
        ],
    )

    share_rows = repo.get_share_records_for_record(record_id, 1)
    assert len(share_rows) == 3
    repo.update_share_status(record_id=record_id, version=1, share_index=1, status="STORED")
    with pytest.raises(ConfigurationError):
        repo.set_record_status(record_id=record_id, status="AVAILABLE")
    for share_index in range(2, 4):
        repo.update_share_status(
            record_id=record_id,
            version=1,
            share_index=share_index,
            status="STORED",
        )
    repo.set_record_status(record_id=record_id, status="AVAILABLE")
    record = repo.get_record(record_id)
    assert record["status"] == "AVAILABLE"


def test_orchestrator_store_and_recover_round_trip(provider_registry, repo):
    orchestrator = StorageOrchestrator(
        repository=repo,
        sss_service=SSSService(),
        akeyless_service=fake_akeyless_service(),
        provider_registry=provider_registry,
    )

    result = orchestrator.store_secret("owner-1", "super-secret-value")
    assert result["status"] == "AVAILABLE"
    recovered = orchestrator.recover_secret(result["record_id"], "owner-1")
    assert recovered == "super-secret-value"

    record = orchestrator.get_status(result["record_id"], "owner-1")
    assert record["confirmed_shares"] == 3


def test_orchestrator_stores_and_recovers_with_two_shares(tmp_path):
    provider_registry = ProviderRegistry(
        FakeStorageAdapter(provider_id)
        for provider_id in ("upstash", "neon", "firebase")
    )
    repo = new_repo(str(tmp_path / "firefly.three-provider.db"))
    orchestrator = StorageOrchestrator(
        repository=repo,
        sss_service=SSSService(),
        akeyless_service=fake_akeyless_service(),
        provider_registry=provider_registry,
    )

    result = orchestrator.store_secret("owner-1", "synthetic-test-secret")
    assert result["status"] == "AVAILABLE"
    assert result["share_count"] == 3
    assert result["threshold"] == 2
    assert orchestrator.recover_secret(result["record_id"], "owner-1") == "synthetic-test-secret"
    assert len(repo.get_share_records_for_record(result["record_id"], 1)) == 3


def test_metadata_requires_all_three_writes_before_available(tmp_path):
    repo = new_repo(str(tmp_path / "firefly.three-provider.db"))
    share_config = [
        {
            "share_index": index,
            "provider_id": provider_id,
            "share_id": f"share-{index}",
            "key_ref": f"key-{provider_id}",
        }
        for index, provider_id in enumerate(("upstash", "neon", "firebase"), start=1)
    ]
    repo.create_pending_operation(
        record_id="three-provider-record",
        owner_id="owner-1",
        version=1,
        share_config=share_config,
        share_count=3,
        threshold=2,
    )
    for index in (1, 2):
        repo.update_share_status(
            record_id="three-provider-record",
            version=1,
            share_index=index,
            status="STORED",
        )

    with pytest.raises(ConfigurationError, match="all configured writes"):
        repo.set_record_status(record_id="three-provider-record", status="AVAILABLE")


def test_provider_registry_has_only_the_three_active_adapters(provider_registry):
    assert provider_registry.provider_ids() == ["upstash", "neon", "firebase"]


def test_three_provider_registry_builds_only_the_active_adapters(monkeypatch):
    from app.storage import registry as registry_module

    settings = Settings(
        akeyless_access_id="test-access-id",
        akeyless_access_key="test-access-key",
        akeyless_key_refs={
            "upstash": "test-upstash-key",
            "neon": "test-neon-key",
            "firebase": "test-firebase-key",
        },
        upstash_rest_url="https://upstash.test",
        upstash_rest_token="test-upstash-token",
        neon_database_url="postgresql://test:test@localhost/firefly",
        firebase_project_id="test-project",
        firestore_collection="test-shares",
    )
    monkeypatch.setattr(
        registry_module,
        "UpstashAdapter",
        Mock(return_value=FakeStorageAdapter("upstash")),
    )
    monkeypatch.setattr(
        registry_module,
        "NeonAdapter",
        Mock(return_value=FakeStorageAdapter("neon")),
    )
    monkeypatch.setattr(
        registry_module,
        "FirebaseAdapter",
        Mock(return_value=FakeStorageAdapter("firebase")),
    )

    registry = registry_module.create_provider_registry(settings)

    assert registry.provider_ids() == ["upstash", "neon", "firebase"]


def test_provider_store_get_delete_round_trip():
    adapter = FakeStorageAdapter("upstash")
    envelope = ShareEnvelope(
        record_id="r1",
        version=1,
        share_index=3,
        provider_id="upstash",
        key_ref="ref",
        format_version=1,
        ciphertext="opaque-ciphertext",
    )
    share_id = build_share_id("r1", 1, 3)
    put = adapter.put_share(share_id, envelope)
    assert put.success is True
    assert adapter.get_share(share_id) == envelope
    deleted = adapter.delete_share(share_id)
    assert deleted.deleted is True


def test_upstash_rest_adapter_uses_persistent_set_without_ttl():
    session = FakeUpstashSession()
    adapter = UpstashAdapter(
        rest_url="https://upstash.test",
        rest_token="test-token",
        session=session,
    )
    envelope = ShareEnvelope("record", 1, 1, "upstash", "ref", 1, "ciphertext")
    share_id = build_share_id("record", 1, 1)

    assert adapter.put_share(share_id, envelope).success
    assert adapter.get_share(share_id) == envelope
    assert session.commands[0][:2] == ["SET", share_id]
    assert "EX" not in session.commands[0]
    assert adapter.delete_share(share_id).deleted
    assert adapter.get_share(share_id) is None


def test_postgres_adapter_rejects_unsafe_table_identifiers():
    with pytest.raises(ValueError):
        NeonAdapter(
            database_url="postgresql://user:password@localhost/firefly",
            schema="public",
            table="shares; DROP TABLE secret_records",
        )


def test_provider_registry_fails_fast_without_real_configuration():
    with pytest.raises(ConfigurationError, match="configuration"):
        from app.storage.registry import create_provider_registry

        create_provider_registry(Settings())


def test_partial_provider_failure_rolls_back_and_records_failed_state(tmp_path, monkeypatch):
    from app.services import orchestrator as orchestrator_module

    upstash = FakeStorageAdapter("upstash")
    neon = FailingStorageAdapter("neon")
    firebase = FakeStorageAdapter("firebase")
    registry = ProviderRegistry((upstash, neon, firebase))
    repo = new_repo(str(tmp_path / "firefly.three-provider.db"))
    monkeypatch.setattr(
        orchestrator_module.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="partial-write-record"),
    )
    orchestrator = StorageOrchestrator(
        repository=repo,
        sss_service=SSSService(),
        akeyless_service=fake_akeyless_service(),
        provider_registry=registry,
    )

    with pytest.raises(RecoveryError, match="provider writes failed"):
        orchestrator.store_secret("owner-1", "synthetic-failure-test")

    assert upstash.data == {}
    assert neon.data == {}
    assert firebase.data == {}
    assert repo.get_record("partial-write-record")["status"] == "FAILED"


def test_unknown_provider_write_is_reconciled_before_retry(tmp_path):
    upstash = UnknownAfterWriteAdapter("upstash")
    neon = FakeStorageAdapter("neon")
    firebase = FakeStorageAdapter("firebase")
    registry = ProviderRegistry((upstash, neon, firebase))
    repo = new_repo(str(tmp_path / "firefly.three-provider.db"))
    orchestrator = StorageOrchestrator(
        repository=repo,
        sss_service=SSSService(),
        akeyless_service=fake_akeyless_service(),
        provider_registry=registry,
    )

    result = orchestrator.store_secret("owner-1", "synthetic-timeout-test")

    assert result["status"] == "AVAILABLE"
    assert upstash.put_attempts == 1
    assert len(upstash.data) == len(neon.data) == len(firebase.data) == 1
    record = orchestrator.get_status(result["record_id"], "owner-1")
    assert record["status"] == "AVAILABLE"
    assert record["confirmed_shares"] == 3


def test_successful_provider_writes_are_read_back_before_available(provider_registry, repo):
    orchestrator = StorageOrchestrator(
        repository=repo,
        sss_service=SSSService(),
        akeyless_service=fake_akeyless_service(),
        provider_registry=provider_registry,
    )

    result = orchestrator.store_secret("owner-1", "readback-confirmed-secret")

    assert result["status"] == "AVAILABLE"
    assert all(adapter.get_calls == 1 for adapter in provider_registry.all())


def test_mismatched_provider_readback_prevents_available(tmp_path, monkeypatch):
    from app.services import orchestrator as orchestrator_module

    upstash = MismatchingReadbackAdapter("upstash")
    neon = FakeStorageAdapter("neon")
    firebase = FakeStorageAdapter("firebase")
    registry = ProviderRegistry((upstash, neon, firebase))
    repo = new_repo(str(tmp_path / "readback-mismatch.db"))
    monkeypatch.setattr(
        orchestrator_module.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="mismatched-readback-record"),
    )
    orchestrator = StorageOrchestrator(
        repository=repo,
        sss_service=SSSService(),
        akeyless_service=fake_akeyless_service(),
        provider_registry=registry,
    )

    with pytest.raises(RecoveryError, match="provider writes failed"):
        orchestrator.store_secret("owner-1", "synthetic-mismatch-secret")

    assert repo.get_record("mismatched-readback-record")["status"] == "FAILED"
    assert upstash.data == {}


def test_missing_provider_readback_retries_same_id_then_fails(tmp_path, monkeypatch):
    from app.services import orchestrator as orchestrator_module

    upstash = MissingReadbackAdapter("upstash")
    registry = ProviderRegistry((upstash, FakeStorageAdapter("neon"), FakeStorageAdapter("firebase")))
    repo = new_repo(str(tmp_path / "readback-missing.db"))
    monkeypatch.setattr(
        orchestrator_module.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="missing-readback-record"),
    )
    orchestrator = StorageOrchestrator(
        repository=repo,
        sss_service=SSSService(),
        akeyless_service=fake_akeyless_service(),
        provider_registry=registry,
    )

    with pytest.raises(RecoveryError, match="provider writes failed"):
        orchestrator.store_secret("owner-1", "synthetic-missing-readback")

    assert upstash.put_attempts == 2
    assert repo.get_record("missing-readback-record")["status"] == "FAILED"


def test_readback_timeout_is_reconciled_before_marking_stored(tmp_path):
    upstash = ReadbackOnceErrorAdapter("upstash")
    registry = ProviderRegistry((upstash, FakeStorageAdapter("neon"), FakeStorageAdapter("firebase")))
    repo = new_repo(str(tmp_path / "readback-timeout.db"))
    orchestrator = StorageOrchestrator(
        repository=repo,
        sss_service=SSSService(),
        akeyless_service=fake_akeyless_service(),
        provider_registry=registry,
    )

    result = orchestrator.store_secret("owner-1", "synthetic-reconciled-readback")

    assert result["status"] == "AVAILABLE"
    assert upstash.readback_attempts == 2


def test_recovery_can_exercise_each_provider_pair(provider_registry, repo):
    orchestrator = StorageOrchestrator(
        repository=repo,
        sss_service=SSSService(),
        akeyless_service=fake_akeyless_service(),
        provider_registry=provider_registry,
    )
    synthetic = "same-secret-for-all-provider-pairs"
    result = orchestrator.store_secret("owner-1", synthetic)

    for pair in combinations(("upstash", "neon", "firebase"), 2):
        recovered = orchestrator._recover_secret_from_provider_ids(
            result["record_id"], "owner-1", set(pair)
        )
        assert recovered == synthetic


def test_fastapi_routes_auth_validate_store_and_recover_through_orchestrator(
    tmp_path, monkeypatch
):
    from app.api import routes
    from app.api import dependencies

    settings = Settings(
        api_token="integration-test-token",
        database_path=str(tmp_path / "api-flow.db"),
    )
    repo = new_repo(settings.database_path)
    registry = ProviderRegistry(
        FakeStorageAdapter(provider_id)
        for provider_id in ("upstash", "neon", "firebase")
    )
    orchestrator = StorageOrchestrator(
        repository=repo,
        sss_service=SSSService(),
        akeyless_service=fake_akeyless_service(),
        provider_registry=registry,
    )
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setitem(app.dependency_overrides, dependencies.get_orchestrator, lambda: orchestrator)

    client = TestClient(app)
    assert client.post("/secrets", json={"secret": "synthetic-api-flow"}).status_code == 401
    headers = {"Authorization": "Bearer integration-test-token"}
    invalid = client.post("/secrets", headers=headers, json={"unexpected": "body"})
    assert invalid.status_code == 422
    oversized = client.post(
        "/secrets",
        headers=headers,
        json={"secret": "😀" * 1025},
    )
    assert oversized.status_code == 413

    stored = client.post("/secrets", headers=headers, json={"secret": "synthetic-api-flow"})
    assert stored.status_code == 201
    assert "secret" not in stored.json()
    record_id = stored.json()["record_id"]
    status_response = client.get(f"/secrets/{record_id}/status", headers=headers)
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "AVAILABLE"
    assert "owner_id" not in status_response.json()
    assert stored.json()["created_at"] == status_response.json()["created_at"]

    recovered = client.post(f"/secrets/{record_id}/recover", headers=headers)
    assert recovered.status_code == 200
    assert recovered.json()["secret"] == "synthetic-api-flow"
    assert recovered.headers["cache-control"] == "no-store"


def test_api_health_and_authentication_are_local(monkeypatch):
    from app.api import dependencies

    monkeypatch.setattr(dependencies, "get_settings", lambda: Settings(api_token="local-test-token"))
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}
    response = client.post("/secrets", json={"secret": "synthetic"})
    assert response.status_code == 401


def test_startup_logs_effective_demo_profile_without_credentials(tmp_path, monkeypatch, caplog):
    from app import main as main_module

    settings = Settings(
        api_token="startup-test-token",
        database_path=str(tmp_path / "firefly.three-provider.db"),
    )
    registry = ProviderRegistry(FakeStorageAdapter(pid) for pid in settings.provider_ids)
    orchestrator = StorageOrchestrator(
        repository=new_repo(settings.database_path),
        sss_service=SSSService(),
        akeyless_service=fake_akeyless_service(),
        provider_registry=registry,
    )
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(main_module, "build_orchestrator", lambda _settings: orchestrator)
    with caplog.at_level(logging.INFO, logger="firefly.runtime"):
        with TestClient(main_module.app) as client:
            assert client.get("/health").json() == {"status": "ok"}

    assert (
        "profile=three_provider providers=upstash,neon,firebase shares=3 threshold=2"
        in caplog.text
    )
    assert settings.database_path in caplog.text


def test_service_preflight_checks_three_providers_and_akeyless(monkeypatch, capsys):
    settings = Settings(
        akeyless_key_refs={
            "upstash": "test-upstash-key",
            "neon": "test-neon-key",
            "firebase": "test-firebase-key",
        }
    )
    registry = ProviderRegistry(
        FakeStorageAdapter(provider_id) for provider_id in settings.provider_ids
    )

    class FakeAkeyless:
        def encrypt_share(self, data, **_kwargs):
            return f"opaque-{data.hex()}"

        def decrypt_share(self, ciphertext, **_kwargs):
            return bytes.fromhex(ciphertext.removeprefix("opaque-"))

    monkeypatch.setattr(check_services, "get_settings", lambda: settings)
    monkeypatch.setattr(check_services, "create_provider_registry", lambda _settings: registry)
    monkeypatch.setattr(check_services, "AkeylessService", FakeAkeyless)

    assert check_services.main() == 0
    output = capsys.readouterr().out
    assert "upstash: ok" in output
    assert "neon: ok" in output
    assert "firebase: ok" in output
    assert output.count("akeyless/") == 3
    assert "Preflight: ok" in output
