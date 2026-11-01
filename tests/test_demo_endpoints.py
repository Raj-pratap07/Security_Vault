from __future__ import annotations

import logging
from contextlib import ExitStack
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.api import dependencies, routes
from app.core.config import Settings, get_settings
from app.core.errors import RecordNotFoundError
from app.services.demo_service import DemoService
from tests.fakes import PROVIDERS, build_stack

TOKEN = "demo-endpoint-test-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
SECRET = "synthetic-demo-endpoint-secret"


@pytest.fixture
def start_server(tmp_path, monkeypatch):
    """Boot the real app (lifespan included) over fake clouds; demo endpoints on or off."""
    stack = ExitStack()

    def start(demo_enabled: bool = True, server_token: str = TOKEN):
        orchestrator, adapters = build_stack(tmp_path)
        settings = Settings(api_token=server_token, demo_endpoints=demo_enabled)
        for module in (main_module, dependencies, routes):
            monkeypatch.setattr(module, "get_settings", lambda: settings)
        monkeypatch.setattr(main_module, "build_orchestrator", lambda _settings: orchestrator)
        client = stack.enter_context(TestClient(main_module.app))
        return client, orchestrator, adapters

    yield start
    stack.close()


def store(client, secret=SECRET) -> str:
    response = client.post("/secrets", headers=HEADERS, json={"secret": secret})
    assert response.status_code == 201, response.text
    return response.json()["record_id"]


def test_demo_endpoints_do_not_exist_unless_switched_on(start_server):
    client, _orchestrator, _adapters = start_server(demo_enabled=False)
    record_id = store(client)

    for method, path in (
        ("get", f"/demo/secrets/{record_id}/shares"),
        ("post", f"/demo/secrets/{record_id}/collect/1"),
        ("post", f"/demo/secrets/{record_id}/rebuild"),
        ("delete", f"/demo/secrets/{record_id}/collected"),
        ("delete", f"/demo/secrets/{record_id}"),
    ):
        assert getattr(client, method)(path, headers=HEADERS).status_code == 404, path


def test_existing_endpoints_are_unchanged_by_the_demo_router(start_server):
    client, _orchestrator, _adapters = start_server()

    assert client.get("/health").json() == {"status": "ok"}
    record_id = store(client)
    status = client.get(f"/secrets/{record_id}/status", headers=HEADERS)
    assert status.status_code == 200 and status.json()["status"] == "AVAILABLE"
    recovered = client.post(f"/secrets/{record_id}/recover", headers=HEADERS)
    assert recovered.status_code == 200 and recovered.json()["secret"] == SECRET
    assert recovered.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer "}, {"Authorization": "Bearer wrong"}], ids=["none", "empty", "wrong"])
def test_demo_endpoints_require_the_bearer_token(start_server, headers):
    client, _orchestrator, _adapters = start_server()
    record_id = store(client)

    assert client.get(f"/demo/secrets/{record_id}/shares", headers=headers).status_code == 401
    assert client.post(f"/demo/secrets/{record_id}/collect/1", headers=headers).status_code == 401
    assert client.post(f"/demo/secrets/{record_id}/rebuild", headers=headers).status_code == 401
    assert client.delete(f"/demo/secrets/{record_id}", headers=headers).status_code == 401


def test_one_share_fails_two_succeed_three_agree_then_cleanup(start_server):
    client, _orchestrator, adapters = start_server()
    record_id = store(client)

    listed = client.get(f"/demo/secrets/{record_id}/shares", headers=HEADERS)
    assert [(s["share_index"], s["provider_id"]) for s in listed.json()] == [(1, "upstash"), (2, "neon"), (3, "firebase")]
    assert all(s["ciphertext_preview"] and SECRET not in s["ciphertext_preview"] for s in listed.json())

    first = client.post(f"/demo/secrets/{record_id}/collect/1", headers=HEADERS)
    assert first.status_code == 200 and first.json()["shares_held"] == [1]
    assert len(first.json()["fingerprint"]) == 8
    refused = client.post(f"/demo/secrets/{record_id}/rebuild", headers=HEADERS)
    assert refused.status_code == 409
    assert refused.json()["rebuilt"] is False and refused.json()["shares_held"] == [1] and refused.json()["shares_needed"] == 2
    assert "secret" not in refused.json() and SECRET not in refused.text
    assert refused.json()["lone_share_preview"]

    client.post(f"/demo/secrets/{record_id}/collect/2", headers=HEADERS)
    rebuilt = client.post(f"/demo/secrets/{record_id}/rebuild", headers=HEADERS)
    assert rebuilt.status_code == 200 and rebuilt.json()["secret"] == SECRET
    assert rebuilt.headers["cache-control"] == "no-store"

    client.post(f"/demo/secrets/{record_id}/collect/3", headers=HEADERS)
    final = client.post(f"/demo/secrets/{record_id}/rebuild", headers=HEADERS).json()
    assert final["secret"] == SECRET
    assert sorted(tuple(p["shares"]) for p in final["pairs"]) == [(1, 2), (1, 3), (2, 3)]
    assert all(p["rebuilt"] for p in final["pairs"])

    cleaned = client.delete(f"/demo/secrets/{record_id}", headers=HEADERS)
    assert cleaned.json() == {"record_id": record_id, "status": "FAILED", "remote_shares_left": 0}
    assert all(not adapter.data for adapter in adapters.values())


