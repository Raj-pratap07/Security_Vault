"""Live Akeyless tests: real AES-256-GCM, no mocks. Opt-in because they call the real service.

    $env:FIREFLY_LIVE_TESTS = "1"; python -m pytest tests/test_real_crypto.py -q

They use the credentials/key references in your local .env, send only synthetic
bytes, and write nothing to the storage providers. They are skipped by default
and cannot be satisfied by the in-memory fake in tests/fakes.py, which is not
cryptography.
"""

from __future__ import annotations

import base64
import os
import secrets

import pytest

from app.core.config import get_settings
from app.core.errors import ProviderError
from app.services.akeyless_service import AkeylessService

pytestmark = pytest.mark.skipif(
    os.environ.get("FIREFLY_LIVE_TESTS") != "1",
    reason="live Akeyless test; set FIREFLY_LIVE_TESTS=1 to run",
)

CONTEXT = {"record_id": "live-crypto-test", "version": 1, "share_index": 1, "provider_id": "upstash"}


@pytest.fixture(scope="module")
def akeyless():
    service = AkeylessService(settings=get_settings())
    yield service
    service.close()


@pytest.fixture
def plaintext() -> bytes:
    return b"\x00synthetic-share-" + secrets.token_bytes(48)


def encrypt(service, data, **overrides):
    args = {**CONTEXT, "key_ref": service.key_refs[overrides.get("provider_id", CONTEXT["provider_id"])], **overrides}
    return service.encrypt_share(data, **args)


def decrypt(service, ciphertext, **overrides):
    args = {**CONTEXT, "key_ref": service.key_refs[overrides.get("provider_id", CONTEXT["provider_id"])], **overrides}
    return service.decrypt_share(ciphertext, **args)


def test_ciphertext_differs_from_plaintext_and_decrypts_back(akeyless, plaintext):
    ciphertext = encrypt(akeyless, plaintext)

    assert ciphertext != base64.b64encode(plaintext).decode()
    assert base64.b64encode(plaintext).decode() not in ciphertext
    assert plaintext not in ciphertext.encode()
    assert decrypt(akeyless, ciphertext) == plaintext


def test_encryption_is_randomized(akeyless, plaintext):
    assert encrypt(akeyless, plaintext) != encrypt(akeyless, plaintext)


@pytest.mark.parametrize(
    "field,value",
    [("record_id", "other-record"), ("version", 2), ("share_index", 2), ("format_version", 2)],
)
def test_any_encryption_context_mismatch_is_rejected(akeyless, plaintext, field, value):
    ciphertext = encrypt(akeyless, plaintext)

    with pytest.raises(ProviderError):
        decrypt(akeyless, ciphertext, **{field: value})


def test_tampered_ciphertext_is_rejected(akeyless, plaintext):
    ciphertext = encrypt(akeyless, plaintext)
    raw = bytearray(base64.b64decode(ciphertext))
    raw[len(raw) // 2] ^= 0x01
    tampered = base64.b64encode(bytes(raw)).decode()

    with pytest.raises(ProviderError):
        decrypt(akeyless, tampered)


def test_ciphertext_does_not_decrypt_under_a_different_providers_key(akeyless, plaintext):
    ciphertext = encrypt(akeyless, plaintext)

    with pytest.raises(ProviderError):
        decrypt(akeyless, ciphertext, provider_id="neon")
