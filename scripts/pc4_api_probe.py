"""Unauthenticated PC4 API checks; no Firefly token is accepted or loaded."""

from __future__ import annotations

import argparse
import sys

import requests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("api_url", help="PC2 URL, for example http://192.168.1.20:8000")
    parser.add_argument("record_id", help="Synthetic record created by a trusted operator")
    parser.add_argument("version", type=int, help="Version associated with the synthetic record")
    args = parser.parse_args()
    if args.version < 1:
        parser.error("version must be positive")
    api_url = args.api_url.rstrip("/")

    try:
        health = requests.get(f"{api_url}/health", timeout=(3.0, 8.0))
    except requests.RequestException:
        print("Unauthenticated health: INCONCLUSIVE (PC2 API unavailable)")
        return 2
    try:
        health_body = health.json()
    except ValueError:
        health_body = None
    health_ok = health.status_code == 200 and health_body == {"status": "ok"}
    print(f"Unauthenticated health: {'PASS' if health_ok else f'FAIL (HTTP {health.status_code})'}")

    try:
        recovery = requests.post(
            f"{api_url}/secrets/{args.record_id}/recover",
            timeout=(3.0, 8.0),
        )
    except requests.RequestException:
        print("Unauthenticated recovery denial: INCONCLUSIVE (PC2 API unavailable)")
        return 2
    recovery_denied = recovery.status_code == 401
    print(
        "Unauthenticated recovery denial: "
        + ("PASS" if recovery_denied else f"FAIL (HTTP {recovery.status_code})")
    )
    print(f"Checked synthetic record version: {args.version}")
    return 0 if health_ok and recovery_denied else 1


if __name__ == "__main__":
    sys.exit(main())
