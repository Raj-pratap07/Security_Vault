from __future__ import annotations

from dataclasses import replace
from itertools import combinations
from unittest.mock import Mock

import pytest

from app.core.config import PROVIDER_PROFILES
from app.core.errors import RecoveryError
from app.services.sss_service import ShareBundle, SSSService
from tests.fakes import (
    PROVIDERS,
    ControllableAdapter,
    FailingStorageAdapter,
    FakeStorageAdapter,
    build_stack,
    forge_share,
)

SECRET = "synthetic-recovery-secret"


def stored_record(tmp_path):
    orchestrator, adapters = build_stack(tmp_path)
    result = orchestrator.store_secret("demo-owner", SECRET)
    return orchestrator, adapters, result["record_id"]


def recover(orchestrator, record_id):
    return orchestrator.recover_secret(record_id, "demo-owner")


# --- provider mapping (item 8) ------------------------------------------------


def test_provider_to_share_and_key_mapping_matches_the_approved_specification(tmp_path):
    assert PROVIDER_PROFILES["three_provider"] == ("upstash", "neon", "firebase")
    orchestrator, _adapters, record_id = stored_record(tmp_path)

    rows = orchestrator.repository.get_share_records_for_record(record_id, 1)

    assert [(row["share_index"], row["provider_id"]) for row in rows] == [
        (1, "upstash"),
        (2, "neon"),
        (3, "firebase"),
    ]
    for row in rows:
        assert row["key_ref"] == orchestrator.akeyless_service.key_refs[row["provider_id"]]


# --- outage tolerance for an existing record (item 9) -------------------------


@pytest.mark.parametrize("down", PROVIDERS)
def test_existing_record_recovers_with_each_single_provider_down(tmp_path, down):
    orchestrator, adapters, record_id = stored_record(tmp_path)
    adapters[down].fail_reads = True

    assert recover(orchestrator, record_id) == SECRET


@pytest.mark.parametrize("pair", list(combinations(PROVIDERS, 2)))
def test_two_providers_down_never_returns_a_secret_and_never_reconstructs(tmp_path, pair, monkeypatch):
    orchestrator, adapters, record_id = stored_record(tmp_path)
    for provider_id in pair:
        adapters[provider_id].fail_reads = True
    rebuild = Mock(side_effect=AssertionError("reconstruction must not run below threshold"))
    monkeypatch.setattr(orchestrator.sss_service, "recover_secret", rebuild)

    with pytest.raises(RecoveryError):
        recover(orchestrator, record_id)

    rebuild.assert_not_called()


def test_a_provider_that_lost_its_object_is_tolerated(tmp_path):
    orchestrator, adapters, record_id = stored_record(tmp_path)
    adapters["neon"].data.clear()

    assert recover(orchestrator, record_id) == SECRET


def test_recovery_stops_after_two_valid_shares(tmp_path):
    orchestrator, adapters, record_id = stored_record(tmp_path)
    before = {pid: adapter.get_calls for pid, adapter in adapters.items()}

    assert recover(orchestrator, record_id) == SECRET

    delta = {pid: adapters[pid].get_calls - before[pid] for pid in PROVIDERS}
    assert sum(delta.values()) == 2
    assert delta["firebase"] == 0


def test_recovery_continues_to_the_third_provider_when_a_candidate_fails(tmp_path):
    orchestrator, adapters, record_id = stored_record(tmp_path)
    adapters["upstash"].fail_reads = True
    before = {pid: adapter.get_calls for pid, adapter in adapters.items()}

    assert recover(orchestrator, record_id) == SECRET

    assert adapters["firebase"].get_calls - before["firebase"] == 1


# --- candidate rejection (item 16) --------------------------------------------


def tamper_ciphertext(adapters, record_id, share_index):
    adapter = adapters[PROVIDERS[share_index - 1]]
    share_id = f"firefly:{record_id}:v1:share:{share_index}"
    adapter.data[share_id] = replace(adapter.data[share_id], ciphertext="AAAA" + adapter.data[share_id].ciphertext[4:])


def test_one_tampered_ciphertext_is_rejected_and_remaining_shares_recover(tmp_path):
    orchestrator, adapters, record_id = stored_record(tmp_path)
    tamper_ciphertext(adapters, record_id, 1)

    assert recover(orchestrator, record_id) == SECRET


