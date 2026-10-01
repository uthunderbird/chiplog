"""Private registration and fresh/replay boundaries for the g1 authority."""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from types import SimpleNamespace

import pytest

from chiplog.capabilities.agent_loop.delivery_work_contracts import (
    DELIVERY_WORK_CLAIM_COMMAND_SCHEMA,
    DELIVERY_WORK_OWNER,
    DELIVERY_WORK_SCHEMA,
    DeliveryWorkSourceMemberV1,
    DeliveryWorkSourceV1,
    PrepareDeliveryWorkClaimV1,
    prepare_delivery_work_claim,
    prepare_delivery_work_claim_projection,
    prepare_delivery_work_genesis,
)
from chiplog.composition.delivery_work_claim_authority import DeliveryWorkClaimAuthority
from chiplog.composition.h1_delivery_work_claim_sources import _H1DeliveryWorkClaimCapture
from chiplog.composition.h1_delivery_work_current_claim_reader import (
    H1DeliveryWorkCurrentClaimHold,
    H1DeliveryWorkCurrentClaimReader,
)
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    DeliveryWorkClaimBatchV1,
    DeliveryWorkGenesisBatchV1,
    ExactReplayQuery,
    InvocationProofRef,
    OwnerCommandBytes,
    OwnerRecordBytes,
    PublicationIdentity,
    WorkerAuthentication,
)
from chiplog.platform._sqlite import (
    PhysicalPublicationCommand,
    PhysicalRecord,
    StoreAdmissionError,
)
from chiplog.platform.broker import BrokerSession
from chiplog.platform.owner_publications import PublicationRejected


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _source() -> DeliveryWorkSourceV1:
    def member(owner: str, kind: str, record_id: str) -> DeliveryWorkSourceMemberV1:
        raw = record_id.encode()
        return DeliveryWorkSourceMemberV1(
            owner=owner,
            record_kind=kind,
            record_id=record_id,
            schema_id="test." + kind + ".v1",
            canonical_record_bytes=raw,
            fingerprint=_digest(raw),
        )

    return DeliveryWorkSourceV1(
        tenant_id="tenant",
        intent_id="intent",
        selected_decision_id="v4",
        selected_decision_fingerprint=_digest(b"v4"),
        terminal_run=member("agent_loop", "RUN", "run"),
        terminal_manifest=member("agent_loop", "MANIFEST", "manifest"),
        intent=member("effects", "INTENT", "intent"),
    )


def _capture() -> _H1DeliveryWorkClaimCapture:
    source = _source()
    return _H1DeliveryWorkClaimCapture(
        source,
        "g0-decision",
        _digest(b"g0-decision"),
        prepare_delivery_work_genesis(source),
        prepare_delivery_work_genesis(source).head,
        BrokerSession(
            tenant_id="tenant",
            broker_epoch=7,
            generation_id="generation",
            owner_id="holder",
            session_id="session",
        ),
        "clock-epoch",
        10,
        20,
    )


