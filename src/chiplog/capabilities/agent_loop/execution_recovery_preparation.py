"""Owner-local weak accounting and strong continuation over a supplied recovery cut.

These proposals bind exact supplied bytes. The broker must authenticate complete
selected/physical enumeration again before publishing either record or a Turn.
"""

from __future__ import annotations

import hashlib
import json

from pydantic import ValidationError

from chiplog.capabilities.agent_loop.direct_effect_terminal_contracts import (
    classify_direct_effect_terminal_v1,
    translate_selected_outcome_v1,
)
from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
from chiplog.capabilities.effects.dispatch_v2 import reference as effects_reference

from .call_acceptance_contracts import (
    CallPreparationRejected,
    CallSubjectHead,
    CancelledBeforeAcceptRecord,
    InitializedCallRecord,
    NotExecutedCallResultRecord,
    ToolCallAcceptedRecord,
    ToolExecutionIntentRecord,
)
from .call_acceptance_preparation import call_record_reference
from .direct_terminal_contracts import (
    AcceptedDirectTerminalAccountingV1,
    AcceptedDirectTerminalDispositionV1,
    AcceptedDirectTerminalResultV1,
)
from .execution_contracts import ExecutionRunRecord
from .execution_recovery_contracts import (
    AcceptedOpenAccountingRecordV1,
    AcceptedOpenRecoveryOpeningV1,
    AcceptedOpenTerminalDispositionV1,
    PrepareAcceptedOpenAccounting,
    PrepareAcceptedOpenRecoveryV1,
    PreparedAcceptedOpenAccounting,
    PreparedAcceptedOpenRecoveryV1,
    PreparedExecutionAccounting,
    PreparedExecutionContinuation,
    PrepareExecutionAccounting,
    PrepareExecutionContinuation,
)
from .execution_recovery_observations import (
    ContinuationReadyRecord,
    ExecutionRecoveryCut,
    OriginalRecoveredClosure,
    RecoverySourceRecord,
    SealedAccountingRecord,
)
from .native_recovery_cut_validation import validate_native_recovery_cut
from .native_recovery_profile_contracts import SCHEMA, SCHEMA_V2, SCHEMA_V3
from .recovery_contracts import Absent, OriginalObligationBinding, Present, RecoveryDTO
from .recovery_domain import (
    RecoveredClosure,
    RecoveryHold,
    RecoveryIntegrityFault,
    SealedAccounting,
    model_continuation_ready,
    sealed_call_accounting,
)
from .recovery_frontier_contracts import (
    CancelledBeforeAcceptResult,
    ConsequentialAcceptedCall,
    DefiniteFailureResult,
    EvidenceReduction,
    InitializedCall,
    RecoveryRequiredResult,
    SuccessResult,
    TerminalCallFrontier,
)
from .recovery_frontier_registry_contracts import (
    execution_native_one_call_cancellation_frontier_registry_v1,
    execution_native_one_call_recovery_frontier_registry_v2,
    frontier_registry_reference,
)

_RUN = "chiplog.agent-loop.execution-record.v2"
_SEAL = "chiplog.call.response-seal.v1"
_INITIALIZED = "chiplog.call.initialized.v1"
_CANCELLED = "chiplog.call.cancelled-before-accept.v1"
_NOT_EXECUTED = "chiplog.call.not-executed-result.v1"
_DIRECT_ACCOUNTING = "chiplog.execution.accepted-direct-terminal-accounting.v1"
_DIRECT_TERMINAL = "chiplog.execution.accepted-direct-terminal-disposition.v1"
_DIRECT_RESULT = "chiplog.execution.accepted-direct-terminal-result.v1"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _record[T: RecoveryDTO](source: RecoverySourceRecord, type_: type[T]) -> T:
    try:
        value = type_.model_validate_json(source.canonical_record_bytes)
    except (ValidationError, ValueError) as error:
        raise RecoveryIntegrityFault("invalid recovery source bytes") from error
    if value.canonical_bytes() != source.canonical_record_bytes:
        raise RecoveryIntegrityFault("noncanonical recovery source bytes")
    return value


def _run_ref(run: ExecutionRunRecord) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=run.run_id,
        revision=Present(head=run.head, fingerprint=run.digest()),
    )


def _source(
    cut: ExecutionRecoveryCut, schema: str, expected: CallSubjectHead, raw: bytes
) -> RecoverySourceRecord:
    matches = [
        item
        for item in cut.complete_sources
        if item.owner == "agent_loop" and item.schema_id == schema and item.subject == expected
    ]
    if len(matches) != 1 or matches[0].canonical_record_bytes != raw:
        raise RecoveryHold("exact selected source is missing, repeated, or differs")
    source = matches[0]

    native_profile = any(
        item.schema_id in (SCHEMA, SCHEMA_V2, SCHEMA_V3) for item in cut.complete_sources
    )
    if source.subject.revision.fingerprint != _sha(raw) or (
        not native_profile and source.physical_record.revision.fingerprint != _sha(raw)
    ):
        raise RecoveryIntegrityFault(
            "source fingerprint differs from bytes or bounded physical row"
        )
    return source


