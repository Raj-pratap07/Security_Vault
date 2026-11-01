from __future__ import annotations

import secrets
import sys

from app.core.config import get_settings
from app.core.errors import ConfigurationError, ProviderError
from app.services.akeyless_service import AkeylessService
from app.storage.registry import create_provider_registry


def main() -> int:
    settings = get_settings()
    try:
        registry = create_provider_registry(settings)
    except ConfigurationError as exc:
        print(f"Configuration: failed ({type(exc).__name__})")
        return 1

    failed = False
    for adapter in registry.all():
        try:
            result = adapter.health_check()
        except (ConfigurationError, ProviderError) as exc:
            print(f"{adapter.provider_id}: failed ({type(exc).__name__})")
            failed = True
            continue
        status = "ok" if result.ok else "failed"
        print(f"{adapter.provider_id}: {status}")
        failed |= not result.ok

    akeyless = AkeylessService()
    record_id = f"preflight-{secrets.token_hex(8)}"
    probe = b"firefly-synthetic-preflight"
    for share_index, provider_id in enumerate(settings.provider_ids, start=1):
        try:
            key_ref = settings.akeyless_key_refs[provider_id]
            ciphertext = akeyless.encrypt_share(
                probe,
                record_id=record_id,
                version=1,
                share_index=share_index,
                provider_id=provider_id,
                key_ref=key_ref,
            )
            plaintext = akeyless.decrypt_share(
                ciphertext,
                record_id=record_id,
                version=1,
                share_index=share_index,
                provider_id=provider_id,
                key_ref=key_ref,
            )
            if plaintext != probe:
                raise ProviderError("Akeyless preflight round trip did not match")
        except (ConfigurationError, ProviderError) as exc:
            print(f"akeyless/{provider_id}: failed ({type(exc).__name__})")
            failed = True
        else:
            print(f"akeyless/{provider_id}: ok")

    print("Preflight: failed" if failed else "Preflight: ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())