from __future__ import annotations

from chiplog.composition.h1_terminal_call_identity import _terminal_call_identity
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall


def _call() -> PublicPortCall:
    return PublicPortCall(
        operation_id="agent_loop.prepare_terminal_work",
        request_id="terminal-request",
        caller=BrokerSession(
            tenant_id="tenant",
            broker_epoch=1,
            generation_id="generation",
            owner_id="broker",
            session_id="broker-session",
        ),
        callee=BrokerSession(
            tenant_id="tenant",
            broker_epoch=1,
            generation_id="generation",
            owner_id="agent_loop",
            session_id="loop-session",
        ),
        schema_id="chiplog.agent-loop.prepare-terminal-work.v1",
        canonical_payload=b"canonical terminal request",
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            absolute_deadline_ns=1,
            policy_version=1,
        ),
        held_resources=("terminal",),
    )


def test_terminal_call_identity_binds_payload_and_admission_metadata() -> None:
    call = _call()
    original_fingerprint, original_frame = _terminal_call_identity(call)

    object.__setattr__(call, "canonical_payload", b"changed terminal request")
    payload_fingerprint, payload_frame = _terminal_call_identity(call)
    assert payload_fingerprint != original_fingerprint
    assert payload_frame != original_frame

    call = _call()
    object.__setattr__(
        call,
        "caller",
        call.caller.model_copy(update={"session_id": "changed-broker-session"}),
    )
    caller_fingerprint, caller_frame = _terminal_call_identity(call)
    assert caller_fingerprint != original_fingerprint
    assert caller_frame == original_frame

    call = _call()
    object.__setattr__(
        call,
        "budget",
        call.budget.model_copy(update={"remaining_calls": 2}),
    )
    budget_fingerprint, budget_frame = _terminal_call_identity(call)
    assert budget_fingerprint != original_fingerprint
    assert budget_frame == original_frame