def _validate_cut(cut: ExecutionRecoveryCut) -> None:
    """Check the bounded native one-call profile without claiming cut authenticity."""

    profiles_v1 = tuple(source for source in cut.complete_sources if source.schema_id == SCHEMA)
    profiles_v2 = tuple(source for source in cut.complete_sources if source.schema_id == SCHEMA_V2)
    profiles_v3 = tuple(source for source in cut.complete_sources if source.schema_id == SCHEMA_V3)
    cancellation_registry = execution_native_one_call_cancellation_frontier_registry_v1()
    recovery_registry = execution_native_one_call_recovery_frontier_registry_v2()
    if cut.frontier.registry == recovery_registry:
        if len(profiles_v2) + len(profiles_v3) != 1 or profiles_v1:
            raise RecoveryHold("V2 registry requires exactly one selected V2/V3 profile")
        validate_native_recovery_cut(cut)
    elif cut.frontier.registry == cancellation_registry:
        if profiles_v2 or profiles_v3 or len(profiles_v1) > 1:
            raise RecoveryHold("V1 registry has an incompatible selected native profile")
        if profiles_v1:
            validate_native_recovery_cut(cut)
    else:
        raise RecoveryHold("unsupported recovery registry")
    native_profile = bool(profiles_v1 or profiles_v2 or profiles_v3)
    if (
        cut.frontier.tenant_id != cut.tenant_id
        or cut.frontier.run_id != cut.current_run.subject_id
        or cut.frontier.tenant_commit_sequence != cut.tenant_commit_sequence
    ):
        raise RecoveryIntegrityFault("frontier tenant, Run, or cut sequence differs")
    if len(cut.complete_ordered_responses) != 1:
        raise RecoveryHold("native one-call profile requires one prior sealed response")
    runs = cut.complete_ordered_run_lineage
    if len(runs) < 2 or any(not isinstance(run, ExecutionRunRecord) for run in runs):
        raise RecoveryHold("native one-call profile requires complete V2 Run lineage")
    for index, run in enumerate(runs):
        assert isinstance(run, ExecutionRunRecord)
        if run.tenant != cut.tenant_id or run.run_id != cut.current_run.subject_id:
            raise RecoveryIntegrityFault("foreign Run in recovery lineage")
        if index and run.predecessor != runs[index - 1].head:
            raise RecoveryIntegrityFault("Run lineage predecessor or order differs")
        _source(cut, _RUN, _run_ref(run), run.canonical_bytes())
    current = runs[-1]
    assert isinstance(current, ExecutionRunRecord)
    if _run_ref(current) != cut.current_run or current.state != "ACTIVE":
        raise RecoveryHold("recovery Run is not the exact current ACTIVE Run")
    attempt = current.turns[-1].attempts[current.turns[-1].selector]
    artifact = attempt.manifest.artifact
    bindings = cut.current_bindings
    if (
        bindings.objective != current.prompt
        or bindings.requested_work != attempt.request
        or bindings.prompt_artifact.fingerprint != artifact.digest()
        or tuple(ref.fingerprint for ref in bindings.ordered_tool_specs)
        != tuple(tool.digest() for tool in artifact.tools)
        or bindings.generated_schema.fingerprint != _sha(artifact.response_schema_json.encode())
        or bindings.policy.fingerprint != current.policy.digest()
        or bindings.no_retry_boundaries
        != tuple(ref.revision for ref in current.no_retry_references)
        or bindings.authority_mandate_heads
        or (not native_profile and bindings.recipient_effect_bindings)
    ):
        raise RecoveryIntegrityFault("frozen bindings differ from bounded native Run")
    view = cut.complete_ordered_responses[0]
    seal = view.seal
    seal_ref = call_record_reference(seal.response_seal_id, seal)
    if (
        view.selected_seal != seal_ref
        or seal.tenant_id != cut.tenant_id
        or seal.original_run_id != current.run_id
        or len(seal.complete_ordered_initialized) != 1
        or len(view.ordered_calls) != 1
        or view.ordered_calls != cut.frontier.ordered_calls
    ):
        raise RecoveryIntegrityFault("sealed response and complete call frontier differ")
    _source(cut, _SEAL, seal_ref, seal.canonical_bytes())
    captured = [
        run
        for run in runs
        if isinstance(run, ExecutionRunRecord) and _run_ref(run) == view.original_run
    ]
    if len(captured) != 1:
        raise RecoveryHold("sealed response has no unique captured Run")
    captured_turn = captured[0].turns[-1]
    if (
        view.original_turn.subject_id != captured_turn.turn_id
        or view.original_turn.revision.head != captured_turn.head
        or view.original_turn.revision.fingerprint != captured_turn.digest()
        or seal.captured_response != view.original_run
        or seal.original_turn_id != captured_turn.turn_id
        or current.turns[-1].response_seal != seal_ref
        or current.turns[-1].initialized_calls != seal.complete_ordered_initialized
    ):
        raise RecoveryIntegrityFault("response lost captured/current Turn binding")
    call = view.ordered_calls[0]
    if not isinstance(call, TerminalCallFrontier):
        raise RecoveryHold("native next-turn call is not terminal")
    init_ref = seal.complete_ordered_initialized[0]
    if call.original_call_id != init_ref.subject_id or call.response_id != seal.response_seal_id:
        raise RecoveryIntegrityFault("native terminal call differs from sealed initialization")
    init = _record(_source_by_ref(cut, _INITIALIZED, init_ref), InitializedCallRecord)
    _source(cut, _INITIALIZED, init_ref, init.canonical_bytes())
    if (
        init.original_call_id != call.original_call_id
        or init.call.original.tenant_id != cut.tenant_id
        or init.call.original.original_run_id != current.run_id
        or init.call.original.original_turn_id != seal.original_turn_id
        or init.call.original.captured_response != view.original_run
        or init.call.classification != "CONSEQUENTIAL"
    ):
        raise RecoveryIntegrityFault("initialized call differs from original response")
    if isinstance(call.acceptance, ConsequentialAcceptedCall) and isinstance(
        call.disposition, RecoveryRequiredResult
    ):
        if cut.frontier.registry != recovery_registry:
            raise RecoveryHold("accepted recovery requires the V2 native recovery registry")
        if len(profiles_v3) != 1 or profiles_v2:
            raise RecoveryHold("accepted recovery requires the selected V3 native profile")
        _validate_accepted_recovery(cut, call, init, init_ref)
        return
    if isinstance(call.acceptance, ConsequentialAcceptedCall) and isinstance(
        call.disposition, (SuccessResult, DefiniteFailureResult)
    ):
        if cut.frontier.registry != recovery_registry:
            raise RecoveryHold("direct terminal requires the V2 native recovery registry")
        if len(profiles_v3) != 1 or profiles_v2:
            raise RecoveryHold("direct terminal requires the selected V3 native profile")
        _validate_direct_terminal(cut, call, init_ref)
        return
    if (
        call.kind != "TERMINAL"
        or not isinstance(call.acceptance, InitializedCall)
        or call.acceptance.initialized != init_ref.revision
        or not isinstance(call.disposition, CancelledBeforeAcceptResult)
    ):
        raise RecoveryHold("native call is not exactly cancelled before acceptance")
    terminal = _unique_record_by_revision(
        cut, _CANCELLED, call.disposition.terminal, CancelledBeforeAcceptRecord
    )
    terminal_head = call_record_reference(terminal.terminal_id, terminal)
    result = _unique_record_by_revision(
        cut, _NOT_EXECUTED, call.disposition.not_executed_result, NotExecutedCallResultRecord
    )
    if (
        terminal.original_call_id != call.original_call_id
        or terminal.original != init.call.original
        or terminal.initialized != init_ref
        or terminal.not_executed_result_id != result.result_id
        or result.original_call_id != call.original_call_id
        or result.initialized != init_ref
        or result.terminal != terminal_head
        or result.outcome != "NOT_EXECUTED"
        or call.disposition.initialized_predecessor != init_ref.revision
    ):
        raise RecoveryIntegrityFault("cancellation terminal/result chain differs")
    expected_sources = (
        *(_run_ref(run) for run in runs if isinstance(run, ExecutionRunRecord)),
        seal_ref,
        init_ref,
        terminal_head,
        call_record_reference(result.result_id, result),
    )
    prefix = cut.complete_sources[: len(expected_sources)]
    if not native_profile:
        if tuple(source.subject for source in prefix) != expected_sources or tuple(
            source.schema_id for source in prefix
        ) != (*(_RUN for _ in runs), _SEAL, _INITIALIZED, _CANCELLED, _NOT_EXECUTED):
            raise RecoveryHold("native recovery source prefix is missing or reordered")
        _validate_extension_sources(cut, cut.complete_sources[len(expected_sources) :])
    if cut.original_closures or cut.current_reductions:
        raise RecoveryHold("unneeded closure or reduction in preaccept cancellation")


