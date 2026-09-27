from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from chiplog.architecture.r7_runtime import R7_PRODUCTION_MANIFEST
from chiplog.capabilities.planning.r7_boundary import R7PlanningCreateDTO
from chiplog.platform import r7_runtime
from chiplog.platform.authority_gate import AuthorityGate, AuthorityGateError
from chiplog.platform.broker import (
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortRejected,
    PublicPortSuccess,
)
from chiplog.platform.r7_leaves import ProductionClock
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime


def _planning_request(runtime: AuthorityBrokerRuntime) -> PublicPortCall:
    payload = R7PlanningCreateDTO(
        tenant_id="tenant-1",
        principal_id="principal-1",
        command_id="command-1",
        intention_line_id="intention-1",
        revision_id="revision-1",
        purpose="Prepare release",
        authority_act_id="act-1",
        trust_reference_bytes=(
            b'{"contour":"CLI","credential_head":"credential-1",'
            b'"freshness_sequence":1,"materialization_head":"materialization-1",'
            b'"peer_credential":"uid:test","session_head":"session-1",'
            b'"source_head":"local","trust_head":"trust-1"}'
        ),
        planning_snapshot_bytes=b'{"commands":[],"head":0,"record_ids":[]}',
    )
    return PublicPortCall(
        operation_id="planning.create_intention_line",
        request_id="request-1",
        caller=BrokerSession(
            tenant_id="tenant-1",
            broker_epoch=1,
            generation_id="generation-1",
            owner_id="broker",
            session_id="broker-session-1",
        ),
        callee=runtime.session("planning"),
        schema_id="chiplog.planning.public.create.v1",
        canonical_payload=payload.canonical_bytes(),
        budget=CallBudget(
            remaining_calls=4,
            remaining_depth=2,
            absolute_deadline_ns=time.monotonic_ns() + 10_000_000_000,
            policy_version=1,
        ),
    )


def test_authenticated_public_dto_routes_to_isolated_planning_owner() -> None:
    with AuthorityBrokerRuntime(
        "tenant-1", 1, "generation-1", R7_PRODUCTION_MANIFEST, b"session-secret"
    ) as runtime:
        result = asyncio.run(runtime.call(_planning_request(runtime)))
        assert isinstance(result, PublicPortSuccess)
        assert result.responder.owner_id == "planning"
        assert b'"disposition":"COMMITTED"' in result.canonical_payload


def test_stale_callee_and_exclusive_resource_hold_reject_before_delivery() -> None:
    with AuthorityBrokerRuntime(
        "tenant-1", 1, "generation-1", R7_PRODUCTION_MANIFEST, b"session-secret"
    ) as runtime:
        request = _planning_request(runtime)
        stale = request.model_copy(
            update={"callee": request.callee.model_copy(update={"session_id": "stale"})}
        )
        result = asyncio.run(runtime.call(stale))
        assert isinstance(result, PublicPortRejected)
        assert result.failure.kind == "STALE_SESSION"

        held = request.model_copy(update={"held_resources": ("sqlite_transaction",)})
        result = asyncio.run(runtime.call(held))
        assert isinstance(result, PublicPortRejected)
        assert result.failure.kind == "PROTOCOL_REJECTED"


def test_guarded_broker_call_admits_the_exact_request_once_immediately_before_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []
    original_send = r7_runtime._send_frame

    def observe_send(connection: object, secret: bytes, payload: bytes) -> None:
        events.append(("send", payload))
        original_send(connection, secret, payload)  # type: ignore[arg-type]

    with AuthorityBrokerRuntime(
        "tenant-1", 1, "generation-1", R7_PRODUCTION_MANIFEST, b"session-secret"
    ) as runtime:
        request = _planning_request(runtime)
        monkeypatch.setattr(r7_runtime, "_send_frame", observe_send)

        def admit(actual: PublicPortCall, owner_request_bytes: bytes) -> None:
            events.append(("guard", owner_request_bytes))
            assert actual is request
            assert owner_request_bytes == r7_runtime._owner_request_bytes(request)

        result = asyncio.run(runtime._call_with_admission_guard(request, admission_guard=admit))
        assert isinstance(result, PublicPortSuccess)
        assert events[0][0] == "guard"
        assert events[0][1] is events[1][1]
        assert [event[0] for event in events] == ["guard", "send"]


def test_guard_failure_sends_no_frame_and_propagates_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sent: list[bytes] = []
    original_send = r7_runtime._send_frame

    def observe_send(connection: object, secret: bytes, payload: bytes) -> None:
        sent.append(payload)
        original_send(connection, secret, payload)  # type: ignore[arg-type]

    class AdmissionDenied(RuntimeError):
        pass

    with AuthorityBrokerRuntime(
        "tenant-1", 1, "generation-1", R7_PRODUCTION_MANIFEST, b"session-secret"
    ) as runtime:
        request = _planning_request(runtime)
        gate = AuthorityGate.for_database(tmp_path / "authority.sqlite")
        monkeypatch.setattr(r7_runtime, "_send_frame", observe_send)

        def deny(actual: PublicPortCall, _owner_request_bytes: bytes) -> None:
            assert actual is request
            gate.require_held()
            raise AdmissionDenied("not admitted")

        with pytest.raises(AdmissionDenied, match="not admitted"):
            asyncio.run(
                runtime._call_with_admission_guard(
                    request, admission_guard=deny, authority_gate=gate
                )
            )
        assert sent == []


