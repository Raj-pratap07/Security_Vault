from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.api import dependencies
from app.core.config import Settings
from app.core.errors import ConfigurationError, RecordNotFoundError
from app.main import app

TOKEN = "unit-test-bearer-token"
URL = "/secrets/some-record/status"


@pytest.fixture
def client_for(monkeypatch):
    """Client whose orchestrator answers 404, so only authentication is under test."""
    orchestrator = Mock()
    orchestrator.get_status.side_effect = RecordNotFoundError("missing")
    monkeypatch.setitem(app.dependency_overrides, dependencies.get_orchestrator, lambda: orchestrator)

    def make(configured_token: str) -> TestClient:
        monkeypatch.setattr(dependencies, "get_settings", lambda: Settings(api_token=configured_token))
        return TestClient(app)

    return make


def test_valid_token_is_accepted(client_for):
    response = client_for(TOKEN).get(URL, headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 404  # authenticated, then the record lookup fails


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer "},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": f"Bearer {TOKEN}x"},
        {"Authorization": f"Basic {TOKEN}"},
    ],
    ids=["absent", "empty-value", "no-value", "invalid", "near-miss", "wrong-scheme"],
)
def test_missing_empty_or_invalid_tokens_are_rejected(client_for, headers):
    response = client_for(TOKEN).get(URL, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("configured", ["", "   "], ids=["empty", "blank"])
@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer "}, {"Authorization": "Bearer anything"}],
    ids=["absent", "empty-value", "any-token"],
)
def test_unconfigured_token_never_authenticates(client_for, configured, headers):
    assert client_for(configured).get(URL, headers=headers).status_code == 401


def test_comparison_uses_constant_time_compare(client_for, monkeypatch):
    spy = Mock(wraps=dependencies.hmac.compare_digest)
    monkeypatch.setattr(dependencies.hmac, "compare_digest", spy)

    client_for(TOKEN).get(URL, headers={"Authorization": f"Bearer {TOKEN}"})

    spy.assert_called_once()


@pytest.mark.parametrize("configured", ["", "   "], ids=["empty", "blank"])
def test_startup_rejects_missing_or_blank_token(configured):
    with pytest.raises(ConfigurationError, match="FIREFLY_API_TOKEN"):
        Settings(api_token=configured).require_api_token()


def test_application_startup_fails_when_token_is_not_configured(monkeypatch):
    monkeypatch.setattr(main_module, "get_settings", lambda: Settings(api_token=""))

    with pytest.raises(ConfigurationError, match="FIREFLY_API_TOKEN"):
        with TestClient(main_module.app):
            pass
