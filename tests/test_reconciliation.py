"""Startup reconciliation after a simulated process death (SimulatedCrash is a BaseException)."""

from __future__ import annotations

import pytest

from app.services.sss_service import SSSService
from tests.fakes import PROVIDERS, SimulatedCrash, build_stack, forge_share


def remote_object_count(adapters) -> int:
    return sum(len(adapter.data) for adapter in adapters.values())


def only_record(orchestrator) -> dict:
    records = orchestrator.repository.list_pending_records()
    assert len(records) == 1
    return records[0]


def share_statuses(orchestrator, record) -> dict:
    rows = orchestrator.repository.get_share_records_for_record(record["record_id"], record["version"])
    return {row["share_index"]: row["status"] for row in rows}


def crash_during_store(orchestrator, secret="synthetic-reconcile-secret"):
    with pytest.raises(SimulatedCrash):
        orchestrator.store_secret("demo-owner", secret)
    return only_record(orchestrator)


def test_crash_after_metadata_creation_is_failed_with_nothing_remote(tmp_path, monkeypatch):
    orchestrator, adapters = build_stack(tmp_path)

    def die(_secret):
        raise SimulatedCrash()

    monkeypatch.setattr(orchestrator.sss_service, "split_secret", die)
    record = crash_during_store(orchestrator)
    assert record["status"] == "PENDING"

    summary = orchestrator.reconcile_pending()

    assert summary["failed"] == 1
    assert orchestrator.repository.get_record(record["record_id"])["status"] == "FAILED"
    assert remote_object_count(adapters) == 0


@pytest.mark.parametrize("crashing_provider", ["neon", "firebase"])
def test_crash_after_one_or_two_writes_removes_partial_shares(tmp_path, crashing_provider):
    orchestrator, adapters = build_stack(tmp_path)
    adapters[crashing_provider].crash_on_put = True
    record = crash_during_store(orchestrator)
    written_before = remote_object_count(adapters)
    assert written_before == PROVIDERS.index(crashing_provider)

    summary = orchestrator.reconcile_pending()

    assert summary["failed"] == 1 and summary["available"] == 0
    assert orchestrator.repository.get_record(record["record_id"])["status"] == "FAILED"
    assert remote_object_count(adapters) == 0
    assert set(share_statuses(orchestrator, record).values()) == {"DELETE_CONFIRMED"}


def test_crash_after_all_writes_before_available_is_promoted_after_verification(tmp_path, monkeypatch):
    orchestrator, adapters = build_stack(tmp_path)
    original = orchestrator.repository.set_record_status

    def crash_on_available(**kwargs):
        if kwargs["status"] == "AVAILABLE":
            raise SimulatedCrash()
        return original(**kwargs)

    monkeypatch.setattr(orchestrator.repository, "set_record_status", crash_on_available)
    record = crash_during_store(orchestrator, "promote-me")
    monkeypatch.undo()

    summary = orchestrator.reconcile_pending()

    assert summary["available"] == 1
    assert orchestrator.repository.get_record(record["record_id"])["status"] == "AVAILABLE"
    assert orchestrator.recover_secret(record["record_id"], "demo-owner") == "promote-me"
    assert remote_object_count(adapters) == 3


def test_crash_after_remote_write_before_share_status_commit_is_promoted(tmp_path, monkeypatch):
    orchestrator, _adapters = build_stack(tmp_path)
    original = orchestrator.repository.update_share_status

    def crash_on_last_stored(**kwargs):
        if kwargs["share_index"] == 3 and kwargs["status"] == "STORED":
            raise SimulatedCrash()
        return original(**kwargs)

    monkeypatch.setattr(orchestrator.repository, "update_share_status", crash_on_last_stored)
    record = crash_during_store(orchestrator, "status-lag")
    monkeypatch.undo()
    assert share_statuses(orchestrator, record)[3] != "STORED"

    orchestrator.reconcile_pending()

    assert orchestrator.repository.get_record(record["record_id"])["status"] == "AVAILABLE"
    assert orchestrator.recover_secret(record["record_id"], "demo-owner") == "status-lag"


def test_inconsistent_share_set_is_not_promoted(tmp_path, monkeypatch):
    orchestrator, adapters = build_stack(tmp_path)
    original = orchestrator.repository.set_record_status

    def crash_on_available(**kwargs):
        if kwargs["status"] == "AVAILABLE":
            raise SimulatedCrash()
        return original(**kwargs)

    monkeypatch.setattr(orchestrator.repository, "set_record_status", crash_on_available)
    record = crash_during_store(orchestrator, "original-secret")
    monkeypatch.undo()
    # Share 3 is validly encrypted for this record but belongs to a different split.
    other_share_3 = SSSService().split_secret("a-different-secret")[2]
    forge_share(orchestrator, adapters, record["record_id"], 3, other_share_3.to_bytes())

    orchestrator.reconcile_pending()

    assert orchestrator.repository.get_record(record["record_id"])["status"] == "FAILED"
    assert remote_object_count(adapters) == 0


