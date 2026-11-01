"""Opt-in live recovery check across each two-provider combination."""

from __future__ import annotations

import secrets
import sys
from itertools import combinations

from app.core.config import get_settings
from app.services.factory import build_orchestrator


def main() -> int:
    try:
        settings = get_settings()
        orchestrator = build_orchestrator(settings)
        providers = orchestrator.provider_registry
        repository = orchestrator.repository
    except Exception as exc:
        print(f"Provider-pair setup: INCONCLUSIVE ({type(exc).__name__})")
        return 2

    synthetic_secret = "firefly-pair-check-" + secrets.token_urlsafe(24)
    try:
        result = orchestrator.store_secret("demo-owner", synthetic_secret)
    except Exception as exc:
        print(f"Provider-pair store: INCONCLUSIVE ({type(exc).__name__})")
        return 2

    record_id = result["record_id"]
    failed = False
    inconclusive = False
    for pair in combinations(settings.provider_ids, 2):
        label = " + ".join(pair)
        try:
            recovered = orchestrator._recover_secret_from_provider_ids(
                record_id,
                "demo-owner",
                set(pair),
            )
        except Exception as exc:
            print(f"{label}: INCONCLUSIVE ({type(exc).__name__})")
            inconclusive = True
        else:
            matched = recovered == synthetic_secret
            print(f"{label}: {'PASS' if matched else 'FAIL'}")
            failed |= not matched

    cleanup_confirmed = False
    try:
        orchestrator.rollback(record_id)
        record = repository.get_record(record_id)
        shares = repository.get_share_records_for_record(record_id, record["version"])
        cleanup_confirmed = all(
            repository_row["status"] == "DELETE_CONFIRMED"
            and providers.get(repository_row["provider_id"]).get_share(
                repository_row["share_id"]
            )
            is None
            for repository_row in shares
        )
    except Exception:
        cleanup_confirmed = False
    print(f"Cleanup: {'CONFIRMED' if cleanup_confirmed else 'UNCONFIRMED'}")
    if failed or not cleanup_confirmed:
        return 1
    return 2 if inconclusive else 0


if __name__ == "__main__":
    sys.exit(main())