def _validate_direct_terminal(
    cut: ExecutionRecoveryCut,
    call: TerminalCallFrontier,
    initialized: CallSubjectHead,
) -> None:
    """Check only the frozen one-call direct terminal projection."""
    acceptance = call.acceptance
    disposition = call.disposition
    if not isinstance(acceptance, ConsequentialAcceptedCall) or not isinstance(
        disposition, (SuccessResult, DefiniteFailureResult)
    ):
        raise RecoveryHold("direct terminal branch has a foreign terminal type")
    if cut.original_closures or cut.current_reductions:
        raise RecoveryHold("direct terminal cannot carry resolver closure or reduction")
    terminal = _unique_record_by_revision(
        cut, _DIRECT_TERMINAL, disposition.terminal, AcceptedDirectTerminalDispositionV1
    )
    result = _unique_record_by_revision(
        cut, _DIRECT_RESULT, disposition.result, AcceptedDirectTerminalResultV1
    )
    accounting_rows = tuple(
        row
        for row in cut.complete_sources
        if row.owner == "agent_loop" and row.schema_id == _DIRECT_ACCOUNTING
    )
    if len(accounting_rows) != 1:
        raise RecoveryHold("direct terminal accounting is missing or ambiguous")
    accounting = _record(accounting_rows[0], AcceptedDirectTerminalAccountingV1)
    outcome_rows = tuple(
        row
        for row in cut.complete_sources
        if row.physical_record.subject_id == accounting.selected_outcome_record_id
        and row.owner == "effects"
        and row.schema_id == "chiplog.effects.dispatch-outcome-record.v2"
    )
    if len(outcome_rows) != 1:
        raise RecoveryHold("direct terminal selected R16 outcome is absent or ambiguous")
    try:
        outcome = DispatchOutcomeRecordV2.model_validate_json(
            outcome_rows[0].canonical_record_bytes
        )
    except (ValidationError, ValueError) as error:
        raise RecoveryIntegrityFault("invalid selected R16 outcome bytes") from error
    if outcome.canonical_bytes() != outcome_rows[0].canonical_record_bytes:
        raise RecoveryIntegrityFault("selected R16 outcome is noncanonical")
    try:
        expected_classification = classify_direct_effect_terminal_v1(
            translate_selected_outcome_v1(outcome)
        )
    except (TypeError, ValueError) as error:
        raise RecoveryHold("selected R16 outcome is outside the frozen direct mapping") from error
    expected_kind = "SUCCEEDED" if isinstance(disposition, SuccessResult) else "FAILED_DEFINITE"
    expected_state = "CONFIRMED" if expected_kind == "SUCCEEDED" else "FAILED_NO_EFFECT"
    classification = result.classification
    expected_semantic = (
        "R14_EFFECT_CONFIRMED" if expected_kind == "SUCCEEDED" else "R14_EFFECT_FAILED_NO_EFFECT"
    )
    selected_outcome = outcome_rows[0]
    external_effect = acceptance.external_effect_intent
    if not isinstance(external_effect, Present):
        raise RecoveryHold("direct terminal acceptance has no external effect intent")
    if (
        call.original_call_id != initialized.subject_id
        or acceptance.initialized != initialized.revision
        or len(acceptance.complete_acceptance_manifest) != 3
        or accounting.original_call_id != call.original_call_id
        or accounting.response_id != call.response_id
        or accounting.accepted.revision != acceptance.accepted
        or accounting.execution_intent.revision != acceptance.execution_intent
        or accounting.external_effect_intent.revision != external_effect
        or not isinstance(accounting.terminal.revision, Present)
        or accounting.terminal.revision != disposition.terminal
        or not isinstance(accounting.result.revision, Present)
        or accounting.result.revision != disposition.result
        or acceptance.complete_acceptance_manifest
        != (
            acceptance.accepted,
            acceptance.execution_intent,
            acceptance.external_effect_intent,
        )
        or accounting.selected_outcome_fingerprint
        != selected_outcome.physical_record.revision.fingerprint
        or accounting.selected_decision != selected_outcome.selected_decision
        or accounting.selected_outcome_record_id != outcome.record.head
        or outcome.canonical_bytes() != selected_outcome.canonical_record_bytes
        or outcome.snapshot.state != expected_state
        or outcome.snapshot.obligation.state != "CLOSED"
        or outcome.snapshot.intent.intent_id != classification.intent.subject_id
        or terminal.kind != expected_kind
        or terminal.original_call_id != call.original_call_id
        or terminal.response_id != call.response_id
        or terminal.accepted.revision != acceptance.accepted
        or terminal.external_effect_intent.revision != external_effect
        or terminal.mapping_id != "R14_DIRECT_EFFECT_TERMINAL_V1"
        or terminal.mapping_version != "r14-direct-effect-terminal.v1"
        or result.result_id != accounting.result.subject_id
        or result.original_call_id != call.original_call_id
        or result.terminal.revision != disposition.terminal
        or classification.disposition != expected_kind
        or classification.semantic_class != expected_semantic
        or classification.mapping_id != "R14_DIRECT_EFFECT_TERMINAL_V1"
        or classification.mapping_version != "r14-direct-effect-terminal.v1"
        or classification != expected_classification
        or terminal.selected_effect_terminal != classification.terminal
        or classification.intent.subject_id != outcome.snapshot.intent.intent_id
        or classification.intent.fingerprint != external_effect.fingerprint
    ):
        raise RecoveryIntegrityFault("direct terminal does not join its exact accepted R16 outcome")
    for evidence in classification.ordered_evidence:
        refs = tuple(
            row
            for row in cut.complete_sources
            if row.physical_record.subject_id == evidence.subject_id
            and row.physical_record.revision.head == evidence.head
            and row.physical_record.revision.fingerprint == evidence.fingerprint
        )
        if len(refs) != 1:
            raise RecoveryHold("direct terminal evidence source is absent or ambiguous")


