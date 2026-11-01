"""Firefly one-PC demo: why 2 shares rebuild a secret and 1 share cannot. Runs entirely in this terminal.

    python -m scripts.demo_one_pc            # rehearsal: in-memory clouds, SIMULATED Akeyless (no credentials)
    python -m scripts.demo_one_pc --live     # real Akeyless, Upstash, Neon, Firestore from .env

Flags: --auto (no prompts, no waiting), --secret TEXT, --plain (ASCII only), --no-color.
No server and no second PC: it uses the same store, fetch, Akeyless-decrypt and rebuild code the
API uses. Metadata lives in a throwaway SQLCipher database (never your real one), and every share
the demo stores is deleted again at the end and verified gone.
"""

from __future__ import annotations

import warnings

warnings.simplefilter("ignore")  # third-party deprecation notices would drown out the story

import argparse
import hashlib
import itertools
import os
import secrets
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, TypeVar

from app.core.config import get_settings
from app.core.errors import ConfigurationError
from app.db.repository import SecretRepository
from app.services.akeyless_service import AkeylessService
from app.services.orchestrator import StorageOrchestrator
from app.services.sss_service import ShareBundle, SSSService
from app.storage.base import build_share_id
from app.storage.registry import ProviderRegistry, create_provider_registry

OWNER = "demo-owner"
CLOUDS = (("upstash", "Upstash"), ("neon", "Neon"), ("firebase", "Firestore"))
CELL = 12  # three cells + borders = 40 columns: fits any terminal, so nothing ever wraps
T = TypeVar("T")


class Ui:
    COLORS = {"green": "32", "red": "31", "yellow": "33", "cyan": "36", "dim": "2", "bold": "1"}

    def __init__(self, color: bool, plain: bool, animate: bool):
        self.color, self.plain, self.animate = color, plain, animate
        self.ok, self.fail, self.dot, self.lock = ("OK", "XX", "-", "#") if plain else ("✔", "✖", "·", "🔒")

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


def fingerprint(bundle: ShareBundle) -> str:
    return hashlib.sha256(bundle.to_bytes()).hexdigest()[:6]


def build(live: bool, workdir: Path) -> StorageOrchestrator:
    if live:
        from scripts.pc2_server import prefer_ipv4

        prefer_ipv4()  # a broken IPv6 route to Google/Neon makes live calls stall on some networks
        settings = get_settings()
        registry = create_provider_registry(settings)
        akeyless = AkeylessService(settings=settings)
    else:
        from tests.fakes import FakeStorageAdapter, fake_akeyless_service

        akeyless = fake_akeyless_service()
        registry = ProviderRegistry(FakeStorageAdapter(pid) for pid, _ in CLOUDS)
    db_path, db_key = str(workdir / "demo-metadata.db"), secrets.token_hex(32)
    SecretRepository.initialize(db_path, db_key)
    return StorageOrchestrator(
        repository=SecretRepository(db_path, db_key),
        sss_service=SSSService(),
        akeyless_service=akeyless,
        provider_registry=registry,
    )


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


