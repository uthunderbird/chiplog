"""Pure owner proposal for closing one selected original R14 obligation."""

from __future__ import annotations

import base64
import hashlib
import json

from pydantic import BaseModel, TypeAdapter, ValidationError

from chiplog.capabilities.agent_loop.direct_effect_terminal_contracts import (
    RESULT_SCHEMA,
    translate_selected_outcome_v1,
)
from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
from chiplog.capabilities.effects.dispatch_v2 import reference as effects_reference

from .call_acceptance_contracts import CallPreparationRejected, CallSubjectHead
from .execution_recovery_contracts import (
    AcceptedOpenRecoveryOpeningV1,
    AcceptedOpenTerminalDispositionV1,
)
from .execution_recovery_observations import RecoverySourceRecord
from .original_recovery_contracts import (
    IndependentResolverAuthority,
    OriginalEffectEvidenceReducerPolicyV1,
    OriginalObligationClosureRecord,
    OriginalResolutionBasis,
    OriginalResolverAuthorityPolicyV1,
    OriginalResolverBatchRecord,
    OriginalResolverInvocationV1,
    PreparedOriginalCallResolution,
    PrepareOriginalCallResolution,
    RecoveredCallOutcomeRecord,
    original_effect_evidence_reducer_policy_v1,
    original_resolver_authority_policy_v1,
)
from .recovery_contracts import Present

_OPENING_SCHEMA = "chiplog.execution.accepted-open-recovery-opening.v1"
_TERMINAL_SCHEMA = "chiplog.execution.accepted-open-terminal-disposition.v1"
_WITNESS_SCHEMA = "chiplog.effects.dispatch-outcome-record.v2"
_RESOLVER_POLICY_SCHEMA = "chiplog.loop.original-resolver-authority-policy.v1"
_REDUCER_POLICY_SCHEMA = "chiplog.loop.original-effect-evidence-reducer-policy.v1"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _physical(source: RecoverySourceRecord, *, owner: str, schema: str) -> CallSubjectHead:
    if (
        source.owner != owner
        or source.schema_id != schema
        or source.physical_record.revision.kind != "PRESENT"
        or source.selected_decision.revision.kind != "PRESENT"
        or not source.selected_decision.subject_id
        or not source.physical_record.subject_id
    ):
        raise ValueError("original resolver source is not an exact selected physical record")
    return source.physical_record


def _decode_source[T: BaseModel](
    source: RecoverySourceRecord, type_: type[T], *, owner: str, schema: str
) -> T:
    _physical(source, owner=owner, schema=schema)
    try:
        adapter: TypeAdapter[T] = TypeAdapter(type_)
        record = adapter.validate_json(source.canonical_record_bytes)
    except (TypeError, ValueError, ValidationError) as error:
        raise ValueError(
            "original resolver source bytes have an invalid registered shape"
        ) from error
    canonical_bytes = getattr(record, "canonical_bytes", None)
    if canonical_bytes is None or canonical_bytes() != source.canonical_record_bytes:
        raise ValueError("original resolver source bytes are noncanonical")
    return record


def _digest_reference(subject_id: str, head: str, fingerprint: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=subject_id,
        revision=Present(head=head, fingerprint=fingerprint),
    )


def _batch_fingerprint(
    rows: tuple[tuple[str, str, str, str, bytes], ...],
) -> str:
    manifest = [
        {
            "owner": owner,
            "record_kind": record_kind,
            "record_id": record_id,
            "schema_id": schema_id,
            "canonical_bytes": base64.urlsafe_b64encode(raw).decode("ascii"),
            "fingerprint": _sha(raw),
        }
        for owner, record_kind, schema_id, record_id, raw in rows
    ]
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return _sha(raw)


