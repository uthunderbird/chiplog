"""Exact H1 current-scope owner frames remain invocation-local."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    H1OwnerCurrentCandidateV1,
)
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    CurrentHermeticExecutionScopeV1,
)
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_installed_h1_runtime,
)
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.broker import (
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortSuccess,
)
from tests.support.composition.h1_scope_current_wire_runtime import (
    _current_request,
)
from tests.support.h1_installed_launch import (
    installed_slot,
    prepare_installed_slot,
)


@pytest.mark.asyncio
async def test_real_current_owner_result_projects_anchor_refs_and_consumes_its_own_wire(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            read = await _current_request(runtime)
            result, wire = await runtime._read_current_hermetic_output_scope_with_wire(read)
            sent, returned = wire.sent, wire.returned

            assert type(result) is CurrentHermeticExecutionScopeV1
            assert result.ordered_current_source_refs == (
                read.admitted_authentication_ref,
                read.source_anchor.decision,
                read.source_anchor.record,
            )
            assert type(returned) is PublicPortSuccess
            assert returned.request_id == sent.request_id
            assert returned.responder == sent.callee
            assert (
                returned.schema_id
                == "chiplog.deployment-trust.current-hermetic-output-scope-result.v1"
            )
            assert wire.sent_at_ns <= wire.returned_at_ns < sent.budget.absolute_deadline_ns
            assert all(key.request_id != sent.request_id for key in runtime._h1_scope_wires)

            old_key = runtime._reserve_h1_scope_wire("scope_current", sent)
            runtime._record_h1_scope_wire(
                old_key,
                sent,
                returned,
                sent_at_ns=wire.sent_at_ns,
                returned_at_ns=wire.returned_at_ns,
            )
            fresh, fresh_wire = await runtime._read_current_hermetic_output_scope_with_wire(read)
            assert type(fresh) is CurrentHermeticExecutionScopeV1
            assert fresh_wire.sent.request_id != sent.request_id
            assert old_key in runtime._h1_scope_wires
            runtime._take_h1_scope_wire(
                "scope_current",
                request_id=old_key.request_id,
                caller=old_key.caller,
                callee=old_key.callee,
            )

            concurrent = await asyncio.gather(
                runtime._read_current_hermetic_output_scope_with_wire(read),
                runtime._read_current_hermetic_output_scope_with_wire(read),
            )
            assert all(type(value[0]) is CurrentHermeticExecutionScopeV1 for value in concurrent)
            assert concurrent[0][1].sent.request_id != concurrent[1][1].sent.request_id
            assert not any(key.role == "scope_current" for key in runtime._h1_scope_wires)


@pytest.mark.asyncio
async def test_gate_held_current_replay_rejects_a_candidate_stale_after_capture(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            first_read = await _current_request(runtime)
            first, first_wire = await runtime._read_current_hermetic_output_scope_with_wire(
                first_read
            )
            assert type(first) is CurrentHermeticExecutionScopeV1
            assert type(first_wire.returned) is PublicPortSuccess
            first_candidate = H1OwnerCurrentCandidateV1.model_validate_json(
                first_wire.returned.canonical_payload
            )

            with runtime._authority_gate().hold():
                current = runtime._replay_current_hermetic_output_scope_held(
                    first_read, first_candidate, callee=first_wire.sent.callee
                )

            runtime.restart_generation()

            with runtime._authority_gate().hold():
                stale = runtime._replay_current_hermetic_output_scope_held(
                    first_read, first_candidate, callee=first_wire.sent.callee
                )

            assert current == first
            assert stale.disposition == "STALE"
            assert not any(key.role == "scope_current" for key in runtime._h1_scope_wires)


@pytest.mark.asyncio
async def test_gate_held_current_replay_rejects_wrong_candidate_and_session(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            read = await _current_request(runtime)
            current, wire = await runtime._read_current_hermetic_output_scope_with_wire(read)
            assert type(current) is CurrentHermeticExecutionScopeV1
            assert type(wire.returned) is PublicPortSuccess
            candidate = H1OwnerCurrentCandidateV1.model_validate_json(
                wire.returned.canonical_payload
            )
            wrong_candidate = candidate.model_copy(
                update={"current": candidate.current.model_copy(update={"selector_generation": 1})}
            )

            with runtime._authority_gate().hold():
                denied = runtime._replay_current_hermetic_output_scope_held(
                    read, wrong_candidate, callee=wire.sent.callee
                )
                stale = runtime._replay_current_hermetic_output_scope_held(
                    read, candidate, callee=wire.sent.caller
                )

            assert denied.disposition == "DENIED"
            assert stale.disposition == "STALE"
            assert not any(key.role == "scope_current" for key in runtime._h1_scope_wires)


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