def _source_for_revision(
    cut: ExecutionRecoveryCut, revision: Present, schema: str
) -> RecoverySourceRecord:
    matches = tuple(
        source
        for source in cut.complete_sources
        if source.owner == "agent_loop"
        and source.schema_id == schema
        and source.subject.revision == revision
    )
    if len(matches) != 1:
        raise RecoveryHold("missing or duplicate accepted-recovery source")
    source = matches[0]
    _source(cut, schema, source.subject, source.canonical_record_bytes)
    return source


def _source_for_subject_head(
    cut: ExecutionRecoveryCut, subject_id: str, head: str, schema: str
) -> RecoverySourceRecord:
    matches = tuple(
        source
        for source in cut.complete_sources
        if source.owner == "agent_loop"
        and source.schema_id == schema
        and source.subject.subject_id == subject_id
        and source.subject.revision.head == head
    )
    if len(matches) != 1:
        raise RecoveryHold("missing or duplicate accepted-recovery subject")
    source = matches[0]
    _source(cut, schema, source.subject, source.canonical_record_bytes)
    return source


def _validate_accepted_recovery(
    cut: ExecutionRecoveryCut,
    call: TerminalCallFrontier,
    initialized: InitializedCallRecord,
    initialized_ref: CallSubjectHead,
) -> None:
    """Validate the owner-visible closed recovery pair without granting authority."""
    from .execution_recovery_contracts import (
        AcceptedOpenRecoveryOpeningV1,
        AcceptedOpenTerminalDispositionV1,
    )
    from .original_recovery_contracts import (
        LoopSemanticReductionRecord,
        OriginalObligationClosureRecord,
        OriginalResolutionBasis,
        OriginalResolverBatchRecord,
        RecoveredCallOutcomeRecord,
    )
    from .recovery_frontier_contracts import EvidenceReduction

    acceptance = call.acceptance
    disposition = call.disposition
    assert isinstance(acceptance, ConsequentialAcceptedCall)
    assert isinstance(disposition, RecoveryRequiredResult)
    if (
        acceptance.initialized != initialized_ref.revision
        or acceptance.external_effect_intent.kind != "PRESENT"
        or disposition.result.kind != "ABSENT"
        or disposition.recovered_outcome.outcome.kind != "PRESENT"
        or disposition.recovered_outcome.original_call_id != call.original_call_id
        or disposition.recovered_outcome.recovery_obligation_id
        != disposition.obligation.obligation_id
    ):
        raise RecoveryHold("accepted recovery frontier is incomplete")

    from chiplog.composition.r14_acceptance_contracts import ACCEPTED_SCHEMA, EXECUTION_SCHEMA

    accepted_source = _source_for_revision(cut, acceptance.accepted, ACCEPTED_SCHEMA)
    accepted = _record(accepted_source, ToolCallAcceptedRecord)
    accepted_ref = call_record_reference(accepted.accepted_id, accepted)
    execution_source = _source_for_revision(cut, acceptance.execution_intent, EXECUTION_SCHEMA)
    execution_intent = _record(execution_source, ToolExecutionIntentRecord)
    execution_ref = call_record_reference(execution_intent.execution_intent_id, execution_intent)
    if (
        acceptance.accepted != accepted_ref.revision
        or acceptance.execution_intent != execution_ref.revision
        or acceptance.complete_acceptance_manifest
        != (acceptance.accepted, acceptance.execution_intent, acceptance.external_effect_intent)
        or accepted.binding.original_call_id != call.original_call_id
        or accepted.binding.initialized != initialized_ref
        or accepted.execution_intent_id != execution_intent.execution_intent_id
        or execution_intent.original_call_id != call.original_call_id
        or execution_intent.initialized != initialized_ref
        or execution_intent.accepted != accepted_ref
        or execution_intent.external_intent.revision != acceptance.external_effect_intent
        or initialized.original_call_id != call.original_call_id
    ):
        raise RecoveryIntegrityFault("accepted call and selected execution intent differ")

    opening_source = _source_for_subject_head(
        cut,
        disposition.obligation.obligation_id,
        disposition.obligation.obligation_head,
        "chiplog.execution.accepted-open-recovery-opening.v1",
    )
    opening = _record(opening_source, AcceptedOpenRecoveryOpeningV1)
    terminal_source = _source_for_revision(
        cut,
        disposition.terminal,
        "chiplog.execution.accepted-open-terminal-disposition.v1",
    )
    terminal = _record(terminal_source, AcceptedOpenTerminalDispositionV1)
    if (
        opening.original != disposition.obligation
        or opening_source.subject.subject_id != disposition.obligation.obligation_id
        or terminal.kind != "RECOVERY_REQUIRED"
        or terminal.original_call_id != call.original_call_id
        or terminal.accepted != accepted_ref
        or terminal.external_effect_intent.revision != acceptance.external_effect_intent
        or terminal.obligation_opening.subject_id != opening.opening_id
        or disposition.terminal.fingerprint != _sha(terminal.canonical_bytes())
    ):
        raise RecoveryIntegrityFault("accepted recovery opening or immutable terminal differs")

    recovered = disposition.recovered_outcome.outcome
    outcome_source = _source_for_revision(cut, recovered, "chiplog.loop.recovered-call-outcome.v1")
    outcome = _record(outcome_source, RecoveredCallOutcomeRecord)
    if len(cut.original_closures) != 1 or len(cut.current_reductions) != 1:
        raise RecoveryHold("accepted recovery requires exactly one closure and current reduction")
    closure_summary = cut.original_closures[0]
    closure_source = _source_for_revision(
        cut, closure_summary.closure, "chiplog.loop.original-obligation-closure.v1"
    )
    closure = _record(closure_source, OriginalObligationClosureRecord)
    batch_source = _source_for_revision(
        cut, closure_summary.resolver_batch, "chiplog.loop.original-resolver-batch.v1"
    )
    batch = _record(batch_source, OriginalResolverBatchRecord)
    basis_source = _source_for_revision(
        cut, outcome.basis.revision, "chiplog.loop.original-resolution-basis.v1"
    )
    basis = _record(basis_source, OriginalResolutionBasis)
    if (
        closure_summary.original != disposition.obligation
        or closure_summary.terminal_disposition != disposition.terminal
        or closure_summary.recovered_outcome != recovered
        or closure_summary.closure != closure_source.subject.revision
        or closure_source.subject.subject_id != closure.closure_id
        or closure.basis != outcome.basis
        or closure.original != disposition.obligation
        or closure.prior_obligation != opening_source.physical_record
        or closure.accepted_witness.revision != closure_summary.accepted_evidence
        or outcome.basis != basis_source.subject
        or outcome.original != disposition.obligation
        or outcome_source.subject.subject_id != outcome.outcome_id
        or outcome.closure != closure_source.subject
        or outcome.accepted_witness.revision != closure_summary.accepted_evidence
        or outcome.accepted_semantic_class != closure_summary.accepted_semantic_class
        or basis.original != disposition.obligation
        or basis.original_terminal != terminal_source.physical_record
        or basis.selected_witness != closure.accepted_witness
        or any(
            not any(source.subject == item for source in cut.complete_sources)
            for item in (
                *basis.complete_evidence_manifest,
                basis.resolver_policy,
                basis.reducer_policy,
            )
        )
        or batch.basis != outcome.basis
        or batch.closure != closure_source.subject
        or batch_source.subject.subject_id != batch.batch_id
        or batch.recovered_outcome != outcome_source.subject
        or batch.batch_id != batch_source.subject.subject_id
    ):
        raise RecoveryIntegrityFault("selected resolution records do not form one exact pair")

    reduction = cut.current_reductions[0]
    if not isinstance(reduction, EvidenceReduction):
        raise RecoveryIntegrityFault("current reduction has an unsupported shape")
    reduction_source = _source_for_revision(
        cut,
        reduction.current_reduction,
        "chiplog.loop.semantic-reduction.v1",
    )
    reduction_record = _record(reduction_source, LoopSemanticReductionRecord)
    if (
        not isinstance(reduction, EvidenceReduction)
        or reduction.stream_id != disposition.obligation.evidence_stream_id
        or reduction.reduction_id != reduction_record.reduction_id
        or reduction.reducer_id != reduction_record.reducer_id
        or reduction.reducer_version != reduction_record.reducer_version
        or reduction_record.stream.stream_id != reduction.stream_id
        or reduction_record.anchor.kind != "RESOLVED"
        or reduction_record.anchor.original != disposition.obligation
        or reduction_record.anchor.accepted_witness.revision != reduction.accepted_witness
        or reduction_record.anchor.recovered_outcome.revision != reduction.accepted_outcome
        or reduction_record.anchor.closure.revision != reduction.obligation_closure
        or reduction_record.anchor.resolver_batch.revision != reduction.resolver_batch
        or tuple(item.revision for item in reduction_record.complete_ordered_evidence)
        != reduction.ordered_consumed_evidence
        or reduction_record.disposition.kind != "CONSUMABLE"
        or reduction_record.disposition.semantic_class != reduction.semantic_class
        or any(
            not any(source.subject == evidence for source in cut.complete_sources)
            for evidence in reduction_record.complete_ordered_evidence
        )
    ):
        raise RecoveryIntegrityFault("selected current reduction differs from its cut")
    if reduction.consumability != "CONSUMABLE":
        raise RecoveryHold("current semantic reduction is not consumable")
    if not any(
        source.subject.revision == closure_summary.accepted_evidence
        for source in cut.complete_sources
    ):
        raise RecoveryHold("accepted witness source is missing")