def _batch(
    capture: _H1DeliveryWorkClaimCapture, command_id: str = "claim"
) -> DeliveryWorkClaimBatchV1:
    projection = prepare_delivery_work_claim_projection(capture.source, capture.g0)
    command = PrepareDeliveryWorkClaimV1(projection=projection)
    command_bytes = command.canonical_bytes()
    record = prepare_delivery_work_claim(
        capture.source,
        capture.g0,
        holder_id=capture.session.owner_id,
        session_id=capture.session.session_id,
        trusted_clock_ns=capture.issued_at_ns,
        lease_expires_at_ns=capture.expires_at_ns,
    )
    return DeliveryWorkClaimBatchV1(
        identity=PublicationIdentity(
            tenant_id="tenant",
            command_id=command_id,
            command_fingerprint=_digest(command_bytes),
            canonicalization_version="chiplog.owner-publication.v1",
        ),
        authentication=WorkerAuthentication(
            invocation=InvocationProofRef(
                issuance_id="issue",
                issuance_fingerprint=_digest(b"issue"),
                broker_epoch="broker",
                broker_session="session",
                runtime_generation="generation",
                operation_subject=command_id,
            ),
            applicability_schema="test.claim.v1",
            applicability_bytes=b"applicability",
            applicability_fingerprint=_digest(b"applicability"),
        ),
        expected=AuthoritativeReadManifest(
            tenant_id="tenant",
            tenant_frontier=1,
            expected_materialization_commitment=_digest(b"before"),
            registry_head="registry",
            registry_fingerprint=_digest(b"registry"),
            ordered_heads=(),
            complete_manifest_fingerprint=_digest(b"manifest"),
        ),
        claim_command=OwnerCommandBytes(
            owner=DELIVERY_WORK_OWNER,
            schema_id=DELIVERY_WORK_CLAIM_COMMAND_SCHEMA,
            canonical_bytes=command_bytes,
            fingerprint=_digest(command_bytes),
        ),
        complete_records=(
            OwnerRecordBytes(
                owner=DELIVERY_WORK_OWNER,
                record_kind="DELIVERY_WORK_STATE",
                record_id=record.record_id,
                schema_id=DELIVERY_WORK_SCHEMA,
                canonical_bytes=record.canonical_bytes(),
                fingerprint=_digest(record.canonical_bytes()),
            ),
        ),
        complete_batch_fingerprint=_digest(b"complete"),
    )


def _authority(recheck) -> DeliveryWorkClaimAuthority:
    authority = DeliveryWorkClaimAuthority()
    authority._sources = SimpleNamespace(recheck=recheck)
    return authority


def _current_reader(
    *, candidates: tuple[object, ...], materialized: frozenset[str]
) -> H1DeliveryWorkCurrentClaimReader:
    reader = object.__new__(H1DeliveryWorkCurrentClaimReader)
    reader._closed = False
    reader._sources = object()
    reader._runtime = SimpleNamespace(
        _owner_decisions=lambda: SimpleNamespace(
            snapshot=lambda: SimpleNamespace(
                decisions=candidates, materialized_command_ids=materialized
            )
        )
    )
    return reader


def _selected_claim_candidate(
    batch: DeliveryWorkClaimBatchV1, capture: _H1DeliveryWorkClaimCapture
) -> SimpleNamespace:
    binding = DeliveryWorkClaimAuthority()._binding_from_capture(batch, capture)
    return SimpleNamespace(
        decision_id="g1-decision",
        decision_fingerprint=_digest(b"g1-decision"),
        prepared=SimpleNamespace(request=batch, delivery_work_claim_binding=binding),
    )


def test_current_reader_requires_selected_materialized_g1_marker() -> None:
    capture = _capture()
    batch = _batch(capture)
    candidate = _selected_claim_candidate(batch, capture)
    reader = _current_reader(candidates=(candidate,), materialized=frozenset())
    genesis = SimpleNamespace(
        decision_id=capture.genesis_decision_id,
        decision_fingerprint=capture.genesis_decision_fingerprint,
    )

    with pytest.raises(H1DeliveryWorkCurrentClaimHold, match="chain differs"):
        reader._selected_claim(capture.source.intent_id, capture.source, genesis, capture.g0)


def test_current_reader_rejects_selected_binding_that_differs_from_g0() -> None:
    capture = _capture()
    batch = _batch(capture)
    candidate = _selected_claim_candidate(batch, capture)
    original_binding = candidate.prepared.delivery_work_claim_binding
    candidate.prepared.delivery_work_claim_binding = original_binding.model_copy(
        update={"genesis_decision_id": "other"}
    )
    reader = _current_reader(
        candidates=(candidate,), materialized=frozenset({batch.identity.command_id})
    )
    genesis = SimpleNamespace(
        decision_id=capture.genesis_decision_id,
        decision_fingerprint=capture.genesis_decision_fingerprint,
    )

    with pytest.raises(H1DeliveryWorkCurrentClaimHold, match="chain differs"):
        reader._selected_claim(capture.source.intent_id, capture.source, genesis, capture.g0)


