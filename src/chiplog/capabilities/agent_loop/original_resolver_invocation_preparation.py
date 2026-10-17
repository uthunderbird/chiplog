"""Closed owner preparation for one selected R14 resolver invocation."""

from __future__ import annotations

import hashlib

from .call_acceptance_contracts import CallPreparationRejected
from .execution_recovery_contracts import (
    AcceptedOpenRecoveryOpeningV1,
    AcceptedOpenTerminalDispositionV1,
)
from .execution_recovery_observations import RecoverySourceRecord
from .original_recovery_contracts import (
    OriginalEffectEvidenceReducerPolicyV1,
    OriginalResolverAuthorityPolicyV1,
    PreparedOriginalResolverInvocationV1,
    PrepareOriginalResolverInvocationV1,
    original_effect_evidence_reducer_policy_v1,
    original_resolver_authority_policy_v1,
)


def _exact_source(source: RecoverySourceRecord, owner: str, schema: str) -> None:
    if (
        source.owner != owner
        or source.schema_id != schema
        or not source.canonical_record_bytes
        or source.subject.revision.kind != "PRESENT"
        or source.selected_decision.revision.kind != "PRESENT"
        or source.physical_record.revision.kind != "PRESENT"
        or source.selected_decision.subject_id != source.selected_decision.revision.head
        or source.physical_record.revision.head
        != "record:" + hashlib.sha256(source.canonical_record_bytes).hexdigest()
        or source.physical_record.revision.fingerprint
        != hashlib.sha256(source.canonical_record_bytes).hexdigest()
    ):
        raise ValueError("resolver invocation source is not an exact selected physical record")


def prepare_original_resolver_invocation_v1(
    request: PrepareOriginalResolverInvocationV1,
) -> PreparedOriginalResolverInvocationV1 | CallPreparationRejected:
    try:
        request = PrepareOriginalResolverInvocationV1.model_validate_json(request.canonical_bytes())
        invocation = request.invocation
        if request.command_id != invocation.invocation_id:
            raise ValueError("resolver invocation command identity differs")
        if invocation.invocation_id != (
            "r14-resolver-invocation:"
            + invocation.tenant_id
            + ":"
            + invocation.original.obligation_id
        ):
            raise ValueError("resolver invocation identity differs from exact obligation")
        opening_source = invocation.accepted_opening_source
        terminal_source = invocation.accepted_terminal_source
        resolver_source = invocation.resolver_policy_source
        reducer_source = invocation.reducer_policy_source
        _exact_source(
            opening_source, "agent_loop", "chiplog.execution.accepted-open-recovery-opening.v1"
        )
        _exact_source(
            terminal_source, "agent_loop", "chiplog.execution.accepted-open-terminal-disposition.v1"
        )
        _exact_source(
            resolver_source, "agent_loop", "chiplog.loop.original-resolver-authority-policy.v1"
        )
        _exact_source(
            reducer_source,
            "agent_loop",
            "chiplog.loop.original-effect-evidence-reducer-policy.v1",
        )
        opening = AcceptedOpenRecoveryOpeningV1.model_validate_json(
            opening_source.canonical_record_bytes
        )
        terminal = AcceptedOpenTerminalDispositionV1.model_validate_json(
            terminal_source.canonical_record_bytes
        )
        resolver_policy = OriginalResolverAuthorityPolicyV1.model_validate_json(
            resolver_source.canonical_record_bytes
        )
        reducer_policy = OriginalEffectEvidenceReducerPolicyV1.model_validate_json(
            reducer_source.canonical_record_bytes
        )
        if (
            invocation.original != opening.original
            or opening_source.subject.subject_id != opening.original.obligation_id
            or opening_source.subject.revision.head != opening.original.obligation_head
            or opening_source.subject.revision.fingerprint
            != hashlib.sha256(opening.canonical_bytes()).hexdigest()
            or opening_source.physical_record.subject_id != opening.opening_id
            or terminal_source.subject.subject_id != terminal.original_call_id
            or terminal_source.subject.revision.head
            != "record:" + hashlib.sha256(terminal.canonical_bytes()).hexdigest()
            or terminal_source.subject.revision.fingerprint
            != hashlib.sha256(terminal.canonical_bytes()).hexdigest()
            or terminal_source.physical_record.subject_id != terminal.terminal_id
            or resolver_source.subject.subject_id != resolver_policy.policy_id
            or resolver_source.physical_record.subject_id != resolver_policy.policy_id
            or reducer_source.subject.subject_id != reducer_policy.policy_id
            or reducer_source.physical_record.subject_id != reducer_policy.policy_id
            or invocation.tenant_id != resolver_policy.tenant_id
            or invocation.tenant_id != reducer_policy.tenant_id
            or invocation.original.original_call_id != terminal.original_call_id
            or terminal.kind != "RECOVERY_REQUIRED"
            or terminal.obligation_opening.subject_id != opening.opening_id
            or terminal.obligation_opening != opening_source.physical_record
            or resolver_policy != original_resolver_authority_policy_v1(invocation.tenant_id)
            or reducer_policy != original_effect_evidence_reducer_policy_v1(invocation.tenant_id)
            or opening_source.selected_decision != terminal_source.selected_decision
            or resolver_source.selected_decision != reducer_source.selected_decision
            or not invocation.trust_journal_head
            or not invocation.trust_response_fingerprint
        ):
            raise ValueError("resolver invocation sources or closed policies disagree")
        digest = hashlib.sha256(invocation.canonical_bytes()).hexdigest()
        return PreparedOriginalResolverInvocationV1(
            source_request_fingerprint=request.digest(),
            invocation=invocation,
            complete_batch_fingerprint=digest,
        )
    except (TypeError, ValueError) as error:
        return CallPreparationRejected(
            command_id=request.command_id, code="HOLD", reason=str(error)
        )
