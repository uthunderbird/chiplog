"""Versioned registry selection for physical zero-call Complete seals."""

from __future__ import annotations

import base64
import hashlib

import pytest
from pydantic import ValidationError

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallAuthorityObservation
from chiplog.capabilities.agent_loop.execution_fan_out_contracts import (
    ExecutionCapturedFanOutProposal,
)
from chiplog.capabilities.agent_loop.execution_fan_out_preparation import (
    prepare_execution_captured_fan_out,
)
from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
    RECOVERY_FRONTIER_REGISTRY_SCHEMA,
    decode_frontier_registry,
    execution_h1_zero_call_frontier_registry_v2,
    execution_zero_call_frontier_registry,
    frontier_registry_reference,
)
from chiplog.composition.r14_execution_complete_seal_records import (
    RetainedExecutionCompleteSeal,
    RetainedExecutionCompleteSealV2,
    build_complete_seal_envelope,
    complete_seal_physical_command,
    retained_execution_complete_seal,
)
from chiplog.composition.r14_execution_fanout_contracts import RetainedExecutionFanOutPreparation
from chiplog.composition.r14_execution_fanout_records import reference
from chiplog.platform.broker import BrokerSession
from tests.support.execution_fan_out import bind_run, fixture


async def _complete_evidence() -> RetainedExecutionFanOutPreparation:
    request = await fixture(complete=True)
    run = request.captured_run
    turn = run.turns[-1]
    attempt = turn.attempts[-1]
    worker = "1:0:callee"
    attempt = attempt.model_copy(
        update={
            "worker_session": worker,
            "manifest": attempt.manifest.model_copy(update={"worker_session": worker}),
        }
    )
    run = run.model_copy(
        update={
            "worker_session": worker,
            "turns": (turn.model_copy(update={"attempts": (attempt,)}),),
        }
    )
    request = bind_run(request, run)
    registry = request.tool_registry
    source = CallAuthorityObservation(
        source_id=registry.registry_id,
        family="TOOL_SCHEMA",
        source=reference(registry.registry_id, registry),
        generation="0",
        frontier="1",
        canonical_value_base64=base64.b64encode(registry.canonical_bytes()).decode(),
        observed_at_ns=1,
        valid_until_ns=20,
    )
    cut = request.request.cut.model_copy(
        update={
            "authority_registry": request.tool_registry_head,
            "sources": (source,),
            "fence": request.request.cut.fence.model_copy(update={"worker_session_id": worker}),
        }
    )
    request = request.model_copy(
        update={"request": request.request.model_copy(update={"cut": cut})}
    )
    proposal = prepare_execution_captured_fan_out(request)
    assert isinstance(proposal, ExecutionCapturedFanOutProposal)
    return RetainedExecutionFanOutPreparation(
        request=request,
        proposal=proposal,
        expected_snapshot_fingerprint="a" * 64,
        caller=BrokerSession(
            tenant_id="tenant",
            broker_epoch=1,
            generation_id="0",
            owner_id="broker",
            session_id="caller",
        ),
        callee=BrokerSession(
            tenant_id="tenant",
            broker_epoch=1,
            generation_id="0",
            owner_id="agent_loop",
            session_id="callee",
        ),
        request_id="request",
        deadline_ns=10,
    )


async def test_default_complete_seal_retains_legacy_v1_wire_and_physical_registry() -> None:
    evidence = await _complete_evidence()
    retained = retained_execution_complete_seal(evidence)
    assert type(retained) is RetainedExecutionCompleteSeal

    # This is the historical wire decoder and must continue to reproduce its command.
    restored = RetainedExecutionCompleteSeal.model_validate_json(retained.canonical_bytes())
    assert restored.canonical_bytes() == retained.canonical_bytes()
    with pytest.raises(ValidationError):
        RetainedExecutionCompleteSealV2.model_validate_json(retained.canonical_bytes())
    command = complete_seal_physical_command(build_complete_seal_envelope(restored))
    registry = execution_zero_call_frontier_registry()
    registry_record = command.records[-1]
    assert registry_record.schema_id == RECOVERY_FRONTIER_REGISTRY_SCHEMA
    assert registry_record.canonical_bytes == registry.canonical_bytes()
    assert registry_record.record_id == (
        "recovery-frontier-registry:"
        + evidence.proposal.sealed_run.head
        + ":"
        + hashlib.sha256(registry.canonical_bytes()).hexdigest()
    )
    assert (
        decode_frontier_registry(
            registry_record.schema_id,
            registry_record.canonical_bytes,
            expected_reference=frontier_registry_reference(registry),
        )
        == registry
    )


async def test_explicit_h1_v2_profile_physically_publishes_v2_registry() -> None:
    evidence = await _complete_evidence()
    retained = retained_execution_complete_seal(evidence, profile="H1_V2")
    assert type(retained) is RetainedExecutionCompleteSealV2
    assert (
        RetainedExecutionCompleteSealV2.model_validate_json(retained.canonical_bytes()) == retained
    )

    command = complete_seal_physical_command(build_complete_seal_envelope(retained))
    registry = execution_h1_zero_call_frontier_registry_v2()
    registry_record = command.records[-1]
    assert registry_record.schema_id == RECOVERY_FRONTIER_REGISTRY_SCHEMA
    assert registry_record.canonical_bytes == registry.canonical_bytes()
    assert registry_record.record_id == (
        "recovery-frontier-registry:"
        + evidence.proposal.sealed_run.head
        + ":"
        + hashlib.sha256(registry.canonical_bytes()).hexdigest()
    )
    assert (
        decode_frontier_registry(
            registry_record.schema_id,
            registry_record.canonical_bytes,
            expected_reference=frontier_registry_reference(registry),
        )
        == registry
    )
