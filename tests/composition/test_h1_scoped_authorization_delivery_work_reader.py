"""Focused lifecycle tests for the joined, inert V4/current-g1 source."""

from __future__ import annotations

import hashlib
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


def _installed(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[object, _Runtime, object, object, object]:
    import chiplog.composition.h1_scoped_authorization_delivery_work_reader as subject

    source = SimpleNamespace(intent_id="intent")
    effects_capture = object.__new__(subject.H1ScopedAuthorizationEffectsHistoryCaptureV1)
    effects_record = SimpleNamespace(locator=SimpleNamespace(intent_id="intent"))
    current_evidence = SimpleNamespace(source=source, g1="g1", binding="binding")

    class _Effects:
        def __init__(self, runtime: object) -> None:
            self._runtime = runtime

        def recheck_held(self, capture: object) -> object:
            runtime._authority_gate().require_held()
            assert capture is effects_capture
            return capture

        def _issued_record_held(self, capture: object) -> object:
            assert capture is effects_capture
            return effects_record

    class _Current:
        def __init__(self, runtime: object) -> None:
            self.runtime = runtime
            self.value = current_evidence

        def read(self, intent_id: str) -> object:
            runtime._authority_gate().require_held()
            assert intent_id == "intent"
            return self.value

    runtime = _Runtime()
    effects, current = _Effects(runtime), _Current(runtime)
    runtime._h1_scoped_authorization_effects_history_reader_v3 = effects
    runtime._h1_delivery_work_current_claim_reader = current
    monkeypatch.setattr(subject, "CommonCliExecutionRuntime", _Runtime)
    monkeypatch.setattr(subject, "H1ScopedAuthorizationEffectsHistoryReaderV1", _Effects)
    monkeypatch.setattr(subject, "H1DeliveryWorkCurrentClaimReader", _Current)
    monkeypatch.setattr(
        subject.H1ScopedAuthorizationDeliveryWorkReaderV1,
        "_source_from_effects_held",
        staticmethod(lambda record: source),
    )
    return subject, runtime, effects_capture, current, source


def test_joined_reader_rechecks_full_current_evidence_and_revokes_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, runtime, effects_capture, current, source = _installed(monkeypatch)
    reader = subject.H1ScopedAuthorizationDeliveryWorkReaderV1(runtime)

    capture = reader.read(effects_capture)
    with runtime._authority_gate().hold():
        evidence = reader.evidence_held(capture)
    assert evidence.source is source
    assert evidence.current_claim is current.value

    current.value = SimpleNamespace(source=source, g1="changed", binding="binding")
    with runtime._authority_gate().hold(), pytest.raises(
        subject.H1ScopedAuthorizationDeliveryWorkHold, match="evidence changed"
    ):
        reader.recheck_held(capture)

    current.value = evidence.current_claim
    reader.release(capture)
    with runtime._authority_gate().hold(), pytest.raises(
        subject.H1ScopedAuthorizationDeliveryWorkHold, match="not reader-issued"
    ):
        reader.evidence_held(capture)


def test_joined_reader_rejects_forgery_and_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, runtime, effects_capture, _, _ = _installed(monkeypatch)
    reader = subject.H1ScopedAuthorizationDeliveryWorkReaderV1(runtime)

    forged = object.__new__(subject.H1ScopedAuthorizationDeliveryWorkCaptureV1)
    with runtime._authority_gate().hold(), pytest.raises(
        subject.H1ScopedAuthorizationDeliveryWorkHold, match="not reader-issued"
    ):
        reader.evidence_held(forged)

    capture = reader.read(effects_capture)
    reader.close()
    with runtime._authority_gate().hold(), pytest.raises(
        subject.H1ScopedAuthorizationDeliveryWorkHold, match="reader is closed"
    ):
        reader.evidence_held(capture)


def test_selected_v4_source_requires_exact_issuance_and_each_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chiplog.composition.h1_scoped_authorization_delivery_work_reader as subject

    def member(record_id: str, raw: bytes) -> object:
        return SimpleNamespace(
            record_kind="MEMBER",
            record_id=record_id,
            schema_id="chiplog.test.member.v1",
            canonical_record_bytes=raw,
            fingerprint=hashlib.sha256(raw).hexdigest(),
        )

    run, manifest, intent = member("run", b"run"), member("manifest", b"manifest"), member(
        "intent", b"intent"
    )

    class _V4:
        def __init__(self) -> None:
            self.assembly = SimpleNamespace(
                prepared_completion=SimpleNamespace(run=object(), terminal_manifest=object())
            )
            self.effects_result = SimpleNamespace(
                intent_result=SimpleNamespace(intent=object())
            )

    class _Batch:
        def __init__(self, records: tuple[object, ...]) -> None:
            self.identity = SimpleNamespace(tenant_id="tenant")
            self.complete_records = records

    def row(owner: str, value: object) -> object:
        return SimpleNamespace(
            owner=owner,
            record_kind=value.record_kind,
            record_id=value.record_id,
            schema_id=value.schema_id,
            canonical_bytes=value.canonical_record_bytes,
            fingerprint=value.fingerprint,
        )

    issuance = _V4()
    batch = _Batch((row("agent_loop", run), row("agent_loop", manifest), row("effects", intent)))
    decision = SimpleNamespace(
        prepared=SimpleNamespace(request=batch),
        decision_id="decision",
        decision_fingerprint="0" * 64,
    )
    effects = SimpleNamespace(
        locator=SimpleNamespace(intent_id="intent"),
        selected=SimpleNamespace(
            intent=SimpleNamespace(subject=SimpleNamespace(subject_id="intent")),
            selected_h1=SimpleNamespace(decision=decision, issuance=issuance),
        ),
    )
    monkeypatch.setattr(subject, "H1CompletionIssuanceV4", _V4)
    monkeypatch.setattr(subject, "CompleteDeliveryBatchV2", _Batch)
    monkeypatch.setattr(subject, "make_terminal_run_member", lambda _: run)
    monkeypatch.setattr(subject, "make_terminal_manifest_member", lambda _: manifest)
    monkeypatch.setattr(subject, "make_scoped_intent_member", lambda _: intent)

    result = subject.H1ScopedAuthorizationDeliveryWorkReaderV1._source_from_effects_held(effects)
    assert result.intent_id == "intent"

    effects.selected.selected_h1.issuance = object()
    with pytest.raises(subject.H1ScopedAuthorizationDeliveryWorkHold, match="V4 Effects issuance"):
        subject.H1ScopedAuthorizationDeliveryWorkReaderV1._source_from_effects_held(effects)

    effects.selected.selected_h1.issuance = issuance
    batch.complete_records = (row("agent_loop", run), row("agent_loop", manifest))
    with pytest.raises(subject.H1ScopedAuthorizationDeliveryWorkHold, match="members differ"):
        subject.H1ScopedAuthorizationDeliveryWorkReaderV1._source_from_effects_held(effects)
