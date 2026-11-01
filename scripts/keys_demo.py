"""Alice has the encryption keys. The attacker steals the data but NOT the keys.

    python -m scripts.keys_demo            # rehearsal: simulated clouds + SIMULATED key service (real AES-GCM)
    python -m scripts.keys_demo --live     # real Akeyless + Upstash + Neon + Firestore from .env

Flags: --auto (no Enter prompts), --secret TEXT, --plain (ASCII only), --no-color.

One PC, one terminal, no server. It uses the project's real store, share-encryption and rebuild code.

  Alice     holds the real Akeyless credentials, so she can decrypt the shares and rebuild the secret.
  Attacker  breaks into all three clouds and steals every encrypted share, but has no Akeyless
            credentials. Without the keys the stolen data is useless.

Only synthetic data is used. Metadata lives in a throwaway database, and the demo secret is deleted
from the clouds at the end and verified gone. In --live mode the attacker makes two harmless failed
login attempts against Akeyless using made-up credentials (no real credential is ever sent).
"""

from __future__ import annotations

import warnings

warnings.simplefilter("ignore")  # third-party deprecation notices would drown out the story

import argparse
import base64
import hashlib
import itertools
import json
import os
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import Settings
from app.core.errors import ConfigurationError, ProviderError
from app.db.repository import SecretRepository
from app.services.akeyless_service import AkeylessService
from app.services.orchestrator import StorageOrchestrator
from app.services.sss_service import ShareBundle, SSSService
from app.storage.base import build_share_id
from app.storage.registry import ProviderRegistry
from scripts.demo_one_pc import CLOUDS, OWNER, Ui, build as build_live

PROVIDERS = tuple(pid for pid, _ in CLOUDS)


# ---------------------------------------------------------------- simulated key service (rehearsal only)


class _Reply:
    def __init__(self, status_code: int, body: dict):
        self.status_code, self._body = status_code, body

    def json(self) -> dict:
        return self._body


class SimulatedKeyService:
    """Stands in for Akeyless offline. Real AES-256-GCM (the `cryptography` library) with the
    encryption context as authenticated data; only the key management is simulated: logins must
    present the right access id/key, and each key name has its own random key."""

    def __init__(self, access_id: str, access_key: str):
        self._login = (access_id, access_key)
        self._keys: dict[str, bytes] = {}
        self._token = secrets.token_hex(16)

    def close(self) -> None:
        pass

    def _key(self, name: str) -> bytes:
        return self._keys.setdefault(name, AESGCM.generate_key(bit_length=256))

    @staticmethod
    def _aad(context: dict) -> bytes:
        return json.dumps(context, sort_keys=True).encode()

    def post(self, url: str, *, json: dict, timeout=None) -> _Reply:  # noqa: A002 - mirrors requests
        operation = url.rsplit("/", 1)[-1]
        if operation == "auth":
            ok = (json.get("access-id"), json.get("access-key")) == self._login
            return _Reply(200, {"token": self._token, "expiration": "2099-01-01T00:00:00Z"}) if ok else _Reply(401, {})
        if json.get("token") != self._token:
            return _Reply(401, {})
        aesgcm = AESGCM(self._key(json["key-name"]))
        aad = self._aad(json["encryption-context"])
        if operation == "encrypt":
            nonce = os.urandom(12)
            blob = nonce + aesgcm.encrypt(nonce, base64.b64decode(json["plaintext"]), aad)
            return _Reply(200, {"result": base64.b64encode(blob).decode()})
        if operation == "decrypt":
            try:
                blob = base64.b64decode(json["ciphertext"], validate=True)
                plain = aesgcm.decrypt(blob[:12], blob[12:], aad)
            except (InvalidTag, ValueError):
                return _Reply(400, {})
            return _Reply(200, {"result": base64.b64encode(plain).decode()})
        return _Reply(404, {})


def build_rehearsal(workdir: Path) -> tuple[StorageOrchestrator, dict]:
    from tests.fakes import FakeStorageAdapter

    login = {"access_id": "p-" + secrets.token_hex(6), "access_key": secrets.token_urlsafe(24)}
    akeyless = AkeylessService(
        key_refs={pid: f"/rehearsal/{pid}-key" for pid in PROVIDERS},
        settings=Settings(),
        api_url="https://akeyless.simulated",
        session=SimulatedKeyService(**login),
        **login,
    )
    db_path, db_key = str(workdir / "demo-metadata.db"), secrets.token_hex(32)
    SecretRepository.initialize(db_path, db_key)
    orchestrator = StorageOrchestrator(
        repository=SecretRepository(db_path, db_key),
        sss_service=SSSService(),
        akeyless_service=akeyless,
        provider_registry=ProviderRegistry(FakeStorageAdapter(pid) for pid in PROVIDERS),
    )
    return orchestrator, login


