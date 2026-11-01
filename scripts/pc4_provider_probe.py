"""Read and permission-check one provider using a PC4-only credential.

Only FIREFLY_PC4_* values are read from the local .env or process environment.
The probe does not use the Firefly API token or call Akeyless.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

import requests
from dotenv import dotenv_values

from app.storage.base import ShareEnvelope, build_share_id
from app.storage.firebase import FirebaseAdapter
from app.storage.neon import NeonAdapter
from app.storage.upstash import UpstashAdapter


SHARE_INDEX = {"upstash": 1, "neon": 2, "firebase": 3}
PC4_ENV = {
    key: value
    for key, value in dotenv_values(Path.cwd() / ".env").items()
    if (key.startswith("FIREFLY_PC4_") or key == "PC4_PROVIDER_IDENTITY_LABEL")
    and value is not None
}


def _setting(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, PC4_ENV.get(name, default))


def _adapter(provider_id: str):
    if provider_id == "upstash":
        return UpstashAdapter(
            rest_url=_setting("FIREFLY_PC4_UPSTASH_URL") or "",
            rest_token=_setting("FIREFLY_PC4_UPSTASH_READ_TOKEN") or "",
        )
    if provider_id == "neon":
        return NeonAdapter(
            database_url=_setting("FIREFLY_PC4_NEON_DATABASE_URL") or "",
            schema=_setting("FIREFLY_PC4_NEON_SCHEMA", "public") or "public",
            table=_setting("FIREFLY_PC4_NEON_TABLE", "firefly_shares") or "firefly_shares",
        )
    return FirebaseAdapter(
        project_id=_setting("FIREFLY_PC4_FIREBASE_PROJECT_ID") or "",
        collection=_setting("FIREFLY_PC4_FIRESTORE_COLLECTION", "firefly_shares") or "firefly_shares",
        credentials_path=_setting("FIREFLY_PC4_FIREBASE_CREDENTIALS_PATH") or "",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=tuple(SHARE_INDEX))
    parser.add_argument("record_id")
    parser.add_argument("--version", type=int, required=True)
    parser.add_argument("--share-id", required=True, help="Exact provider key/document ID")
    args = parser.parse_args()

    identity_label = _setting("PC4_PROVIDER_IDENTITY_LABEL")
    if not identity_label:
        print("Provider identity: INCONCLUSIVE (PC4_PROVIDER_IDENTITY_LABEL is missing)")
        return 2
    print(f"Provider identity: {identity_label}")

    expected_share_id = build_share_id(
        args.record_id,
        args.version,
        SHARE_INDEX[args.provider],
    )
    if args.share_id != expected_share_id:
        print("Envelope read: FAIL (share ID does not match record/version/provider mapping)")
        return 1

    try:
        adapter = _adapter(args.provider)
        envelope = adapter.get_share(args.share_id)
    except Exception as exc:
        print(f"Restricted envelope read: INCONCLUSIVE ({type(exc).__name__})")
        return 2

    if envelope is None:
        print("Restricted envelope read: FAIL (record not found)")
        return 1
    envelope_matches = (
        envelope.record_id == args.record_id
        and envelope.version == args.version
        and envelope.share_index == SHARE_INDEX[args.provider]
        and envelope.provider_id == args.provider
        and bool(envelope.ciphertext)
    )
    print(f"Restricted envelope read: {'PASS' if envelope_matches else 'FAIL'}")
    if not envelope_matches:
        return 1

    probe_id = f"firefly-pc4-permission-probe-{secrets.token_hex(12)}"
    probe_envelope = ShareEnvelope(
        record_id="pc4-permission-probe",
        version=1,
        share_index=SHARE_INDEX[args.provider],
        provider_id=args.provider,
        key_ref="non-secret-permission-probe",
        format_version=1,
        ciphertext="non-secret-permission-probe",
    )
    try:
        write_result = adapter.put_share(probe_id, probe_envelope)
    except Exception as exc:
        print(f"Restricted write denial: INCONCLUSIVE ({type(exc).__name__})")
        return 2

    if write_result.success:
        try:
            cleanup = adapter.delete_share(probe_id)
            cleanup_ok = cleanup.deleted or cleanup.status == "CONFIRMED_ABSENT"
        except Exception:
            cleanup_ok = False
        print("Restricted write denial: FAIL (write was permitted)")
        print(f"Permission probe cleanup: {'CONFIRMED' if cleanup_ok else 'UNCONFIRMED'}")
        return 1
    if write_result.status == "UNKNOWN":
        print("Restricted write denial: INCONCLUSIVE (outcome unknown)")
        return 2
    print("Restricted write denial: PASS")
    print("Akeyless decryption: NOT TESTED (this probe has no Akeyless integration)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
