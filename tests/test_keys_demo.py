from __future__ import annotations

import base64
import tempfile
from pathlib import Path

import pytest

from app.core.errors import ProviderError
from scripts import keys_demo
from scripts.demo_one_pc import Ui


def test_keys_demo_rehearsal_alice_gets_the_secret_and_the_attacker_gets_nothing(capsys):
    workdir = Path(tempfile.mkdtemp())
    orchestrator, _login = keys_demo.build_rehearsal(workdir)
    ui = Ui(color=False, plain=True, animate=False)

    assert keys_demo.run_demo(ui, orchestrator, "synthetic-keys-demo-secret", live=False) is True

    output = capsys.readouterr().out
    assert output.count("BLOCKED") == 4 and "BREACH" not in output
    assert "SECRET REBUILT: synthetic-keys-demo-secret" in output
    assert "deleted from all 3 clouds (verified)" in output


def test_simulated_key_service_is_real_aes_gcm_and_enforces_logins_and_context():
    workdir = Path(tempfile.mkdtemp())
    orchestrator, login = keys_demo.build_rehearsal(workdir)
    akeyless = orchestrator.akeyless_service
    common = dict(record_id="r1", version=1, share_index=2, provider_id="neon", key_ref=akeyless.key_refs["neon"])

    ciphertext = akeyless.encrypt_share(b"share-bytes", **common)

    assert base64.b64decode(ciphertext) != b"share-bytes" and b"share-bytes" not in base64.b64decode(ciphertext)
    assert akeyless.decrypt_share(ciphertext, **common) == b"share-bytes"
    with pytest.raises(ProviderError):  # same key, different encryption context
        akeyless.decrypt_share(ciphertext, **{**common, "share_index": 3})
    attacker = keys_demo.attacker_service(orchestrator, live=False)
    with pytest.raises(ProviderError):  # wrong credentials
        attacker.decrypt_share(ciphertext, **common)
