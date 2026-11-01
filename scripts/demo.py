"""Firefly interactive demo, driven entirely through the FastAPI server.

    # terminal 1: the server (real services)
    $env:FIREFLY_DEMO_ENDPOINTS = "true"
    uvicorn app.main:app --port 8000
    # terminal 2: this demo
    python -m scripts.demo

    # practice without credentials: simulated clouds and Akeyless
    uvicorn scripts.demo_server:create_app --factory --port 8000
    python -m scripts.demo --rehearsal

Flags: --url, --token, --rehearsal, --auto, --secret TEXT, --plain, --no-color.
Uses only synthetic data. Every share the demo stores is deleted again at the end.
"""

from __future__ import annotations

import warnings

warnings.simplefilter("ignore")  # third-party deprecation notices would drown out the story

import argparse
import itertools
import os
import re
import secrets
import sys
import threading
import time
from typing import Any, Callable, TypeVar

import requests

CLOUDS = ("Upstash", "Neon", "Firestore")
CELL = 12  # three cells + borders = 40 columns: fits any terminal, so nothing ever wraps
REHEARSAL_TOKEN = "rehearsal-token"
T = TypeVar("T")


class Ui:
    COLORS = {"green": "32", "red": "31", "yellow": "33", "cyan": "36", "dim": "2", "bold": "1"}

    def __init__(self, color: bool, plain: bool, animate: bool):
        self.color, self.plain, self.animate = color, plain, animate
        self.ok, self.fail, self.dot, self.lock = ("OK", "XX", "-", "#") if plain else ("✔", "✖", "·", "🔒")
        self.arrow = "->" if plain else "→"

    def paint(self, color: str, text: str) -> str:
        return f"\033[{self.COLORS[color]}m{text}\033[0m" if self.color else text

    def beat(self, seconds: float) -> None:
        if self.animate:
            time.sleep(seconds)

    def say(self, text: str = "") -> None:
        print(f"  {text}")

    def wait(self, prompt: str) -> None:
        if self.animate:
            try:
                input(self.paint("cyan", f"\n  >> press Enter to {prompt} "))
            except EOFError:
                pass
        else:
            print()

    def working(self, label: str, work: Callable[[], T]) -> T:
        """Spinner on its own line while work runs; the line is erased afterwards."""
        if not self.animate:
            return work()
        stop = threading.Event()

        def spin() -> None:
            for frame in itertools.cycle("|/-\\" if self.plain else "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"):
                if stop.is_set():
                    return
                sys.stdout.write(f"\r  {self.paint('cyan', frame)} {label}")
                sys.stdout.flush()
                time.sleep(0.08)

        thread = threading.Thread(target=spin, daemon=True)
        thread.start()
        try:
            return work()
        finally:
            stop.set()
            thread.join()
            sys.stdout.write("\r" + " " * (len(label) + 6) + "\r")
            sys.stdout.flush()

    def boxes(self, cells: list[tuple[str, str, str]]) -> None:
        """Three side-by-side cloud boxes: (name, status text, status color)."""
        if self.plain:
            tl, tm, tr, bl, bm, br, v, h = "+", "+", "+", "+", "+", "+", "|", "-"
        else:
            tl, tm, tr, bl, bm, br, v, h = "┌", "┬", "┐", "└", "┴", "┘", "│", "─"

        def edge(left: str, mid: str, right: str) -> str:
            return "  " + left + mid.join(h * CELL for _ in cells) + right

        print(edge(tl, tm, tr))
        print("  " + v + v.join(self.paint("bold", name.center(CELL)) for name, _s, _c in cells) + v)
        print("  " + v + v.join(self.paint(color, status.center(CELL)) for _n, status, color in cells) + v)
        print(edge(bl, bm, br))


