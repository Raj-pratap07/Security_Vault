"""Share-holder demo for PC1. Needs only:  pip install requests shamirs

    python share_holders.py deal    --url http://PC2_IP:8011 --token <TOKEN>   # dealer: store a secret, hand out cards
    python share_holders.py open    bob                                         # Bob holds 1 share
    python share_holders.py open    carol                                       # Carol holds 2 shares
    python share_holders.py cleanup --url http://PC2_IP:8011 --token <TOKEN>   # delete the demo data

Bob and Carol need no token and no server: they only hold share cards (files) and try
to rebuild the secret on their own machine.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import sys
import time
from pathlib import Path

import requests
import shamirs

G, R, Y, C, D, B, X = "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[2m", "\033[1m", "\033[0m"
WALLETS = {"bob": [1], "carol": [2, 3]}  # which share cards each person is handed
STATE = Path("share_demo_state.json")


def say(text: str = "") -> None:
    print("  " + text)


def ask(prompt: str) -> None:
    """Wait for Enter; keep going if there is no keyboard (for example piped input)."""
    try:
        input(prompt)
    except EOFError:
        pass


def wallet(name: str) -> Path:
    return Path(f"{name}_wallet")


def deal(args: argparse.Namespace) -> int:
    headers = {"Authorization": f"Bearer {args.token}"}
    secret = args.secret or "FIREFLY-" + secrets.token_hex(8).upper()
    say(f"{B}DEALER (authorized){X}   stores a secret and hands out share cards")
    say(f"secret: {B}{secret}{X}")
    if not args.auto:
        ask(f"\n  {C}>> press Enter to store it {X}")
    stored = requests.post(f"{args.url}/secrets", headers=headers, json={"secret": secret}, timeout=90)
    if stored.status_code != 201:
        say(f"{R}store failed: HTTP {stored.status_code} (wrong token?){X}")
        return 1
    record_id = stored.json()["record_id"]
    STATE.write_text(json.dumps({"record_id": record_id, "secret": secret}))
    say(f"{G}✔ stored.{X} 3 encrypted shares are now in 3 clouds. Rule: any 2 rebuild it.")
    for who, indexes in WALLETS.items():
        wallet(who).mkdir(exist_ok=True)
        for old in wallet(who).glob("share*.json"):
            old.unlink()
        for index in indexes:
            response = requests.get(f"{args.url}/demo/secrets/{record_id}/export/{index}", headers=headers, timeout=90)
            if response.status_code == 404:
                say(f"{R}✖ /demo is switched off on PC2. Restart it with:  python -m scripts.pc2_server --share-demo --show-token{X}")
                return 1
            if response.status_code != 200:
                say(f"{R}✖ could not get share {index} (HTTP {response.status_code}){X}")
                return 1
            (wallet(who) / f"share{index}.json").write_text(json.dumps(response.json()))
        say(f"  {B}{who.capitalize():6}{X} was handed {len(indexes)} share card(s): {', '.join(f'share{i}' for i in indexes)}   {D}-> {wallet(who)}\\{X}")
    return 0


def rebuild(cards: list[dict]) -> bytes:
    """Rebuild locally from share cards using the shamirs library (Shamir interpolation)."""
    threshold = cards[0]["threshold"]
    bundles = [json.loads(base64.b64decode(card["share_b64"])) for card in cards]
    chunks = len(bundles[0]["fragments"])
    out = bytearray()
    for position in range(chunks):
        shares = [shamirs.share.from_bytes(base64.b64decode(bundle["fragments"][position]["share"])) for bundle in bundles]
        length = bundles[0]["fragments"][position]["length"]
        out.extend(shamirs.interpolate(shares[:threshold], threshold=threshold).to_bytes(length, "big"))
    return bytes(out)


def open_wallet(args: argparse.Namespace) -> int:
    name = args.who.lower()
    cards = [json.loads(path.read_text()) for path in sorted(wallet(name).glob("share*.json"))]
    if not cards:
        say(f"{R}{name}'s wallet is empty. Run the dealer first (deal).{X}")
        return 1
    threshold = cards[0]["threshold"]
    say(f"{B}{name.upper()}{X} opens their wallet: holds {B}{len(cards)} share card(s){X}  (shares {', '.join(str(c['share_index']) for c in cards)})")
    for card in cards:
        first = json.loads(base64.b64decode(card["share_b64"]))["fragments"][0]["share"]
        value = shamirs.share.from_bytes(base64.b64decode(first)).value
        say(f"  share {card['share_index']} looks like: {D}{value:032x}...  (random numbers){X}")
    say(f"rule: {B}{threshold}{X} shares are needed to rebuild the secret")
    if not args.auto:
        ask(f"\n  {C}>> press Enter to try to rebuild the secret {X}")
    for _ in range(12):
        sys.stdout.write(f"\r  {C}trying to rebuild...{X}")
        sys.stdout.flush()
        time.sleep(0.1)
    sys.stdout.write("\r" + " " * 40 + "\r")
    if len(cards) < threshold:
        say(f"{Y}✖ CANNOT REBUILD.{X} {name.capitalize()} has {len(cards)} share(s) but {threshold} are needed.")
        say(f"{D}One share is just random numbers: it says nothing about the secret.{X}")
        return 0
    secret = rebuild(cards).decode("utf-8")
    say(f"{G}✔ SECRET REBUILT: {B}{secret}{X}")
    expected = json.loads(STATE.read_text())["secret"] if STATE.exists() else None
    if expected is not None:
        say(f"{D}{'identical to what the dealer stored' if secret == expected else 'DOES NOT MATCH the stored secret'}{X}")
    return 0


def cleanup(args: argparse.Namespace) -> int:
    record_id = json.loads(STATE.read_text())["record_id"]
    response = requests.delete(f"{args.url}/demo/secrets/{record_id}", headers={"Authorization": f"Bearer {args.token}"}, timeout=90)
    left = response.json().get("remote_shares_left", -1) if response.status_code == 200 else -1
    say(f"{G}✔ demo data deleted from all 3 clouds (verified){X}" if left == 0 else f"{R}could not confirm deletion (HTTP {response.status_code}){X}")
    return 0 if left == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("deal", "cleanup"):
        p = sub.add_parser(name)
        p.add_argument("--url", required=True)
        p.add_argument("--token", default=os.environ.get("FIREFLY_API_TOKEN"), required=False)
        p.add_argument("--secret")
        p.add_argument("--auto", action="store_true")
    p = sub.add_parser("open")
    p.add_argument("who", choices=["bob", "carol"])
    p.add_argument("--auto", action="store_true")
    args = parser.parse_args()
    if os.name == "nt":
        os.system("")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.command in ("deal", "cleanup"):
        args.url = args.url.rstrip("/")
        if not args.token:
            print("  needs --token")
            return 2
    print()
    try:
        return {"deal": deal, "open": open_wallet, "cleanup": cleanup}[args.command](args)
    except requests.ConnectionError:
        print(f"  Cannot reach PC2 at {getattr(args, 'url', '?')}. Is the server running?")
        return 2
    finally:
        print()


if __name__ == "__main__":
    sys.exit(main())