def _continuation_closures(
    cut: ExecutionRecoveryCut, accounting: SealedAccounting
) -> tuple[tuple[OriginalRecoveredClosure, ...], tuple[EvidenceReduction, ...]]:
    closures = tuple(
        RecoveredClosure(
            original_call_id=item.original.original_call_id,
            obligation_id=item.original.obligation_id,
            original_obligation_head=item.original.obligation_head,
            terminal_disposition=item.terminal_disposition,
            outcome=item.recovered_outcome,
            accepted_evidence=item.accepted_evidence,
            closure=item.closure,
            resolver_batch=item.resolver_batch,
            resolver_id=item.original.resolver_id,
            resolver_version=item.original.resolver_version,
            reducer_id=item.original.reducer_id,
            reducer_version=item.original.reducer_version,
            reduction_id=item.reduction_id,
            accepted_semantic_class=item.accepted_semantic_class,
        )
        for item in cut.original_closures
    )
    if any(call.disposition.kind == "RECOVERY_REQUIRED" for call in accounting.calls):
        model_continuation_ready(accounting, closures, cut.current_reductions)
        return cut.original_closures, cut.current_reductions
    if closures or cut.current_reductions:
        raise RecoveryHold("cancellation readiness contains recovery closure or reduction")
    model_continuation_ready(accounting, (), ())
    return (), ()


