from __future__ import annotations

import base64
import logging
import secrets
import traceback

import pytest
from fastapi.testclient import TestClient

from app.api import dependencies, routes
from app.core.config import Settings
from app.core.errors import RecoveryError
from app.main import app
from app.services.orchestrator import StorageOrchestrator
from app.services.sss_service import SSSService
from app.storage.registry import ProviderRegistry
from app.storage.upstash import UpstashAdapter
from tests.fakes import (
    ControllableAdapter,
    FailingStorageAdapter,
    FakeStorageAdapter,
    FakeUpstashSession,
    build_stack,
    fake_akeyless_service,
    new_repo,
)

TOKEN = "redaction-test-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def every_rendering(secret: str) -> list[str]:
    raw = secret.encode("utf-8")
    return [secret, base64.b64encode(raw).decode(), raw.hex()]


def dump_metadata(repository) -> str:
    with repository._session() as connection:
        rows = connection.execute("SELECT * FROM secret_records").fetchall()
        rows += connection.execute("SELECT * FROM share_operations").fetchall()
    return repr(rows)


@pytest.fixture
def api(tmp_path, monkeypatch):
    def make(adapters):
        orchestrator, by_id = build_stack(tmp_path, adapters)
        settings = Settings(api_token=TOKEN)
        monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
        monkeypatch.setattr(routes, "get_settings", lambda: settings)
        monkeypatch.setitem(app.dependency_overrides, dependencies.get_orchestrator, lambda: orchestrator)
        return TestClient(app), orchestrator, by_id

    return make


def test_canary_secret_never_reaches_logs_responses_providers_or_metadata(api, caplog):
    canary = "CANARY-" + secrets.token_hex(16)
    client, orchestrator, adapters = api(None)
    caplog.set_level(logging.DEBUG)

    created = client.post("/secrets", headers=HEADERS, json={"secret": canary})
    record_id = created.json()["record_id"]
    status_body = client.get(f"/secrets/{record_id}/status", headers=HEADERS).text
    recovered = client.post(f"/secrets/{record_id}/recover", headers=HEADERS)
    unauthorized = client.post("/secrets", json={"secret": canary})
    oversized = client.post("/secrets", headers=HEADERS, json={"secret": canary + "x" * 5000})
    missing = client.post("/secrets/nope/recover", headers=HEADERS)
    orchestrator.reconcile_pending()

    assert recovered.json()["secret"] == canary  # the only place the secret may appear
    surfaces = {
        "log": caplog.text,
        "create response": created.text,
        "status response": status_body,
        "401 response": unauthorized.text,
        "oversized response": oversized.text,
        "404 response": missing.text,
        "provider payloads": repr([adapter.data for adapter in adapters.values()]),
        "metadata rows": dump_metadata(orchestrator.repository),
    }
    for rendering in every_rendering(canary):
        for name, text in surfaces.items():
            assert rendering not in text, f"canary leaked into {name}"


def test_canary_never_reaches_error_responses_logs_or_tracebacks_on_failure(api, caplog):
    canary = "CANARY-" + secrets.token_hex(16)
    adapters = [ControllableAdapter("upstash"), FailingStorageAdapter("neon"), ControllableAdapter("firebase")]
    client, orchestrator, by_id = api(adapters)
    by_id["upstash"].fail_deletes = True  # also exercise the cleanup-failure path
    caplog.set_level(logging.DEBUG)

    failed = client.post("/secrets", headers=HEADERS, json={"secret": canary})
    with pytest.raises(RecoveryError) as excinfo:
        orchestrator.store_secret("demo-owner", canary)
    rendered_exception = "".join(
        traceback.format_exception(type(excinfo.value), excinfo.value, excinfo.value.__traceback__)
    )
    orchestrator.reconcile_pending()

    assert failed.status_code == 503
    assert failed.json() == {"detail": "Secret recovery is unavailable"}
    surfaces = {
        "log": caplog.text,
        "error response": failed.text,
        "exception": repr(excinfo.value) + str(excinfo.value),
        "traceback": rendered_exception,
        "metadata rows": dump_metadata(orchestrator.repository),
    }
    for rendering in every_rendering(canary):
        for name, text in surfaces.items():
            assert rendering not in text, f"canary leaked into {name}"


