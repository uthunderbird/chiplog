"""Private H1 scope wire captures retain exact concurrent broker exchanges."""

from __future__ import annotations

import asyncio
import time
from typing import Literal

import pytest

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.platform.broker import (
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortSuccess,
)


def _session(owner_id: str, suffix: str) -> BrokerSession:
    return BrokerSession(
        tenant_id="hermetic-tenant",
        broker_epoch=1,
        generation_id="generation-" + suffix,
        owner_id=owner_id,
        session_id=owner_id + "-session-" + suffix,
    )


def _call(role: Literal["scope_issue", "scope_current"], suffix: str) -> PublicPortCall:
    operation = {
        "scope_issue": "deployment_trust.issue_hermetic_output_scope",
        "scope_current": "deployment_trust.read_current_hermetic_output_scope",
    }[role]
    return PublicPortCall(
        operation_id=operation,
        request_id="request-" + suffix,
        caller=_session("broker", suffix),
        callee=_session("deployment_trust", suffix),
        schema_id="chiplog.deployment-trust.owner-call.v1",
        canonical_payload=(role + "-payload-" + suffix).encode(),
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            absolute_deadline_ns=time.monotonic_ns() + 60_000_000_000,
            policy_version=1,
        ),
    )


async def _record_after_barrier(
    runtime: CommonCliExecutionRuntime,
    role: Literal["scope_issue", "scope_current"],
    call: PublicPortCall,
    barrier: asyncio.Barrier,
) -> None:
    key = runtime._reserve_h1_scope_wire(role, call)
    await barrier.wait()
    runtime._record_h1_scope_wire(
        key,
        call,
        PublicPortSuccess(
            request_id=call.request_id,
            responder=call.callee,
            schema_id="chiplog.deployment-trust.owner-result.v1",
            canonical_payload=("result-" + call.request_id).encode(),
        ),
        sent_at_ns=time.monotonic_ns(),
        returned_at_ns=time.monotonic_ns(),
    )


@pytest.mark.asyncio
async def test_concurrent_scope_capture_lookup_cannot_splice_broker_frames() -> None:
    runtime = object.__new__(CommonCliExecutionRuntime)
    first = _call("scope_issue", "first")
    second = _call("scope_issue", "second")
    barrier = asyncio.Barrier(2)

    await asyncio.gather(
        _record_after_barrier(runtime, "scope_issue", first, barrier),
        _record_after_barrier(runtime, "scope_issue", second, barrier),
    )

    with pytest.raises(LoopRejected, match="absent"):
        runtime._take_h1_scope_wire(
            "scope_issue",
            request_id=first.request_id,
            caller=second.caller,
            callee=second.callee,
        )
    second_wire = runtime._take_h1_scope_wire(
        "scope_issue",
        request_id=second.request_id,
        caller=second.caller,
        callee=second.callee,
    )
    first_wire = runtime._take_h1_scope_wire(
        "scope_issue",
        request_id=first.request_id,
        caller=first.caller,
        callee=first.callee,
    )
    assert (second_wire.sent, second_wire.returned.request_id) == (second, second.request_id)
    assert (first_wire.sent, first_wire.returned.request_id) == (first, first.request_id)
    with pytest.raises(LoopRejected, match="absent"):
        runtime._take_h1_scope_wire(
            "scope_issue",
            request_id=second.request_id,
            caller=second.caller,
            callee=second.callee,
        )


@pytest.mark.parametrize("role", ("scope_issue", "scope_current"))
def test_duplicate_private_scope_capture_key_is_rejected_before_owner_call(
    role: Literal["scope_issue", "scope_current"],
) -> None:
    runtime = object.__new__(CommonCliExecutionRuntime)
    call = _call(role, "duplicate")
    runtime._reserve_h1_scope_wire(role, call)

    with pytest.raises(LoopRejected, match="already in flight"):
        runtime._reserve_h1_scope_wire(role, call)