def test_rebuild_with_nothing_collected_is_refused(start_server):
    client, _orchestrator, _adapters = start_server()
    record_id = store(client)

    response = client.post(f"/demo/secrets/{record_id}/rebuild", headers=HEADERS)

    assert response.status_code == 409 and response.json()["shares_held"] == []


def test_reset_discards_collected_shares(start_server):
    client, _orchestrator, _adapters = start_server()
    record_id = store(client)
    for index in (1, 2):
        client.post(f"/demo/secrets/{record_id}/collect/{index}", headers=HEADERS)

    assert client.delete(f"/demo/secrets/{record_id}/collected", headers=HEADERS).status_code == 204

    assert client.post(f"/demo/secrets/{record_id}/rebuild", headers=HEADERS).status_code == 409


@pytest.mark.parametrize("index", [0, 4, 99])
def test_invalid_share_index_is_not_found(start_server, index):
    client, _orchestrator, _adapters = start_server()
    record_id = store(client)

    assert client.post(f"/demo/secrets/{record_id}/collect/{index}", headers=HEADERS).status_code == 404


def test_unknown_record_is_not_found_everywhere(start_server):
    client, _orchestrator, _adapters = start_server()

    for method, path in (
        ("get", "/demo/secrets/nope/shares"),
        ("post", "/demo/secrets/nope/collect/1"),
        ("post", "/demo/secrets/nope/rebuild"),
        ("delete", "/demo/secrets/nope/collected"),
        ("delete", "/demo/secrets/nope"),
    ):
        assert getattr(client, method)(path, headers=HEADERS).status_code == 404, path


def test_collecting_from_a_deleted_record_is_refused(start_server):
    client, _orchestrator, _adapters = start_server()
    record_id = store(client)
    client.delete(f"/demo/secrets/{record_id}", headers=HEADERS)

    assert client.post(f"/demo/secrets/{record_id}/collect/1", headers=HEADERS).status_code == 503


def test_unreachable_cloud_is_a_generic_refusal_and_others_still_work(start_server):
    client, _orchestrator, adapters = start_server()
    record_id = store(client)
    adapters["neon"].fail_reads = True

    down = client.post(f"/demo/secrets/{record_id}/collect/2", headers=HEADERS)

    assert down.status_code == 503 and "simulated provider outage" not in down.text
    assert client.post(f"/demo/secrets/{record_id}/collect/1", headers=HEADERS).status_code == 200


def test_tampered_share_is_never_collected(start_server):
    client, _orchestrator, adapters = start_server()
    record_id = store(client)
    share_id = f"firefly:{record_id}:v1:share:1"
    stored = adapters["upstash"].data[share_id]
    adapters["upstash"].data[share_id] = replace(stored, ciphertext="AAAA" + stored.ciphertext[4:])

    assert client.post(f"/demo/secrets/{record_id}/collect/1", headers=HEADERS).status_code == 503
    assert client.post(f"/demo/secrets/{record_id}/rebuild", headers=HEADERS).json()["shares_held"] == []


