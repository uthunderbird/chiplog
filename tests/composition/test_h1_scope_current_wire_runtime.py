"""Exact H1 current-scope owner frames remain invocation-local."""

from __future__ import annotations

import time

import pytest

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
)
from chiplog.platform.broker import (
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortSuccess,
)


def test_current_scope_wire_rejects_swapped_missing_and_expired_frames() -> None:
    runtime = object.__new__(CommonCliExecutionRuntime)
    first = _call("first")
    second = _call("second")
    first_key = runtime._reserve_h1_scope_wire("scope_current", first)
    second_key = runtime._reserve_h1_scope_wire("scope_current", second)
    result = _result(first)
    now = time.monotonic_ns()
    runtime._record_h1_scope_wire(first_key, first, result, sent_at_ns=now, returned_at_ns=now)
    runtime._record_h1_scope_wire(
        second_key, second, _result(second), sent_at_ns=now, returned_at_ns=now
    )

    with pytest.raises(LoopRejected, match="absent"):
        runtime._take_h1_scope_wire(
            "scope_current",
            request_id=first.request_id,
            caller=second.caller,
            callee=second.callee,
        )
    with pytest.raises(LoopRejected, match="absent"):
        runtime._take_h1_scope_wire(
            "scope_current",
            request_id="missing",
            caller=first.caller,
            callee=first.callee,
        )
    expired = _call("expired", deadline_ns=time.monotonic_ns() - 1)
    expired_key = runtime._reserve_h1_scope_wire("scope_current", expired)
    late = time.monotonic_ns()
    with pytest.raises(LoopRejected, match="reservation differs"):
        runtime._record_h1_scope_wire(
            expired_key, expired, _result(expired), sent_at_ns=late, returned_at_ns=late
        )
    with pytest.raises(LoopRejected, match="absent"):
        runtime._take_h1_scope_wire(
            "scope_current",
            request_id=expired.request_id,
            caller=expired.caller,
            callee=expired.callee,
        )


def _call(suffix: str, *, deadline_ns: int | None = None) -> PublicPortCall:
    caller = BrokerSession(
        tenant_id="hermetic-tenant",
        broker_epoch=1,
        generation_id="generation-" + suffix,
        owner_id="broker",
        session_id="broker-session-" + suffix,
    )
    callee = BrokerSession(
        tenant_id="hermetic-tenant",
        broker_epoch=1,
        generation_id="generation-" + suffix,
        owner_id="deployment_trust",
        session_id="deployment-trust-session-" + suffix,
    )
    return PublicPortCall(
        operation_id="deployment_trust.read_current_hermetic_output_scope",
        request_id="request-" + suffix,
        caller=caller,
        callee=callee,
        schema_id="chiplog.deployment-trust.owner-call.v1",
        canonical_payload=b"current-wire",
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            absolute_deadline_ns=deadline_ns or time.monotonic_ns() + 60_000_000_000,
            policy_version=1,
        ),
    )


def _result(call: PublicPortCall) -> PublicPortSuccess:
    return PublicPortSuccess(
        request_id=call.request_id,
        responder=call.callee,
        schema_id="chiplog.deployment-trust.owner-result.v1",
        canonical_payload=b"candidate",
    )