def prepare_original_call_resolution(
    request: PrepareOriginalCallResolution,
) -> PreparedOriginalCallResolution | CallPreparationRejected:
    """Prepare basis, closure, outcome and batch; grant no publication authority."""
    try:
        request = PrepareOriginalCallResolution.model_validate_json(request.canonical_bytes())
        cut = request.cut
        original = cut.original
        if (
            cut.tenant_commit_sequence < 1
            or cut.obligation_stream.owner != "agent_loop"
            or cut.obligation_stream.stream_id != original.obligation_stream_id
            or cut.expected_closure.kind != "ABSENT"
            or cut.expected_recovered_outcome.kind != "ABSENT"
            or cut.complete_selected_effects_reductions
        ):
            raise ValueError(
                "original resolver cut is stale, already closed or has effects reductions"
            )
        if not isinstance(cut.authority, IndependentResolverAuthority):
            raise ValueError("original recovery requires independent resolver authority")

        opening = _decode_source(
            cut.current_obligation,
            AcceptedOpenRecoveryOpeningV1,
            owner="agent_loop",
            schema=_OPENING_SCHEMA,
        )
        terminal = _decode_source(
            cut.call_terminal,
            AcceptedOpenTerminalDispositionV1,
            owner="agent_loop",
            schema=_TERMINAL_SCHEMA,
        )
        policy = _decode_source(
            cut.resolver_policy,
            OriginalResolverAuthorityPolicyV1,
            owner="agent_loop",
            schema=_RESOLVER_POLICY_SCHEMA,
        )
        reducer = _decode_source(
            cut.reducer_policy,
            OriginalEffectEvidenceReducerPolicyV1,
            owner="agent_loop",
            schema=_REDUCER_POLICY_SCHEMA,
        )
        witness = _decode_source(
            cut.selected_witness,
            DispatchOutcomeRecordV2,
            owner="effects",
            schema=_WITNESS_SCHEMA,
        )
        authority_record = _decode_source(
            cut.authority.registered_authority,
            OriginalResolverAuthorityPolicyV1,
            owner="agent_loop",
            schema=_RESOLVER_POLICY_SCHEMA,
        )
        invocation = _decode_source(
            cut.authority.authenticated_invocation,
            OriginalResolverInvocationV1,
            owner="agent_loop",
            schema="chiplog.loop.original-resolver-invocation.v1",
        )
        if (
            policy != original_resolver_authority_policy_v1(cut.tenant_id)
            or reducer != original_effect_evidence_reducer_policy_v1(cut.tenant_id)
            or authority_record != policy
            or original.resolver_id != policy.resolver_id
            or original.resolver_version != policy.resolver_version
            or original.reducer_id != reducer.reducer_id
            or original.reducer_version != reducer.reducer_version
            or opening.original != original
            or cut.current_obligation.physical_record.subject_id != opening.opening_id
            or cut.current_obligation.physical_record.revision.head
            != "record:" + _sha(cut.current_obligation.canonical_record_bytes)
            or cut.current_obligation.physical_record.revision.fingerprint
            != _sha(cut.current_obligation.canonical_record_bytes)
            or cut.current_obligation.subject.subject_id != original.obligation_id
            or cut.current_obligation.subject.revision.head != original.obligation_head
            or cut.current_obligation.subject.revision.fingerprint
            != _sha(cut.current_obligation.canonical_record_bytes)
            or terminal.kind != "RECOVERY_REQUIRED"
            or terminal.original_call_id != original.original_call_id
            or cut.call_terminal.physical_record.subject_id != terminal.terminal_id
            or cut.call_terminal.physical_record.revision.head
            != "record:" + _sha(cut.call_terminal.canonical_record_bytes)
            or cut.call_terminal.physical_record.revision.fingerprint
            != _sha(cut.call_terminal.canonical_record_bytes)
            or terminal.obligation_opening != cut.current_obligation.physical_record
            or cut.call_terminal.subject.subject_id != original.original_call_id
            or cut.call_terminal.subject.revision.fingerprint
            != _sha(cut.call_terminal.canonical_record_bytes)
            or invocation.original != original
            or invocation.tenant_id != cut.tenant_id
            or invocation.principal_id != policy.principal_id
            or not invocation.trust_journal_head
            or not invocation.trust_response_fingerprint
            or cut.authority.authenticated_invocation.physical_record.subject_id
            != invocation.invocation_id
            or cut.authority.authenticated_invocation.physical_record.revision.head
            != "record:" + _sha(invocation.canonical_bytes())
            or cut.authority.authenticated_invocation.physical_record.revision.fingerprint
            != _sha(invocation.canonical_bytes())
            or invocation.accepted_opening_source != cut.current_obligation
            or invocation.accepted_terminal_source != cut.call_terminal
            or invocation.resolver_policy_source != cut.resolver_policy
            or invocation.reducer_policy_source != cut.reducer_policy
        ):
            raise ValueError("selected original obligation, policies or resolver authority differ")

        expected_intent = effects_reference(
            witness.snapshot.intent.intent_id, witness.snapshot.intent.canonical_bytes()
        )
        if (
            (expected_intent.subject_id, expected_intent.head, expected_intent.fingerprint)
            != (
                terminal.external_effect_intent.subject_id,
                terminal.external_effect_intent.revision.head,
                terminal.external_effect_intent.revision.fingerprint,
            )
            or cut.selected_witness.physical_record != cut.selected_witness.subject
            or cut.selected_witness.physical_record.subject_id != witness.record.subject_id
            or cut.selected_witness.physical_record.revision.head != witness.record.head
            or cut.selected_witness.physical_record.revision.fingerprint
            != witness.record.fingerprint
        ):
            raise ValueError("selected R16 witness does not bind the original accepted intent")
        status = translate_selected_outcome_v1(witness)

        evidence_refs: list[CallSubjectHead] = []
        if len(cut.complete_ordered_evidence) != len(witness.snapshot.evidence):
            raise ValueError("selected R16 evidence source inventory is incomplete")
        from chiplog.capabilities.effects.dispatch_outcomes import evidence_head

        for source, evidence, expected_head in zip(
            cut.complete_ordered_evidence,
            witness.snapshot.evidence,
            witness.snapshot.evidence_heads,
            strict=True,
        ):
            carrier = _decode_source(
                source,
                DispatchOutcomeRecordV2,
                owner="effects",
                schema=_WITNESS_SCHEMA,
            )
            if (
                source.subject.subject_id != expected_head.subject_id
                or source.subject.revision.head != expected_head.head
                or source.subject.revision.fingerprint != expected_head.fingerprint
                or carrier.kind != "EVIDENCE_RECORDED"
                or carrier.command.operation != "APPEND_EVIDENCE"
                or carrier.command.evidence != evidence
                or evidence_head(carrier.command.evidence) != expected_head
                or carrier.snapshot.intent != witness.snapshot.intent
            ):
                raise ValueError("selected R16 evidence carrier differs from the closed witness")
            if source.physical_record != _digest_reference(
                carrier.record.subject_id, carrier.record.head, carrier.record.fingerprint
            ):
                raise ValueError("selected R16 evidence carrier physical record differs")
            evidence_refs.append(source.physical_record)

        witness_ref = cut.selected_witness.physical_record
        resolver_ref = cut.resolver_policy.physical_record
        reducer_ref = cut.reducer_policy.physical_record
        source_fingerprint = request.digest()
        basis_id = request.command_id + ":basis"
        basis = OriginalResolutionBasis(
            basis_id=basis_id,
            source_request_fingerprint=source_fingerprint,
            original=original,
            original_terminal=cut.call_terminal.physical_record,
            selected_witness=witness_ref,
            complete_evidence_manifest=tuple(evidence_refs),
            resolver_policy=resolver_ref,
            reducer_policy=reducer_ref,
            reduction_id="r14-original-semantic-reduction:" + original.obligation_id,
            accepted_semantic_class=status.semantic_class,
        )
        basis_bytes = basis.canonical_bytes()
        basis_ref = _digest_reference(basis_id, "record:" + _sha(basis_bytes), _sha(basis_bytes))
        predicate_bytes = (
            original.closure_predicate_id + "\0" + original.closure_predicate_version
        ).encode()
        predicate_ref = _digest_reference(
            original.closure_predicate_id,
            original.closure_predicate_version,
            _sha(predicate_bytes),
        )
        closure_id = request.command_id + ":closure"
        closure = OriginalObligationClosureRecord(
            closure_id=closure_id,
            basis=basis_ref,
            original=original,
            prior_obligation=cut.current_obligation.physical_record,
            accepted_witness=witness_ref,
            closure_predicate=predicate_ref,
        )
        closure_bytes = closure.canonical_bytes()
        closure_ref = _digest_reference(
            closure_id, "record:" + _sha(closure_bytes), _sha(closure_bytes)
        )
        outcome_id = request.command_id + ":outcome"
        recovered = RecoveredCallOutcomeRecord(
            outcome_id=outcome_id,
            basis=basis_ref,
            original=original,
            closure=closure_ref,
            accepted_witness=witness_ref,
            result_schema=RESULT_SCHEMA,
            canonical_result_bytes=status.canonical_bytes(),
            accepted_semantic_class=status.semantic_class,
        )
        recovered_bytes = recovered.canonical_bytes()
        recovered_ref = _digest_reference(
            outcome_id, "record:" + _sha(recovered_bytes), _sha(recovered_bytes)
        )
        batch_id = request.command_id + ":batch"
        batch = OriginalResolverBatchRecord(
            batch_id=batch_id,
            command_id=request.command_id,
            basis=basis_ref,
            closure=closure_ref,
            recovered_outcome=recovered_ref,
            source_cut_fingerprint=cut.digest(),
        )
        rows = (
            (
                "agent_loop",
                "ORIGINAL_RESOLUTION_BASIS",
                "chiplog.loop.original-resolution-basis.v1",
                basis_id,
                basis_bytes,
            ),
            (
                "agent_loop",
                "ORIGINAL_OBLIGATION_CLOSURE",
                closure.schema_id,
                closure_id,
                closure_bytes,
            ),
            (
                "agent_loop",
                "RECOVERED_CALL_OUTCOME",
                recovered.schema_id,
                outcome_id,
                recovered_bytes,
            ),
            (
                "agent_loop",
                "ORIGINAL_RESOLVER_BATCH",
                "chiplog.loop.original-resolver-batch.v1",
                batch_id,
                batch.canonical_bytes(),
            ),
        )
        return PreparedOriginalCallResolution(
            source_request_fingerprint=source_fingerprint,
            basis=basis,
            closure=closure,
            recovered_outcome=recovered,
            batch=batch,
            complete_batch_fingerprint=_batch_fingerprint(rows),
        )
    except (AttributeError, KeyError, TypeError, ValueError, ValidationError) as error:
        return CallPreparationRejected(
            command_id=request.command_id,
            code="HOLD",
            reason=str(error),
        )