def test_canary_secret_only_ever_appears_in_the_rebuild_response(start_server, caplog):
    canary = "CANARY-demo-endpoints-4f9a2c71"
    client, _orchestrator, adapters = start_server()
    caplog.set_level(logging.DEBUG)
    record_id = store(client, canary)

    responses = [
        client.get(f"/demo/secrets/{record_id}/shares", headers=HEADERS),
        client.post(f"/demo/secrets/{record_id}/collect/1", headers=HEADERS),
        client.post(f"/demo/secrets/{record_id}/rebuild", headers=HEADERS),  # only 1 share: refused
        client.post(f"/demo/secrets/{record_id}/collect/2", headers=HEADERS),
    ]
    rebuilt = client.post(f"/demo/secrets/{record_id}/rebuild", headers=HEADERS)
    cleaned = client.delete(f"/demo/secrets/{record_id}", headers=HEADERS)

    assert rebuilt.json()["secret"] == canary
    for response in (*responses, cleaned):
        assert canary not in response.text
    assert canary not in caplog.text


# --- memory bounds on held shares --------------------------------------------


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_held_shares_expire_after_the_ttl(tmp_path):
    orchestrator, _adapters = build_stack(tmp_path)
    clock = Clock()
    demo = DemoService(orchestrator, ttl_seconds=60, clock=clock)
    record_id = orchestrator.store_secret("demo-owner", SECRET)["record_id"]
    demo.collect(record_id, "demo-owner", 1)
    demo.collect(record_id, "demo-owner", 2)
    assert demo.rebuild(record_id, "demo-owner")["rebuilt"] is True

    clock.now = 61

    result = demo.rebuild(record_id, "demo-owner")
    assert result["rebuilt"] is False and result["shares_held"] == []


def test_number_of_records_held_in_memory_is_bounded(tmp_path):
    orchestrator, _adapters = build_stack(tmp_path)
    clock = Clock()
    demo = DemoService(orchestrator, max_records=2, clock=clock)
    ids = []
    for _ in range(3):
        ids.append(orchestrator.store_secret("demo-owner", SECRET)["record_id"])
        demo.collect(ids[-1], "demo-owner", 1)
        clock.now += 1

    assert len(demo._held) == 2 and ids[0] not in demo._held


def test_other_owners_records_are_invisible(tmp_path):
    orchestrator, _adapters = build_stack(tmp_path)
    demo = DemoService(orchestrator)
    record_id = orchestrator.store_secret("someone-else", SECRET)["record_id"]

    with pytest.raises(RecordNotFoundError):
        demo.collect(record_id, "demo-owner", 1)


# --- the demo client, end to end ---------------------------------------------


def run_client(client, live=False):
    from scripts import demo

    ui = demo.Ui(color=False, plain=True, animate=False)
    return demo.run_demo(ui, demo.Api(client, "http://testserver", TOKEN), "CANARY-demo-client-secret", live=live)


def test_demo_client_walks_the_story_through_the_api(start_server, capsys):
    client, _orchestrator, adapters = start_server()

    assert run_client(client) is True

    output = capsys.readouterr().out
    rounds = output.split("ROUND ")[1:]
    assert len(rounds) == 3
    assert "CANNOT REBUILD (HTTP 409)" in rounds[0] and "SECRET REBUILT" not in rounds[0]
    assert "SECRET REBUILT: " in rounds[1] and "CANARY-demo-client-secret" in rounds[1]
    assert "from every pair" in rounds[2] and "1+2" in rounds[2] and "2+3" in rounds[2]
    assert "POST /demo/secrets/" in output and "/collect/1" in output
    assert "rejects a caller with no token (HTTP 401)" in output
    assert "deleted from all 3 clouds" in output
    assert max(len(line) for line in output.splitlines()) <= 78  # never wraps in a normal terminal
    assert all(not adapter.data for adapter in adapters.values())


def test_demo_client_explains_when_demo_endpoints_are_switched_off(start_server, capsys):
    client, _orchestrator, _adapters = start_server(demo_enabled=False)

    assert run_client(client) is False

    assert "FIREFLY_DEMO_ENDPOINTS" in capsys.readouterr().out


def test_rehearsal_server_factory_runs_the_demo_with_no_credentials(monkeypatch, capsys):
    from scripts import demo_server

    for name in ("FIREFLY_API_TOKEN", "FIREFLY_DEMO_ENDPOINTS", "FIREFLY_ENV"):
        monkeypatch.setenv(name, "placeholder")  # registered so the factory's changes are undone
    monkeypatch.setattr(main_module, "build_orchestrator", main_module.build_orchestrator)
    get_settings.cache_clear()
    try:
        app = demo_server.create_app()
        with TestClient(app) as client:
            from scripts import demo

            ui = demo.Ui(color=False, plain=True, animate=False)
            api = demo.Api(client, "http://testserver", demo_server.REHEARSAL_TOKEN)
            assert demo.run_demo(ui, api, "rehearsal-secret", live=False) is True
    finally:
        get_settings.cache_clear()
    assert "SECRET REBUILT" in capsys.readouterr().out
    assert PROVIDERS  # fakes cover exactly the three supported providers


