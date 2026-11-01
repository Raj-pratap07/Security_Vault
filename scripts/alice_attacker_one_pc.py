"""Alice (authorized) and the attacker, on ONE PC, in ONE terminal.

    python -m scripts.alice_attacker_one_pc            # rehearsal: simulated clouds, no credentials
    python -m scripts.alice_attacker_one_pc --live     # real Akeyless + Upstash + Neon + Firestore from .env

Flags: --auto (no Enter prompts), --secret TEXT, --stolen-token PARTIAL.

It starts the Firefly server inside this terminal, then runs the unchanged Alice and attacker code from
scripts/two_pc_demo.py against it. Alice holds the real token and gets the plaintext back. The attacker
knows the server address and Alice's record id but has no valid token, and gets nothing. Alice's demo
secret is deleted from the clouds at the end and verified gone.
"""

from __future__ import annotations

import warnings

warnings.simplefilter("ignore")  # third-party deprecation notices would drown out the story

import argparse
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

from scripts.pc2_server import REHEARSAL_TOKEN, prefer_ipv4  # also keeps the /demo back door OFF
from scripts import two_pc_demo  # the original, frozen Alice/attacker code


def free_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]
    finally:
        probe.close()


def start_server(live: bool):
    """Run the real FastAPI app in a background thread. Returns (server, app, url, token)."""
    import uvicorn

    port = free_port()
    if live:
        from app.core.config import get_settings
        from app.main import app

        prefer_ipv4()  # a broken IPv6 route to Google/Neon makes live calls stall on some networks
        token = get_settings().api_token
    else:
        from scripts.demo_server import create_app

        app, token = create_app(), REHEARSAL_TOKEN
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    import requests

    for _ in range(120):
        try:
            if requests.get(f"{url}/health", timeout=2).status_code == 200 and getattr(app.state, "orchestrator", None) is not None:
                return server, app, url, token
        except requests.RequestException:
            pass
        time.sleep(0.5)
    raise RuntimeError("the server did not start")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="use the real services configured in .env")
    parser.add_argument("--auto", action="store_true", help="no Enter prompts")
    parser.add_argument("--secret")
    parser.add_argument("--stolen-token", help="a partial or stale credential the attacker holds")
    args = parser.parse_args()
    if os.name == "nt":
        os.system("")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print()
    mode = "LIVE: real Akeyless + Upstash + Neon + Firestore" if args.live else "REHEARSAL: simulated clouds and Akeyless (not real encryption)"
    print(f"  {two_pc_demo.B}ALICE AND THE ATTACKER{two_pc_demo.X}   one PC, one terminal")
    print(f"  {two_pc_demo.D}{mode}{two_pc_demo.X}")
    print(f"  {two_pc_demo.D}starting the Firefly server...{two_pc_demo.X}")
    try:
        server, app, url, token = start_server(args.live)
    except Exception as exc:
        print(f"  Setup failed: {type(exc).__name__}. Check .env (see README), or run without --live.")
        return 2
    print(f"  {two_pc_demo.D}server up at {url}{two_pc_demo.X}\n")

    # Alice's record-id note goes in a throwaway folder. The working directory is NOT changed:
    # the app's database and credential paths are relative to the project folder.
    two_pc_demo.RECORD_FILE = Path(tempfile.mkdtemp(prefix="firefly-alice-attacker-")) / "alice_record_id.txt"
    outcome = 1
    record_id = None
    try:
        alice_args = argparse.Namespace(url=url, token=token, secret=args.secret, auto=args.auto)
        outcome = two_pc_demo.alice(alice_args)
        if two_pc_demo.RECORD_FILE.exists():
            record_id = two_pc_demo.RECORD_FILE.read_text().strip()
        print()
        attacker_args = argparse.Namespace(url=url, record_id=record_id or "unknown", stolen_token=args.stolen_token, auto=args.auto)
        outcome = max(outcome, two_pc_demo.attack(attacker_args))
    except KeyboardInterrupt:
        print("\n  interrupted")
        outcome = 130
    finally:
        if record_id:
            try:
                orchestrator = app.state.orchestrator
                orchestrator.rollback(record_id)
                row_ids = orchestrator.repository.get_share_records_for_record(record_id, orchestrator.repository.get_record(record_id)["version"])
                gone = all(orchestrator.provider_registry.get(r["provider_id"]).get_share(r["share_id"]) is None for r in row_ids)
                print(f"\n  {two_pc_demo.D}{'demo secret deleted from all 3 clouds (verified)' if gone else 'WARNING: could not confirm the demo secret was deleted'}{two_pc_demo.X}")
                outcome = outcome if gone else 1
            except Exception as exc:
                print(f"\n  WARNING: cleanup could not be confirmed ({type(exc).__name__}). Check record {record_id[:8]}...")
                outcome = 1
        server.should_exit = True
    print()
    return outcome


if __name__ == "__main__":
    sys.exit(main())