# ---------------------------------------------------------------- the demo


def short(ciphertext: str) -> str:
    return ciphertext[:22] + "..."


def attacker_service(orchestrator: StorageOrchestrator, live: bool) -> AkeylessService:
    """An Akeyless client that has the right API address and key NAMES (these are not secret) but
    made-up credentials, like someone who broke into the clouds and has no key access."""
    real = orchestrator.akeyless_service
    fake_login = {"access_id": "p-" + secrets.token_hex(6), "access_key": secrets.token_urlsafe(24)}
    if live:
        return AkeylessService(key_refs=real.key_refs, settings=real.settings, api_url=real.api_url, **fake_login)
    return AkeylessService(
        key_refs=real.key_refs,
        settings=real.settings,
        api_url=real.api_url,
        session=SimulatedKeyService(access_id="p-someone-else", access_key="not-the-attackers"),
        **fake_login,
    )


def run_demo(ui: Ui, orchestrator: StorageOrchestrator, secret: str, live: bool) -> bool:
    repo, registry, sss = orchestrator.repository, orchestrator.provider_registry, orchestrator.sss_service
    results: list[bool] = []

    def blocked(label: str, was_blocked: bool, note: str = "") -> None:
        results.append(was_blocked)
        mark = ui.paint("yellow", "[BLOCKED]" if ui.plain else "🛡 BLOCKED") if was_blocked else ui.paint("red", f"{ui.fail} BREACH")
        ui.say(f"{mark} {label}" + (ui.paint("dim", f"   {note}") if note else ""))

    # ---- step 1: Alice stores
    ui.say(ui.paint("cyan", "STEP 1") + "  Alice protects a secret")
    ui.say(f"secret: {ui.paint('bold', secret)}")
    record_id = ui.working("splitting, encrypting each share with its Akeyless key, storing in 3 clouds...", lambda: orchestrator.store_secret(OWNER, secret)["record_id"])
    record = repo.get_record(record_id)
    rows = repo.get_share_records_for_record(record_id, record["version"])
    ui.say(f"{ui.paint('green', ui.ok)} STORED. Each cloud holds one share, encrypted with a key only Akeyless holds:")
    stolen: list[tuple[int, str, str]] = []
    for row, (pid, name) in zip(rows, CLOUDS):
        envelope = registry.get(pid).get_share(row["share_id"])
        stolen.append((row["share_index"], pid, envelope.ciphertext))
        ui.say(f"  share {row['share_index']}  {name:<9} {ui.lock} {ui.paint('dim', short(envelope.ciphertext))}")

    # ---- step 2: the attacker
    ui.wait("let the attacker break into the clouds")
    ui.say(ui.paint("cyan", "STEP 2") + "  The attacker breaks into ALL THREE clouds")
    ui.say(ui.paint("dim", "He copies every stored share. He has NO Akeyless credentials."))
    ui.boxes([(name, f"{ui.ok} stolen", "red") for _p, name in CLOUDS])
    ui.say()
    ciphertexts = [c for _i, _p, c in stolen]
    secret_b64 = base64.b64encode(secret.encode()).decode()
    blocked("read the stolen data", not any(secret in c or secret_b64 in c for c in ciphertexts), "scrambled ciphertext, the secret is not in it")

    def parses_as_share(ciphertext: str) -> bool:
        try:
            ShareBundle.from_bytes(base64.b64decode(ciphertext, validate=True))
            return True
        except Exception:
            return False

    blocked("treat the stolen data as raw shares and rebuild", not any(parses_as_share(c) for c in ciphertexts), "it is not a share until it is decrypted")

    attacker = attacker_service(orchestrator, live)
    index, pid, ciphertext = stolen[0]
    key_ref = rows[0]["key_ref"]
    note = "Akeyless refused the attacker's login" if live else "the key service refused the attacker's login"
    ui.working("attacker asks Akeyless to decrypt with his own credentials...", lambda: ui.beat(1.0))
    try:
        attacker.decrypt_share(ciphertext, record_id=record_id, version=record["version"], share_index=index, provider_id=pid, key_ref=key_ref)
        got_in = True
    except ProviderError as exc:
        got_in = False
        note = f"{note}: {exc}"
    blocked("decrypt with the attacker's own credentials", not got_in, note)

    reply = attacker.session.post(
        f"{attacker.api_url}/decrypt",
        json={"key-name": key_ref, "ciphertext": ciphertext, "output-format": "base64", "encryption-context": attacker.build_encryption_context(record_id=record_id, version=record["version"], share_index=index, provider_id=pid)},
        timeout=attacker.timeout if live else None,
    )
    blocked("ask Akeyless to decrypt with no login at all", reply.status_code != 200, f"HTTP {reply.status_code}")
    attacker.close()
    ui.say()
    ui.say(ui.paint("yellow", "Result: the attacker holds 3 encrypted shares and cannot read any of them."))

    # ---- step 3: Alice reads her secret back
    ui.wait("let Alice fetch her secret with the real keys")
    ui.say(ui.paint("cyan", "STEP 3") + "  Alice fetches the secret. She has the Akeyless credentials.")
    held: list[ShareBundle] = []
    for row, (_pid, name) in zip(rows[:2], CLOUDS[:2]):
        started = time.monotonic()
        bundle = ui.working(f"Alice reads share {row['share_index']} from {name} and decrypts it with Akeyless...", lambda row=row: orchestrator.load_valid_share(record, row))
        if bundle is None:
            ui.say(ui.paint("red", f"{ui.fail} Alice could not decrypt share {row['share_index']}"))
            return False
        held.append(bundle)
        took = f"   ({(time.monotonic() - started) * 1000:.0f} ms, real network call)" if live else ""
        ui.say(f"{ui.paint('green', ui.ok)} share {row['share_index']} decrypted{ui.paint('dim', took)}")
    rebuilt = sss.recover_secret(held).decode("utf-8")
    good = rebuilt == secret
    results.append(good)
    ui.say(f"{ui.paint('green' if good else 'red', ui.ok if good else ui.fail)} SECRET REBUILT: {ui.paint('bold', rebuilt)}")
    ui.say(ui.paint("dim", "identical to what Alice stored" if good else "DOES NOT MATCH"))

    ui.say()
    ui.say(ui.paint("bold", "RESULT"))
    ui.say(f"  Alice     {ui.paint('green', ui.ok + '  has the keys  ->  gets the secret')}")
    ui.say(f"  Attacker  {ui.paint('yellow', ui.fail + '  stole the data, no keys  ->  gets nothing')}")

    orchestrator.rollback(record_id)
    gone = all(registry.get(p).get_share(build_share_id(record_id, record["version"], i)) is None for i, (p, _n) in enumerate(CLOUDS, start=1))
    ui.say()
    ui.say(ui.paint("dim", "demo shares deleted from all 3 clouds (verified)" if gone else "WARNING: some demo shares could not be confirmed deleted"))
    return all(results) and gone


