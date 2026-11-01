"""PC2 launcher: start the protected Firefly server so PC1 can attack it over the network.

    python -m scripts.pc2_server                 # real server (real Akeyless + clouds)
    python -m scripts.pc2_server --rehearsal     # simulated clouds, token "rehearsal-token"
    python -m scripts.pc2_server --show-token    # also print the real token (give it to Alice only)

It checks the database and token first, prefers IPv4 (a broken IPv6 route makes Google/Neon
calls stall on some networks), prints this PC's address and firewall status, then serves on
0.0.0.0. The demo back-door endpoints stay OFF, so an attacker has only the normal API.
"""

from __future__ import annotations

import os

import argparse
import socket
import subprocess
import sys

# Set before settings load. Off for the attack demo (no /demo back door); on only with --share-demo.
os.environ["FIREFLY_DEMO_ENDPOINTS"] = "true" if "--share-demo" in sys.argv else "false"

REHEARSAL_TOKEN = "rehearsal-token"


def lan_ip() -> str:
    """This PC's outbound IPv4 address (a UDP connect sends no packets)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def port_free(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def firewall_open(port: int) -> bool | None:
    """True/False if we can tell, None if netsh could not be run."""
    if os.name != "nt":
        return None
    try:
        out = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", "name=Firefly demo"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return str(port) in out and "Allow" in out


def prefer_ipv4() -> None:
    """Make requests/urllib3 (Akeyless, Upstash, Google auth) and Neon's libpq use IPv4."""
    import psycopg
    import urllib3.util.connection as urllib3_connection
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from app.storage import postgres

    urllib3_connection.HAS_IPV6 = False

    def connect_ipv4(self):
        info = conninfo_to_dict(self.database_url)
        port = int(info.get("port") or 5432)
        address = socket.getaddrinfo(info["host"], port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
        return psycopg.connect(make_conninfo(self.database_url, connect_timeout=self.timeout_seconds, sslmode="require", hostaddr=address))

    postgres.PostgresStorageAdapter._connect = connect_ipv4


def preflight_real() -> str:
    """Fail early with a plain message. Returns the API token."""
    from app.core.config import get_settings
    from app.db.repository import SecretRepository

    settings = get_settings()
    if not settings.api_token.strip():
        sys.exit("  X FIREFLY_API_TOKEN is empty. Set it in .env.")
    if not os.path.isfile(settings.database_path):
        sys.exit(f"  X Metadata database not found: {settings.database_path}\n    Create it once with:  python -m scripts.init_db")
    try:
        SecretRepository(settings.database_path, settings.sqlcipher_key, share_count=settings.share_count, threshold=settings.threshold)
    except Exception as exc:
        sys.exit(f"  X Metadata database check failed: {exc}")
    print("  OK  .env loaded, token set, metadata database opens and passes its integrity check")
    return settings.api_token


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--rehearsal", action="store_true", help="simulated clouds and Akeyless, no credentials")
    parser.add_argument("--show-token", action="store_true", help="print the real token (give it to Alice only)")
    parser.add_argument("--share-demo", action="store_true", help="enable /demo so the dealer can hand out share cards (part 2)")
    parser.add_argument("--no-ipv4", action="store_true", help="do not force IPv4")
    args = parser.parse_args()

    sys.path.insert(0, os.getcwd())
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("\n  PC2: Firefly protected server\n")

    if args.rehearsal:
        token = REHEARSAL_TOKEN
        print("  OK  REHEARSAL mode: simulated clouds and Akeyless (not real encryption)")
    else:
        token = preflight_real()
    if not args.no_ipv4:
        prefer_ipv4()
        print("  OK  IPv4 preferred for cloud calls")
    if not port_free(args.port):
        sys.exit(f"  X Port {args.port} is already in use (an old server?). Pick another: --port {args.port + 1}")

    ip = lan_ip()
    fw = firewall_open(args.port)
    url = f"http://{ip}:{args.port}"
    print("  !!  /demo endpoints ON (share-card demo): do NOT run the attacker demo in this mode" if args.share_demo else "  OK  demo back-door endpoints: OFF")
    print(f"  {'OK ' if fw else '!! '} Windows Firewall rule for port {args.port}: " + ("open" if fw else "NOT FOUND" if fw is False else "could not check"))
    if not fw:
        print(f'        run once as Administrator:  netsh advfirewall firewall add rule name="Firefly demo" dir=in action=allow protocol=TCP localport={args.port}')
    print()
    print(f"  This PC's address (give to PC1):   {url}")
    print(f"  PC1 checks reachability:           open {url}/health in a browser  ->  {{\"status\":\"ok\"}}")
    print()
    token_text = token if (args.rehearsal or args.show_token) else "<run again with --show-token, or: python -c \"from app.core.config import get_settings; print(get_settings().api_token)\">"
    if args.share_demo:
        print(f"  PC1, dealer:   python share_holders.py deal --url {url} --token {token_text}")
        print("  PC1, holders:  python share_holders.py open bob      then      python share_holders.py open carol")
        print(f"  PC1, cleanup:  python share_holders.py cleanup --url {url} --token {token_text}")
    else:
        print(f"  PC1, Alice:    python two_pc_demo.py alice --url {url} --token {token_text}")
        print(f"  PC1, attacker: python two_pc_demo.py attack --url {url}")
    print("\n  Starting server. Leave this window open; Ctrl+C stops it.\n")

    import uvicorn

    if args.rehearsal:
        uvicorn.run("scripts.demo_server:create_app", factory=True, host="0.0.0.0", port=args.port, log_level="info")
    else:
        uvicorn.run("app.main:app", host="0.0.0.0", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
