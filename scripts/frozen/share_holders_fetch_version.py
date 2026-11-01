"""Share-holder demo for PC1. One file. Needs:  pip install requests shamirs

    python share_holders.py            # runs the whole demo (asks for PC2's address and token)

Or step by step:
    python share_holders.py deal    --url http://PC2_IP:8011 --token TOKEN
    python share_holders.py fetch bob    --url http://PC2_IP:8011     # Bob asks PC2 for the secret (1 share: refused)
    python share_holders.py fetch carol  --url http://PC2_IP:8011     # Carol asks PC2 for the secret (2 shares: works)
    python share_holders.py fetch mallory --url http://PC2_IP:8011    # optional: 1 real share + 1 forged share
    python share_holders.py open bob                                  # rebuild on this PC only, no server
    python share_holders.py cleanup --url http://PC2_IP:8011 --token TOKEN

Bob and Carol need no token and no server: they only hold share cards (files) and try
to rebuild the secret on their own machine.
"""

import sys

# If the file was copied incompletely, say so instead of silently doing nothing.
try:
    _text = open(__file__, encoding="utf-8").read()
except OSError:
    _text = ""
if _text and not _text.rstrip().endswith("# END OF FILE"):
    print("\n  share_holders.py is INCOMPLETE (the end of the file is missing).")
    print("  Copy the whole file again, including the very last line:  # END OF FILE\n")
    sys.exit(1)

import argparse
import base64
import getpass
import json
import os
import secrets
import time
from pathlib import Path

import requests
import shamirs

G, R, Y, C, D, B, X = "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[2m", "\033[1m", "\033[0m"
WALLETS = {"bob": [1], "carol": [2, 3]}  # which share cards each person is handed
STATE = Path("share_demo_state.json")
DEFAULT_PORT = 8011


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


def normalize_url(value: str) -> str:
    """Accept '10.0.0.5', '10.0.0.5:8011' or a full URL."""
    value = value.strip().rstrip("/")
    if not value.startswith("http"):
        value = "http://" + value
    if value.count(":") == 1:  # scheme only, no port
        value += f":{DEFAULT_PORT}"
    return value


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
        say(f"  {B}{who.capitalize():6}{X} was handed {len(indexes)} share card(s): {', '.join(f'share{i}' for i in indexes)}   {D}-> {wallet(who)}{X}")
    return 0


def rebuild(cards: list) -> bytes:
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
        say(f"{R}{name}'s wallet is empty. Run the dealer first (deal), in this same folder.{X}")
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


def forged_wallet() -> list:
    """Mallory: Bob's one real share card, plus a made-up card for share 2."""
    real = [json.loads(path.read_text()) for path in sorted(wallet("bob").glob("share*.json"))]
    if not real:
        return []
    fake = dict(real[0], share_index=2, share_b64=base64.b64encode(os.urandom(len(base64.b64decode(real[0]["share_b64"])))).decode())
    return real + [fake]


def fetch(args: argparse.Namespace) -> int:
    """The holder shows their share cards to PC2 and asks for the secret. PC2 decides."""
    name = args.who.lower()
    cards = forged_wallet() if name == "mallory" else [json.loads(path.read_text()) for path in sorted(wallet(name).glob("share*.json"))]
    if not cards:
        say(f"{R}{name}'s wallet is empty. Run the dealer first (deal), in this same folder.{X}")
        return 1
    record_id = cards[0]["record_id"]
    what = "1 real share + 1 FORGED share" if name == "mallory" else f"{len(cards)} share card(s)"
    say(f"{B}{name.upper()}{X} goes to PC2 holding {B}{what}{X} and asks: {B}\"give me the secret\"{X}")
    if not args.auto:
        ask(f"\n  {C}>> press Enter to send the share(s) to PC2 {X}")
    body = {"cards": [{"share_index": c["share_index"], "share_b64": c["share_b64"]} for c in cards]}
    for _ in range(12):
        sys.stdout.write(f"\r  {C}PC2 is checking the shares...{X}")
        sys.stdout.flush()
        time.sleep(0.1)
    response = requests.post(f"{args.url}/demo/secrets/{record_id}/fetch", json=body, timeout=90)
    sys.stdout.write("\r" + " " * 40 + "\r")
    if response.status_code == 404:
        say(f"{R}✖ PC2 says not found. Was PC2 started with --share-demo, and was deal run?{X}")
        return 1
    if response.status_code not in (200, 409):
        say(f"{R}✖ unexpected answer from PC2 (HTTP {response.status_code}){X}")
        return 1
    result = response.json()
    say(f"{D}PC2 checked {result['shares_presented']} share(s): {result['shares_valid']} genuine, needs {result['shares_needed']}{X}")
    if result["invalid_shares"]:
        say(f"{Y}share {', '.join(map(str, result['invalid_shares']))} was NOT genuine and was ignored{X}")
    if not result["rebuilt"]:
        say(f"{Y}✖ PC2 REFUSED.{X} {name.capitalize()} cannot fetch the secret: {result['shares_valid']} genuine share(s), {result['shares_needed']} required.")
        return 0
    secret = result["secret"]
    say(f"{G}✔ PC2 RETURNED THE SECRET: {B}{secret}{X}")
    expected = json.loads(STATE.read_text())["secret"] if STATE.exists() else None
    if expected is not None:
        say(f"{D}{'identical to what the dealer stored' if secret == expected else 'DOES NOT MATCH the stored secret'}{X}")
    return 0


