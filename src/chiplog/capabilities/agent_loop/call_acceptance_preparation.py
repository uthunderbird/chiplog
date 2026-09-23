"""Pure call lifecycle proposals. No authentication, storage or dispatch authority.

Composition must verify the complete observed history and sources again at the
writer cut. A valid proposal is neither a winning CAS nor a committed result.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import Literal

from pydantic import ValidationError

from .call_acceptance_contracts import (
    AcceptConsequentialCallRequest,
    CallLifecycleObservation,
    CallPreparationCut,
    CallPreparationRejected,
    CallSubjectHead,
    CancelBeforeAcceptRequest,
    CancelledBeforeAcceptRecord,
    InitializedCallRecord,
    InitializedReadOnlyLineage,
    NotExecutedCallResultRecord,
    OriginalCallKey,
    PreparedConsequentialAcceptance,
    PreparedPreAcceptCancellation,
    ToolCallAcceptedRecord,
    ToolExecutionIntentRecord,
)
from .recovery_contracts import Absent, NonSchedulerFence, NotApplicable, Present, RecoveryDTO

FailureCode = Literal["DENIED", "STALE", "CONFLICT", "UNSUPPORTED", "INTEGRITY_FAULT", "HOLD"]


class _Rejected(ValueError):
    def __init__(self, code: FailureCode, reason: str) -> None:
        self.code = code
        super().__init__(reason)


def _require(condition: bool, reason: str, code: FailureCode = "INTEGRITY_FAULT") -> None:
    if not condition:
        raise _Rejected(code, reason)


def call_subject_id(original: OriginalCallKey) -> str:
    return "call:" + original.digest()


def call_record_reference(subject_id: str, record: RecoveryDTO) -> CallSubjectHead:
    digest = record.digest()
    return CallSubjectHead(
        subject_id=subject_id, revision=Present(head="record:" + digest, fingerprint=digest)
    )


def _canonical_base64(raw: str) -> None:
    try:
        decoded = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as error:
        raise _Rejected("INTEGRITY_FAULT", "invalid canonical base64 observation") from error
    _require(base64.b64encode(decoded).decode() == raw, "noncanonical base64 observation")


def _initialized(record: InitializedCallRecord, tenant: str) -> CallSubjectHead:
    call = record.call
    _require(call.original.tenant_id == tenant, "foreign original call tenant")
    _require(record.original_call_id == call_subject_id(call.original), "original call ID differs")
    _canonical_base64(call.canonical_call_base64)
    if call.classification == "READ_ONLY":
        _require(
            isinstance(call.retry_lineage, InitializedReadOnlyLineage),
            "read-only call lacks its original retry lineage",
        )
        assert isinstance(call.retry_lineage, InitializedReadOnlyLineage)
        _require(
            call.retry_lineage.lineage.original_call_id == record.original_call_id,
            "retry lineage belongs to another original call",
        )
    else:
        _require(
            isinstance(call.retry_lineage, NotApplicable), "non-read-only call has retry lineage"
        )
    return call_record_reference(record.original_call_id, record)


def _cut(cut: CallPreparationCut) -> dict[str, CallLifecycleObservation]:
    if not isinstance(cut.fence, NonSchedulerFence):
        raise _Rejected("UNSUPPORTED", "scheduler acceptance requires the registered lease checker")
    _require(
        cut.current_run.subject_id == cut.fence.run_id
        and cut.current_run.revision.head == cut.fence.run_head,
        "current Run and execution fence differ",
        "STALE",
    )
    inventory = cut.predecessor_inventory
    _require(
        inventory.tenant_id == cut.tenant_id
        and inventory.tenant_commit_sequence == cut.tenant_commit_sequence,
        "inventory belongs to another tenant or predecessor cut",
    )
    _require(
        cut.complete_call_inventory
        == call_record_reference("call-inventory:" + cut.tenant_id, inventory),
        "inventory content differs from its reference",
    )
    ids = tuple(row.original_call_id for row in inventory.ordered_calls)
    _require(ids == tuple(sorted(set(ids))), "inventory identities duplicate or are unordered")
    lineages: set[str] = set()
    for row in inventory.ordered_calls:
        expected = _initialized(row.initialized_record, cut.tenant_id)
        _require(
            row.original_call_id == row.initialized_record.original_call_id
            and row.initialized == expected
            and row.acceptance.initialized == expected.revision,
            "inventory member differs from its original initialization",
        )
        lineage = row.initialized_record.call.retry_lineage
        if isinstance(lineage, InitializedReadOnlyLineage):
            identity = lineage.lineage.lineage_id
            _require(identity not in lineages, "retry lineage reused by another original call")
            lineages.add(identity)
        acceptance = row.acceptance
        if acceptance.kind == "READ_ONLY_ACCEPTED":
            _require(
                isinstance(lineage, InitializedReadOnlyLineage)
                and acceptance.lineage == lineage.lineage
                and acceptance.lineage_id == lineage.lineage.lineage_id,
                "accepted read-only lineage differs from original initialization",
            )
        elif acceptance.kind == "CONSEQUENTIAL_ACCEPTED":
            _require(
                row.initialized_record.call.classification == "CONSEQUENTIAL",
                "non-consequential initialization cannot become consequential acceptance",
            )
            manifest: tuple[Present, ...] = (acceptance.accepted, acceptance.execution_intent)
            if isinstance(acceptance.external_effect_intent, Present):
                manifest += (acceptance.external_effect_intent,)
            _require(
                acceptance.complete_acceptance_manifest == manifest,
                "observed acceptance manifest differs",
            )
    source_ids = tuple(source.source_id for source in cut.sources)
    _require(len(set(source_ids)) == len(source_ids), "duplicate authority observation source")
    for source in cut.sources:
        _canonical_base64(source.canonical_value_base64)
        _require(
            source.observed_at_ns < source.valid_until_ns,
            "authority observation has no live acquisition interval",
            "STALE",
        )
    return {row.original_call_id: row for row in inventory.ordered_calls}


def _pending(
    cut: CallPreparationCut,
    original_call_id: str,
    original: OriginalCallKey,
    initialized: CallSubjectHead,
    record: InitializedCallRecord,
) -> None:
    rows = _cut(cut)
    _require(
        record.call.original == original
        and record.original_call_id == original_call_id
        and initialized == _initialized(record, cut.tenant_id),
        "requested original initialization differs",
    )
    current = rows.get(original_call_id)
    _require(current is not None, "original call absent from predecessor inventory", "STALE")
    assert current is not None
    _require(
        current.initialized == initialized and current.initialized_record == record,
        "requested initialization differs from observed member",
        "STALE",
    )
    _require(
        current.acceptance.kind == "INITIALIZED" and isinstance(current.terminal, Absent),
        "original call already accepted or terminal",
        "CONFLICT",
    )


def _proposal_digest(proposal: RecoveryDTO) -> str:
    value = json.loads(proposal.canonical_bytes())
    del value["proposal_fingerprint"]
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _failure(command_id: object, error: Exception) -> CallPreparationRejected:
    return CallPreparationRejected(
        command_id=command_id if isinstance(command_id, str) and command_id else "invalid-command",
        code=error.code if isinstance(error, _Rejected) else "INTEGRITY_FAULT",
        reason=str(error) if isinstance(error, _Rejected) else "malformed call preparation request",
    )


def prepare_consequential_acceptance(
    request: AcceptConsequentialCallRequest,
) -> PreparedConsequentialAcceptance | CallPreparationRejected:
    try:
        # Revalidate serialized nested data, including unchecked model_copy instances.
        request = AcceptConsequentialCallRequest.model_validate_json(request.canonical_bytes())
        binding = request.binding
        _pending(
            binding.cut,
            binding.original_call_id,
            binding.original,
            binding.initialized,
            request.initialized_record,
        )
        call = request.initialized_record.call
        _require(call.classification == "CONSEQUENTIAL", "call is not consequential", "DENIED")
        _require(
            binding.tool_schema == call.tool_schema and binding.tool_policy == call.tool_policy,
            "acceptance changes the initialized tool schema or policy",
            "STALE",
        )
        digest = request.digest()
        accepted = ToolCallAcceptedRecord(
            accepted_id="accepted:" + digest,
            source_command_id=request.command_id,
            binding=binding,
            execution_intent_id="execution:" + digest,
        )
        accepted_ref = call_record_reference(accepted.accepted_id, accepted)
        execution = ToolExecutionIntentRecord(
            execution_intent_id=accepted.execution_intent_id,
            original_call_id=binding.original_call_id,
            initialized=binding.initialized,
            accepted=accepted_ref,
            external_intent=binding.external_intent,
        )
        proposal = PreparedConsequentialAcceptance(
            source_request_fingerprint=digest,
            accepted=accepted,
            execution_intent=execution,
            complete_acceptance_manifest=(
                accepted_ref,
                call_record_reference(execution.execution_intent_id, execution),
                binding.external_intent,
            ),
            proposal_fingerprint="0" * 64,
        )
        return proposal.model_copy(update={"proposal_fingerprint": _proposal_digest(proposal)})
    except (_Rejected, ValidationError, ValueError, TypeError) as error:
        return _failure(getattr(request, "command_id", None), error)


def prepare_pre_accept_cancellation(
    request: CancelBeforeAcceptRequest,
) -> PreparedPreAcceptCancellation | CallPreparationRejected:
    try:
        request = CancelBeforeAcceptRequest.model_validate_json(request.canonical_bytes())
        _pending(
            request.cut,
            request.original_call_id,
            request.original,
            request.initialized,
            request.initialized_record,
        )
        digest = request.digest()
        terminal = CancelledBeforeAcceptRecord(
            terminal_id="cancelled:" + digest,
            source_command_id=request.command_id,
            original_call_id=request.original_call_id,
            original=request.original,
            initialized=request.initialized,
            cancellation_act=request.cancellation_act,
            cut=request.cut,
            not_executed_result_id="not-executed:" + digest,
        )
        terminal_ref = call_record_reference(terminal.terminal_id, terminal)
        result = NotExecutedCallResultRecord(
            result_id=terminal.not_executed_result_id,
            original_call_id=request.original_call_id,
            initialized=request.initialized,
            terminal=terminal_ref,
            outcome="NOT_EXECUTED",
        )
        proposal = PreparedPreAcceptCancellation(
            source_request_fingerprint=digest,
            terminal=terminal,
            result=result,
            complete_ordered_record_manifest=(
                terminal_ref,
                call_record_reference(result.result_id, result),
            ),
            proposal_fingerprint="0" * 64,
        )
        return proposal.model_copy(update={"proposal_fingerprint": _proposal_digest(proposal)})
    except (_Rejected, ValidationError, ValueError, TypeError) as error:
        return _failure(getattr(request, "command_id", None), error)