def _validate_extension_sources(
    cut: ExecutionRecoveryCut, sources: tuple[RecoverySourceRecord, ...]
) -> None:
    """Fail closed until R16/R17 source rows and absence proofs have a fixed join.

    The V24 native prefix is known, but a complete recovery cut also contains
    authority, effect, evidence, delivery, and other registered families. Each
    future row needs a versioned decoder, exact source order, and a join to the
    frontier's explicit presence/absence member. Merely accepting extra canonical
    bytes here would let a caller omit or forge those families. The broker/writer
    must still independently authenticate the entire cut even after this join is
    implemented.
    """
    del cut
    if sources:
        raise RecoveryHold("full recovery source extension is not registered")


def _source_by_ref(
    cut: ExecutionRecoveryCut, schema: str, ref: CallSubjectHead
) -> RecoverySourceRecord:
    matches = [
        source
        for source in cut.complete_sources
        if source.owner == "agent_loop" and source.schema_id == schema and source.subject == ref
    ]
    if len(matches) != 1:
        raise RecoveryHold("missing or duplicate selected recovery source")
    return matches[0]


def _unique_record_by_revision[T: RecoveryDTO](
    cut: ExecutionRecoveryCut, schema: str, revision: Present, type_: type[T]
) -> T:
    matches = [
        source
        for source in cut.complete_sources
        if source.owner == "agent_loop"
        and source.schema_id == schema
        and source.subject.revision == revision
    ]
    if len(matches) != 1:
        raise RecoveryHold("missing or duplicate terminal source")
    record = _record(matches[0], type_)
    identity = getattr(record, "terminal_id", None) or getattr(record, "result_id", None)
    if not isinstance(identity, str):
        raise RecoveryIntegrityFault("terminal source lacks a stable record identity")
    _source(cut, schema, call_record_reference(identity, record), record.canonical_bytes())
    return record


