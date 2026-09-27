"""Runtime-private H1 preissuance port contracts.

These tests deliberately do not manufacture an installed runtime mount.  The
installed-launch/runtime context manager is owned by the launch integration;
until it publishes a mounted canonical runtime, a native positive route cannot
be exercised without turning a fixture into authority.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter_ns
from types import SimpleNamespace
from typing import cast

import pytest

from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_launch_enrollment import InstalledH1Launch
from chiplog.composition.h1_preissuance_registration import H1PreissuanceSourceViolation
from chiplog.composition.h1_runtime_preissuance_port import (
    _H1PreterminalCompletionProof,
    _H1PreterminalScopeSnapshot,
    _H1RuntimePreissuancePort,
    _IssuedPreterminalCompletionProof,
)
from chiplog.composition.h1_terminal_call_identity import _terminal_call_identity
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall


def test_port_refuses_noncanonical_runtime_before_any_custody_or_launch_access() -> None:
    with pytest.raises(TypeError, match="canonical common CLI runtime"):
        _H1RuntimePreissuancePort(
            cast("CommonCliExecutionRuntime", object()),
            cast("InstalledH1Launch", object()),
        )


def test_port_does_not_accept_a_caller_made_preissuance_locator() -> None:
    """A locator cannot become a selection before the mounted issuer reads it."""

    # The opaque capability has no public constructor.  This is a regression
    # guard that the new port module never introduces one as a convenience.
    from chiplog.composition.h1_preissuance_registration import H1PreissuanceSelection

    with pytest.raises(TypeError, match="issuer-held"):
        H1PreissuanceSelection()


class _HeldGate:
    @contextmanager
    def hold(self) -> Iterator[None]:
        yield


class _Enrollment:
    def __init__(self) -> None:
        self.reservations: list[dict[str, object]] = []

    def _reserve_terminal_clearance(self, **kwargs: object) -> object:
        self.reservations.append(kwargs)
        return object()


def _terminal_call(*, deadline_ns: int = 100) -> PublicPortCall:
    caller = BrokerSession(
        tenant_id="tenant",
        broker_epoch=1,
        generation_id="generation",
        owner_id="broker",
        session_id="broker-session",
    )
    return PublicPortCall(
        operation_id="agent_loop.prepare_terminal_work",
        request_id="terminal-request",
        caller=caller,
        callee=caller.model_copy(update={"owner_id": "agent_loop", "session_id": "loop-session"}),
        schema_id="chiplog.agent-loop.prepare-terminal-work.v1",
        canonical_payload=b"terminal request",
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            absolute_deadline_ns=deadline_ns,
            policy_version=1,
        ),
    )


def _port_with_proof(*, effects_returned_at_ns: int = 10) -> tuple[
    _H1RuntimePreissuancePort,
    object,
    _H1PreterminalCompletionProof,
    _Enrollment,
]:
    session = SimpleNamespace(_cut=object())
    effects_exchange = SimpleNamespace(returned_at_ns=effects_returned_at_ns)
    session._effects_exchange = effects_exchange
    snapshot = _H1PreterminalScopeSnapshot(
        session=session,
        first_path=cast("object", object()),
        native_cap=cast("object", object()),
        scope_cap=object(),
        delivery_receipt=object(),
        effects_exchange=effects_exchange,
        effects_source=cast("object", object()),
        terminal_sent=None,
        terminal_call_fingerprint=None,
        terminal_owner_frame_bytes=None,
    )
    proof = object.__new__(_H1PreterminalCompletionProof)
    enrollment = _Enrollment()
    port = object.__new__(_H1RuntimePreissuancePort)
    object.__setattr__(port, "_gate", _HeldGate())
    object.__setattr__(
        port,
        "_runtime",
        SimpleNamespace(_h1_live_completion_enrollment=enrollment),
    )
    object.__setattr__(port, "_preterminal_clearances", {})
    object.__setattr__(
        port,
        "_preterminal_proofs",
        {
            id(proof): _IssuedPreterminalCompletionProof(
                proof,
                session,
                enrollment,
                session._cut,
                snapshot,
                cast("object", object()),
            )
        },
    )
    return port, session, proof, enrollment


def test_terminal_bind_uses_issued_proof_once_without_final_fence_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, session, proof, enrollment = _port_with_proof()
    sent = _terminal_call()

    def final_fence_must_not_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("terminal bind must defer full fence replay to admission")

    monkeypatch.setattr(
        _H1RuntimePreissuancePort, "_final_fence_snapshot", final_fence_must_not_run
    )
    started_at_ns = perf_counter_ns()
    clearance = port._bind_terminal_clearance(session, proof, sent)
    bind_elapsed_ns = perf_counter_ns() - started_at_ns

    assert bind_elapsed_ns >= 0  # Retained test-local sent-to-bind timing instrumentation.
    assert port._preterminal_proofs == {}
    assert port._preterminal_clearances[id(clearance)][0] is clearance
    reserved = enrollment.reservations == [
        {
            "session": session,
            "cut": session._cut,
            "sent": sent,
            "scope_snapshot": port._preterminal_clearances[id(clearance)][1],
            "preterminal_wire": port._preterminal_clearances[id(clearance)][2],
        }
    ]
    assert reserved
    fingerprint, owner_frame = _terminal_call_identity(sent)
    snapshot = port._preterminal_clearances[id(clearance)][1]
    assert snapshot.terminal_sent is sent
    assert (snapshot.terminal_call_fingerprint, snapshot.terminal_owner_frame_bytes) == (
        fingerprint,
        owner_frame,
    )
    with pytest.raises(H1PreissuanceSourceViolation, match="unavailable or expired"):
        port._bind_terminal_clearance(session, proof, sent)


def test_terminal_bind_wrong_session_burns_proof_before_a_later_replay() -> None:
    port, session, proof, _enrollment = _port_with_proof()
    sent = _terminal_call()

    with pytest.raises(H1PreissuanceSourceViolation, match="unavailable or expired"):
        port._bind_terminal_clearance(object(), proof, sent)
    with pytest.raises(H1PreissuanceSourceViolation, match="unavailable or expired"):
        port._bind_terminal_clearance(session, proof, sent)


def test_terminal_bind_rejects_terminal_deadline_before_proved_effects_return() -> None:
    port, session, proof, enrollment = _port_with_proof(effects_returned_at_ns=101)

    with pytest.raises(H1PreissuanceSourceViolation, match="source changed before bind"):
        port._bind_terminal_clearance(session, proof, _terminal_call(deadline_ns=100))
    assert port._preterminal_proofs == {}
    assert enrollment.reservations == []