def test_unknown_write_outcome_is_read_back_then_cleaned_up_not_promoted(tmp_path):
    orchestrator, adapters = build_stack(tmp_path)
    adapters["firebase"].crash_on_put = True
    record = crash_during_store(orchestrator)
    adapters["firebase"].crash_on_put = False
    adapters["upstash"].fail_reads = True  # existing object cannot be verified

    summary = orchestrator.reconcile_pending()

    assert summary["available"] == 0
    assert orchestrator.repository.get_record(record["record_id"])["status"] == "FAILED"
    assert remote_object_count(adapters) == 0


def test_failed_deletion_leaves_cleanup_required_until_a_later_restart_confirms(tmp_path):
    orchestrator, adapters = build_stack(tmp_path)
    adapters["firebase"].crash_on_put = True
    record = crash_during_store(orchestrator)
    adapters["upstash"].fail_deletes = True

    first = orchestrator.reconcile_pending()

    persisted = orchestrator.repository.get_record(record["record_id"])
    assert first["cleanup_required"] == 1
    assert persisted["status"] == "CLEANUP_REQUIRED"
    assert persisted["last_error_code"] == "CLEANUP_UNCONFIRMED"
    statuses = share_statuses(orchestrator, record)
    assert statuses[1] == "DELETE_PENDING" and statuses[2] == "DELETE_CONFIRMED"
    assert len(adapters["upstash"].data) == 1  # still remote, still tracked

    adapters["upstash"].fail_deletes = False
    second = orchestrator.reconcile_pending()

    assert second["failed"] == 1
    assert orchestrator.repository.get_record(record["record_id"])["status"] == "FAILED"
    assert remote_object_count(adapters) == 0


def test_cleanup_required_record_is_never_promoted_even_if_every_share_still_verifies(tmp_path):
    orchestrator, adapters = build_stack(tmp_path)
    stored = orchestrator.store_secret("demo-owner", "kept-secret")
    orchestrator.repository.set_record_status(record_id=stored["record_id"], status="CLEANUP_REQUIRED")
    for adapter in adapters.values():
        adapter.fail_deletes = True

    orchestrator.reconcile_pending()

    assert orchestrator.repository.get_record(stored["record_id"])["status"] == "CLEANUP_REQUIRED"


def test_repeated_restarts_are_idempotent(tmp_path, monkeypatch):
    orchestrator, adapters = build_stack(tmp_path)
    original = orchestrator.repository.set_record_status

    def crash_on_available(**kwargs):
        if kwargs["status"] == "AVAILABLE":
            raise SimulatedCrash()
        return original(**kwargs)

    monkeypatch.setattr(orchestrator.repository, "set_record_status", crash_on_available)
    record = crash_during_store(orchestrator, "stable")
    monkeypatch.undo()

    first = orchestrator.reconcile_pending()
    second = orchestrator.reconcile_pending()
    third = orchestrator.reconcile_pending()

    assert first["available"] == 1
    assert second["examined"] == third["examined"] == 0
    assert orchestrator.repository.get_record(record["record_id"])["status"] == "AVAILABLE"
    assert orchestrator.recover_secret(record["record_id"], "demo-owner") == "stable"
    assert remote_object_count(adapters) == 3


def test_available_and_failed_records_are_left_alone(tmp_path):
    orchestrator, adapters = build_stack(tmp_path)
    good = orchestrator.store_secret("demo-owner", "good")
    before = remote_object_count(adapters)

    summary = orchestrator.reconcile_pending()

    assert summary["examined"] == 0
    assert remote_object_count(adapters) == before == 3
    assert orchestrator.repository.get_record(good["record_id"])["status"] == "AVAILABLE"


def test_one_unresolvable_record_does_not_block_the_rest(tmp_path, monkeypatch):
    orchestrator, adapters = build_stack(tmp_path)
    adapters["neon"].crash_on_put = True
    first = crash_during_store(orchestrator)
    adapters["neon"].crash_on_put = False
    second_record = orchestrator.create_record("demo-owner")
    real_rollback = orchestrator.rollback

    def flaky_rollback(record_id):
        if record_id == first["record_id"]:
            raise RuntimeError("simulated metadata failure")
        return real_rollback(record_id)

    monkeypatch.setattr(orchestrator, "rollback", flaky_rollback)

    summary = orchestrator.reconcile_pending()

    assert summary["unresolved"] == 1 and summary["failed"] == 1
    assert orchestrator.repository.get_record(second_record["record_id"])["status"] == "FAILED"