def test_two_tampered_ciphertexts_leave_fewer_than_two_valid_shares(tmp_path):
    orchestrator, adapters, record_id = stored_record(tmp_path)
    tamper_ciphertext(adapters, record_id, 1)
    tamper_ciphertext(adapters, record_id, 2)

    with pytest.raises(RecoveryError):
        recover(orchestrator, record_id)


@pytest.mark.parametrize(
    "field,value",
    [("record_id", "another-record"), ("version", 2), ("provider_id", "neon"), ("share_index", 3), ("key_ref", "other-key"), ("format_version", 2)],
)
def test_envelope_that_disagrees_with_metadata_is_rejected(tmp_path, field, value):
    orchestrator, adapters, record_id = stored_record(tmp_path)
    share_id = f"firefly:{record_id}:v1:share:1"
    adapters["upstash"].data[share_id] = replace(adapters["upstash"].data[share_id], **{field: value})
    adapters["neon"].fail_reads = True  # only the mismatching share and firebase remain

    with pytest.raises(RecoveryError):
        recover(orchestrator, record_id)


def test_valid_ciphertext_carrying_another_shares_content_is_rejected(tmp_path):
    orchestrator, adapters, record_id = stored_record(tmp_path)
    share_one = SSSService().split_secret(SECRET)[0]
    # Provider 2 is made to hold a correctly encrypted "index 1" share: a duplicate index.
    forge_share(orchestrator, adapters, record_id, 2, share_one.to_bytes())
    adapters["firebase"].fail_reads = True

    with pytest.raises(RecoveryError):
        recover(orchestrator, record_id)


@pytest.mark.parametrize(
    "garbage",
    [b"", b"not json", b'{"index":2,"fragments":[]}', b'{"index":9,"fragments":[{"length":1,"share":"AA=="}]}', b'{"index":2,"fragments":[{"length":99,"share":"AA=="}]}'],
    ids=["empty", "not-json", "no-fragments", "bad-index", "bad-length"],
)
def test_malformed_share_payload_is_rejected(tmp_path, garbage):
    orchestrator, adapters, record_id = stored_record(tmp_path)
    forge_share(orchestrator, adapters, record_id, 2, garbage)
    adapters["firebase"].fail_reads = True

    with pytest.raises(RecoveryError):
        recover(orchestrator, record_id)


@pytest.mark.parametrize("garbage", [b"", b"x", b"[]", b'{"index":"a"}', b"\xff\xfe"])
def test_sss_decode_rejects_malformed_payloads(garbage):
    with pytest.raises(ValueError):
        SSSService.decode_share(garbage)


def test_sss_never_combines_fewer_than_two_or_duplicate_indices():
    service = SSSService()
    shares = service.split_secret(SECRET)
    with pytest.raises(ValueError):
        service.recover_secret(shares[:1])
    with pytest.raises(ValueError, match="distinct"):
        service.recover_secret([shares[0], shares[0]])
    assert isinstance(SSSService.decode_share(shares[0].to_bytes()), ShareBundle)


def test_unrelated_valid_shares_that_do_not_combine_raise_a_controlled_error(tmp_path, monkeypatch):
    orchestrator, _adapters, record_id = stored_record(tmp_path)
    monkeypatch.setattr(
        orchestrator.sss_service, "recover_secret", Mock(side_effect=ValueError("inconsistent"))
    )

    with pytest.raises(RecoveryError, match="could not be combined"):
        recover(orchestrator, record_id)


# --- rollback / cleanup failures (items 5, 12) --------------------------------


