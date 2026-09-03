from __future__ import annotations

from pathlib import Path

from chiplog.platform.read_ledger import BrokerReadLedger, BrokerReadState, ReadOperation


def _state() -> BrokerReadState:
    return BrokerReadState(
        tenant_id="tenant-1",
        broker_epoch=1,
        credential_session_head="credential-session-1",
        endpoint_channel_head="endpoint-1",
        file_wal_observation="file-1",
        journal_head="journal-1",
        materialization_commitment="commitment-1",
        owner_generation="generation-1",
        owner_draining=False,
        principal_contour_head="contour-1",
        storage_mutation_generation=0,
        trust_transition_head="trust-1",
        authority_surface_digest="surface-1",
        amr_fingerprint="amr-1",
    )


def _operation(attempt: str = "attempt-1") -> ReadOperation:
    return ReadOperation(
        variant="PLANNING_PUBLICATIONS",
        request_id="request-1",
        read_attempt_id=attempt,
        tenant_id="tenant-1",
        broker_epoch=1,
        owner_id="projections",
        generation_id="generation-1",
        session_id="session-1",
        capability_id="planning.read",
        target_id="planning-store",
        max_rows=10,
        response_slot_id=f"slot-{attempt}",
    )


def _ledger(path: Path) -> tuple[BrokerReadLedger, BrokerReadState]:
    ledger = BrokerReadLedger(path)
    state = _state()
    ledger.publish_initial_state(state)
    return ledger, state


def test_release_winner_enqueues_exact_bytes_once_and_replay_is_disposition_only(
    tmp_path: Path,
) -> None:
    ledger, state = _ledger(tmp_path / "read.sqlite3")
    operation = _operation()
    ledger.begin(operation, state)
    released = ledger.release(operation, state, "proof-1", b"result")
    assert released.state == "RELEASED" and released.enqueued
    assert ledger.dequeue(operation) == b"result"
    assert ledger.dequeue(operation) is None
    replay = ledger.release(operation, state, "proof-1", b"result")
    assert replay.state == "RELEASED" and replay.dequeued
    assert ledger.dequeue(operation) is None


def test_invalidator_winner_suppresses_result_bytes(tmp_path: Path) -> None:
    ledger, state = _ledger(tmp_path / "read.sqlite3")
    operation = _operation()
    ledger.begin(operation, state)
    ledger.start_owner_drain("tenant-1", state.fingerprint())
    stale = ledger.release(operation, state, "proof-1", b"must-not-cross")
    assert stale.state == "STALE_OR_INDETERMINATE_READ"
    assert not stale.enqueued
    assert ledger.dequeue(operation) is None


def test_fresh_attempt_after_invalidation_uses_new_state_and_identity(tmp_path: Path) -> None:
    ledger, state = _ledger(tmp_path / "read.sqlite3")
    old = _operation()
    ledger.begin(old, state)
    successor = ledger.invalidate_owner_generation("tenant-1", state.fingerprint(), "generation-2")
    assert ledger.release(old, state, "proof-old", b"old").state == ("STALE_OR_INDETERMINATE_READ")
    fresh = _operation("attempt-2").model_copy(
        update={"generation_id": "generation-2", "response_slot_id": "slot-attempt-2"}
    )
    ledger.begin(fresh, successor)
    assert ledger.release(fresh, successor, "proof-new", b"new").state == "RELEASED"
    assert ledger.dequeue(fresh) == b"new"
