"""Lifecycle tests for the V22 fresh current-trust reader control boundary."""

from __future__ import annotations

import asyncio
import hashlib
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest

from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    H1CurrentBrokerRouteV1,
    H1OwnerCurrentCallV1,
    H1OwnerCurrentCandidateV1,
)
from chiplog.composition.common_cli_execution_runtime import _H1ScopeWire
from chiplog.composition.h1_runtime_preissuance_port import _H1RuntimePreissuancePort
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess
from chiplog.platform.r7_trust import TrustOwnerCall
from tests.support.h1_selected_scope_source import build_h1_selected_scope_source


class _Gate:
    def __init__(self) -> None:
        self.held = False

    @contextmanager
    def hold(self):
        assert not self.held
        self.held = True
        try:
            yield
        finally:
            self.held = False

    def require_held(self) -> None:
        assert self.held


class _JoinedCapture:
    pass


class _JoinedReader:
    def __init__(self, runtime: object) -> None:
        self._runtime = runtime


class _Current:
    pass


class _Runtime:
    def __init__(self) -> None:
        self.gate = _Gate()
        self.calls = 0
        self.cancel = False
        self.mutate = False
        self.version = 0
        self.issued_journal = 0

    def _authority_gate(self) -> _Gate:
        return self.gate

    async def _read_current_hermetic_output_scope_with_wire(self, request: object):
        del request
        assert not self.gate.held
        self.calls += 1
        await asyncio.sleep(0)
        if self.mutate:
            self.version += 1
        if self.cancel:
            raise asyncio.CancelledError
        return _Current(), SimpleNamespace()


@dataclass(frozen=True)
class _Record:
    joined_capture: _JoinedCapture
    selected_scope: object
    request: object
    version: int


def _reader(monkeypatch: pytest.MonkeyPatch):
    import chiplog.composition.h1_current_trust_reader as subject

    runtime = _Runtime()
    joined = _JoinedReader(runtime)
    runtime._h1_scoped_authorization_delivery_work_reader_v1 = joined
    monkeypatch.setattr(subject, "CommonCliExecutionRuntime", _Runtime)
    monkeypatch.setattr(subject, "CurrentHermeticExecutionScopeV1", _Current)
    monkeypatch.setattr(subject, "H1ScopedAuthorizationDeliveryWorkReaderV1", _JoinedReader)
    monkeypatch.setattr(subject, "H1ScopedAuthorizationDeliveryWorkCaptureV1", _JoinedCapture)
    reader = subject.H1CurrentTrustReaderV1(runtime)
    joined_capture = _JoinedCapture()
    selected_scope, request = object(), object()

    def capture_held(self, value: object) -> _Record:
        self._runtime._authority_gate().require_held()
        assert value is joined_capture
        return _Record(joined_capture, selected_scope, request, runtime.version)

    def verify(self, wire: object, selected: object, request: object) -> None:
        self._runtime._authority_gate().require_held()
        del wire, selected, request

    def replay(self, record: _Record, wire: object, request: object, result: object) -> object:
        self._runtime._authority_gate().require_held()
        del record, wire, request, result
        return object()

    monkeypatch.setattr(subject.H1CurrentTrustReaderV1, "_capture_held", capture_held)
    monkeypatch.setattr(
        subject.H1CurrentTrustReaderV1,
        "_fresh_selected_scope_held",
        lambda self, selected, request, result: object(),
    )
    monkeypatch.setattr(subject.H1CurrentTrustReaderV1, "_verify_exact_wire_held", verify)
    monkeypatch.setattr(subject.H1CurrentTrustReaderV1, "_replay_and_role_held", replay)
    return subject, runtime, reader, joined_capture


@pytest.mark.asyncio
async def test_current_reader_releases_gate_for_each_new_owner_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, runtime, reader, joined = _reader(monkeypatch)
    capture = reader.capture(joined)
    seen_held: list[bool] = []

    assert (
        await reader.with_current_held(capture, lambda _: seen_held.append(runtime.gate.held))
        is None
    )
    assert (
        await reader.with_current_held(capture, lambda _: seen_held.append(runtime.gate.held))
        is None
    )
    assert runtime.calls == 2
    assert seen_held == [True, True]
    assert not runtime.gate.held


@pytest.mark.asyncio
async def test_current_reader_rejects_mutation_during_owner_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, runtime, reader, joined = _reader(monkeypatch)
    capture = reader.capture(joined)
    runtime.mutate = True
    consumed = False

    with pytest.raises(subject.H1CurrentTrustHold, match="changed during owner read"):
        await reader.with_current_held(capture, lambda _: consumed)
    assert runtime.calls == 1
    assert not consumed and not runtime.gate.held