# --- token mismatches are diagnosed, never silent ----------------------------


def run_with_token(client, token, source, capsys):
    from scripts import demo

    ui = demo.Ui(color=False, plain=True, animate=False)
    api = demo.Api(client, "http://testserver", token, source)
    result = demo.run_demo(ui, api, "x-secret", live=token != demo.REHEARSAL_TOKEN)
    return result, capsys.readouterr().out


def test_live_client_against_rehearsal_server_says_to_use_rehearsal(start_server, capsys):
    from scripts import demo

    client, _o, _a = start_server(server_token=demo.REHEARSAL_TOKEN)

    ok, output = run_with_token(client, "the-env-token-value", ".env", capsys)

    assert ok is False
    assert "REHEARSAL server" in output and "--rehearsal" in output
    assert "the-env-token-value" not in output and "came from: .env" in output


def test_rehearsal_client_against_live_server_says_to_drop_the_flag(start_server, capsys):
    from scripts import demo

    client, _o, _a = start_server(server_token="a-real-server-token")

    ok, output = run_with_token(client, demo.REHEARSAL_TOKEN, "--rehearsal", capsys)

    assert ok is False
    assert "NOT the rehearsal server" in output and "without --rehearsal" in output


def test_plain_token_mismatch_lists_the_usual_causes_without_revealing_tokens(start_server, capsys):
    client, _o, _a = start_server(server_token="the-servers-secret-token")

    ok, output = run_with_token(client, "a-different-client-token", "the FIREFLY_API_TOKEN environment variable", capsys)

    assert ok is False
    assert "different FIREFLY_API_TOKEN values" in output and "Remove-Item Env:FIREFLY_API_TOKEN" in output
    assert "the-servers-secret-token" not in output and "a-different-client-token" not in output
    assert "environment variable" in output


def test_token_source_is_reported_correctly(monkeypatch):
    import argparse

    from scripts import demo

    args = argparse.Namespace(rehearsal=False, token=None)
    monkeypatch.setenv("FIREFLY_API_TOKEN", "from-the-shell")
    assert demo.resolve_token(args) == ("from-the-shell", "the FIREFLY_API_TOKEN environment variable")
    assert demo.resolve_token(argparse.Namespace(rehearsal=False, token="explicit")) == ("explicit", "--token")
    assert demo.resolve_token(argparse.Namespace(rehearsal=True, token=None)) == (demo.REHEARSAL_TOKEN, "--rehearsal")


def test_demo_client_explains_a_server_timeout_instead_of_a_traceback(monkeypatch, capsys):
    import sys

    import requests

    from scripts import demo

    class SlowSession:
        def get(self, *args, **kwargs):
            raise requests.ReadTimeout("simulated")

        post = delete = get

    monkeypatch.setattr(demo.requests, "Session", SlowSession)
    monkeypatch.setattr(sys, "argv", ["demo", "--rehearsal", "--auto", "--secret", "x"])

    assert demo.main() == 2

    output = capsys.readouterr().out
    assert "took longer than 60 s" in output and "IPv6" in output and "Traceback" not in output


# --- share cards: one holder cannot rebuild, two can --------------------------


def test_exported_share_cards_rebuild_only_at_the_threshold(start_server):
    import base64
    import json

    import shamirs

    client, _orchestrator, _adapters = start_server()
    secret = "share-card-secret-value-123456"
    record_id = client.post("/secrets", headers=HEADERS, json={"secret": secret}).json()["record_id"]
    cards = [client.get(f"/demo/secrets/{record_id}/export/{n}", headers=HEADERS).json() for n in (1, 2, 3)]

    assert all(card["threshold"] == 2 and card["record_id"] == record_id for card in cards)

    def rebuild(selected):
        bundles = [json.loads(base64.b64decode(card["share_b64"])) for card in selected]
        out = bytearray()
        for position in range(len(bundles[0]["fragments"])):
            shares = [shamirs.share.from_bytes(base64.b64decode(b["fragments"][position]["share"])) for b in bundles]
            out.extend(shamirs.interpolate(shares, threshold=2).to_bytes(bundles[0]["fragments"][position]["length"], "big"))
        return bytes(out).decode()

    assert rebuild([cards[0], cards[1]]) == secret
    assert rebuild([cards[1], cards[2]]) == secret
    assert secret not in json.dumps(cards[0])
    with pytest.raises(Exception):
        rebuild([cards[0]])


