"""Focused guards for private claim-source capture identity and lease freshness."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from chiplog.capabilities.agent_loop.delivery_work_contracts import (
    DeliveryWorkSourceV1,
    prepare_delivery_work_genesis,
)
from chiplog.composition.h1_delivery_work_claim_sources import (
    H1DeliveryWorkClaimSourceHold,
    H1DeliveryWorkClaimSources,
    _exact_owner_member_count,
    _H1DeliveryWorkClaimCapture,
)
from chiplog.platform._owner_publication_contracts import (
    DeliveryWorkGenesisBatchV1,
    OwnerRecordBytes,
)
from chiplog.platform.broker import BrokerSession
from chiplog.platform.workspace_snapshot import workspace_snapshot


def _capture(*, session: BrokerSession | None = None, epoch: str = "epoch", now: int = 10):
    source = DeliveryWorkSourceV1.model_validate(
        {
            "tenant_id": "tenant",
            "intent_id": "intent",
            "selected_decision_id": "v4",
            "selected_decision_fingerprint": "a" * 64,
            "terminal_run": {
                "owner": "agent_loop",
                "record_kind": "RUN",
                "record_id": "run",
                "schema_id": "run.v1",
                "canonical_record_bytes": b"run",
                "fingerprint": hashlib.sha256(b"run").hexdigest(),
            },
            "terminal_manifest": {
                "owner": "agent_loop",
                "record_kind": "MANIFEST",
                "record_id": "manifest",
                "schema_id": "manifest.v1",
                "canonical_record_bytes": b"manifest",
                "fingerprint": hashlib.sha256(b"manifest").hexdigest(),
            },
            "intent": {
                "owner": "effects",
                "record_kind": "INTENT",
                "record_id": "intent",
                "schema_id": "intent.v1",
                "canonical_record_bytes": b"intent",
                "fingerprint": hashlib.sha256(b"intent").hexdigest(),
            },
        }
    )
    g0 = prepare_delivery_work_genesis(source)
    session = session or BrokerSession(
        tenant_id="tenant",
        broker_epoch=1,
        generation_id="gen",
        owner_id="agent_loop",
        session_id="session",
    )
    return _H1DeliveryWorkClaimCapture(
        source, "g0-decision", "b" * 64, g0, g0.head, session, epoch, now, now + 60_000_000_000
    )


def _reader_with(fresh: _H1DeliveryWorkClaimCapture) -> H1DeliveryWorkClaimSources:
    reader = object.__new__(H1DeliveryWorkClaimSources)
    reader._issued = {}
    reader._closed = False
    reader._runtime = SimpleNamespace(
        _authority_gate=lambda: SimpleNamespace(hold=lambda: __import__("contextlib").nullcontext())
    )
    reader._capture_held = lambda _: fresh
    return reader


def test_selected_source_member_matches_owner_bytes_across_wire_types() -> None:
    member = _capture().source.terminal_run
    row = OwnerRecordBytes(
        owner=member.owner,
        record_kind=member.record_kind,
        record_id=member.record_id,
        schema_id=member.schema_id,
        canonical_bytes=member.canonical_record_bytes,
        fingerprint=member.fingerprint,
    )
    assert _exact_owner_member_count(member, (row,)) == 1
    assert _exact_owner_member_count(member, (row, row)) == 2
    assert _exact_owner_member_count(member, (row.model_copy(update={"record_id": "other"}),)) == 0


def test_g0_read_joins_active_workspace_snapshot(tmp_path: Path) -> None:
    capture = _capture()
    source, state = capture.source, capture.g0
    record_bytes = state.canonical_bytes()
    fingerprint = hashlib.sha256(record_bytes).hexdigest()
    request = DeliveryWorkGenesisBatchV1.model_construct(
        identity=SimpleNamespace(
            tenant_id=source.tenant_id,
            command_id="genesis",
            command_fingerprint="f" * 64,
        ),
        operation="agent_loop.delivery_work.genesis.v1",
        expected=SimpleNamespace(tenant_frontier=0),
        complete_records=(
            OwnerRecordBytes(
                owner="agent_loop",
                record_kind="DELIVERY_WORK_STATE",
                record_id=state.record_id,
                schema_id=state.schema_id,
                canonical_bytes=record_bytes,
                fingerprint=fingerprint,
            ),
        ),
    )
    decision = SimpleNamespace(prepared=SimpleNamespace(request=request), tenant_commit_sequence=1)
    database = tmp_path / "work.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE records (tenant_id TEXT, record_id TEXT, owner TEXT, "
            "schema_id TEXT, canonical_bytes BLOB, commit_sequence INTEGER)"
        )
        connection.execute(
            "CREATE TABLE publications (tenant_id TEXT, operation_kind TEXT, "
            "idempotency_key TEXT, request_fingerprint TEXT, "
            "commit_sequence INTEGER, record_ids TEXT)"
        )
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)",
            (source.tenant_id, state.record_id, "agent_loop", state.schema_id, record_bytes, 1),
        )
        connection.execute(
            "INSERT INTO publications VALUES (?, ?, ?, ?, ?, ?)",
            (source.tenant_id, request.operation, "genesis", "f" * 64, 1, state.record_id),
        )
    reader = object.__new__(H1DeliveryWorkClaimSources)
    reader._runtime = SimpleNamespace(_database=database)
    with workspace_snapshot(database):
        assert reader._read_exact_g0(decision, source) == state


def test_recheck_rejects_unissued_capture() -> None:
    reader = _reader_with(_capture())
    with pytest.raises(H1DeliveryWorkClaimSourceHold, match="not reader-issued"):
        reader.recheck(_capture())


@pytest.mark.parametrize("field", ("session", "clock_epoch"))
def test_recheck_rejects_changed_session_or_epoch(field: str) -> None:
    original = _capture()
    fresh = _capture(
        session=BrokerSession(
            tenant_id="tenant",
            broker_epoch=2,
            generation_id="gen",
            owner_id="agent_loop",
            session_id="session",
        )
        if field == "session"
        else None,
        epoch="changed" if field == "clock_epoch" else "epoch",
    )
    reader = _reader_with(fresh)
    reader._issued[id(original)] = original
    with pytest.raises(H1DeliveryWorkClaimSourceHold, match="no longer current"):
        reader.recheck(original)


def test_recheck_rejects_expired_original_lease() -> None:
    original = _capture(now=10)
    fresh = _capture(now=original.expires_at_ns)
    reader = _reader_with(fresh)
    reader._issued[id(original)] = original
    with pytest.raises(H1DeliveryWorkClaimSourceHold, match="no longer current"):
        reader.recheck(original)


def test_recheck_keeps_original_lease_terms() -> None:
    original = _capture(now=10)
    reader = _reader_with(_capture(now=11))
    reader._issued[id(original)] = original

    assert reader.recheck(original) is None
    assert (original.issued_at_ns, original.expires_at_ns) == (10, 60_000_000_010)


def test_missing_materialized_marker_holds_the_selected_g0() -> None:
    source = _capture().source
    request = DeliveryWorkGenesisBatchV1.model_construct(
        selected_source=source,
        selected_decision_id=source.selected_decision_id,
        selected_decision_fingerprint=source.selected_decision_fingerprint,
        identity=SimpleNamespace(command_id="g0-command"),
    )
    candidate = SimpleNamespace(prepared=SimpleNamespace(request=request))
    reader = object.__new__(H1DeliveryWorkClaimSources)
    reader._runtime = SimpleNamespace(
        _owner_decisions=lambda: SimpleNamespace(
            snapshot=lambda: SimpleNamespace(
                decisions=(candidate,), materialized_command_ids=frozenset()
            )
        )
    )

    with pytest.raises(H1DeliveryWorkClaimSourceHold, match="selected and materialized"):
        reader._find_materialized_genesis(
            SimpleNamespace(
                decision_id=source.selected_decision_id,
                decision_fingerprint=source.selected_decision_fingerprint,
            ),
            source.intent_id,
        )


@pytest.mark.parametrize("mutation", ("g0", "g1", "marker"))
def test_recheck_rejects_changed_materialized_selection(mutation: str) -> None:
    original = _capture()
    fresh = _capture()
    if mutation == "g0":
        fresh = replace(fresh, g0_head="changed-head")
    elif mutation == "marker":
        fresh = replace(fresh, genesis_decision_id="other")
    else:
        # g1 is detected by the real held reader before it can return a capture.
        reader = _reader_with(fresh)
        reader._issued[id(original)] = original
        reader._capture_held = lambda _: (_ for _ in ()).throw(
            H1DeliveryWorkClaimSourceHold("delivery-work g1 is already present")
        )
        with pytest.raises(H1DeliveryWorkClaimSourceHold, match="g1"):
            reader.recheck(original)
        return
    reader = _reader_with(fresh)
    reader._issued[id(original)] = original
    with pytest.raises(H1DeliveryWorkClaimSourceHold, match="no longer current"):
        reader.recheck(original)