def run_demo(ui: Ui, orchestrator: StorageOrchestrator, secret: str, live: bool) -> bool:
    repo, registry, sss = orchestrator.repository, orchestrator.provider_registry, orchestrator.sss_service
    ui.say(f"secret: {ui.paint('bold', secret)}")
    ui.say()
    record_id = ui.working("splitting into 3 shares, encrypting, storing in 3 clouds...", lambda: orchestrator.store_secret(OWNER, secret)["record_id"])
    record = repo.get_record(record_id)
    rows = repo.get_share_records_for_record(record_id, record["version"])
    ui.say(f"{ui.paint('green', ui.ok)} STORED. Each cloud holds ONE encrypted share, never the secret:")
    ui.say()
    for row, (pid, name) in zip(rows, CLOUDS):
        ciphertext = registry.get(pid).get_share(row["share_id"]).ciphertext
        ui.say(f"  share {row['share_index']}  {name:<9} {ui.lock} {ui.paint('dim', ciphertext[:24] + '...')}")
    ui.say()
    ui.say(ui.paint("yellow", "Now we try to get the secret back. The rule: any 2 of 3 shares rebuild it."))

    held: list[ShareBundle] = []
    verdicts: list[bool] = []
    for round_number in (1, 2, 3):
        noun = "share" if round_number == 1 else "shares"
        ui.wait(f"collect {round_number} {noun}")
        ui.say(ui.paint("cyan", f"ROUND {round_number}") + f"  we hold {round_number} {noun}")

        row, (_pid, name) = rows[round_number - 1], CLOUDS[round_number - 1]
        started = time.monotonic()
        bundle = ui.working(f"fetching share {round_number} from {name} and decrypting it...", lambda row=row: orchestrator.load_valid_share(record, row))
        if bundle is None:
            ui.say(ui.paint("red", f"{ui.fail} could not fetch a valid share from {name}"))
            return False
        held.append(bundle)
        elapsed_ms = (time.monotonic() - started) * 1000
        ui.boxes([(cloud_name, f"{ui.ok} {fingerprint(held[i])}" if i < len(held) else ui.dot, "green" if i < len(held) else "dim") for i, (_p, cloud_name) in enumerate(CLOUDS)])
        if live:
            ui.say(ui.paint("dim", f"{name} answered in {elapsed_ms:.0f} ms (real network call)"))
        ui.working(f"trying to rebuild the secret from {round_number} {noun}...", lambda: ui.beat(1.0))

        if round_number == 1:
            try:
                sss.recover_secret(held)
                verdicts.append(False)
                ui.say(ui.paint("red", f"{ui.fail} UNEXPECTED: rebuilt from a single share"))
            except ValueError:
                verdicts.append(True)
                ui.say(ui.paint("yellow", f"{ui.fail} CANNOT REBUILD. One share is not enough."))
                ui.say(ui.paint("dim", f"all one share holds: {sss.share_preview(held[0])}...  (random numbers)"))
        elif round_number == 2:
            rebuilt = sss.recover_secret(held).decode("utf-8")
            verdicts.append(rebuilt == secret)
            ui.say(ui.paint("green", f"{ui.ok} SECRET REBUILT: ") + ui.paint("bold", rebuilt))
            ui.say(ui.paint("dim", "identical to what you entered"))
        else:
            pairs = list(itertools.combinations(range(3), 2))
            results = [sss.recover_secret([held[a], held[b]]).decode("utf-8") == secret for a, b in pairs]
            verdicts.append(all(results))
            summary = "  ".join(f"{a + 1}+{b + 1} {ui.ok if good else ui.fail}" for (a, b), good in zip(pairs, results))
            ui.say(ui.paint("green", f"{ui.ok} SECRET REBUILT from every pair:  ") + summary)

    ui.say()
    ui.say(ui.paint("bold", "RESULT"))
    ui.say(f"  1 share   {ui.paint('yellow', ui.fail + '  nothing')}")
    ui.say(f"  2 shares  {ui.paint('green', ui.ok + '  your secret')}")
    ui.say(f"  3 shares  {ui.paint('green', ui.ok + '  your secret')}")

    orchestrator.rollback(record_id)
    gone = all(registry.get(pid).get_share(build_share_id(record_id, record["version"], i)) is None for i, (pid, _n) in enumerate(CLOUDS, start=1))
    ui.say()
    ui.say(ui.paint("dim", "demo shares deleted from all 3 clouds (verified)" if gone else "WARNING: some demo shares could not be confirmed deleted"))
    return all(verdicts) and gone


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="use the real services configured in .env")
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

    print()
    ui.say(ui.paint("yellow", "✦ ") + ui.paint("bold", "F I R E F L Y") + ui.paint("dim", "   split a secret across 3 clouds"))
    ui.say(ui.paint("dim", "LIVE: real Akeyless + Upstash + Neon + Firestore" if args.live else "REHEARSAL: in-memory clouds, simulated Akeyless (not real encryption)"))
    ui.say()

    workdir = Path(tempfile.mkdtemp(prefix="firefly-demo-"))
    success = False
    try:
        try:
            orchestrator = build(args.live, workdir)
        except (ConfigurationError, ValueError) as exc:
            print(f"  Setup failed: {exc}\n  Check .env (see README) or run without --live.")
            return 2
        try:
            success = run_demo(ui, orchestrator, choose_secret(ui, args), args.live)
        finally:
            orchestrator.close()
    except KeyboardInterrupt:
        print("\n  interrupted")
        return 130
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    print()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
