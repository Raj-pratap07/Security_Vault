"""Exercise the authenticated FastAPI routes against configured live services.

Pass --max-size to store a secret of exactly FIREFLY_MAX_SECRET_BYTES, which
checks the largest supported payload against the real providers and Akeyless.
"""

from __future__ import annotations

import argparse
import secrets
import sys

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app


def _cleanup(orchestrator, record_id: str) -> bool:
    orchestrator.rollback(record_id)
    record = orchestrator.repository.get_record(record_id)
    rows = orchestrator.repository.get_share_records_for_record(record_id, record["version"])
    absent = all(
        orchestrator.provider_registry.get(row["provider_id"]).get_share(row["share_id"]) is None
        for row in rows
    )
    return absent and all(row["status"] == "DELETE_CONFIRMED" for row in rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-size", action="store_true", help="use a maximum-size synthetic secret")
    args = parser.parse_args()

    record_id = None
    outcome = 0
    try:
        settings = get_settings()
        if not settings.api_token:
            print("Live API: INCONCLUSIVE (API token is not configured)")
            return 2
        if args.max_size:
            synthetic = "x" * settings.max_secret_bytes
        else:
            synthetic = "firefly-live-api-" + secrets.token_urlsafe(24)
        headers = {"Authorization": f"Bearer {settings.api_token}"}
        with TestClient(app) as client:
            orchestrator = app.state.orchestrator
            try:
                create = client.post("/secrets", headers=headers, json={"secret": synthetic})
                if create.status_code != 201:
                    print(f"Live API store: FAIL (HTTP {create.status_code})")
                    return 1
                response_data = create.json()
                record_id = response_data["record_id"]
                if response_data["status"] != "AVAILABLE":
                    print("Live API store: FAIL (record is not AVAILABLE)")
                    outcome = 1
                else:
                    status_response = client.get(f"/secrets/{record_id}/status", headers=headers)
                    recovered = client.post(f"/secrets/{record_id}/recover", headers=headers)
                    checks_pass = (
                        status_response.status_code == 200
                        and status_response.json().get("status") == "AVAILABLE"
                        and recovered.status_code == 200
                        and recovered.json().get("secret") == synthetic
                        and recovered.headers.get("cache-control") == "no-store"
                    )
                    label = "max-size " if args.max_size else ""
                    print(f"Live authenticated API {label}store/status/recovery: {'PASS' if checks_pass else 'FAIL'}")
                    outcome = 0 if checks_pass else 1

                unauthenticated = client.post(f"/secrets/{record_id}/recover")
                unauth_pass = unauthenticated.status_code == 401
                print(f"Live API unauthenticated recovery denied: {'PASS' if unauth_pass else 'FAIL'}")
                if not unauth_pass:
                    outcome = 1
            finally:
                if record_id is not None:
                    try:
                        cleanup_ok = _cleanup(orchestrator, record_id)
                        print(f"Live API test cleanup: {'CONFIRMED' if cleanup_ok else 'UNCONFIRMED'}")
                        if not cleanup_ok:
                            outcome = 1
                    except Exception as exc:
                        print(f"Live API test cleanup: UNCONFIRMED ({type(exc).__name__})")
                        outcome = 1
    except Exception as exc:
        print(f"Live API: INCONCLUSIVE ({type(exc).__name__})")
        outcome = 2
    return outcome


if __name__ == "__main__":
    sys.exit(main())
