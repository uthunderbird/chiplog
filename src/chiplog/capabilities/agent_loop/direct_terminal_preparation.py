"""Isolated V30 R14 accepted-call direct terminal proposal preparation."""

from __future__ import annotations

import hashlib
import json

from chiplog.capabilities.effects.dispatch_authority_contracts import SelectedEffectHistoryMember
from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
from chiplog.capabilities.effects.dispatch_v2 import reference as effects_reference

from .call_acceptance_contracts import (
    CallPreparationRejected,
    CallSubjectHead,
    ToolCallAcceptedRecord,
    ToolExecutionIntentRecord,
)
from .call_acceptance_preparation import call_record_reference
from .direct_effect_terminal_contracts import (
    classify_direct_effect_terminal_v1,
    translate_selected_outcome_v1,
)
from .direct_terminal_contracts import (
    AcceptedDirectTerminalAccountingV1,
    AcceptedDirectTerminalDispositionV1,
    AcceptedDirectTerminalResultV1,
    PrepareAcceptedDirectTerminalV1,
    PreparedAcceptedDirectTerminalV1,
)
from .execution_contracts import ExecutionRunRecord
from .recovery_contracts import Present
from .recovery_domain import RecoveryHold, RecoveryIntegrityFault


def _run_ref(run: ExecutionRunRecord) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=run.run_id,
        revision=Present(head=run.head, fingerprint=run.digest()),
    )


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _rejected(command_id: str, error: Exception) -> CallPreparationRejected:
    return CallPreparationRejected(
        command_id=command_id,
        code="HOLD" if isinstance(error, RecoveryHold) else "INTEGRITY_FAULT",
        reason=str(error),
    )


