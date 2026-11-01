"""Create and retain one synthetic AVAILABLE record for the PC4 probe."""

from __future__ import annotations

import argparse
import secrets
import sys

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.storage.base import build_share_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    settings = get_settings()
    if not settings.api_token:
        print("PC4 test record: FAILED (API token is not configured)")
        return 1

    synthetic = "firefly-pc4-synthetic-" + secrets.token_urlsafe(24)
    headers = {"Authorization": f"Bearer {settings.api_token}"}
    with TestClient(app) as client:
        created = client.post("/secrets", headers=headers, json={"secret": synthetic})
        if created.status_code != 201:
            print(f"PC4 test record: FAILED (store returned HTTP {created.status_code})")
            return 1

        record_id = created.json()["record_id"]
        status = client.get(f"/secrets/{record_id}/status", headers=headers)
        if status.status_code != 200 or status.json().get("status") != "AVAILABLE":
            print(f"PC4 test record: NOT AVAILABLE; record_id={record_id}")
            return 1

        version = status.json()["version"]
        print("PC4 test record created and retained: AVAILABLE")
        print(f"record_id={record_id}")
        print(f"version={version}")
        for share_index, provider_id in enumerate(settings.provider_ids, start=1):
            share_id = build_share_id(record_id, version, share_index)
            print(f"provider_id={provider_id} share_index={share_index} share_id={share_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
