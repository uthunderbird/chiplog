"""Pure physical producer for an execution call cancelled before acceptance.

It validates the retained owner exchange and projects only its two call-lifecycle
members.  Selection, current-source admission, replay and public runtime mounting
remain outside this module.
"""

from __future__ import annotations

import base64
import hashlib
import json

from chiplog.adapters.driven.loop_sqlite import OWNER
from chiplog.capabilities.agent_loop import call_acceptance_contracts as call
from chiplog.capabilities.agent_loop.recovery_contracts import RecoveryDTO
from chiplog.composition.r14_cancellation_contracts import (
    CANCELLATION_SCHEMA,
    NOT_EXECUTED_SCHEMA,
)
from chiplog.composition.r14_execution_cancellation_contracts import (
    EXECUTION_CANCELLATION_OPERATION,
    MAX_EXECUTION_CANCELLATION_BYTES,
    ExecutionCancellationPhysicalEnvelope,
    RetainedExecutionCancellationPreparation,
)
from chiplog.composition.r14_execution_fanout_records import reference
from chiplog.composition.r14_fanout_contracts import FanOutPhysicalMember
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError("invalid execution cancellation: " + reason)


def _without(value: RecoveryDTO, field: str) -> str:
    body = json.loads(value.canonical_bytes())
    del body[field]
    return hashlib.sha256(
        json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _member(identity: str, schema: str, raw: bytes) -> FanOutPhysicalMember:
    return FanOutPhysicalMember(
        record_id=identity,
        owner=OWNER,
        schema_id=schema,
        canonical_payload_base64=base64.b64encode(raw).decode(),
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


def _verify(evidence: RetainedExecutionCancellationPreparation) -> None:
    act, trust, request, proposal = (
        evidence.act,
        evidence.trust,
        evidence.request,
        evidence.proposal,
    )
    _require(
        act.submission.original_call_id == request.original_call_id
        and act.submission.initialized == request.initialized
        and act.submission.current_run == request.cut.current_run
        and act.authenticated_reference_bytes == trust.authenticated_reference_bytes
        and act.trust_evidence_fingerprint == trust.digest(),
        "act, request or retained trust differs",
    )
    _require(
        request.original_call_id == request.initialized_record.original_call_id
        and request.original == request.initialized_record.call.original
        and request.initialized == act.submission.initialized
        and request.cut.tenant_id == evidence.run_predecessor.tenant
        and request.cut.current_run == act.submission.current_run
        and bool(request.cut.sources),
        "request source, initialized call or Run binding differs",
    )
    digest = request.digest()
    terminal = call.CancelledBeforeAcceptRecord(
        terminal_id="cancelled:" + digest,
        source_command_id=request.command_id,
        original_call_id=request.original_call_id,
        original=request.original,
        initialized=request.initialized,
        cancellation_act=request.cancellation_act,
        cut=request.cut,
        not_executed_result_id="not-executed:" + digest,
    )
    result = call.NotExecutedCallResultRecord(
        result_id=terminal.not_executed_result_id,
        original_call_id=request.original_call_id,
        initialized=request.initialized,
        terminal=reference(terminal.terminal_id, terminal),
        outcome="NOT_EXECUTED",
    )
    _require(
        proposal.source_request_fingerprint == digest
        and proposal.terminal == terminal
        and proposal.result == result
        and proposal.complete_ordered_record_manifest
        == (reference(terminal.terminal_id, terminal), reference(result.result_id, result))
        and proposal.proposal_fingerprint == _without(proposal, "proposal_fingerprint"),
        "owner terminal, result or manifest differs from request",
    )
    sent, returned = evidence.owner_request, evidence.owner_response
    _require(
        sent.operation_id == "agent_loop.prepare_pre_accept_cancellation"
        and sent.schema_id == "chiplog.call.cancellation-preparation.v1"
        and sent.canonical_payload == request.canonical_bytes()
        and sent.caller.owner_id == "broker"
        and sent.callee.owner_id == OWNER
        and sent.caller.tenant_id == sent.callee.tenant_id == request.cut.tenant_id
        and sent.caller.broker_epoch == sent.callee.broker_epoch
        and sent.caller.generation_id == sent.callee.generation_id
        and returned.request_id == sent.request_id
        and returned.responder == sent.callee
        and returned.schema_id == "chiplog.call.preparation-result.v1"
        and returned.canonical_payload == proposal.canonical_bytes(),
        "retained owner exchange differs from request or proposal",
    )


def build_execution_cancellation_envelope(
    evidence: RetainedExecutionCancellationPreparation,
) -> ExecutionCancellationPhysicalEnvelope:
    """Derive terminal then NOT_EXECUTED records from one retained owner exchange."""
    evidence = RetainedExecutionCancellationPreparation.model_validate_json(
        evidence.canonical_bytes()
    )
    _verify(evidence)
    proposal = evidence.proposal
    records = (
        _member(
            proposal.terminal.terminal_id,
            CANCELLATION_SCHEMA,
            proposal.terminal.canonical_bytes(),
        ),
        _member(
            proposal.result.result_id,
            NOT_EXECUTED_SCHEMA,
            proposal.result.canonical_bytes(),
        ),
    )
    _require(
        len({member.record_id for member in records}) == 2
        and all(
            "\n" not in member.record_id and "\r" not in member.record_id for member in records
        ),
        "physical member identities differ or are unsafe",
    )
    envelope = ExecutionCancellationPhysicalEnvelope(
        tenant_id=evidence.request.cut.tenant_id,
        idempotency_key=evidence.request.command_id,
        expected_head=evidence.request.cut.tenant_commit_sequence,
        retained_preparation_fingerprint=evidence.digest(),
        records=records,
        request_fingerprint="0" * 64,
    )
    envelope = envelope.model_copy(
        update={"request_fingerprint": _without(envelope, "request_fingerprint")}
    )
    _require(
        len(envelope.canonical_bytes()) <= MAX_EXECUTION_CANCELLATION_BYTES,
        "physical byte bound exceeded",
    )
    return envelope


def execution_cancellation_command(
    evidence: RetainedExecutionCancellationPreparation,
) -> PhysicalPublicationCommand:
    """Return the exact two-record command; this function has no writer authority."""
    envelope = build_execution_cancellation_envelope(evidence)
    return PhysicalPublicationCommand(
        tenant_id=envelope.tenant_id,
        operation_kind=EXECUTION_CANCELLATION_OPERATION,
        idempotency_key=envelope.idempotency_key,
        request_fingerprint=envelope.request_fingerprint,
        expected_head=envelope.expected_head,
        fence_generation=envelope.fence_generation,
        expected_fence_frontier=envelope.expected_fence_frontier,
        minimum_fence_frontier=envelope.minimum_fence_frontier,
        records=tuple(
            PhysicalRecord(
                member.record_id,
                member.owner,
                member.schema_id,
                base64.b64decode(member.canonical_payload_base64, validate=True),
                member.fingerprint,
            )
            for member in envelope.records
        ),
    )