def test_validation_errors_do_not_echo_the_submitted_secret(api):
    client, _orchestrator, _adapters = api(None)
    canary = "CANARY-" + secrets.token_hex(16)

    too_long = client.post("/secrets", headers=HEADERS, json={"secret": canary + "x" * 5000})
    wrong_type = client.post("/secrets", headers=HEADERS, json={"secret": [canary]})
    extra_field = client.post("/secrets", headers=HEADERS, json={"unexpected": canary})

    for response in (too_long, wrong_type, extra_field):
        assert response.status_code == 422
        assert canary not in response.text
        assert response.json()["detail"][0]["type"]


def test_recovery_failure_response_is_generic(api):
    client, orchestrator, adapters = api(None)
    record_id = client.post("/secrets", headers=HEADERS, json={"secret": "x-secret"}).json()["record_id"]
    for adapter in adapters.values():
        adapter.fail_reads = True

    response = client.post(f"/secrets/{record_id}/recover", headers=HEADERS)

    assert response.status_code == 503
    assert response.json() == {"detail": "Secret recovery is unavailable"}
    assert "simulated provider outage" not in response.text


# --- maximum payload size (item 7) -------------------------------------------

MAX_BYTES = Settings().max_secret_bytes
WORST_CASE_SECRETS = {
    "ascii": "A" * MAX_BYTES,
    "four-byte-utf8": "\U0001F600" * (MAX_BYTES // 4),
    "mixed": ("é€\U0001F600z" * MAX_BYTES)[: MAX_BYTES // 4] ,
}


@pytest.mark.parametrize("name", list(WORST_CASE_SECRETS))
def test_maximum_size_secret_round_trips_through_every_stage(tmp_path, name):
    secret = WORST_CASE_SECRETS[name]
    assert len(secret.encode("utf-8")) <= MAX_BYTES
    if name != "mixed":
        assert len(secret.encode("utf-8")) == MAX_BYTES

    sss = SSSService()
    shares = sss.split_secret(secret)
    share_bytes = shares[0].to_bytes()
    # Measured for the 4 KiB limit: ~22 KB per share, ~30 KB once Base64'd for Akeyless.
    assert len(share_bytes) < 25_000
    assert len(base64.b64encode(share_bytes)) < 33_000

    session = FakeUpstashSession()
    upstash = UpstashAdapter(rest_url="https://upstash.test", rest_token="t", session=session)
    registry = ProviderRegistry([upstash, FakeStorageAdapter("neon"), FakeStorageAdapter("firebase")])
    orchestrator = StorageOrchestrator(
        repository=new_repo(tmp_path / f"{name}.db"),
        sss_service=sss,
        akeyless_service=fake_akeyless_service(),
        provider_registry=registry,
    )

    stored = orchestrator.store_secret("demo-owner", secret)

    assert stored["status"] == "AVAILABLE"
    assert orchestrator.recover_secret(stored["record_id"], "demo-owner") == secret
    for pair in (("upstash", "neon"), ("upstash", "firebase"), ("neon", "firebase")):
        assert orchestrator._recover_secret_from_provider_ids(
            stored["record_id"], "demo-owner", set(pair)
        ) == secret


def test_api_rejects_a_secret_one_byte_over_the_limit(api):
    client, _orchestrator, _adapters = api(None)

    over = client.post("/secrets", headers=HEADERS, json={"secret": "A" * (MAX_BYTES)})
    assert over.status_code == 201
    too_big = client.post("/secrets", headers=HEADERS, json={"secret": "\U0001F600" * (MAX_BYTES // 4) + "A"})
    assert too_big.status_code == 413