def test_export_is_invisible_when_the_demo_flag_is_off(start_server):
    off, _o, _a = start_server(demo_enabled=False)

    assert off.get("/demo/secrets/x/export/1", headers=HEADERS).status_code == 404


def test_export_requires_the_token_and_a_real_record(start_server):
    on, _o, _a = start_server()

    assert on.get("/demo/secrets/x/export/1").status_code == 401
    assert on.get("/demo/secrets/missing/export/1", headers=HEADERS).status_code == 404


# --- fetch: holders present share cards; the server checks they are genuine ------


@pytest.fixture
def cards_for(start_server):
    """A started demo server, a stored secret, and its three share cards."""
    client, orchestrator, adapters = start_server()
    secret = "fetch-demo-secret-value-98765"
    record_id = client.post("/secrets", headers=HEADERS, json={"secret": secret}).json()["record_id"]
    cards = {n: client.get(f"/demo/secrets/{record_id}/export/{n}", headers=HEADERS).json() for n in (1, 2, 3)}
    return client, record_id, secret, cards


def _present(*cards):
    return {"cards": [{"share_index": c["share_index"], "share_b64": c["share_b64"]} for c in cards]}


def test_fetch_needs_no_token_but_one_share_gets_nothing(cards_for):
    client, record_id, secret, cards = cards_for

    response = client.post(f"/demo/secrets/{record_id}/fetch", json=_present(cards[1]))  # no Authorization header

    assert response.status_code == 409
    body = response.json()
    assert body["rebuilt"] is False and body["shares_valid"] == 1 and body["shares_needed"] == 2
    assert "secret" not in body and secret not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("pair", [(1, 2), (1, 3), (2, 3), (1, 2, 3)])
def test_fetch_with_two_or_more_genuine_shares_returns_the_secret(cards_for, pair):
    client, record_id, secret, cards = cards_for

    response = client.post(f"/demo/secrets/{record_id}/fetch", json=_present(*[cards[n] for n in pair]))

    assert response.status_code == 200
    assert response.json()["secret"] == secret and response.json()["invalid_shares"] == []


def test_fetch_ignores_a_duplicated_share(cards_for):
    client, record_id, secret, cards = cards_for

    response = client.post(f"/demo/secrets/{record_id}/fetch", json=_present(cards[1], cards[1]))

    assert response.status_code == 409 and response.json()["shares_valid"] == 1


def test_fetch_rejects_forged_and_altered_cards(cards_for):
    import base64

    client, record_id, secret, cards = cards_for
    altered = dict(cards[2])
    raw = bytearray(base64.b64decode(cards[2]["share_b64"]))
    raw[len(raw) // 2] ^= 0x01
    altered["share_b64"] = base64.b64encode(bytes(raw)).decode()
    swapped = dict(cards[2], share_index=3)  # share 2's data claimed as share 3

    for forged in (altered, swapped, dict(cards[2], share_b64="not-base64!!")):
        response = client.post(f"/demo/secrets/{record_id}/fetch", json=_present(cards[1], forged))
        assert response.status_code == 409, forged["share_index"]
        assert response.json()["shares_valid"] == 1 and forged["share_index"] in response.json()["invalid_shares"]
        assert secret not in response.text


def test_fetch_on_an_unknown_record_and_with_bad_bodies(cards_for):
    client, record_id, _secret, cards = cards_for

    assert client.post("/demo/secrets/doesnotexist/fetch", json=_present(cards[1])).status_code == 404
    assert client.post(f"/demo/secrets/{record_id}/fetch", json={"cards": []}).status_code == 422
    assert client.post(f"/demo/secrets/{record_id}/fetch", json={"cards": [{"share_index": 9, "share_b64": "x"}]}).status_code == 422


def test_fetch_is_invisible_when_the_demo_flag_is_off(start_server):
    client, _o, _a = start_server(demo_enabled=False)

    assert client.post("/demo/secrets/x/fetch", json={"cards": [{"share_index": 1, "share_b64": "eA=="}]}).status_code == 404