def test_current_reader_rejects_physical_g1_mutant() -> None:
    capture = _capture()
    batch = _batch(capture)
    binding = DeliveryWorkClaimAuthority()._binding_from_capture(batch, capture)
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE publications (
            tenant_id TEXT, operation_kind TEXT, idempotency_key TEXT,
            request_fingerprint TEXT, commit_sequence INTEGER, record_ids TEXT
        );
        CREATE TABLE records (
            tenant_id TEXT, record_id TEXT, owner TEXT, schema_id TEXT,
            canonical_bytes BLOB, commit_sequence INTEGER
        );
        """
    )
    record = batch.complete_records[0]
    connection.execute(
        "INSERT INTO publications VALUES (?,?,?,?,?,?)",
        (
            batch.identity.tenant_id,
            batch.operation,
            batch.identity.command_id,
            batch.identity.command_fingerprint,
            2,
            record.record_id,
        ),
    )
    connection.execute(
        "INSERT INTO records VALUES (?,?,?,?,?,?)",
        (batch.identity.tenant_id, record.record_id, record.owner, record.schema_id, b"mutant", 2),
    )
    connection.commit()
    connection.execute("BEGIN")
    reader = object.__new__(H1DeliveryWorkCurrentClaimReader)

    with pytest.raises(H1DeliveryWorkCurrentClaimHold, match="not physical"):
        reader._read_exact_g1(
            connection, batch, SimpleNamespace(tenant_commit_sequence=2), binding
        )


@pytest.mark.parametrize("now_ns", [9, 20])
def test_current_reader_rejects_clock_outside_half_open_lease(now_ns: int) -> None:
    capture = _capture()
    batch = _batch(capture)
    binding = DeliveryWorkClaimAuthority()._binding_from_capture(batch, capture)
    runtime = SimpleNamespace(
        _supervisor=SimpleNamespace(
            runtime=lambda: SimpleNamespace(session=lambda _: capture.session)
        ),
        _require_dispatch_resources=lambda: SimpleNamespace(clock=lambda: ("clock-epoch", now_ns)),
    )

    with pytest.raises(H1DeliveryWorkCurrentClaimHold, match="current broker session or clock"):
        H1DeliveryWorkCurrentClaimReader._require_current(runtime, "tenant", binding)


def test_current_reader_rejects_current_session_identity_mismatch_and_close() -> None:
    capture = _capture()
    batch = _batch(capture)
    binding = DeliveryWorkClaimAuthority()._binding_from_capture(batch, capture)
    wrong = capture.session.model_copy(update={"session_id": "other"})
    runtime = SimpleNamespace(
        _supervisor=SimpleNamespace(runtime=lambda: SimpleNamespace(session=lambda _: wrong)),
        _require_dispatch_resources=lambda: SimpleNamespace(clock=lambda: ("clock-epoch", 10)),
    )
    with pytest.raises(H1DeliveryWorkCurrentClaimHold, match="current broker session or clock"):
        H1DeliveryWorkCurrentClaimReader._require_current(runtime, "tenant", binding)

    reader = object.__new__(H1DeliveryWorkCurrentClaimReader)
    reader._closed = True
    reader._runtime = None
    reader._sources = None
    with pytest.raises(H1DeliveryWorkCurrentClaimHold, match="closed"):
        reader._require_open()


def test_register_consumes_capture_for_one_exact_batch() -> None:
    capture = _capture()
    batch = _batch(capture)
    authority = _authority(lambda _: None)

    authority.register(batch, capture)
    authority.register(batch, capture)

    with pytest.raises(RuntimeError, match="already bound"):
        authority.register(_batch(capture, "different-command"), capture)


def test_prepare_rechecks_then_derives_all_private_claimant_fields() -> None:
    capture = _capture()
    batch = _batch(capture)
    calls: list[object] = []
    authority = _authority(lambda observed: calls.append(observed))
    authority.register(batch, capture)

    prepared = asyncio.run(authority.prepare(batch))

    assert not isinstance(prepared, PublicationRejected)
    assert calls == [capture]
    binding = prepared.delivery_work_claim_binding
    assert binding is not None
    assert binding.claimant_clock.model_dump() == {
        "holder_id": "holder",
        "session_id": "session",
        "broker_epoch": "7",
        "runtime_generation": "generation",
        "clock_epoch": "clock-epoch",
        "lease_expires_at_ns": 20,
        "trusted_clock_ns": 10,
    }


def test_prepare_rejects_unregistered_or_stale_capture() -> None:
    capture = _capture()
    batch = _batch(capture)
    authority = _authority(lambda _: None)

    assert isinstance(asyncio.run(authority.prepare(batch)), PublicationRejected)

    authority.register(batch, capture)
    authority._sources = SimpleNamespace(
        recheck=lambda _: (_ for _ in ()).throw(RuntimeError("stale"))
    )
    assert isinstance(asyncio.run(authority.prepare(batch)), PublicationRejected)


@pytest.mark.parametrize("mutation", ("session", "clock", "expiry"))
def test_check_prepared_rejects_rotated_session_or_expired_clock(mutation: str) -> None:
    capture = _capture()
    batch = _batch(capture)
    authority = _authority(lambda _: None)
    authority.register(batch, capture)
    prepared = asyncio.run(authority.prepare(batch))
    assert not isinstance(prepared, PublicationRejected)
    session = (
        capture.session.model_copy(update={"session_id": "rotated"})
        if mutation == "session"
        else capture.session
    )
    clock = (
        ("rotated-clock", 10)
        if mutation == "clock"
        else ("clock-epoch", 20 if mutation == "expiry" else 10)
    )
    authority._runtime = SimpleNamespace(
        _supervisor=SimpleNamespace(
            runtime=lambda: SimpleNamespace(session=lambda _: session)
        ),
        _require_dispatch_resources=lambda: SimpleNamespace(clock=lambda: clock),
    )

    assert authority.check_prepared(prepared) == "STALE"


def test_replay_authentication_never_touches_current_capture_or_clock() -> None:
    authority = DeliveryWorkClaimAuthority()
    query = ExactReplayQuery.model_construct(
        operation="agent_loop.delivery_work.claim.v1",
        identity=SimpleNamespace(tenant_id="tenant", command_id="claim"),
    )

    assert authority.authenticate_replay(query) is None


def test_preselect_requires_exact_provisional_g1_at_the_writer_sequence() -> None:
    capture = _capture()
    batch = _batch(capture)
    authority = _authority(lambda _: None)
    authority.register(batch, capture)
    genesis = SimpleNamespace(
        decision_id=capture.genesis_decision_id,
        decision_fingerprint=capture.genesis_decision_fingerprint,
        prepared=SimpleNamespace(request=DeliveryWorkGenesisBatchV1.model_construct()),
    )
    authority._runtime = SimpleNamespace(
        _owner_decisions=lambda: SimpleNamespace(
            snapshot=lambda: SimpleNamespace(decisions=(genesis,))
        )
    )
    authority._exact_g0 = lambda *_: True
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE records (
            tenant_id TEXT, record_id TEXT, owner TEXT, schema_id TEXT,
            canonical_bytes BLOB, commit_sequence INTEGER
        );
        CREATE TABLE publications (
            tenant_id TEXT, operation_kind TEXT, idempotency_key TEXT,
            request_fingerprint TEXT, commit_sequence INTEGER, record_ids TEXT
        );
        """
    )
    record = batch.complete_records[0]
    sequence = batch.expected.tenant_frontier + 1
    binding = authority._registrations[id(capture)].binding
    connection.execute("BEGIN")
    try:
        assert authority._unselected_state(batch, binding, connection) == "ABSENT"
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)",
            (
                "tenant",
                record.record_id,
                record.owner,
                record.schema_id,
                record.canonical_bytes,
                sequence,
            ),
        )
        connection.execute(
            "INSERT INTO publications VALUES (?, ?, ?, ?, ?, ?)",
            (
                "tenant",
                batch.operation,
                "claim",
                batch.identity.command_fingerprint,
                sequence,
                record.record_id,
            ),
        )
        assert authority._unselected_state(batch, binding, connection) == "COMPLETE"
    finally:
        connection.rollback()