def prepare_accepted_direct_terminal_v1(
    request: PrepareAcceptedDirectTerminalV1,
) -> PreparedAcceptedDirectTerminalV1 | CallPreparationRejected:
    """Prepare the first typed R14 terminal only from one exact closed R16 result."""
    try:
        request = PrepareAcceptedDirectTerminalV1.model_validate_json(request.canonical_bytes())
        cut = request.cut
        content = cut.model_dump(mode="json")
        del content["cut_fingerprint"]
        expected_cut_fingerprint = _sha(
            json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        )
        if cut.cut_fingerprint != expected_cut_fingerprint:
            raise RecoveryIntegrityFault("accepted/direct terminal cut fingerprint differs")
        if (
            cut.selected_outcome_fingerprint != _sha(cut.selected_outcome_bytes)
            or cut.outcome_commit_sequence > cut.tenant_commit_sequence
            or cut.selected_outcome_record_id not in cut.batch_record_ids
            or len(set(cut.batch_record_ids)) != len(cut.batch_record_ids)
        ):
            raise RecoveryIntegrityFault("accepted/direct selected publication provenance differs")

        outcome = DispatchOutcomeRecordV2.model_validate_json(cut.selected_outcome_bytes)
        member = SelectedEffectHistoryMember.model_validate_json(cut.selected_effect_member_bytes)
        run = ExecutionRunRecord.model_validate_json(cut.current_run_bytes)
        accepted = ToolCallAcceptedRecord.model_validate_json(cut.accepted_record_bytes)
        execution_intent = ToolExecutionIntentRecord.model_validate_json(
            cut.execution_intent_record_bytes
        )
        if (
            outcome.snapshot.obligation.state != "CLOSED"
            or outcome.snapshot.conflicting_evidence
            or outcome.snapshot.state not in ("CONFIRMED", "FAILED_NO_EFFECT")
        ):
            raise RecoveryHold(
                "accepted direct terminal requires one nonconflicting closed R16 result"
            )
        status = translate_selected_outcome_v1(outcome)
        classification = classify_direct_effect_terminal_v1(status)
        intent = outcome.snapshot.intent
        intent_ref = effects_reference(intent.intent_id, intent.canonical_bytes())
        accepted_ref = call_record_reference(accepted.accepted_id, accepted)
        execution_intent_ref = call_record_reference(
            execution_intent.execution_intent_id, execution_intent
        )
        expected_decision = member.selected_decision
        if (
            outcome.canonical_bytes() != cut.selected_outcome_bytes
            or member.canonical_bytes() != cut.selected_effect_member_bytes
            or member.record != outcome.record
            or member.record_bytes != cut.selected_outcome_bytes
            or member.kind != "RECONCILED"
            or member.intent != intent_ref
            or member.semantics != status.semantic_binding
            or classification.semantic_binding != cut.semantic_binding
            or expected_decision.subject_id != cut.selected_decision.subject_id
            or expected_decision.head != cut.selected_decision.revision.head
            or expected_decision.fingerprint != cut.selected_decision.revision.fingerprint
            or member.publication_ordinal != cut.publication_ordinal
            or run.canonical_bytes() != cut.current_run_bytes
            or _run_ref(run) != cut.current_run
            or run.tenant != cut.tenant_id
            or run.state != "ACTIVE"
            or accepted.canonical_bytes() != cut.accepted_record_bytes
            or execution_intent.canonical_bytes() != cut.execution_intent_record_bytes
            or accepted_ref != cut.accepted
            or execution_intent_ref != cut.execution_intent
            or accepted.execution_intent_id != execution_intent.execution_intent_id
            or accepted.binding.original_call_id != cut.original_call_id
            or accepted.binding.initialized != cut.initialized
            or accepted.binding.external_intent != cut.external_effect_intent
            or execution_intent.original_call_id != cut.original_call_id
            or execution_intent.initialized != cut.initialized
            or execution_intent.accepted != cut.accepted
            or execution_intent.external_intent != cut.external_effect_intent
            or intent_ref.subject_id != cut.external_effect_intent.subject_id
            or intent_ref.fingerprint != cut.external_effect_intent.revision.fingerprint
            or cut.initialized.subject_id != cut.original_call_id
            or request.command_id != "accepted-direct-terminal:" + cut.cut_fingerprint
            or cut.prior_terminal != "ABSENT"
        ):
            raise RecoveryHold(
                "accepted direct terminal source and exact R16 selection do not join"
            )

        terminal = AcceptedDirectTerminalDispositionV1(
            kind=classification.disposition,
            terminal_id="accepted-direct-terminal-record:" + cut.cut_fingerprint,
            source_cut_fingerprint=cut.cut_fingerprint,
            original_call_id=cut.original_call_id,
            response_id=cut.response_id,
            accepted=cut.accepted,
            external_effect_intent=cut.external_effect_intent,
            selected_effect_terminal=outcome.record,
            mapping_id="R14_DIRECT_EFFECT_TERMINAL_V1",
            mapping_version="r14-direct-effect-terminal.v1",
        )
        terminal_digest = _sha(terminal.canonical_bytes())
        terminal_ref = CallSubjectHead(
            subject_id=terminal.terminal_id,
            revision=Present(head="record:" + terminal_digest, fingerprint=terminal_digest),
        )
        result = AcceptedDirectTerminalResultV1(
            result_id="accepted-direct-terminal-result:" + cut.cut_fingerprint,
            source_cut_fingerprint=cut.cut_fingerprint,
            original_call_id=cut.original_call_id,
            terminal=terminal_ref,
            classification=classification,
        )
        result_digest = _sha(result.canonical_bytes())
        result_ref = CallSubjectHead(
            subject_id=result.result_id,
            revision=Present(head="record:" + result_digest, fingerprint=result_digest),
        )
        accounting = AcceptedDirectTerminalAccountingV1(
            accounting_id="accepted-direct-terminal-accounting:" + cut.cut_fingerprint,
            source_cut_fingerprint=cut.cut_fingerprint,
            original_call_id=cut.original_call_id,
            response_id=cut.response_id,
            accepted=cut.accepted,
            execution_intent=cut.execution_intent,
            external_effect_intent=cut.external_effect_intent,
            terminal=terminal_ref,
            result=result_ref,
            selected_outcome_record_id=cut.selected_outcome_record_id,
            selected_outcome_fingerprint=cut.selected_outcome_fingerprint,
            selected_decision=cut.selected_decision,
            outcome_commit_sequence=cut.outcome_commit_sequence,
            publication_ordinal=cut.publication_ordinal,
            batch_record_ids=cut.batch_record_ids,
        )
        return PreparedAcceptedDirectTerminalV1(
            source_request_fingerprint=request.digest(),
            accounting=accounting,
            terminal=terminal,
            result=result,
        )
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        return _rejected(request.command_id, error)
