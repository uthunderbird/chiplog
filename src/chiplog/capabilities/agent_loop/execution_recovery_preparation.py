"""Owner-local weak accounting and strong continuation over a supplied recovery cut.

These proposals bind exact supplied bytes. The broker must authenticate complete
selected/physical enumeration again before publishing either record or a Turn.
"""

from __future__ import annotations

import hashlib
import json

from pydantic import ValidationError

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
    RecoverySourceRecord,
    SealedAccountingRecord,
)
from .native_recovery_cut_validation import validate_native_recovery_cut
from .native_recovery_profile_contracts import SCHEMA, SCHEMA_V2
from .recovery_contracts import Absent, OriginalObligationBinding, Present, RecoveryDTO
from .recovery_domain import (
    RecoveryHold,
    RecoveryIntegrityFault,
    model_continuation_ready,
    sealed_call_accounting,
)
from .recovery_frontier_contracts import CancelledBeforeAcceptResult, InitializedCall
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

    native_profile = any(item.schema_id in (SCHEMA, SCHEMA_V2) for item in cut.complete_sources)
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
    cancellation_registry = execution_native_one_call_cancellation_frontier_registry_v1()
    recovery_registry = execution_native_one_call_recovery_frontier_registry_v2()
    if cut.frontier.registry == recovery_registry:
        if len(profiles_v2) != 1 or profiles_v1:
            raise RecoveryHold("V2 registry requires exactly one selected V2 profile")
        validate_native_recovery_cut(cut)
    elif cut.frontier.registry == cancellation_registry:
        if profiles_v2 or len(profiles_v1) > 1:
            raise RecoveryHold("V1 registry has an incompatible selected native profile")
        if profiles_v1:
            validate_native_recovery_cut(cut)
    else:
        raise RecoveryHold("unsupported recovery registry")
    native_profile = bool(profiles_v1 or profiles_v2)
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
    init_ref = seal.complete_ordered_initialized[0]
    if (
        call.original_call_id != init_ref.subject_id
        or call.response_id != seal.response_seal_id
        or call.kind != "TERMINAL"
        or not isinstance(call.acceptance, InitializedCall)
        or call.acceptance.initialized != init_ref.revision
        or not isinstance(call.disposition, CancelledBeforeAcceptResult)
    ):
        raise RecoveryHold("native call is not exactly cancelled before acceptance")
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
            model_continuation_ready(joined, (), ())
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
                    complete_original_closures=(),
                    complete_current_reductions=(),
                )
            )
        return PreparedExecutionContinuation(
            source_request_fingerprint=request.digest(),
            complete_continuations=tuple(continuations),
        )
    except (ValueError, TypeError) as error:
        return _rejected(request.command_id, error)
