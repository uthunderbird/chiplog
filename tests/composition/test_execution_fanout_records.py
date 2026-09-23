"""Independent verifier attacks; self-consistent digests are not semantic proof."""

import base64
import hashlib
import json

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallAuthorityObservation
from chiplog.capabilities.agent_loop.execution_fan_out_contracts import (
    ExecutionCapturedFanOutProposal,
)
from chiplog.capabilities.agent_loop.execution_fan_out_preparation import (
    prepare_execution_captured_fan_out,
)
from chiplog.capabilities.agent_loop.recovery_contracts import RecoveryDTO
from chiplog.composition.r14_execution_fanout_contracts import RetainedExecutionFanOutPreparation
from chiplog.composition.r14_execution_fanout_records import (
    build_envelope,
    physical_command,
    reference,
)
from chiplog.platform.broker import BrokerSession
from tests.support.execution_fan_out import bind_run, fixture


def _digest(value: RecoveryDTO) -> str:
    wire = json.loads(value.canonical_bytes())
    del wire["proposal_fingerprint"]
    return hashlib.sha256(
        json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


async def _evidence(complete: bool = False) -> RetainedExecutionFanOutPreparation:
    request = await fixture(complete=complete)
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


@pytest.mark.parametrize("complete", [False, True])
async def test_exact_owner_run_is_first_atomic_member(complete: bool) -> None:
    evidence = await _evidence(complete)
    envelope = build_envelope(evidence)
    command = physical_command(envelope)
    assert command.records[0].canonical_bytes == evidence.proposal.sealed_run.canonical_bytes()
    assert len(command.records) == 2 + len(evidence.proposal.fan_out.initialized_records)
    assert all(member.owner == "agent_loop" for member in command.records)


@pytest.mark.parametrize(
    "mutation", ["state", "origin", "predecessor", "event", "attempt", "turn", "calls"]
)
@pytest.mark.parametrize("complete", [False, True])
async def test_resigned_owner_run_mutations_reject(mutation: str, complete: bool) -> None:
    evidence = await _evidence(complete)
    build_envelope(evidence)
    run = evidence.proposal.sealed_run
    turn = run.turns[-1]
    attempt = turn.attempts[-1]
    if mutation == "state":
        run = run.model_copy(update={"state": "SUCCEEDED"})
    elif mutation == "origin":
        run = run.model_copy(update={"principal": "other"})
    elif mutation == "predecessor":
        run = run.model_copy(update={"predecessor": "other"})
    elif mutation == "event":
        run = run.model_copy(update={"event": "CompleteAcceptance"})
    else:
        if mutation == "attempt":
            attempt = attempt.model_copy(update={"receipt": "replacement", "head": "pending"})
            attempt = attempt.model_copy(update={"head": "execution-attempt:" + attempt.digest()})
            turn = turn.model_copy(update={"attempts": (attempt,)})
        elif mutation == "turn":
            turn = turn.model_copy(update={"ordinal": turn.ordinal + 1})
        else:
            turn = turn.model_copy(update={"initialized_calls": None})
        turn = turn.model_copy(update={"head": "pending"})
        turn = turn.model_copy(update={"head": "execution-turn:" + turn.digest()})
        run = run.model_copy(update={"turns": (turn,)})
    run = run.model_copy(update={"head": "pending"})
    run = run.model_copy(update={"head": "loop:" + run.digest()})
    proposal = evidence.proposal.model_copy(update={"sealed_run": run})
    proposal = proposal.model_copy(update={"proposal_fingerprint": _digest(proposal)})
    with pytest.raises(ValueError):
        build_envelope(evidence.model_copy(update={"proposal": proposal}))


@pytest.mark.parametrize("mutation", ["omit", "duplicate", "reorder", "manifest", "classification"])
async def test_resigned_initialized_set_mutations_reject(mutation: str) -> None:
    evidence = await _evidence()
    build_envelope(evidence)
    fanout = evidence.proposal.fan_out
    records = fanout.initialized_records
    if mutation == "omit":
        fanout = fanout.model_copy(update={"initialized_records": records[:-1]})
    elif mutation == "duplicate":
        fanout = fanout.model_copy(update={"initialized_records": (*records, records[-1])})
    elif mutation == "reorder":
        fanout = fanout.model_copy(update={"initialized_records": tuple(reversed(records))})
    elif mutation == "manifest":
        fanout = fanout.model_copy(
            update={
                "complete_ordered_record_manifest": fanout.complete_ordered_record_manifest[:-1]
            }
        )
    else:
        changed = records[-1].model_copy(
            update={"call": records[-1].call.model_copy(update={"classification": "PROPOSAL_ONLY"})}
        )
        fanout = fanout.model_copy(update={"initialized_records": (*records[:-1], changed)})
    fanout = fanout.model_copy(update={"proposal_fingerprint": _digest(fanout)})
    proposal = evidence.proposal.model_copy(update={"fan_out": fanout})
    proposal = proposal.model_copy(update={"proposal_fingerprint": _digest(proposal)})
    with pytest.raises(ValueError, match="captured semantics"):
        build_envelope(evidence.model_copy(update={"proposal": proposal}))


@pytest.mark.parametrize("mutation", ["tenant", "generation", "worker", "deadline"])
async def test_retained_exchange_must_match_capture_and_source_cut(mutation: str) -> None:
    evidence = await _evidence()
    build_envelope(evidence)
    if mutation == "deadline":
        evidence = evidence.model_copy(update={"deadline_ns": 21})
    else:
        key = {"tenant": "tenant_id", "generation": "generation_id", "worker": "session_id"}[
            mutation
        ]
        evidence = evidence.model_copy(
            update={"callee": evidence.callee.model_copy(update={key: "other"})}
        )
    with pytest.raises(ValueError):
        build_envelope(evidence)