def cleanup(args: argparse.Namespace) -> int:
    if not STATE.exists():
        say(f"{R}nothing to clean up (no {STATE} here). Run deal first, in this same folder.{X}")
        return 1
    record_id = json.loads(STATE.read_text())["record_id"]
    response = requests.delete(f"{args.url}/demo/secrets/{record_id}", headers={"Authorization": f"Bearer {args.token}"}, timeout=90)
    left = response.json().get("remote_shares_left", -1) if response.status_code == 200 else -1
    say(f"{G}✔ demo data deleted from all 3 clouds (verified){X}" if left == 0 else f"{R}could not confirm deletion (HTTP {response.status_code}){X}")
    return 0 if left == 0 else 1


def run_all(args: argparse.Namespace) -> int:
    """The whole demo in order: deal, Bob fetches, Carol fetches, cleanup."""
    say(f"{B}Firefly share-holder demo{X}   server: {args.url}")
    say()
    if deal(args) != 0:
        return 1
    for who in ("bob", "carol"):
        say()
        args.who = who
        if fetch(args) != 0:
            return 1
    say()
    if not args.auto:
        ask(f"  {C}>> press Enter to delete the demo data from the clouds {X}")
    return cleanup(args)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", default="all", choices=["all", "deal", "fetch", "open", "cleanup"])
    parser.add_argument("who", nargs="?", choices=["bob", "carol", "mallory"])
    parser.add_argument("--url", default=os.environ.get("FIREFLY_URL"), help="PC2 address, e.g. 10.221.52.231 or http://10.221.52.231:8011")
    parser.add_argument("--token", default=os.environ.get("FIREFLY_API_TOKEN"))
    parser.add_argument("--secret")
    parser.add_argument("--auto", action="store_true", help="no Enter prompts")
    args = parser.parse_args()
    if os.name == "nt":
        os.system("")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.command in ("open", "fetch") and not args.who:
        parser.error(f"{args.command} needs bob or carol:  python share_holders.py {args.command} bob")
    if args.command == "open" and args.who == "mallory":
        parser.error("open is for bob or carol (mallory only uses fetch)")
    print()
    needs_url = args.command in ("all", "deal", "fetch", "cleanup")
    needs_token = args.command in ("all", "deal", "cleanup")  # holders fetch with their shares, not a token
    if needs_url:
        if not args.url:
            args.url = input("  PC2 address (for example 10.221.52.231): ").strip()
        if needs_token and not args.token:
            args.token = getpass.getpass("  Token from PC2's window (hidden as you type): ").strip()
        if not args.url or (needs_token and not args.token):
            print("  An address" + (" and a token are both needed." if needs_token else " is needed."))
            return 2
        args.url = normalize_url(args.url)
    try:
        run = {"all": run_all, "deal": deal, "fetch": fetch, "open": open_wallet, "cleanup": cleanup}[args.command]
        return run(args)
    except requests.ConnectionError:
        print(f"  Cannot reach PC2 at {getattr(args, 'url', '?')}.")
        print("  Check: PC2's server is running, both PCs are on the same network, the IP is current.")
        return 2
    finally:
        print()


main()
# END OF FILE