class Api:
    """Thin client for the Firefly server. `session` is a requests.Session (or a Starlette TestClient)."""

    def __init__(self, session: Any, base_url: str, token: str, token_source: str = "given"):
        self.session, self.base_url, self.token, self.token_source = session, base_url.rstrip("/"), token, token_source
        self.last_trace = ""

    def call(self, method: str, path: str, *, auth: bool = True, **kwargs: Any):
        headers = {"Authorization": f"Bearer {self.token}"} if auth else {}
        started = time.monotonic()
        response = getattr(self.session, method)(self.base_url + path, headers=headers, timeout=60, **kwargs)
        elapsed = (time.monotonic() - started) * 1000
        short = re.sub(r"[0-9a-f]{32}", lambda match: match.group(0)[:6] + "..", path)
        self.last_trace = f"{method.upper()} {short}  {response.status_code}  {elapsed:.0f} ms"
        return response


def resolve_token(args: argparse.Namespace) -> tuple[str, str]:
    """The bearer token to use and where it came from (never the value itself)."""
    if args.rehearsal:
        return REHEARSAL_TOKEN, "--rehearsal"
    if args.token:
        return args.token, "--token"
    if os.environ.get("FIREFLY_API_TOKEN"):  # checked before .env is loaded, so we know the real source
        return os.environ["FIREFLY_API_TOKEN"], "the FIREFLY_API_TOKEN environment variable"
    from app.core.config import get_settings

    return get_settings().api_token, ".env"


def explain_unauthorized(ui: Ui, api: Api) -> None:
    """The server said 401 to our token. Work out which kind of mismatch it is."""
    ui.say(ui.paint("red", f"{ui.fail} the server rejected our bearer token (HTTP 401)."))
    ui.say(ui.paint("dim", f"   this demo's token came from: {api.token_source}"))
    probe = None
    if api.token != REHEARSAL_TOKEN:
        saved, api.token = api.token, REHEARSAL_TOKEN
        try:
            probe = api.call("get", "/demo/secrets/probe/shares")
        finally:
            api.token = saved
    if api.token == REHEARSAL_TOKEN:
        ui.say("   This server is NOT the rehearsal server, but you ran the demo with --rehearsal.")
        ui.say("   Fix: run the demo without --rehearsal, or start the rehearsal server:")
        ui.say("        python -m uvicorn scripts.demo_server:create_app --factory --port 8000")
    elif probe is not None and probe.status_code != 401:
        ui.say("   This is the REHEARSAL server (simulated clouds) but the demo is in live mode.")
        ui.say("   Fix: run the demo with --rehearsal:   python -m scripts.demo --rehearsal")
    else:
        ui.say("   The server and the demo are using different FIREFLY_API_TOKEN values. Check:")
        ui.say("     1. The server was (re)started AFTER the token in .env was last changed.")
        ui.say("     2. A stale shell variable is not overriding .env in one terminal:")
        ui.say("          Remove-Item Env:FIREFLY_API_TOKEN     (then restart the server too)")
        ui.say("     3. Or pass it explicitly:   python -m scripts.demo --token <the server's token>")


def choose_secret(ui: Ui, args: argparse.Namespace) -> str:
    if args.secret:
        return args.secret
    random_key = "FIREFLY-" + secrets.token_hex(8).upper()
    if not ui.animate:
        return random_key
    try:
        typed = input(ui.paint("bold", "  Enter a secret to protect ") + ui.paint("dim", "(press Enter for a random key): ")).strip()
    except EOFError:
        typed = ""
    return typed[:1000] or random_key


def trace(ui: Ui, api: Api) -> None:
    ui.say(ui.paint("dim", f"  {ui.arrow} {api.last_trace}"))