def test_failed_delete_during_rollback_persists_cleanup_required(tmp_path):
    adapters = [
        ControllableAdapter("upstash"),
        FailingStorageAdapter("neon"),
        ControllableAdapter("firebase"),
    ]
    orchestrator, by_id = build_stack(tmp_path, adapters)
    by_id["upstash"].fail_deletes = True

    with pytest.raises(RecoveryError, match="provider writes failed"):
        orchestrator.store_secret("demo-owner", SECRET)

    record = orchestrator.repository.list_pending_records()[0]
    assert record["status"] == "CLEANUP_REQUIRED"
    assert record["last_error_code"] == "CLEANUP_UNCONFIRMED"
    rows = {r["provider_id"]: r for r in orchestrator.repository.get_share_records_for_record(record["record_id"], 1)}
    assert rows["upstash"]["status"] == "DELETE_PENDING"
    assert rows["upstash"]["last_error_code"] == "DELETE_UNCONFIRMED"
    assert rows["neon"]["status"] == "DELETE_CONFIRMED"
    assert len(by_id["upstash"].data) == 1  # the object really is still remote


def test_delete_that_raises_is_treated_as_unconfirmed(tmp_path):
    adapters = [ControllableAdapter("upstash"), FailingStorageAdapter("neon"), ControllableAdapter("firebase")]
    orchestrator, by_id = build_stack(tmp_path, adapters)
    by_id["firebase"].delete_share = Mock(side_effect=RuntimeError("provider exploded"))

    with pytest.raises(RecoveryError):
        orchestrator.store_secret("demo-owner", SECRET)

    assert orchestrator.repository.list_pending_records()[0]["status"] == "CLEANUP_REQUIRED"


def test_rollback_failure_does_not_mask_the_original_error(tmp_path, monkeypatch):
    adapters = [ControllableAdapter("upstash"), FailingStorageAdapter("neon"), ControllableAdapter("firebase")]
    orchestrator, _by_id = build_stack(tmp_path, adapters)
    monkeypatch.setattr(orchestrator, "rollback", Mock(side_effect=OSError("rollback exploded")))

    with pytest.raises(RecoveryError, match="provider writes failed"):
        orchestrator.store_secret("demo-owner", SECRET)

    record = orchestrator.repository.list_pending_records()[0]
    assert record["status"] == "CLEANUP_REQUIRED"
    assert record["last_error_code"] == "ROLLBACK_FAILED"


def test_even_if_persisting_the_cleanup_state_fails_the_original_error_survives(tmp_path, monkeypatch):
    adapters = [ControllableAdapter("upstash"), FailingStorageAdapter("neon"), ControllableAdapter("firebase")]
    orchestrator, _by_id = build_stack(tmp_path, adapters)
    monkeypatch.setattr(orchestrator, "rollback", Mock(side_effect=OSError("rollback exploded")))
    real_set = orchestrator.repository.set_record_status
    monkeypatch.setattr(
        orchestrator.repository,
        "set_record_status",
        lambda **kw: (_ for _ in ()).throw(OSError("db gone")) if kw["status"] == "CLEANUP_REQUIRED" else real_set(**kw),
    )

    with pytest.raises(RecoveryError, match="provider writes failed"):
        orchestrator.store_secret("demo-owner", SECRET)


# --- attempt counting ---------------------------------------------------------


def test_attempt_count_counts_real_write_attempts_only(tmp_path):
    orchestrator, _adapters, record_id = stored_record(tmp_path)
    rows = orchestrator.repository.get_share_records_for_record(record_id, 1)
    assert [row["attempt_count"] for row in rows] == [1, 1, 1]

    # Status changes alone must not look like attempts.
    orchestrator.repository.update_share_status(record_id=record_id, version=1, share_index=1, status="STORED")
    orchestrator.repository.update_share_status(record_id=record_id, version=1, share_index=1, status="STORED")
    assert orchestrator.repository.get_share_record(record_id, 1, 1)["attempt_count"] == 1


def test_retry_after_missing_readback_counts_two_attempts(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.services import orchestrator as orchestrator_module
    from tests.fakes import MissingReadbackAdapter

    monkeypatch.setattr(orchestrator_module.uuid, "uuid4", lambda: SimpleNamespace(hex="missing-readback"))
    adapters = [MissingReadbackAdapter("upstash"), FakeStorageAdapter("neon"), FakeStorageAdapter("firebase")]
    orchestrator, _ = build_stack(tmp_path, adapters)

    with pytest.raises(RecoveryError):
        orchestrator.store_secret("demo-owner", SECRET)

    upstash_row = orchestrator.repository.get_share_record("missing-readback", 1, 1)
    assert upstash_row["attempt_count"] == 2