def test_guard_that_exhausts_budget_sends_no_frame(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = 10
    gate = AuthorityGate.for_database(tmp_path / "authority.sqlite")
    sent: list[bytes] = []
    original_send = r7_runtime._send_frame

    def observe_send(connection: object, secret: bytes, payload: bytes) -> None:
        sent.append(payload)
        original_send(connection, secret, payload)  # type: ignore[arg-type]

    with AuthorityBrokerRuntime(
        "tenant-1", 1, "generation-1", R7_PRODUCTION_MANIFEST, b"session-secret"
    ) as runtime:
        request = _planning_request(runtime)
        request = request.model_copy(
            update={"budget": request.budget.model_copy(update={"absolute_deadline_ns": 11})}
        )
        monkeypatch.setattr(ProductionClock, "monotonic_ns", lambda _clock: now)
        monkeypatch.setattr(r7_runtime, "_send_frame", observe_send)

        def exhaust_budget(actual: PublicPortCall, _owner_request_bytes: bytes) -> None:
            nonlocal now
            assert actual is request
            gate.require_held()
            now = 11

        result = asyncio.run(
            runtime._call_with_admission_guard(
                request, admission_guard=exhaust_budget, authority_gate=gate
            )
        )
        assert isinstance(result, PublicPortRejected)
        assert result.failure.kind == "DEADLINE_EXCEEDED"
        assert sent == []


def test_authority_gate_spans_guard_and_send_but_not_receive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    gate = AuthorityGate.for_database(tmp_path / "authority.sqlite")
    mutation_attempted = threading.Event()
    mutation_entered = threading.Event()
    mutator: threading.Thread | None = None
    guarded_payload: bytes | None = None
    guarded_exchange_active = False
    original_send = r7_runtime._send_frame
    original_receive = r7_runtime._receive_frame

    def attempt_mutation() -> None:
        mutation_attempted.set()
        with gate.hold():
            mutation_entered.set()

    def observe_send(connection: object, secret: bytes, payload: bytes) -> None:
        nonlocal guarded_exchange_active
        if payload != guarded_payload:
            original_send(connection, secret, payload)  # type: ignore[arg-type]
            return
        gate.require_held()
        assert mutation_attempted.wait(timeout=1)
        assert not mutation_entered.wait(timeout=0.1)
        guarded_exchange_active = True
        original_send(connection, secret, payload)  # type: ignore[arg-type]

    def observe_receive(connection: object, secret: bytes) -> bytes:
        nonlocal guarded_exchange_active
        if not guarded_exchange_active:
            return original_receive(connection, secret)  # type: ignore[arg-type]
        with pytest.raises(AuthorityGateError):
            gate.require_held()
        assert mutation_entered.wait(timeout=1)
        try:
            return original_receive(connection, secret)  # type: ignore[arg-type]
        finally:
            guarded_exchange_active = False

    with AuthorityBrokerRuntime(
        "tenant-1", 1, "generation-1", R7_PRODUCTION_MANIFEST, b"session-secret"
    ) as runtime:
        request = _planning_request(runtime)
        guarded_payload = r7_runtime._owner_request_bytes(request)
        monkeypatch.setattr(r7_runtime, "_send_frame", observe_send)
        monkeypatch.setattr(r7_runtime, "_receive_frame", observe_receive)

        def admit(actual: PublicPortCall, owner_request_bytes: bytes) -> None:
            nonlocal mutator
            assert actual is request
            assert owner_request_bytes == r7_runtime._owner_request_bytes(request)
            gate.require_held()
            mutator = threading.Thread(target=attempt_mutation)
            mutator.start()

        result = asyncio.run(
            runtime._call_with_admission_guard(request, admission_guard=admit, authority_gate=gate)
        )
        assert isinstance(result, PublicPortSuccess)
    assert mutator is not None
    mutator.join(timeout=1)
    assert not mutator.is_alive()
    assert mutation_entered.is_set()


def test_guard_call_mutation_cannot_change_the_prepared_owner_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[bytes] = []
    original_send = r7_runtime._send_frame

    def observe_send(connection: object, secret: bytes, payload: bytes) -> None:
        sent.append(payload)
        original_send(connection, secret, payload)  # type: ignore[arg-type]

    class FrameMismatch(RuntimeError):
        pass

    with AuthorityBrokerRuntime(
        "tenant-1", 1, "generation-1", R7_PRODUCTION_MANIFEST, b"session-secret"
    ) as runtime:
        request = _planning_request(runtime)
        monkeypatch.setattr(r7_runtime, "_send_frame", observe_send)

        def mutate(actual: PublicPortCall, owner_request_bytes: bytes) -> None:
            assert actual is request
            object.__setattr__(actual, "canonical_payload", b"mutated-after-frame-preparation")
            if r7_runtime._owner_request_bytes(actual) != owner_request_bytes:
                raise FrameMismatch("guard detected owner-frame mutation")

        with pytest.raises(FrameMismatch, match="owner-frame mutation"):
            asyncio.run(runtime._call_with_admission_guard(request, admission_guard=mutate))
        assert sent == []