def run_demo(ui: Ui, api: Api, secret: str, live: bool) -> bool:
    health = api.call("get", "/health", auth=False)
    if health.status_code != 200:
        ui.say(ui.paint("red", f"{ui.fail} server is not healthy (HTTP {health.status_code})"))
        return False
    denied = api.call("post", "/secrets", auth=False, json={"secret": "x"})
    ui.say(f"{ui.paint('green' if denied.status_code == 401 else 'red', ui.ok if denied.status_code == 401 else ui.fail)} the API rejects a caller with no token (HTTP {denied.status_code})")
    ui.say()
    ui.say(f"secret: {ui.paint('bold', secret)}")
    ui.say()

    created = ui.working("POST /secrets: splitting into 3 shares, encrypting, storing in 3 clouds...", lambda: api.call("post", "/secrets", json={"secret": secret}))
    if created.status_code == 401:
        explain_unauthorized(ui, api)
        return False
    if created.status_code != 201:
        ui.say(ui.paint("red", f"{ui.fail} store failed (HTTP {created.status_code}): {created.text[:120]}"))
        return False
    record_id = created.json()["record_id"]
    store_trace = api.last_trace
    shares = api.call("get", f"/demo/secrets/{record_id}/shares")
    if shares.status_code == 404:
        ui.say(ui.paint("red", f"{ui.fail} the demo endpoints are switched off on the server."))
        ui.say("   Restart it with:  $env:FIREFLY_DEMO_ENDPOINTS = \"true\"; uvicorn app.main:app --port 8000")
        return False
    ui.say(f"{ui.paint('green', ui.ok)} STORED. Each cloud holds ONE encrypted share, never the secret:")
    ui.say()
    for share in shares.json():
        name = CLOUDS[share["share_index"] - 1]
        ui.say(f"  share {share['share_index']}  {name:<9} {ui.lock} {ui.paint('dim', (share['ciphertext_preview'] or '?') + '...')}")
    ui.say(ui.paint("dim", f"  {ui.arrow} {store_trace}"))
    trace(ui, api)
    ui.say()
    ui.say(ui.paint("yellow", "Now we try to get the secret back. The rule: any 2 of 3 shares rebuild it."))

    verdicts: list[bool] = []
    fingerprints: dict[int, str] = {}
    for round_number in (1, 2, 3):
        noun = "share" if round_number == 1 else "shares"
        ui.wait(f"collect {round_number} {noun}")
        ui.say(ui.paint("cyan", f"ROUND {round_number}") + f"  we hold {round_number} {noun}")
        name = CLOUDS[round_number - 1]
        collected = ui.working(f"fetching share {round_number} from {name} and decrypting it...", lambda n=round_number: api.call("post", f"/demo/secrets/{record_id}/collect/{n}"))
        if collected.status_code != 200:
            ui.say(ui.paint("red", f"{ui.fail} could not collect share {round_number} (HTTP {collected.status_code})"))
            return False
        info = collected.json()
        fingerprints[round_number] = info["fingerprint"][:6]
        ui.boxes([(cloud, f"{ui.ok} {fingerprints[i + 1]}" if (i + 1) in fingerprints else ui.dot, "green" if (i + 1) in fingerprints else "dim") for i, cloud in enumerate(CLOUDS)])
        if live:
            ui.say(ui.paint("dim", f"{name} answered in {info['latency_ms']:.0f} ms (real network call)"))
        trace(ui, api)
        ui.working(f"POST /rebuild: trying to rebuild the secret from {round_number} {noun}...", lambda: ui.beat(1.0))
        rebuilt = api.call("post", f"/demo/secrets/{record_id}/rebuild")
        body = rebuilt.json()

        if round_number == 1:
            refused = rebuilt.status_code == 409 and not body.get("rebuilt") and secret not in rebuilt.text
            verdicts.append(refused)
            ui.say(ui.paint("yellow", f"{ui.fail} CANNOT REBUILD (HTTP {rebuilt.status_code}). One share is not enough.") if refused else ui.paint("red", f"{ui.fail} UNEXPECTED: rebuild answered HTTP {rebuilt.status_code}"))
            if body.get("lone_share_preview"):
                ui.say(ui.paint("dim", f"all one share holds: {body['lone_share_preview']}...  (random numbers)"))
        elif round_number == 2:
            good = rebuilt.status_code == 200 and body.get("secret") == secret
            verdicts.append(good)
            ui.say(ui.paint("green", f"{ui.ok} SECRET REBUILT: ") + ui.paint("bold", str(body.get("secret"))) if good else ui.paint("red", f"{ui.fail} UNEXPECTED: HTTP {rebuilt.status_code}"))
            if good:
                ui.say(ui.paint("dim", "identical to what you entered"))
        else:
            pairs = body.get("pairs") or []
            good = rebuilt.status_code == 200 and body.get("secret") == secret and len(pairs) == 3 and all(p["rebuilt"] for p in pairs)
            verdicts.append(good)
            summary = "  ".join(f"{'+'.join(map(str, p['shares']))} {ui.ok if p['rebuilt'] else ui.fail}" for p in pairs)
            ui.say(ui.paint("green", f"{ui.ok} SECRET REBUILT from every pair:  ") + summary if good else ui.paint("red", f"{ui.fail} UNEXPECTED: HTTP {rebuilt.status_code}"))
        trace(ui, api)

    ui.say()
    ui.say(ui.paint("bold", "RESULT"))
    ui.say(f"  1 share   {ui.paint('yellow', ui.fail + '  nothing')}")
    ui.say(f"  2 shares  {ui.paint('green', ui.ok + '  your secret')}")
    ui.say(f"  3 shares  {ui.paint('green', ui.ok + '  your secret')}")

    cleaned = api.call("delete", f"/demo/secrets/{record_id}")
    left = cleaned.json().get("remote_shares_left", -1) if cleaned.status_code == 200 else -1
    ui.say()
    ui.say(ui.paint("dim", "demo shares deleted from all 3 clouds (verified)" if left == 0 else "WARNING: some demo shares could not be confirmed deleted"))
    return all(verdicts) and left == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="server base URL")
    parser.add_argument("--token", help="bearer token (default: FIREFLY_API_TOKEN from the environment or .env)")
    parser.add_argument("--rehearsal", action="store_true", help="talk to scripts.demo_server (simulated clouds)")
    parser.add_argument("--auto", action="store_true", help="no prompts or waiting")
    parser.add_argument("--secret", help="secret to protect (skips the prompt)")
    parser.add_argument("--plain", action="store_true", help="ASCII only (no emoji or box drawing)")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    tty = sys.stdout.isatty()
    if tty and not args.no_color and os.name == "nt":
        os.system("")  # enable ANSI escape handling in the Windows console
    ui = Ui(color=tty and not args.no_color, plain=args.plain, animate=tty and not args.auto)

    token, token_source = resolve_token(args)

    print()
    ui.say(ui.paint("yellow", "✦ ") + ui.paint("bold", "F I R E F L Y") + ui.paint("dim", "   split a secret across 3 clouds"))
    mode = "REHEARSAL: in-memory clouds, simulated Akeyless (not real encryption)" if args.rehearsal else "LIVE: real Akeyless + Upstash + Neon + Firestore"
    ui.say(ui.paint("dim", f"{mode}   server: {args.url}"))
    ui.say(ui.paint("dim", f"token from: {token_source}" + ("" if token else "  (EMPTY: set FIREFLY_API_TOKEN in .env)")))
    ui.say()

    try:
        success = run_demo(ui, Api(requests.Session(), args.url, token, token_source), choose_secret(ui, args), live=not args.rehearsal)
    except requests.ConnectionError:
        server = "uvicorn scripts.demo_server:create_app --factory --port 8000" if args.rehearsal else '$env:FIREFLY_DEMO_ENDPOINTS = "true"; uvicorn app.main:app --port 8000'
        print(f"  Cannot reach {args.url}. Start the server first:\n    {server}")
        return 2
    except requests.Timeout:
        print("\n  The server took longer than 60 s to answer, so a cloud is not responding.")
        print("  Common cause on Windows: a broken IPv6 route to Google/Neon (IPv4 works, IPv6 times out).")
        print("  Check the server terminal, run `python -m scripts.check_services`, or use --rehearsal.")
        print("  A half-written demo record is cleaned up the next time the server starts.")
        return 2
    except KeyboardInterrupt:
        print("\n  interrupted")
        return 130
    print()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