def _accounting(cut: ExecutionRecoveryCut) -> tuple[SealedAccountingRecord, ...]:
    registry = frontier_registry_reference(cut.frontier.registry)
    frontier = call_record_reference("recovery-frontier:" + cut.frontier.run_id, cut.frontier)
    records = []
    for view in cut.complete_ordered_responses:
        if view.frontier_head != frontier:
            raise RecoveryIntegrityFault("response frontier differs from current cut")
        seal = view.seal
        joined = sealed_call_accounting(
            seal.response_seal_id,
            seal.digest(),
            tuple(item.subject_id for item in seal.complete_ordered_initialized),
            view.ordered_calls,
        )
        preimage = {
            "domain": "chiplog.execution.sealed-accounting.v1",
            "source_cut_fingerprint": cut.digest(),
            "sealed_response": view.selected_seal.model_dump(mode="json"),
            "sealed_manifest_fingerprint": seal.digest(),
            "registry": registry.model_dump(mode="json"),
            "frontier": frontier.model_dump(mode="json"),
            "complete_ordered_calls": [item.model_dump(mode="json") for item in joined.calls],
        }
        identity = "sealed-accounting:" + _sha(
            json.dumps(preimage, sort_keys=True, separators=(",", ":")).encode()
        )
        records.append(
            SealedAccountingRecord(
                accounting_id=identity,
                source_cut_fingerprint=cut.digest(),
                sealed_response=view.selected_seal,
                sealed_manifest_fingerprint=seal.digest(),
                registry=registry,
                frontier=frontier,
                complete_ordered_calls=joined.calls,
            )
        )
    return tuple(records)


def _rejected(command_id: str, error: Exception) -> CallPreparationRejected:
    return CallPreparationRejected(
        command_id=command_id,
        code="HOLD" if isinstance(error, RecoveryHold) else "INTEGRITY_FAULT",
        reason=str(error),
    )


def prepare_execution_accounting(
    request: PrepareExecutionAccounting,
) -> PreparedExecutionAccounting | CallPreparationRejected:
    try:
        request = PrepareExecutionAccounting.model_validate_json(request.canonical_bytes())
        _validate_cut(request.cut)
        return PreparedExecutionAccounting(
            source_request_fingerprint=request.digest(),
            complete_accounting=_accounting(request.cut),
        )
    except (ValueError, TypeError) as error:
        return _rejected(request.command_id, error)


def prepare_accepted_open_accounting(
    request: PrepareAcceptedOpenAccounting,
) -> PreparedAcceptedOpenAccounting | CallPreparationRejected:
    """Create only effects-specific weak accounting for one selected OPEN source."""
    try:
        request = PrepareAcceptedOpenAccounting.model_validate_json(request.canonical_bytes())
        cut = request.cut
        content = cut.model_dump(mode="json")
        del content["cut_fingerprint"]
        fingerprint = _sha(
            json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        )
        if cut.cut_fingerprint != fingerprint:
            raise RecoveryIntegrityFault("accepted/open cut fingerprint differs")
        if (
            cut.selected_outcome_fingerprint != _sha(cut.selected_outcome_bytes)
            or cut.outcome_commit_sequence > cut.tenant_commit_sequence
            or cut.selected_outcome_record_id not in cut.batch_record_ids
            or len(set(cut.batch_record_ids)) != len(cut.batch_record_ids)
        ):
            raise RecoveryIntegrityFault("accepted/open selected publication provenance differs")
        outcome = DispatchOutcomeRecordV2.model_validate_json(cut.selected_outcome_bytes)
        run = ExecutionRunRecord.model_validate_json(cut.current_run_bytes)
        accepted = ToolCallAcceptedRecord.model_validate_json(cut.accepted_record_bytes)
        execution_intent = ToolExecutionIntentRecord.model_validate_json(
            cut.execution_intent_record_bytes
        )
        intent = outcome.snapshot.intent
        intent_ref = effects_reference(intent.intent_id, intent.canonical_bytes())
        accepted_ref = call_record_reference(accepted.accepted_id, accepted)
        execution_intent_ref = call_record_reference(
            execution_intent.execution_intent_id, execution_intent
        )
        if (
            outcome.canonical_bytes() != cut.selected_outcome_bytes
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
            or accepted.binding.original_call_id != cut.original_call_id
            or accepted.binding.initialized != cut.initialized
            or accepted.binding.external_intent != cut.external_effect_intent
            or execution_intent.original_call_id != cut.original_call_id
            or execution_intent.initialized != cut.initialized
            or execution_intent.accepted != cut.accepted
            or execution_intent.external_intent != cut.external_effect_intent
            or outcome.snapshot.state != "OUTCOME_UNKNOWN"
            or outcome.snapshot.obligation.state != "OPEN"
            or cut.external_effect_intent.subject_id != intent.intent_id
            or cut.external_effect_intent.revision.fingerprint != intent_ref.fingerprint
            or outcome.snapshot.obligation.original_intent != intent_ref
            or cut.initialized.subject_id != cut.original_call_id
            or request.command_id != "accepted-open-accounting:" + cut.cut_fingerprint
        ):
            raise RecoveryHold("accepted/open call and selected R16 obligation do not join")
        accounting = AcceptedOpenAccountingRecordV1(
            accounting_id="accepted-open-accounting:" + cut.cut_fingerprint,
            source_cut_fingerprint=cut.cut_fingerprint,
            original_call_id=cut.original_call_id,
            accepted=cut.accepted,
            execution_intent=cut.execution_intent,
            external_effect_intent=cut.external_effect_intent,
            selected_outcome_record_id=cut.selected_outcome_record_id,
            selected_outcome_bytes=cut.selected_outcome_bytes,
            selected_outcome_fingerprint=cut.selected_outcome_fingerprint,
            selected_decision=cut.selected_decision,
            outcome_commit_sequence=cut.outcome_commit_sequence,
            publication_ordinal=cut.publication_ordinal,
            batch_record_ids=cut.batch_record_ids,
        )
        return PreparedAcceptedOpenAccounting(
            source_request_fingerprint=request.digest(), accounting=accounting
        )
    except (ValueError, TypeError) as error:
        return _rejected(request.command_id, error)