def test_malformed_exact_capture_is_burned_without_affecting_other_key() -> None:
    runtime = object.__new__(CommonCliExecutionRuntime)
    malformed = _call("scope_issue", "malformed")
    intact = _call("scope_issue", "intact")
    malformed_key = runtime._reserve_h1_scope_wire("scope_issue", malformed)
    intact_key = runtime._reserve_h1_scope_wire("scope_issue", intact)
    runtime._h1_scope_wires[malformed_key] = _wire(
        malformed,
        PublicPortSuccess(
            request_id="substituted-request",
            responder=malformed.callee,
            schema_id="chiplog.deployment-trust.owner-result.v1",
            canonical_payload=b"substituted",
        ),
    )
    runtime._record_h1_scope_wire(
        intact_key,
        intact,
        PublicPortSuccess(
            request_id=intact.request_id,
            responder=intact.callee,
            schema_id="chiplog.deployment-trust.owner-result.v1",
            canonical_payload=b"intact",
        ),
        sent_at_ns=time.monotonic_ns(),
        returned_at_ns=time.monotonic_ns(),
    )

    with pytest.raises(LoopRejected, match="response differs"):
        runtime._take_h1_scope_wire(
            "scope_issue",
            request_id=malformed.request_id,
            caller=malformed.caller,
            callee=malformed.callee,
        )
    with pytest.raises(LoopRejected, match="absent"):
        runtime._take_h1_scope_wire(
            "scope_issue",
            request_id=malformed.request_id,
            caller=malformed.caller,
            callee=malformed.callee,
        )
    wire = runtime._take_h1_scope_wire(
        "scope_issue",
        request_id=intact.request_id,
        caller=intact.caller,
        callee=intact.callee,
    )
    assert (wire.sent, wire.returned.request_id) == (intact, intact.request_id)


def test_request_id_handoff_requires_one_live_identity_and_cancellation_burns_it() -> None:
    runtime = object.__new__(CommonCliExecutionRuntime)
    first = _call("scope_issue", "shared")
    second = _call("scope_issue", "other")
    second = PublicPortCall(
        operation_id=second.operation_id,
        request_id=first.request_id,
        caller=second.caller,
        callee=second.callee,
        schema_id=second.schema_id,
        canonical_payload=second.canonical_payload,
        budget=second.budget,
    )
    first_key = runtime._reserve_h1_scope_wire("scope_issue", first)
    second_key = runtime._reserve_h1_scope_wire("scope_issue", second)
    now = time.monotonic_ns()
    runtime._record_h1_scope_wire(
        first_key, first, _result(first), sent_at_ns=now, returned_at_ns=now
    )
    runtime._record_h1_scope_wire(
        second_key, second, _result(second), sent_at_ns=now, returned_at_ns=now
    )

    with pytest.raises(LoopRejected, match="absent or ambiguous"):
        runtime._take_h1_scope_wire_for_request("scope_issue", request_id=first.request_id)
    assert first_key in runtime._h1_scope_wires
    assert second_key in runtime._h1_scope_wires

    runtime._discard_h1_scope_wire(first_key)
    wire = runtime._take_h1_scope_wire_for_request("scope_issue", request_id=first.request_id)
    assert wire.sent is second
    with pytest.raises(LoopRejected, match="absent or ambiguous"):
        runtime._take_h1_scope_wire_for_request("scope_issue", request_id=first.request_id)


def test_invalid_timing_burns_the_reserved_scope_wire() -> None:
    runtime = object.__new__(CommonCliExecutionRuntime)
    call = _call("scope_current", "reversed-time")
    key = runtime._reserve_h1_scope_wire("scope_current", call)
    now = time.monotonic_ns()

    with pytest.raises(LoopRejected, match="reservation differs"):
        runtime._record_h1_scope_wire(
            key,
            call,
            _result(call),
            sent_at_ns=now + 1,
            returned_at_ns=now,
        )
    with pytest.raises(LoopRejected, match="absent"):
        runtime._take_h1_scope_wire(
            "scope_current",
            request_id=call.request_id,
            caller=call.caller,
            callee=call.callee,
        )


def _wire(call: PublicPortCall, result: PublicPortSuccess):
    from chiplog.composition.common_cli_execution_runtime import _H1ScopeWire

    now = time.monotonic_ns()
    return _H1ScopeWire(call, result, now, now)


def _result(call: PublicPortCall) -> PublicPortSuccess:
    return PublicPortSuccess(
        request_id=call.request_id,
        responder=call.callee,
        schema_id="chiplog.deployment-trust.owner-result.v1",
        canonical_payload=b"result-" + call.request_id.encode(),
    )
