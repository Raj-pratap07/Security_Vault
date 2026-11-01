"""Two-PC demo client. Copy ONLY this file to PC1; it needs just `pip install requests`.

PC1 holds no Firefly code or keys. It talks to the Firefly server on PC2 over HTTP.

    python two_pc_demo.py alice  --url http://PC2_IP:8000 --token <REAL_TOKEN>
    python two_pc_demo.py attack --url http://PC2_IP:8000 --record-id <ID_FROM_ALICE> [--stolen-token <partial/old token>]
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
import time
from pathlib import Path

import requests

G, R, Y, C, D, B, X = "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[2m", "\033[1m", "\033[0m"
RECORD_FILE = Path("alice_record_id.txt")


def say(text: str = "") -> None:
    print("  " + text)


def call(method: str, url: str, token: str | None = None, **kwargs):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return requests.request(method, url, headers=headers, timeout=60, **kwargs)


def pause(auto: bool, text: str) -> None:
    if not auto:
        try:
            input(f"\n  {C}>> press Enter to {text} {X}")
        except EOFError:
            pass


def alice(args: argparse.Namespace) -> int:
    say(f"{B}USER 1: ALICE (authorized){X}   holds the real credential")
    secret = args.secret or "FIREFLY-" + secrets.token_hex(8).upper()
    say(f"secret to protect: {B}{secret}{X}")
    pause(args.auto, "store it in Firefly")
    stored = call("post", f"{args.url}/secrets", args.token, json={"secret": secret})
    if stored.status_code != 201:
        say(f"{R}store failed: HTTP {stored.status_code}  (is the token right?){X}")
        return 1
    record_id = stored.json()["record_id"]
    RECORD_FILE.write_text(record_id)
    say(f"{G}✔ stored.{X} Alice's key to get it back is the record id: {B}{record_id}{X}")
    say(f"{D}(also saved to {RECORD_FILE}){X}")
    pause(args.auto, "fetch it back as Alice")
    got = call("post", f"{args.url}/secrets/{record_id}/recover", args.token)
    ok = got.status_code == 200 and got.json().get("secret") == secret
    say(f"{G}✔ ALICE GOT THE PLAINTEXT: {B}{got.json().get('secret')}{X}" if ok else f"{R}✖ failed: HTTP {got.status_code}{X}")
    return 0 if ok else 1


def attack(args: argparse.Namespace) -> int:
    rid = args.record_id
    say(f"{B}USER 2: MALLORY (attacker){X}   knows the server address and a leaked record id")
    say(f"target record id: {rid}")
    blocked, breached = 0, 0

    def attempt(label: str, response, secret_ok_codes=(200, 201)) -> None:
        nonlocal blocked, breached
        got_in = response.status_code in secret_ok_codes
        text = response.text
        leaked = '"secret"' in text
        if got_in or leaked:
            breached += 1
            say(f"{R}✖ BREACH  {label}  -> HTTP {response.status_code}  {text[:80]}{X}")
        else:
            blocked += 1
            say(f"{Y}🛡 BLOCKED{X} {label}  {D}-> HTTP {response.status_code}{X}")
        time.sleep(0.15)

    recover = f"{args.url}/secrets/{rid}/recover"
    pause(args.auto, "start the attack")
    say(f"\n{C}Round 1: no credentials{X}")
    attempt("recover with no token", call("post", recover))
    attempt("recover with an empty token", call("post", recover, ""))

    say(f"\n{C}Round 2: guessing the password{X}")
    for guess in ["admin", "password", "firefly", "demo-token", "123456", "secret", "letmein", "changeme"]:
        attempt(f"token guess '{guess}'", call("post", recover, guess))

    if args.stolen_token:
        say(f"\n{C}Round 3: a partial / stale credential the attacker obtained{X}")
        attempt("stolen token", call("post", recover, args.stolen_token))
        attempt("stolen token with one character changed", call("post", recover, args.stolen_token[:-1] + ("A" if args.stolen_token[-1] != "A" else "B")))
        attempt("stolen token truncated", call("post", recover, args.stolen_token[: max(1, len(args.stolen_token) // 2)]))

    say(f"\n{C}Round 4: hunting for other people's secrets{X}")
    for _ in range(3):
        attempt("random record id", call("post", f"{args.url}/secrets/{secrets.token_hex(16)}/recover", "attacker-guess"))
    attempt("read status of the leaked id", call("get", f"{args.url}/secrets/{rid}/status", "attacker-guess"))
    attempt("store junk (write access?)", call("post", f"{args.url}/secrets", "attacker-guess", json={"secret": "x"}))

    say(f"\n{C}Round 5: looking for a back door{X}")
    attempt("demo endpoint: list shares", call("get", f"{args.url}/demo/secrets/{rid}/shares", "attacker-guess"))
    attempt("demo endpoint: rebuild", call("post", f"{args.url}/demo/secrets/{rid}/rebuild", "attacker-guess"))
    attempt("demo endpoint: delete the data", call("delete", f"{args.url}/demo/secrets/{rid}", "attacker-guess"))

    say()
    say(f"{B}RESULT{X}")
    say(f"  {Y}🛡 {blocked} attacks blocked{X}")
    say(f"  {(G if breached == 0 else R)}{'✔' if breached == 0 else '✖'} {breached} plaintext leaks{X}")
    if breached == 0:
        say(f"\n  {G}Attacker walked away with nothing. The secret is only on PC2, behind a credential,{X}")
        say(f"  {G}and even stolen cloud data is split into 3 encrypted shares.{X}")
    return 0 if breached == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("role", choices=["alice", "attack"])
    parser.add_argument("--url", required=True, help="Firefly server on PC2, e.g. http://192.168.1.20:8000")
    parser.add_argument("--token", default=os.environ.get("FIREFLY_API_TOKEN"), help="alice only: the real credential")
    parser.add_argument("--record-id", help="attack: the leaked record id (default: read alice_record_id.txt)")
    parser.add_argument("--stolen-token", help="attack: a partial/stale credential the attacker holds")
    parser.add_argument("--secret", help="alice: secret to store (default random)")
    parser.add_argument("--auto", action="store_true", help="no Enter prompts")
    args = parser.parse_args()
    args.url = args.url.rstrip("/")
    if os.name == "nt":
        os.system("")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print()
    try:
        if args.role == "alice":
            if not args.token:
                print("  alice needs --token (the real credential)")
                return 2
            return alice(args)
        if not args.record_id:
            args.record_id = RECORD_FILE.read_text().strip() if RECORD_FILE.exists() else secrets.token_hex(16)
        return attack(args)
    except requests.ConnectionError:
        print(f"  Cannot reach {args.url}. Is the server running with --host 0.0.0.0 and the firewall open?")
        return 2
    finally:
        print()


if __name__ == "__main__":
    sys.exit(main())