@pytest.mark.asyncio
async def test_current_reader_cancellation_and_bad_captures_do_not_consume_or_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, runtime, reader, joined = _reader(monkeypatch)
    capture = reader.capture(joined)
    issued = dict(reader._issued)
    runtime.cancel = True

    with pytest.raises(asyncio.CancelledError):
        await reader.with_current_held(capture, lambda _: (_ for _ in ()).throw(AssertionError))
    assert reader._issued == issued
    assert runtime.issued_journal == 0 and not runtime.gate.held

    runtime.cancel = False
    with pytest.raises(subject.H1CurrentTrustHold, match="not reader-issued"):
        await reader.with_current_held(object(), lambda _: None)
    reader.release(capture)
    with pytest.raises(subject.H1CurrentTrustHold, match="not reader-issued"):
        await reader.with_current_held(capture, lambda _: None)


@pytest.mark.asyncio
async def test_current_reader_rejects_awaitable_consumer_and_closes_dependents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, runtime, reader, joined = _reader(monkeypatch)
    capture = reader.capture(joined)

    async def consumer(_: object) -> None:
        return None

    with pytest.raises(TypeError, match="must not return an awaitable"):
        await reader.with_current_held(capture, consumer)
    reader.close()
    assert not reader._issued and not runtime.gate.held
    with pytest.raises(subject.H1CurrentTrustHold, match="closed"):
        reader.capture(joined)


def _real_current_wire(*, substituted_observation: bool = False) -> tuple[object, _H1ScopeWire]:
    """A canonical owner DTO/wire pair, independent of an installed runtime."""
    selected = build_h1_selected_scope_source()
    request = selected.current_request
    if substituted_observation:
        expected = request.expected_trust_observation
        request = request.model_copy(
            update={
                "expected_trust_observation": expected.model_copy(
                    update={"logical_snapshot_head": "another-current-logical-head"}
                )
            }
        )
    route = H1CurrentBrokerRouteV1(
        tenant_id="hermetic-tenant",
        broker_epoch=1,
        runtime_generation="generation",
        broker_session_id="broker-session",
        owner_session_id="owner-session",
        request_id="current-request",
    )
    pinned = H1OwnerCurrentCallV1(
        route=route,
        read_request_bytes=request.canonical_bytes(),
        request_digest=hashlib.sha256(request.canonical_bytes()).hexdigest(),
    )
    candidate = H1OwnerCurrentCandidateV1(
        route=route,
        request_digest=pinned.request_digest,
        current=selected.current_result,
    )
    caller = BrokerSession(
        tenant_id="hermetic-tenant",
        broker_epoch=1,
        generation_id="generation",
        owner_id="broker",
        session_id="broker-session",
    )
    callee = BrokerSession(
        tenant_id="hermetic-tenant",
        broker_epoch=1,
        generation_id="generation",
        owner_id="deployment_trust",
        session_id="owner-session",
    )
    sent = PublicPortCall(
        operation_id="deployment_trust.read_current_hermetic_output_scope",
        request_id="current-request",
        caller=caller,
        callee=callee,
        schema_id="chiplog.deployment-trust.owner-call.v1",
        canonical_payload=TrustOwnerCall(
            mode="READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1",
            snapshot_bytes=b"real-inert-trust-snapshot",
            request_bytes=pinned.canonical_bytes(),
        ).canonical_bytes(),
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            absolute_deadline_ns=time.monotonic_ns() + 30_000_000_000,
            policy_version=1,
        ),
    )
    returned = PublicPortSuccess(
        request_id=sent.request_id,
        responder=callee,
        schema_id="chiplog.deployment-trust.current-hermetic-output-scope-result.v1",
        canonical_payload=candidate.canonical_bytes(),
    )
    now = time.monotonic_ns()
    return selected, _H1ScopeWire(sent, returned, now, now)


def test_current_reader_checks_real_canonical_owner_wire_against_exact_selected_request() -> None:
    """A coherent current DTO is rejected when it belongs to another exact read."""
    import chiplog.composition.h1_current_trust_reader as subject

    selected, wire = _real_current_wire()
    reader = object.__new__(subject.H1CurrentTrustReaderV1)
    reader._runtime = SimpleNamespace(
        _h1_preissuance_registration_source_port=_H1RuntimePreissuancePort
    )
    reader._verify_exact_wire_held(wire, selected, selected.current_request)

    _, substituted = _real_current_wire(substituted_observation=True)
    with pytest.raises(subject.H1CurrentTrustHold, match="fresh H1 current wire differs"):
        reader._verify_exact_wire_held(substituted, selected, selected.current_request)

    candidate = H1OwnerCurrentCandidateV1.model_validate_json(wire.returned.canonical_payload)
    swapped_candidate = candidate.model_copy(
        update={"current": candidate.current.model_copy(update={"selector_generation": 1})}
    )
    swapped = replace(
        wire,
        returned=wire.returned.model_copy(
            update={"canonical_payload": swapped_candidate.canonical_bytes()}
        ),
    )
    with pytest.raises(subject.H1CurrentTrustHold, match="fresh H1 current wire differs"):
        reader._verify_exact_wire_held(swapped, selected, selected.current_request)
