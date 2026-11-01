"""Shared in-memory fakes. The fake Akeyless is NOT cryptography: see tests/test_real_crypto.py."""

from __future__ import annotations

from app.core.config import Settings
from app.services.akeyless_service import AkeylessService
from app.storage.base import BaseStorageAdapter, DeleteResult, HealthResult, PutResult, ShareEnvelope
from app.db.repository import SecretRepository


def new_repo(path) -> SecretRepository:
    return SecretRepository.initialize(str(path), "firefly-demo-key")


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class FakeAkeylessSession:
    def __init__(self):
        self.contexts = {}

    def close(self):
        pass

    def post(self, url, *, json, timeout):
        if url.endswith("/auth"):
            return FakeResponse(200, {"token": "test-token", "expiration": "2099-01-01T00:00:00Z"})
        if url.endswith("/encrypt"):
            self.contexts[json["plaintext"]] = (json["key-name"], json["encryption-context"])
            return FakeResponse(200, {"result": json["plaintext"]})
        if url.endswith("/decrypt"):
            context = self.contexts.get(json["ciphertext"])
            if context != (json["key-name"], json["encryption-context"]):
                return FakeResponse(400, {"error": "invalid context"})
            return FakeResponse(200, {"result": json["ciphertext"]})
        return FakeResponse(404, {})


class FakeUpstashSession:
    def __init__(self):
        self.data = {}
        self.commands = []

    def post(self, url, *, json, headers, timeout):
        self.commands.append(json)
        command = json[0]
        if command == "SET":
            self.data[json[1]] = json[2]
            return FakeResponse(200, {"result": "OK"})
        if command == "GET":
            return FakeResponse(200, {"result": self.data.get(json[1])})
        if command == "DEL":
            existed = json[1] in self.data
            self.data.pop(json[1], None)
            return FakeResponse(200, {"result": int(existed)})
        if command == "PING":
            return FakeResponse(200, {"result": "PONG"})
        return FakeResponse(400, {})


class FakeStorageAdapter(BaseStorageAdapter):
    def __init__(self, provider_id):
        self.provider_id = provider_id
        self.data = {}
        self.get_calls = 0

    def put_share(self, share_id, envelope):
        self.data[share_id] = envelope
        return PutResult(self.provider_id, share_id, "STORED", True)

    def get_share(self, share_id):
        self.get_calls += 1
        return self.data.get(share_id)

    def delete_share(self, share_id):
        existed = share_id in self.data
        self.data.pop(share_id, None)
        return DeleteResult(
            self.provider_id,
            share_id,
            "DELETE_CONFIRMED" if existed else "CONFIRMED_ABSENT",
            existed,
            existed,
        )

    def health_check(self):
        return HealthResult(self.provider_id, "ok", True)


class FailingStorageAdapter(FakeStorageAdapter):
    def put_share(self, share_id, envelope):
        return PutResult(self.provider_id, share_id, "WRITE_FAILED", False)


class UnknownAfterWriteAdapter(FakeStorageAdapter):
    def __init__(self, provider_id):
        super().__init__(provider_id)
        self.put_attempts = 0

    def put_share(self, share_id, envelope):
        self.put_attempts += 1
        self.data[share_id] = envelope
        if self.put_attempts == 1:
            return PutResult(self.provider_id, share_id, "UNKNOWN", False)
        return PutResult(self.provider_id, share_id, "STORED", True)


class MissingReadbackAdapter(FakeStorageAdapter):
    def __init__(self, provider_id):
        super().__init__(provider_id)
        self.put_attempts = 0

    def put_share(self, share_id, envelope):
        self.put_attempts += 1
        return PutResult(self.provider_id, share_id, "STORED", True)


class MismatchingReadbackAdapter(FakeStorageAdapter):
    def put_share(self, share_id, envelope):
        self.data[share_id] = ShareEnvelope(
            record_id=envelope.record_id,
            version=envelope.version,
            share_index=envelope.share_index,
            provider_id=envelope.provider_id,
            key_ref=envelope.key_ref,
            format_version=envelope.format_version,
            ciphertext="unexpected-ciphertext",
        )
        return PutResult(self.provider_id, share_id, "STORED", True)


class ReadbackOnceErrorAdapter(FakeStorageAdapter):
    def __init__(self, provider_id):
        super().__init__(provider_id)
        self.readback_attempts = 0

    def get_share(self, share_id):
        self.get_calls += 1
        self.readback_attempts += 1
        if self.readback_attempts == 1:
            raise RuntimeError("safe simulated read timeout")
        return self.data.get(share_id)


def fake_akeyless_service():
    return AkeylessService(
        key_refs={
            "upstash": "test-upstash-key",
            "neon": "test-key",
            "firebase": "test-firebase-key",
        },
        settings=Settings(),
        api_url="https://akeyless.test",
        access_id="test-access-id",
        access_key="test-access-key",
        session=FakeAkeylessSession(),
    )


PROVIDERS = ("upstash", "neon", "firebase")


class SimulatedCrash(BaseException):
    """Process death: not an Exception, so no rollback/cleanup handler may run."""


class ControllableAdapter(FakeStorageAdapter):
    def __init__(self, provider_id):
        super().__init__(provider_id)
        self.fail_reads = False
        self.fail_deletes = False
        self.crash_on_put = False

    def put_share(self, share_id, envelope):
        if self.crash_on_put:
            raise SimulatedCrash()
        return super().put_share(share_id, envelope)

    def get_share(self, share_id):
        if self.fail_reads:
            self.get_calls += 1
            raise RuntimeError("simulated provider outage")
        return super().get_share(share_id)

    def delete_share(self, share_id):
        if self.fail_deletes:
            return DeleteResult(self.provider_id, share_id, "DELETE_FAILED", False, False)
        return super().delete_share(share_id)


def build_stack(tmp_path, adapters=None, name="stack.db"):
    """Real repository/SSS/orchestrator over fake adapters and fake Akeyless."""
    from app.services.orchestrator import StorageOrchestrator
    from app.services.sss_service import SSSService
    from app.storage.registry import ProviderRegistry

    adapters = adapters or [ControllableAdapter(provider_id) for provider_id in PROVIDERS]
    orchestrator = StorageOrchestrator(
        repository=new_repo(tmp_path / name),
        sss_service=SSSService(),
        akeyless_service=fake_akeyless_service(),
        provider_registry=ProviderRegistry(adapters),
    )
    return orchestrator, {adapter.provider_id: adapter for adapter in adapters}


def forge_share(orchestrator, adapters, record_id, share_index, plaintext_share_bytes):
    """Overwrite a provider's stored ciphertext with a correctly-contextualized one."""
    provider_id = PROVIDERS[share_index - 1]
    adapter = adapters[provider_id]
    share_id = f"firefly:{record_id}:v1:share:{share_index}"
    original = adapter.data[share_id]
    ciphertext = orchestrator.akeyless_service.encrypt_share(
        plaintext_share_bytes,
        record_id=record_id,
        version=1,
        share_index=share_index,
        provider_id=provider_id,
        key_ref=original.key_ref,
    )
    adapter.data[share_id] = ShareEnvelope(
        record_id=original.record_id,
        version=original.version,
        share_index=original.share_index,
        provider_id=original.provider_id,
        key_ref=original.key_ref,
        format_version=original.format_version,
        ciphertext=ciphertext,
    )