def test_preselect_rechecks_claimant_after_initial_admission() -> None:
    capture = _capture()
    batch = _batch(capture)
    authority = _authority(lambda _: None)
    authority.register(batch, capture)
    prepared = asyncio.run(authority.prepare(batch))
    assert not isinstance(prepared, PublicationRejected)
    authority._runtime = SimpleNamespace(
        _owner_decisions=lambda: SimpleNamespace(lookup=lambda *_: None)
    )
    authority._unselected_state = lambda *_: "COMPLETE"
    record = batch.complete_records[0]
    command = PhysicalPublicationCommand(
        tenant_id=batch.identity.tenant_id,
        operation_kind=batch.operation,
        idempotency_key=batch.identity.command_id,
        request_fingerprint=batch.identity.command_fingerprint,
        expected_head=batch.expected.tenant_frontier,
        fence_generation=prepared.fence_generation,
        expected_fence_frontier=prepared.fence_frontier,
        minimum_fence_frontier=prepared.fence_frontier,
        records=(
            PhysicalRecord(
                record.record_id,
                record.owner,
                record.schema_id,
                record.canonical_bytes,
                record.fingerprint,
            ),
        ),
    )
    connection = sqlite3.connect(":memory:")
    authority._current_claimant_clock_matches = lambda _: False
    with pytest.raises(StoreAdmissionError, match="expired before selection"):
        authority.verify(command, "PRESELECT", connection)
    authority._current_claimant_clock_matches = lambda _: True
    assert authority.verify(command, "PRESELECT", connection).selected_identity is None


