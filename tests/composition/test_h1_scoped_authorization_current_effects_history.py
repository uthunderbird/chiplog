"""Focused lifecycle checks for the inert V4/g1 current Effects history source."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest


class _Gate:
    held = False

    @contextmanager
    def hold(self):
        self.held = True
        try:
            yield
        finally:
            self.held = False

    def require_held(self) -> None:
        if not self.held:
            raise RuntimeError("gate is not held")


class _Runtime:
    def __init__(self) -> None:
        self._gate = _Gate()

    def _authority_gate(self) -> _Gate:
        return self._gate


def _installed(monkeypatch: pytest.MonkeyPatch):
    import chiplog.composition.h1_scoped_authorization_current_effects_history as subject

    runtime = _Runtime()
    joined_capture = object.__new__(subject.H1ScopedAuthorizationDeliveryWorkCaptureV1)

    class _Joined:
        def __init__(self, installed_runtime: object) -> None:
            self._runtime = installed_runtime
            self.changed = False

        def recheck_held(self, capture: object) -> object:
            runtime._authority_gate().require_held()
            if self.changed:
                raise subject.H1ScopedAuthorizationDeliveryWorkHold("changed")
            assert capture is joined_capture
            return capture

    class _Clock:
        def __init__(self, installed_runtime: object) -> None:
            self._runtime = installed_runtime
            self.released: list[object] = []

        def read(self, capture: object) -> object:
            assert capture is joined_capture
            return object()

        def release(self, capture: object) -> None:
            self.released.append(capture)

    joined, clock = _Joined(runtime), _Clock(runtime)
    runtime._h1_scoped_authorization_delivery_work_reader_v1 = joined
    runtime._h1_scoped_authorization_clock_reader_v1 = clock
    monkeypatch.setattr(subject, "CommonCliExecutionRuntime", _Runtime)
    monkeypatch.setattr(subject, "H1ScopedAuthorizationDeliveryWorkReaderV1", _Joined)
    monkeypatch.setattr(subject, "H1ScopedAuthorizationClockReaderV1", _Clock)
    reader = subject.H1ScopedAuthorizationCurrentEffectsHistoryReaderV1(runtime)
    record = subject._CurrentEffectsHistoryRecord(
        joined_capture,
        object(),
        object(),
        object(),
        object(),
        object(),
        object(),
        SimpleNamespace(reader_id="inert"),
    )
    monkeypatch.setattr(
        subject.H1ScopedAuthorizationCurrentEffectsHistoryReaderV1,
        "_read_record_held",
        lambda *_: record,
    )
    return subject, runtime, joined_capture, clock, reader


def test_current_history_capture_is_reader_issued_rechecked_and_revoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, runtime, joined_capture, clock, reader = _installed(monkeypatch)

    capture = reader.read(joined_capture)
    with runtime._authority_gate().hold():
        assert reader.role_held(capture).reader_id == "inert"
    reader.release(capture)
    assert len(clock.released) == 1
    with (
        runtime._authority_gate().hold(),
        pytest.raises(
            subject.H1ScopedAuthorizationCurrentEffectsHistoryHold, match="not reader-issued"
        ),
    ):
        reader.role_held(capture)


def test_current_history_capture_rejects_forgery_and_close_revokes_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, runtime, joined_capture, clock, reader = _installed(monkeypatch)

    forged = object.__new__(subject.H1ScopedAuthorizationCurrentEffectsHistoryCaptureV1)
    with (
        runtime._authority_gate().hold(),
        pytest.raises(
            subject.H1ScopedAuthorizationCurrentEffectsHistoryHold, match="not reader-issued"
        ),
    ):
        reader.role_held(forged)
    foreign_reader = subject.H1ScopedAuthorizationCurrentEffectsHistoryReaderV1(runtime)
    foreign = foreign_reader.read(joined_capture)
    with (
        runtime._authority_gate().hold(),
        pytest.raises(
            subject.H1ScopedAuthorizationCurrentEffectsHistoryHold, match="not reader-issued"
        ),
    ):
        reader.role_held(foreign)
    foreign_reader.close()
    capture = reader.read(joined_capture)
    reader.close()
    assert len(clock.released) == 2
    with (
        runtime._authority_gate().hold(),
        pytest.raises(
            subject.H1ScopedAuthorizationCurrentEffectsHistoryHold, match="reader is closed"
        ),
    ):
        reader.role_held(capture)


def test_current_history_rejects_substituted_database_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chiplog.composition.h1_scoped_authorization_current_effects_history as subject

    record = SimpleNamespace(
        record_id="intent",
        record_kind="ExternalActionIntent",
        schema_id="schema",
        fingerprint="fingerprint",
        canonical_bytes=b"intent",
    )
    source_member = SimpleNamespace(
        record_id=record.record_id,
        record_kind=record.record_kind,
        schema_id=record.schema_id,
        fingerprint=record.fingerprint,
        canonical_record_bytes=record.canonical_bytes,
    )
    source = SimpleNamespace(tenant_id="tenant", intent_id="intent", intent=source_member)
    selected = SimpleNamespace(
        decision_id="selected",
        decision_fingerprint="selected-fingerprint",
        tenant_commit_sequence=4,
        prepared=SimpleNamespace(request=SimpleNamespace(complete_records=(record,))),
    )
    claim = SimpleNamespace(
        decision_id="claim",
        decision_fingerprint="claim-fingerprint",
        tenant_commit_sequence=4,
        resulting_commitment="commitment",
    )
    journal = SimpleNamespace(head="journal", decisions=(selected, claim))
    cut = SimpleNamespace(
        rows=(SimpleNamespace(record=record, commit_sequence=4, publication_ordinal=0),),
        tenant_id="tenant",
        owner_journal_head="journal",
        tenant_frontier=4,
        materialization_commitment="commitment",
        physical_path="/database",
        physical_device=7,
        physical_inode=9,
    )
    evidence = SimpleNamespace(
        source=source,
        current_claim=SimpleNamespace(
            claim_decision_id="claim", claim_decision_fingerprint="claim-fingerprint"
        ),
    )
    runtime = SimpleNamespace(
        _database_identity=("/database", 7, 9), _check_database_identity=lambda: None
    )
    issuance = SimpleNamespace(
        effects_result=SimpleNamespace(intent_result=SimpleNamespace(intent=object()))
    )
    monkeypatch.setattr(
        subject,
        "make_scoped_intent_member",
        lambda _: SimpleNamespace(**vars(source_member)),
    )

    assert (
        subject.H1ScopedAuthorizationCurrentEffectsHistoryReaderV1._require_joined_current_held(
            runtime, cut, journal, selected, issuance, evidence
        )
        is claim
    )
    substituted = SimpleNamespace(**(vars(cut) | {"physical_inode": 10}))
    with pytest.raises(ValueError, match="current materialized frontier differs"):
        subject.H1ScopedAuthorizationCurrentEffectsHistoryReaderV1._require_joined_current_held(
            runtime, substituted, journal, selected, issuance, evidence
        )


@pytest.mark.parametrize(
    "mutation",
    (None, "inode", "duplicate", "ordinal", "tenant", "head", "frontier", "commitment"),
)
def test_material_reader_executes_joined_r16_and_physical_binding(
    monkeypatch: pytest.MonkeyPatch, mutation: str | None
) -> None:
    """Exercise the reader path itself, without starting the owner-process fixture."""
    import chiplog.composition.h1_scoped_authorization_current_effects_history as subject

    runtime = _Runtime()
    runtime._database_identity = ("/database", 7, 9)
    runtime._check_database_identity = lambda: None
    runtime._owner_decisions = lambda: object()
    joined_capture, clock_capture = object(), object()
    record = SimpleNamespace(
        record_id="intent",
        record_kind="ExternalActionIntent",
        schema_id="schema",
        fingerprint="fingerprint",
        canonical_bytes=b"intent",
    )
    member = SimpleNamespace(
        record_id="intent",
        record_kind="ExternalActionIntent",
        schema_id="schema",
        fingerprint="fingerprint",
        canonical_record_bytes=b"intent",
    )
    source = SimpleNamespace(tenant_id="tenant", intent_id="intent", intent=member)

    class _Selected:
        def __init__(
            self, decision_id: str, fingerprint: str, sequence: int, commitment: str
        ) -> None:
            self.decision_id = decision_id
            self.decision_fingerprint = fingerprint
            self.tenant_commit_sequence = sequence
            self.resulting_commitment = commitment
            self.prepared = SimpleNamespace(request=SimpleNamespace(complete_records=(record,)))

    class _V4:
        def __init__(self) -> None:
            self.effects_result = SimpleNamespace(intent_result=SimpleNamespace(intent=object()))

    selected = _Selected("selected", "selected-fingerprint", 4, "commitment")
    claim = _Selected("claim", "claim-fingerprint", 4, "commitment")
    journal = SimpleNamespace(head="journal", decisions=(selected, claim))
    cut = SimpleNamespace(
        rows=(SimpleNamespace(record=record, commit_sequence=4, publication_ordinal=0),),
        tenant_id="tenant",
        owner_journal_head="journal",
        tenant_frontier=4,
        materialization_commitment="commitment",
        physical_path="/database",
        physical_device=7,
        physical_inode=9,
    )
    evidence = SimpleNamespace(
        source=source,
        current_claim=SimpleNamespace(
            claim_decision_id="claim", claim_decision_fingerprint="claim-fingerprint"
        ),
    )
    if mutation == "inode":
        cut.physical_inode = 10
    elif mutation == "duplicate":
        cut.rows = (*cut.rows, cut.rows[0])
    elif mutation == "ordinal":
        cut.rows = (SimpleNamespace(record=record, commit_sequence=4, publication_ordinal=1),)
    elif mutation == "tenant":
        cut.tenant_id = "foreign"
    elif mutation == "head":
        cut.owner_journal_head = "other"
    elif mutation == "frontier":
        cut.tenant_frontier = 5
    elif mutation == "commitment":
        cut.materialization_commitment = "other"
    joined_calls: list[str] = []
    clock_calls: list[str] = []
    effects = SimpleNamespace(
        selected=SimpleNamespace(selected_h1=SimpleNamespace(decision=selected, issuance=_V4()))
    )
    joined = SimpleNamespace(
        recheck_held=lambda value: (joined_calls.append("recheck"), value)[1],
        _issued_record_held=lambda _: SimpleNamespace(effects_capture="effects", evidence=evidence),
        _effects=SimpleNamespace(_issued_record_held=lambda _: effects),
    )
    clock = SimpleNamespace(
        role_held=lambda _: SimpleNamespace(
            clock_contract="clock", clock_epoch="epoch", valid_until_ns=50
        ),
        recheck_held=lambda value: (clock_calls.append("recheck"), value)[1],
    )
    reader = object.__new__(subject.H1ScopedAuthorizationCurrentEffectsHistoryReaderV1)
    reader._runtime, reader._joined, reader._clock = runtime, joined, clock
    monkeypatch.setattr(subject, "SelectedOwnerDecision", _Selected)
    monkeypatch.setattr(subject, "H1CompletionIssuanceV4", _V4)
    monkeypatch.setattr(subject, "make_scoped_intent_member", lambda _: member)
    monkeypatch.setattr(
        subject, "_read_materialized_effects_with_history", lambda *_: (cut, journal)
    )
    history = object()
    monkeypatch.setattr(subject, "interpret_h1_producer_effects_history", lambda *_, **__: history)
    monkeypatch.setattr(subject, "_effects_history", lambda value: value)
    monkeypatch.setattr(subject, "h1_effects_history_capture", lambda *_args, **_kwargs: "source")

    with runtime._authority_gate().hold():
        if mutation is None:
            material = reader._read_record_held(joined_capture, clock_capture)
            assert material.history is history and material.source == "source"
            assert joined_calls == ["recheck", "recheck"] and clock_calls == ["recheck"]
        else:
            with pytest.raises(subject.H1ScopedAuthorizationCurrentEffectsHistoryHold):
                reader._read_record_held(joined_capture, clock_capture)