def choose_secret(ui: Ui, args: argparse.Namespace) -> str:
    if args.secret:
        return args.secret
    random_key = "FIREFLY-" + secrets.token_hex(8).upper()
    if not ui.animate:
        return random_key
    try:
        typed = input(ui.paint("bold", "  Enter a secret for Alice to protect ") + ui.paint("dim", "(press Enter for a random key): ")).strip()
    except EOFError:
        typed = ""
    return typed[:1000] or random_key


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="use the real services configured in .env")
    parser.add_argument("--auto", action="store_true", help="no prompts or waiting")
    parser.add_argument("--secret")
    parser.add_argument("--plain", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    tty = sys.stdout.isatty()
    if tty and not args.no_color and os.name == "nt":
        os.system("")
    ui = Ui(color=tty and not args.no_color, plain=args.plain, animate=tty and not args.auto)

    print()
    ui.say(ui.paint("yellow", "✦ ") + ui.paint("bold", "F I R E F L Y") + ui.paint("dim", "   Alice has the keys. The attacker has the data, not the keys."))
    ui.say(ui.paint("dim", "LIVE: real Akeyless + Upstash + Neon + Firestore" if args.live else "REHEARSAL: simulated clouds + simulated key service (real AES-GCM encryption)"))
    ui.say()
    workdir = Path(tempfile.mkdtemp(prefix="firefly-keys-demo-"))
    success = False
    try:
        try:
            orchestrator = build_live(True, workdir) if args.live else build_rehearsal(workdir)[0]
        except (ConfigurationError, ValueError) as exc:
            print(f"  Setup failed: {exc}\n  Check .env (see README) or run without --live.")
            return 2
        try:
            success = run_demo(ui, orchestrator, choose_secret(ui, args), args.live)
        finally:
            orchestrator.close()
    except KeyboardInterrupt:
        print("\n  interrupted")
        return 130
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    print()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