def prepare_accepted_open_recovery_v1(
    request: PrepareAcceptedOpenRecoveryV1,
) -> PreparedAcceptedOpenRecoveryV1 | CallPreparationRejected:
    """Prepare a weak source fact plus immutable loop-owned OPEN obligation."""
    try:
        request = PrepareAcceptedOpenRecoveryV1.model_validate_json(request.canonical_bytes())
        accounting_result = prepare_accepted_open_accounting(
            PrepareAcceptedOpenAccounting(
                command_id="accepted-open-accounting:" + request.cut.cut_fingerprint,
                cut=request.cut,
            )
        )
        if not isinstance(accounting_result, PreparedAcceptedOpenAccounting):
            raise RecoveryHold("accepted/open accounting source is not selectable")
        cut = request.cut
        effect = DispatchOutcomeRecordV2.model_validate_json(cut.selected_outcome_bytes)
        obligation = effect.snapshot.obligation
        obligation_id = "native-open-obligation:" + _sha(
            (cut.original_call_id + "\x00" + cut.external_effect_intent.subject_id).encode()
        )
        opening = AcceptedOpenRecoveryOpeningV1(
            opening_id="original-obligation-opening:" + obligation_id,
            source_cut_fingerprint=cut.cut_fingerprint,
            original=OriginalObligationBinding(
                original_run_id=cut.current_run.subject_id,
                original_call_id=cut.original_call_id,
                obligation_id=obligation_id,
                obligation_stream_id="original-obligation-stream:" + cut.original_call_id,
                obligation_head="open:" + obligation_id,
                closure_predicate_id="R14_ORIGINAL_EFFECT_CHILDREN_TERMINAL",
                closure_predicate_version="r14-native-effect-closure.v1",
                resolver_id="R14_AUTHENTICATED_ORIGINAL_PRINCIPAL_RESOLVER",
                resolver_version="r14-original-resolver.v1",
                reducer_id="R14_ORIGINAL_EFFECT_EVIDENCE_REDUCER",
                reducer_version="r14-effect-evidence.v1",
                evidence_stream_id=cut.external_effect_intent.subject_id,
                evidence_head=Absent(),
            ),
            evidence_sought=tuple(
                CallSubjectHead(
                    subject_id=child.subject_id,
                    revision=Present(head=child.head, fingerprint=child.fingerprint),
                )
                for child in obligation.original_children
            ),
            safe_actions=("APPEND_AUTHENTICATED_ORIGINAL_EVIDENCE", "RESOLVE_ORIGINAL_OBLIGATION"),
            denied_actions=("RESEND_ORIGINAL_EFFECT", "ASSUME_ABSENCE_IS_NO_EFFECT"),
        )
        opening_digest = _sha(opening.canonical_bytes())
        opening_ref = CallSubjectHead(
            subject_id=opening.opening_id,
            revision=Present(head="record:" + opening_digest, fingerprint=opening_digest),
        )
        terminal = AcceptedOpenTerminalDispositionV1(
            terminal_id="accepted-open-terminal:" + cut.cut_fingerprint,
            source_cut_fingerprint=cut.cut_fingerprint,
            original_call_id=cut.original_call_id,
            response_id=cut.response_id,
            accepted=cut.accepted,
            external_effect_intent=cut.external_effect_intent,
            obligation_opening=opening_ref,
        )
        return PreparedAcceptedOpenRecoveryV1(
            source_request_fingerprint=request.digest(),
            accounting=accounting_result.accounting,
            opening=opening,
            terminal=terminal,
        )
    except (ValueError, TypeError, AttributeError) as error:
        return _rejected(request.command_id, error)


def prepare_execution_continuation(
    request: PrepareExecutionContinuation,
) -> PreparedExecutionContinuation | CallPreparationRejected:
    try:
        request = PrepareExecutionContinuation.model_validate_json(request.canonical_bytes())
        _validate_cut(request.cut)
        expected = _accounting(request.cut)
        if request.complete_accounting != expected:
            raise RecoveryHold("accounting is missing, extra, reordered, or from another cut")
        continuations = []
        for view, accounting in zip(request.cut.complete_ordered_responses, expected, strict=True):
            joined = sealed_call_accounting(
                view.seal.response_seal_id,
                view.seal.digest(),
                tuple(ref.subject_id for ref in view.seal.complete_ordered_initialized),
                view.ordered_calls,
            )
            closures, reductions = _continuation_closures(request.cut, joined)
            preimage = (
                b"chiplog.execution.continuation-ready.v1\x00"
                + request.cut.digest().encode()
                + accounting.canonical_bytes()
            )
            continuations.append(
                ContinuationReadyRecord(
                    readiness_id="continuation-ready:" + _sha(preimage),
                    source_cut_fingerprint=request.cut.digest(),
                    accounting=accounting,
                    complete_original_closures=closures,
                    complete_current_reductions=reductions,
                )
            )
        return PreparedExecutionContinuation(
            source_request_fingerprint=request.digest(),
            complete_continuations=tuple(continuations),
        )
    except (ValueError, TypeError) as error:
        return _rejected(request.command_id, error)