def test_selected_recovery_replay_does_not_require_a_live_claimant_clock() -> None:
    capture = _capture()
    batch = _batch(capture)
    binding = DeliveryWorkClaimAuthority()._binding_from_capture(batch, capture)
    selected = SimpleNamespace(
        decision_id="selected-g1",
        decision_fingerprint=_digest(b"selected-g1"),
        tenant_commit_sequence=2,
        resulting_commitment=_digest(b"result"),
        prepared=SimpleNamespace(request=batch, delivery_work_claim_binding=binding),
    )
    authority = DeliveryWorkClaimAuthority()
    authority._runtime = SimpleNamespace(
        _owner_decisions=lambda: SimpleNamespace(lookup=lambda *_: selected)
    )
    authority._current_claimant_clock_matches = lambda _: (_ for _ in ()).throw(
        AssertionError("historical selected recovery read the live claimant clock")
    )
    authority._state_on_connection = lambda *_: "COMPLETE"
    record = batch.complete_records[0]
    command = PhysicalPublicationCommand(
        tenant_id=batch.identity.tenant_id,
        operation_kind=batch.operation,
        idempotency_key=batch.identity.command_id,
        request_fingerprint=batch.identity.command_fingerprint,
        expected_head=batch.expected.tenant_frontier,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=(
            PhysicalRecord(
                record.record_id,
                record.owner,
                record.schema_id,
                record.canonical_bytes,
                record.fingerprint,
            ),
        ),
    )

    verified = authority.verify(command, "REPLAY", sqlite3.connect(":memory:"))

    assert verified.selected_identity == selected.decision_id
