"""Owner-local weak accounting and strong continuation over a supplied recovery cut.

These proposals bind exact supplied bytes. The broker must authenticate complete
selected/physical enumeration again before publishing either record or a Turn.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from pydantic import ValidationError

from chiplog.capabilities.agent_loop.direct_effect_terminal_contracts import (
    classify_direct_effect_terminal_v1,
    translate_selected_outcome_v1,
)
from chiplog.capabilities.effects.contracts import ExactHead
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
    ExecutionRecoveryClassified,
    ExecutionSuccessorEdge,
    NonSchedulerSuccessor,
    PrepareAcceptedOpenAccounting,
    PrepareAcceptedOpenRecoveryV1,
    PreparedAcceptedOpenAccounting,
    PreparedAcceptedOpenRecoveryV1,
    PreparedExecutionAccounting,
    PreparedExecutionContinuation,
    PreparedExecutionResume,
    PreparedExecutionSuccessor,
    PreparedExecutionSuccessorStart,
    PreparedExecutionSuspension,
    PrepareExecutionAccounting,
    PrepareExecutionContinuation,
    PrepareExecutionResume,
    PrepareExecutionSuccessor,
    PrepareExecutionSuccessorStart,
    PrepareExecutionSuspension,
)
from .execution_recovery_observations import (
    ContinuationReadyRecord,
    ExecutionRecoveryCut,
    ExecutionSuspensionBaseline,
    ExecutionSuspensionPair,
    OriginalRecoveredClosure,
    RecoverySourceRecord,
    SealedAccountingRecord,
)
from .native_recovery_cut_validation import validate_native_recovery_cut
from .native_recovery_profile_contracts import (
    SCHEMA,
    SCHEMA_V2,
    SCHEMA_V3,
    SCHEMA_V4,
    SCHEMA_V5,
    native_recovery_fingerprint,
)
from .recovery_contracts import (
    Absent,
    NonSchedulerFence,
    OriginalObligationBinding,
    Present,
    RecoveryDTO,
)
from .recovery_domain import (
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
    RecoveryFrontier,
    RecoveryRequiredResult,
    SuccessResult,
    TerminalCallFrontier,
)
from .recovery_frontier_registry_contracts import (
    execution_native_one_call_cancellation_frontier_registry_v1,
    execution_native_one_call_recovery_frontier_registry_v2,
    frontier_registry_reference,
)
from .recovery_record_contracts import AGENT_LOOP_OWNER, RecoveryRecordMember
from .successor_record_contracts import (
    SUCCESSOR_INITIALIZATION_SCHEMA,
    ExecutionSuccessorInitializationRecord,
    _expected_successor_inheritance,
    _pending_head,
    _validate_admitted_successor_source,
)

_RUN = "chiplog.agent-loop.execution-record.v2"
_SEAL = "chiplog.call.response-seal.v1"
_INITIALIZED = "chiplog.call.initialized.v1"
_CANCELLED = "chiplog.call.cancelled-before-accept.v1"
_NOT_EXECUTED = "chiplog.call.not-executed-result.v1"
_DIRECT_ACCOUNTING = "chiplog.execution.accepted-direct-terminal-accounting.v1"
_DIRECT_TERMINAL = "chiplog.execution.accepted-direct-terminal-disposition.v1"
_DIRECT_RESULT = "chiplog.execution.accepted-direct-terminal-result.v1"
_ACCEPTED_CALL = "chiplog.call.accepted.v1"
_EXECUTION_INTENT = "chiplog.call.execution-intent.v1"


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
        byte_matches = sum(item.canonical_record_bytes == raw for item in matches)
        raise RecoveryHold(
            "exact selected source is missing, repeated, or differs: "
            f"schema={schema} subject={expected.subject_id} "
            f"head={expected.revision.head} matches={len(matches)} byte_matches={byte_matches}"
        )
    source = matches[0]

    native_profile = any(
        item.schema_id in (SCHEMA, SCHEMA_V2, SCHEMA_V3, SCHEMA_V4, SCHEMA_V5)
        for item in cut.complete_sources
    )
    if source.subject.revision.fingerprint != _sha(raw) or (
        not native_profile and source.physical_record.revision.fingerprint != _sha(raw)
    ):
        raise RecoveryIntegrityFault(
            "source fingerprint differs from bytes or bounded physical row"
        )
    return source


def _validate_cut(cut: ExecutionRecoveryCut, *, allow_suspended: bool = False) -> None:
    """Check the bounded native one-call profile without claiming cut authenticity."""

    profiles_v1 = tuple(source for source in cut.complete_sources if source.schema_id == SCHEMA)
    profiles_v2 = tuple(source for source in cut.complete_sources if source.schema_id == SCHEMA_V2)
    profiles_v3 = tuple(source for source in cut.complete_sources if source.schema_id == SCHEMA_V3)
    profiles_v4 = tuple(source for source in cut.complete_sources if source.schema_id == SCHEMA_V4)
    profiles_v5 = tuple(source for source in cut.complete_sources if source.schema_id == SCHEMA_V5)
    cancellation_registry = execution_native_one_call_cancellation_frontier_registry_v1()
    recovery_registry = execution_native_one_call_recovery_frontier_registry_v2()
    if cut.frontier.registry == recovery_registry:
        if (
            len(profiles_v2) + len(profiles_v3) + len(profiles_v4) + len(profiles_v5) != 1
            or profiles_v1
        ):
            raise RecoveryHold("V2 registry requires exactly one selected V2/V3/V4/V5 profile")
        validate_native_recovery_cut(cut)
    elif cut.frontier.registry == cancellation_registry:
        if profiles_v2 or profiles_v3 or profiles_v4 or profiles_v5 or len(profiles_v1) > 1:
            raise RecoveryHold("V1 registry has an incompatible selected native profile")
        if profiles_v1:
            validate_native_recovery_cut(cut)
    else:
        raise RecoveryHold("unsupported recovery registry")
    native_profile = bool(profiles_v1 or profiles_v2 or profiles_v3 or profiles_v4 or profiles_v5)
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
    allowed_states = ("ACTIVE", "SUSPENDED") if allow_suspended else ("ACTIVE",)
    if _run_ref(current) != cut.current_run or current.state not in allowed_states:
        raise RecoveryHold("recovery Run is not the exact current Run for this route")
    attempt = current.turns[-1].attempts[current.turns[-1].selector]
    artifact = attempt.manifest.artifact
    bindings = cut.current_bindings
    binding_mismatches = tuple(
        name
        for name, mismatch in (
            ("objective", bindings.objective != current.prompt),
            ("requested_work", bindings.requested_work != attempt.request),
            ("prompt_artifact", bindings.prompt_artifact.fingerprint != artifact.digest()),
            (
                "ordered_tool_specs",
                tuple(ref.fingerprint for ref in bindings.ordered_tool_specs)
                != tuple(tool.digest() for tool in artifact.tools),
            ),
            (
                "generated_schema",
                bindings.generated_schema.fingerprint
                != _sha(artifact.response_schema_json.encode()),
            ),
            ("policy", bindings.policy.fingerprint != current.policy.digest()),
            (
                "no_retry_boundaries",
                bindings.no_retry_boundaries
                != tuple(ref.revision for ref in current.no_retry_references),
            ),
            ("authority_mandate_heads", bool(bindings.authority_mandate_heads)),
            (
                "recipient_effect_bindings",
                not native_profile and bool(bindings.recipient_effect_bindings),
            ),
        )
        if mismatch
    )
    if binding_mismatches:
        raise RecoveryIntegrityFault(
            "frozen bindings differ from bounded native Run: " + ",".join(binding_mismatches)
        )
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
        if len(profiles_v3) + len(profiles_v5) != 1 or profiles_v2 or profiles_v4:
            raise RecoveryHold("accepted recovery requires the selected V3 or V5 native profile")
        _validate_accepted_recovery(cut, call, init, init_ref)
        return
    if isinstance(call.acceptance, ConsequentialAcceptedCall) and isinstance(
        call.disposition, (SuccessResult, DefiniteFailureResult)
    ):
        if cut.frontier.registry != recovery_registry:
            raise RecoveryHold("direct terminal requires the V2 native recovery registry")
        if len(profiles_v3) + len(profiles_v5) != 1 or profiles_v2 or profiles_v4:
            raise RecoveryHold("direct terminal requires the selected V3 or V5 native profile")
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


def _expected_suspended_frontier(
    baseline_frontier: RecoveryFrontier,
    predecessor_run: CallSubjectHead,
    suspended_run: CallSubjectHead,
    tenant_commit_sequence: int,
) -> RecoveryFrontier:
    if tenant_commit_sequence != baseline_frontier.tenant_commit_sequence + 1:
        raise RecoveryHold("resume cut is not the exact suspension publication successor")
    matches = tuple(
        (index, row)
        for index, row in enumerate(baseline_frontier.ordered_members)
        if row.family == "RUN" and row.subject == baseline_frontier.run_id
    )
    if len(matches) != 1:
        raise RecoveryHold("baseline frontier has no unique Run member")
    index, run_member = matches[0]
    if run_member.ordered_heads != (predecessor_run.revision,):
        raise RecoveryHold("baseline Run member differs from selected suspension predecessor")
    updated = run_member.model_copy(
        update={"ordered_heads": (suspended_run.revision,), "fingerprint": "0" * 64}
    )
    member_value = updated.model_dump(mode="json")
    del member_value["fingerprint"]
    updated = updated.model_copy(
        update={"fingerprint": native_recovery_fingerprint("member", member_value)}
    )
    members = list(baseline_frontier.ordered_members)
    members[index] = updated
    expected = baseline_frontier.model_copy(
        update={
            "tenant_commit_sequence": tenant_commit_sequence,
            "ordered_members": tuple(members),
            "fingerprint": "0" * 64,
        }
    )
    frontier_value = expected.model_dump(mode="json")
    del frontier_value["fingerprint"]
    return expected.model_copy(
        update={"fingerprint": native_recovery_fingerprint("frontier", frontier_value)}
    )


def _validate_obligation_resume_successor(
    cut: ExecutionRecoveryCut,
    baseline: ExecutionSuspensionBaseline,
    suspended_run: ExecutionRunRecord,
) -> None:
    """Prove the one registered open-obligation → closed-resume frontier path."""
    from .execution_recovery_contracts import AcceptedOpenRecoveryOpeningV1

    profiles = tuple(source for source in cut.complete_sources if source.schema_id == SCHEMA_V5)
    if len(profiles) != 1 or len(baseline.original_obligations) != 1:
        raise RecoveryHold("obligation resume requires one V5 profile and one frozen obligation")
    if len(baseline.frontier.ordered_calls) != 1 or len(cut.frontier.ordered_calls) != 1:
        raise RecoveryHold("obligation resume requires one source and one current call")
    original_call = baseline.frontier.ordered_calls[0]
    current_call = cut.frontier.ordered_calls[0]
    original_disposition = original_call.disposition
    current_disposition = current_call.disposition
    binding = baseline.original_obligations[0]
    if (
        not isinstance(original_disposition, RecoveryRequiredResult)
        or not isinstance(current_disposition, RecoveryRequiredResult)
        or original_disposition.recovered_outcome.outcome.kind != "ABSENT"
        or current_disposition.recovered_outcome.outcome.kind != "PRESENT"
        or original_disposition.obligation != binding
        or current_disposition.obligation != binding
        or original_call.original_call_id != current_call.original_call_id
        or original_call.acceptance != current_call.acceptance
        or original_disposition.terminal != current_disposition.terminal
    ):
        raise RecoveryHold("current recovery is not the exact closure of the frozen obligation")
    if len(cut.original_closures) != 1 or len(cut.current_reductions) != 1:
        raise RecoveryHold("obligation resume requires one closure and one current reduction")
    closure = cut.original_closures[0]
    reduction = cut.current_reductions[0]
    current_evidence_members = tuple(
        member for member in cut.frontier.ordered_members if member.family == "EVIDENCE"
    )
    current_call_members = tuple(
        member for member in cut.frontier.ordered_members if member.family == "CALL"
    )
    if (
        closure.original != binding
        or closure.terminal_disposition != current_disposition.terminal
        or closure.recovered_outcome != current_disposition.recovered_outcome.outcome
        or reduction.reduction_id != closure.reduction_id
        or reduction.obligation_closure != closure.closure
        or reduction.accepted_outcome != closure.recovered_outcome
        or reduction.consumability != "CONSUMABLE"
        or len(current_evidence_members) != 1
        or current_evidence_members[0].ordered_heads != reduction.ordered_consumed_evidence
        or len(current_call_members) != 1
        or current_call_members[0].ordered_heads[-2:]
        != (
            current_disposition.terminal,
            current_disposition.recovered_outcome.outcome,
        )
    ):
        raise RecoveryHold("closure and consumable reduction do not cover the frozen obligation")

    expected = _expected_suspended_frontier(
        baseline.frontier,
        baseline.predecessor_run,
        _run_ref(suspended_run),
        baseline.frontier.tenant_commit_sequence + 1,
    )
    if cut.frontier.tenant_commit_sequence <= expected.tenant_commit_sequence:
        raise RecoveryHold("obligation closure did not advance beyond suspension publication")
    allowed = {
        "CALL",
        "EVIDENCE",
        "AUTHORITY",
        "POLICY",
        "OBLIGATION",
        "SEMANTIC_REDUCTION",
    }
    before_by_family = {row.family: row for row in expected.ordered_members}
    current_by_family = {row.family: row for row in cut.frontier.ordered_members}
    if tuple(before_by_family) != tuple(current_by_family):
        raise RecoveryHold("obligation resume changed native frontier family inventory")
    opening_sources = tuple(
        source
        for source in cut.complete_sources
        if source.schema_id == "chiplog.execution.accepted-open-recovery-opening.v1"
        and source.subject.subject_id == binding.obligation_id
        and source.subject.revision.head == binding.obligation_head
    )
    if len(opening_sources) != 1:
        raise RecoveryHold("obligation resume lost or duplicated its original opening")
    opening = _record(opening_sources[0], AcceptedOpenRecoveryOpeningV1)
    if opening.original != binding:
        raise RecoveryHold("obligation resume opening no longer binds the frozen cause")
    for family, before in before_by_family.items():
        after = current_by_family[family]
        if family not in allowed:
            if after != before:
                raise RecoveryHold(f"obligation resume changed unrelated frontier family {family}")
            continue
        if family in {"EVIDENCE", "POLICY"}:
            prefix = (
                ()
                if family == "EVIDENCE" and before.ordered_heads == (Absent(),)
                else before.ordered_heads
            )
            if after.ordered_heads[: len(prefix)] != prefix:
                raise RecoveryHold(f"obligation resume rewrote existing {family} heads")
        elif family == "OBLIGATION":
            if (
                len(before.ordered_heads) != 2
                or len(after.ordered_heads) != 4
                or after.ordered_heads[0] != before.ordered_heads[0]
                or after.ordered_heads[1] != current_disposition.recovered_outcome.outcome
                or after.ordered_heads[2] != closure.closure
                or after.ordered_heads[3] != closure.resolver_batch
            ):
                raise RecoveryHold("obligation resume changed the opening or closure shape")
        elif family == "SEMANTIC_REDUCTION":
            if before.ordered_heads != (Absent(),) or after.ordered_heads != (
                reduction.current_reduction,
            ):
                raise RecoveryHold("obligation resume has an unexpected reduction transition")
        elif family == "AUTHORITY":
            resolver_policies = tuple(
                source
                for source in cut.complete_sources
                if source.schema_id == "chiplog.loop.original-resolver-authority-policy.v1"
            )
            invocations = tuple(
                source
                for source in cut.complete_sources
                if source.schema_id == "chiplog.loop.original-resolver-invocation.v1"
            )
            reducer_policies = tuple(
                source
                for source in cut.complete_sources
                if source.schema_id == "chiplog.loop.original-effect-evidence-reducer-policy.v1"
            )
            if len(resolver_policies) != 1 or len(invocations) != 1 or len(reducer_policies) != 1:
                raise RecoveryHold(
                    "obligation resume lacks one exact resolver authority and invocation"
                )
            from .original_recovery_contracts import (
                OriginalEffectEvidenceReducerPolicyV1,
                OriginalResolverAuthorityPolicyV1,
                OriginalResolverInvocationV1,
            )

            policy = _record(resolver_policies[0], OriginalResolverAuthorityPolicyV1)
            invocation = _record(invocations[0], OriginalResolverInvocationV1)
            reducer_policy = _record(reducer_policies[0], OriginalEffectEvidenceReducerPolicyV1)
            if (
                policy.tenant_id != cut.tenant_id
                or policy.principal_id != "hermetic-principal"
                or policy.resolver_id != binding.resolver_id
                or policy.resolver_version != binding.resolver_version
                or invocation.original != binding
                or invocation.tenant_id != cut.tenant_id
                or invocation.principal_id != policy.principal_id
                or reducer_policy.tenant_id != cut.tenant_id
                or reducer_policy.reducer_id != binding.reducer_id
                or reducer_policy.reducer_version != binding.reducer_version
            ):
                raise RecoveryHold("obligation resume resolver policy or invocation is foreign")
            from .execution_recovery_contracts import AcceptedOpenTerminalDispositionV1
            from .original_recovery_contracts import original_resolver_authority_policy_v1

            terminal_sources = tuple(
                source
                for source in cut.complete_sources
                if source.schema_id == "chiplog.execution.accepted-open-terminal-disposition.v1"
                and source.subject.subject_id == current_call.original_call_id
                and source.subject.revision == current_disposition.terminal
            )
            if len(terminal_sources) != 1:
                raise RecoveryHold("obligation resume lost or duplicated its selected terminal")
            terminal = _record(terminal_sources[0], AcceptedOpenTerminalDispositionV1)
            expected_policy = original_resolver_authority_policy_v1(cut.tenant_id)
            if (
                policy != expected_policy
                or invocation.accepted_opening_source != opening_sources[0]
                or invocation.accepted_terminal_source != terminal_sources[0]
                or invocation.resolver_policy_source != resolver_policies[0]
                or invocation.reducer_policy_source != reducer_policies[0]
                or terminal.obligation_opening.subject_id != opening.opening_id
                or terminal.original_call_id != current_call.original_call_id
                or terminal.external_effect_intent.revision
                != current_call.acceptance.external_effect_intent
            ):
                raise RecoveryHold(
                    "resolver invocation does not bind the exact opening and terminal"
                )
            selected_resolver_sources = tuple(
                source
                for source in cut.complete_sources
                if source.schema_id.startswith("chiplog.loop.original-resolver-")
                or source.schema_id == "chiplog.loop.original-effect-evidence-reducer-policy.v1"
            )
            if after.ordered_heads != (
                *before.ordered_heads,
                *(source.subject.revision for source in selected_resolver_sources),
            ):
                raise RecoveryHold(
                    "obligation resume authority family has extra or missing sources"
                )
            policy_member = current_by_family["POLICY"]
            if policy_member.ordered_heads != (
                *before_by_family["POLICY"].ordered_heads,
                *(source.subject.revision for source in selected_resolver_sources),
            ):
                raise RecoveryHold("obligation resume policy family has extra or missing sources")


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
    outcome_rows = _selected_direct_outcome_sources(
        cut.complete_sources,
        accounting.selected_outcome_record_id,
        accounting.selected_decision,
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
    checks = (
        ("call_initialized", call.original_call_id == initialized.subject_id),
        ("acceptance_initialized", acceptance.initialized == initialized.revision),
        ("acceptance_manifest_length", len(acceptance.complete_acceptance_manifest) == 3),
        ("accounting_call", accounting.original_call_id == call.original_call_id),
        ("accounting_response", accounting.response_id == call.response_id),
        ("accounting_accepted", accounting.accepted.revision == acceptance.accepted),
        (
            "accounting_execution_intent",
            accounting.execution_intent.revision == acceptance.execution_intent,
        ),
        (
            "accounting_external_intent",
            accounting.external_effect_intent.revision == external_effect,
        ),
        ("accounting_terminal_present", isinstance(accounting.terminal.revision, Present)),
        ("accounting_terminal", accounting.terminal.revision == disposition.terminal),
        ("accounting_result_present", isinstance(accounting.result.revision, Present)),
        ("accounting_result", accounting.result.revision == disposition.result),
        (
            "acceptance_manifest",
            acceptance.complete_acceptance_manifest
            == (
                acceptance.accepted,
                acceptance.execution_intent,
                acceptance.external_effect_intent,
            ),
        ),
        (
            "selected_outcome_fingerprint",
            accounting.selected_outcome_fingerprint
            == selected_outcome.physical_record.revision.fingerprint,
        ),
        (
            "selected_decision",
            accounting.selected_decision == selected_outcome.selected_decision,
        ),
        (
            "selected_outcome_record_id",
            accounting.selected_outcome_record_id == outcome.record.head,
        ),
        (
            "selected_outcome_bytes",
            outcome.canonical_bytes() == selected_outcome.canonical_record_bytes,
        ),
        ("outcome_state", outcome.snapshot.state == expected_state),
        ("obligation_closed", outcome.snapshot.obligation.state == "CLOSED"),
        ("outcome_intent", outcome.snapshot.intent.intent_id == classification.intent.subject_id),
        ("terminal_kind", terminal.kind == expected_kind),
        ("terminal_call", terminal.original_call_id == call.original_call_id),
        ("terminal_response", terminal.response_id == call.response_id),
        ("terminal_accepted", terminal.accepted.revision == acceptance.accepted),
        ("terminal_external_intent", terminal.external_effect_intent.revision == external_effect),
        ("terminal_mapping_id", terminal.mapping_id == "R14_DIRECT_EFFECT_TERMINAL_V1"),
        ("terminal_mapping_version", terminal.mapping_version == "r14-direct-effect-terminal.v1"),
        ("result_id", result.result_id == accounting.result.subject_id),
        ("result_call", result.original_call_id == call.original_call_id),
        ("result_terminal", result.terminal.revision == disposition.terminal),
        ("classification_disposition", classification.disposition == expected_kind),
        ("classification_semantic", classification.semantic_class == expected_semantic),
        ("classification_mapping_id", classification.mapping_id == "R14_DIRECT_EFFECT_TERMINAL_V1"),
        (
            "classification_mapping_version",
            classification.mapping_version == "r14-direct-effect-terminal.v1",
        ),
        ("classification", classification == expected_classification),
        ("selected_effect_terminal", terminal.selected_effect_terminal == classification.terminal),
        (
            "classification_intent",
            classification.intent.subject_id == outcome.snapshot.intent.intent_id,
        ),
        (
            "classification_intent_fingerprint",
            classification.intent.fingerprint == external_effect.fingerprint,
        ),
    )
    mismatches = tuple(name for name, matched in checks if not matched)
    if mismatches:
        raise RecoveryIntegrityFault(
            "direct terminal does not join its exact accepted R16 outcome: " + ",".join(mismatches)
        )
    for evidence in classification.ordered_evidence:
        refs = _selected_direct_evidence_sources(cut.complete_sources, evidence)
        if len(refs) != 1:
            raise RecoveryHold("direct terminal evidence source is absent or ambiguous")


def _selected_direct_outcome_sources(
    sources: tuple[RecoverySourceRecord, ...],
    record_id: str,
    selected_decision: CallSubjectHead,
) -> tuple[RecoverySourceRecord, ...]:
    """Find the R16 outcome bound to its selected member and physical record ID."""
    matches = tuple(
        row
        for row in sources
        if row.owner == "effects"
        and row.schema_id == "chiplog.effects.dispatch-outcome-record.v2"
        and row.physical_record.subject_id == record_id
        and row.selected_decision == selected_decision
    )
    unique: dict[tuple[bytes, CallSubjectHead, CallSubjectHead], RecoverySourceRecord] = {}
    for row in matches:
        unique[(row.canonical_record_bytes, row.selected_decision, row.physical_record)] = row
    return tuple(unique.values())


def _selected_direct_evidence_sources(
    sources: tuple[RecoverySourceRecord, ...], evidence: ExactHead
) -> tuple[RecoverySourceRecord, ...]:
    """Find the authenticated R16 carrier for an exact semantic evidence head."""
    matches = tuple(
        row
        for row in sources
        if row.owner == "effects"
        and row.schema_id == "chiplog.effects.dispatch-outcome-record.v2"
        and row.subject.subject_id == evidence.subject_id
        and row.subject.revision == Present(head=evidence.head, fingerprint=evidence.fingerprint)
    )
    unique: dict[tuple[bytes, CallSubjectHead, CallSubjectHead], RecoverySourceRecord] = {}
    for row in matches:
        unique[(row.canonical_record_bytes, row.selected_decision, row.physical_record)] = row
    return tuple(unique.values())


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
    unique = {
        (
            source.owner,
            source.schema_id,
            source.selected_decision,
            source.physical_record,
            source.canonical_record_bytes,
        ): source
        for source in matches
    }
    if len(unique) != 1:
        raise RecoveryHold(
            "missing or duplicate accepted-recovery source: "
            f"schema={schema} head={revision.head} matches={len(unique)}"
        )
    source = next(iter(unique.values()))
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
        raise RecoveryHold(
            "missing or duplicate accepted-recovery subject: "
            f"schema={schema} subject={subject_id} head={head} matches={len(matches)}"
        )
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
        or disposition.recovered_outcome.original_call_id != call.original_call_id
        or disposition.recovered_outcome.recovery_obligation_id
        != disposition.obligation.obligation_id
    ):
        raise RecoveryHold("accepted recovery frontier is incomplete")

    accepted_source = _source_for_revision(cut, acceptance.accepted, _ACCEPTED_CALL)
    accepted = _record(accepted_source, ToolCallAcceptedRecord)
    accepted_ref = call_record_reference(accepted.accepted_id, accepted)
    execution_source = _source_for_revision(cut, acceptance.execution_intent, _EXECUTION_INTENT)
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
    if recovered.kind == "ABSENT":
        if cut.original_closures or cut.current_reductions:
            raise RecoveryHold("open accepted recovery carries a closure or reduction")
        return
    if recovered.kind != "PRESENT":
        raise RecoveryHold("accepted recovery outcome marker is invalid")
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


@dataclass(frozen=True)
class _OriginalContinuationJoin:
    closure: OriginalRecoveredClosure
    reduction: EvidenceReduction


def _original_continuation_join(
    cut: ExecutionRecoveryCut,
    summary: OriginalRecoveredClosure,
    reduction: EvidenceReduction,
) -> _OriginalContinuationJoin:
    """Check real source domains; selection authority remains with the cut publisher."""
    from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
    from chiplog.capabilities.effects.dispatch_outcomes import evidence_head
    from chiplog.capabilities.effects.dispatch_v2 import reference as effects_reference

    from .direct_effect_terminal_contracts import translate_selected_outcome_v1
    from .original_recovery_contracts import (
        LoopSemanticReductionRecord,
        OriginalObligationClosureRecord,
        OriginalResolutionBasis,
        OriginalResolverBatchRecord,
        RecoveredCallOutcomeRecord,
    )

    def exact[T](
        ref: CallSubjectHead,
        schema: str,
        type_: type[T],
        *,
        physical: bool = False,
        owner: str = "agent_loop",
    ) -> tuple[RecoverySourceRecord, T]:
        rows = [
            row
            for row in cut.complete_sources
            if row.owner == owner
            and row.schema_id == schema
            and (
                (row.subject == ref or row.physical_record == ref)
                if owner == "effects"
                else (row.physical_record if physical else row.subject) == ref
            )
        ]
        if len(rows) != 1:
            raise RecoveryHold("original join source is missing or duplicate")
        row = rows[0]
        value = _record(row, type_)
        if owner == "effects":
            embedded = value.record
            body_ref = CallSubjectHead(
                subject_id=embedded.subject_id,
                revision=Present(head=embedded.head, fingerprint=embedded.fingerprint),
            )
            digest = _sha(row.canonical_record_bytes)
            carrier_ref = CallSubjectHead(
                subject_id=embedded.head,
                revision=Present(head="record:" + digest, fingerprint=digest),
            )
            child_ref = None
            if value.command.evidence is not None:
                child = evidence_head(value.command.evidence)
                child_ref = CallSubjectHead(
                    subject_id=child.subject_id,
                    revision=Present(head=child.head, fingerprint=child.fingerprint),
                )
            if (
                ref != body_ref
                or row.subject not in (body_ref, child_ref)
                or row.physical_record not in (body_ref, carrier_ref)
            ):
                raise RecoveryIntegrityFault("original effects carrier differs")
        else:
            _source(cut, schema, row.subject, row.canonical_record_bytes)
            identifier = next(
                getattr(value, field)
                for field in ("basis_id", "closure_id", "outcome_id", "batch_id", "reduction_id")
                if hasattr(value, field)
            )
            if isinstance(value, LoopSemanticReductionRecord):
                identifier = schema + ":" + _sha(value.canonical_bytes())
            expected = CallSubjectHead(
                subject_id=identifier,
                revision=Present(
                    head="record:" + _sha(value.canonical_bytes()),
                    fingerprint=_sha(value.canonical_bytes()),
                ),
            )
            if row.subject != expected:
                raise RecoveryIntegrityFault("original source body identity differs")
        return row, value

    rs = _source_for_revision(
        cut, reduction.current_reduction, "chiplog.loop.semantic-reduction.v1"
    )
    _, record = exact(rs.subject, "chiplog.loop.semantic-reduction.v1", LoopSemanticReductionRecord)
    anchor = record.anchor
    if anchor.kind != "RESOLVED" or record.disposition.kind != "CONSUMABLE":
        raise RecoveryHold("original current reduction is not resolved and consumable")
    cs, closure = exact(
        anchor.closure,
        "chiplog.loop.original-obligation-closure.v1",
        OriginalObligationClosureRecord,
        physical=True,
    )
    os, outcome = exact(
        anchor.recovered_outcome,
        "chiplog.loop.recovered-call-outcome.v1",
        RecoveredCallOutcomeRecord,
        physical=True,
    )
    bs, batch = exact(
        anchor.resolver_batch,
        "chiplog.loop.original-resolver-batch.v1",
        OriginalResolverBatchRecord,
        physical=True,
    )
    basis_source, basis = exact(
        closure.basis, "chiplog.loop.original-resolution-basis.v1", OriginalResolutionBasis
    )
    original = summary.original
    if (
        closure.original != original
        or outcome.original != original
        or basis.original != original
        or anchor.original != original
        or outcome.basis != basis_source.subject
        or batch.basis != basis_source.subject
        or outcome.closure != cs.subject
        or batch.closure != cs.subject
        or batch.recovered_outcome != os.subject
        or basis.selected_witness != closure.accepted_witness
        or basis.selected_witness != outcome.accepted_witness
        or basis.selected_witness != anchor.accepted_witness
        or basis.original_terminal.revision != summary.terminal_disposition
        or basis.reduction_id != record.reduction_id
        or summary.reduction_id != record.reduction_id
        or record.stream.stream_id != original.evidence_stream_id
        or record.stream.subject.subject_id != original.obligation_id
        or (record.reducer_id, record.reducer_version)
        != (original.reducer_id, original.reducer_version)
        or summary.recovered_outcome != os.physical_record.revision
        or summary.closure != cs.physical_record.revision
        or summary.resolver_batch != bs.physical_record.revision
        or summary.accepted_evidence != basis.selected_witness.revision
        or len(set(record.complete_ordered_evidence)) != len(record.complete_ordered_evidence)
        or len({item.head for item in reduction.ordered_consumed_evidence})
        != len(reduction.ordered_consumed_evidence)
        or record.complete_ordered_evidence[: len(basis.complete_evidence_manifest)]
        != basis.complete_evidence_manifest
        or len(set(basis.complete_evidence_manifest)) != len(basis.complete_evidence_manifest)
        or any(
            value != basis.accepted_semantic_class
            for value in (
                outcome.accepted_semantic_class,
                anchor.accepted_semantic_class,
                record.disposition.semantic_class,
                summary.accepted_semantic_class,
                reduction.semantic_class,
            )
        )
        or reduction.stream_id != record.stream.stream_id
        or reduction.reduction_id != record.reduction_id
        or (reduction.reducer_id, reduction.reducer_version)
        != (record.reducer_id, record.reducer_version)
        or reduction.consumability != "CONSUMABLE"
        or reduction.accepted_witness != anchor.accepted_witness.revision
        or reduction.accepted_outcome != anchor.recovered_outcome.revision
        or reduction.obligation_closure != anchor.closure.revision
        or reduction.resolver_batch != anchor.resolver_batch.revision
        or reduction.ordered_consumed_evidence
        != tuple(item.revision for item in record.complete_ordered_evidence)
    ):
        raise RecoveryIntegrityFault("original continuation chain or projection differs")
    _, witness = exact(
        basis.selected_witness,
        "chiplog.effects.dispatch-outcome-record.v2",
        DispatchOutcomeRecordV2,
        physical=True,
        owner="effects",
    )
    if (
        witness.command.operation != "RESOLVE_OBLIGATION"
        or len(witness.snapshot.evidence) != len(basis.complete_evidence_manifest)
        or len(witness.snapshot.evidence_heads) != len(basis.complete_evidence_manifest)
        or translate_selected_outcome_v1(witness).semantic_class != basis.accepted_semantic_class
    ):
        raise RecoveryIntegrityFault("original accepted witness snapshot differs")
    intent = effects_reference(
        witness.snapshot.intent.intent_id, witness.snapshot.intent.canonical_bytes()
    )
    from .execution_recovery_contracts import (
        AcceptedOpenRecoveryOpeningV1,
        AcceptedOpenTerminalDispositionV1,
    )

    terminal_rows = [
        row
        for row in cut.complete_sources
        if row.owner == "agent_loop"
        and row.schema_id == "chiplog.execution.accepted-open-terminal-disposition.v1"
        and row.physical_record == basis.original_terminal
        and row.subject
        == CallSubjectHead(
            subject_id=original.original_call_id,
            revision=summary.terminal_disposition,
        )
    ]
    opening_rows = [
        row
        for row in cut.complete_sources
        if row.owner == "agent_loop"
        and row.schema_id == "chiplog.execution.accepted-open-recovery-opening.v1"
        and row.physical_record == closure.prior_obligation
        and row.subject
        == CallSubjectHead(
            subject_id=original.obligation_id,
            revision=Present(
                head=original.obligation_head,
                fingerprint=closure.prior_obligation.revision.fingerprint,
            ),
        )
    ]
    if len(terminal_rows) != 1 or len(opening_rows) != 1:
        raise RecoveryHold("original immutable terminal or opening source missing or duplicate")
    terminal_source, opening_source = terminal_rows[0], opening_rows[0]
    terminal = _record(terminal_source, AcceptedOpenTerminalDispositionV1)
    opening = _record(opening_source, AcceptedOpenRecoveryOpeningV1)
    for row in (terminal_source, opening_source):
        _source(cut, row.schema_id, row.subject, row.canonical_record_bytes)
    if (
        terminal.kind != "RECOVERY_REQUIRED"
        or terminal.original_call_id != original.original_call_id
        or terminal.obligation_opening != opening_source.physical_record
        or opening.original != original
        or opening_source.subject.subject_id != original.obligation_id
        or opening_source.subject.revision.head != original.obligation_head
        or terminal.external_effect_intent
        != CallSubjectHead(
            subject_id=intent.subject_id,
            revision=Present(head=intent.head, fingerprint=intent.fingerprint),
        )
    ):
        raise RecoveryIntegrityFault("original witness does not bind immutable terminal intent")
    if witness.command.intent != intent:
        raise RecoveryIntegrityFault("original witness command intent differs")
    for index, ref in enumerate(record.complete_ordered_evidence):
        _, evidence = exact(
            ref,
            "chiplog.effects.dispatch-outcome-record.v2",
            DispatchOutcomeRecordV2,
            physical=True,
            owner="effects",
        )
        if (
            evidence.kind != "EVIDENCE_RECORDED"
            or evidence.command.operation != "APPEND_EVIDENCE"
            or evidence.command.evidence is None
            or evidence.snapshot.intent != witness.snapshot.intent
            or evidence.command.intent != intent
        ):
            raise RecoveryIntegrityFault("original evidence carrier or intent differs")
        if index < len(basis.complete_evidence_manifest) and (
            evidence.command.evidence != witness.snapshot.evidence[index]
            or evidence_head(evidence.command.evidence) != witness.snapshot.evidence_heads[index]
        ):
            raise RecoveryIntegrityFault("original accepted evidence inventory differs")
    return _OriginalContinuationJoin(summary, reduction)


def _continuation_closures(
    cut: ExecutionRecoveryCut, accounting: SealedAccounting
) -> tuple[tuple[OriginalRecoveredClosure, ...], tuple[EvidenceReduction, ...]]:
    required = {
        call.original_call_id: call
        for call in accounting.calls
        if call.disposition.kind == "RECOVERY_REQUIRED"
    }
    closures = {item.original.original_call_id: item for item in cut.original_closures}
    reductions = {item.reduction_id: item for item in cut.current_reductions}
    if len(closures) != len(cut.original_closures) or len(reductions) != len(
        cut.current_reductions
    ):
        raise RecoveryIntegrityFault("duplicate recovery closure or semantic reduction")
    if set(closures) != set(required):
        raise RecoveryHold("recovered closure membership is incomplete or contains extra subjects")
    joined: list[_OriginalContinuationJoin] = []
    for call_id, call in required.items():
        summary = closures[call_id]
        disposition = call.disposition
        reduction = reductions.get(summary.reduction_id)
        if (
            reduction is None
            or disposition.recovered_outcome.outcome.kind != "PRESENT"
            or summary.original != disposition.obligation
            or summary.terminal_disposition != disposition.terminal
            or summary.recovered_outcome != disposition.recovered_outcome.outcome
        ):
            raise RecoveryHold("original recovered frontier differs")
        joined.append(_original_continuation_join(cut, summary, reduction))
    if set(reductions) != {item.reduction_id for item in closures.values()}:
        raise RecoveryHold("extra current semantic reduction")
    if required:
        # Return the unchanged projections carried by freshly constructed proofs.
        closure_proofs = {proof.closure: proof for proof in joined}
        reduction_proofs = {proof.reduction: proof for proof in joined}
        return (
            tuple(closure_proofs[item].closure for item in cut.original_closures),
            tuple(reduction_proofs[item].reduction for item in cut.current_reductions),
        )
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
        raise RecoveryHold(
            "missing or duplicate selected recovery source: "
            f"schema={schema} subject={ref.subject_id} head={ref.revision.head} "
            f"matches={len(matches)}"
        )
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
    cut_fingerprint: str | None = None
    records = []
    for view in cut.complete_ordered_responses:
        if view.frontier_head != frontier:
            raise RecoveryIntegrityFault("response frontier differs from current cut")
        seal = view.seal
        seal_fingerprint = seal.digest()
        joined = sealed_call_accounting(
            seal.response_seal_id,
            seal_fingerprint,
            tuple(item.subject_id for item in seal.complete_ordered_initialized),
            view.ordered_calls,
        )
        if cut_fingerprint is None:
            cut_fingerprint = cut.digest()
        preimage = {
            "domain": "chiplog.execution.sealed-accounting.v1",
            "source_cut_fingerprint": cut_fingerprint,
            "sealed_response": view.selected_seal.model_dump(mode="json"),
            "sealed_manifest_fingerprint": seal_fingerprint,
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
                source_cut_fingerprint=cut_fingerprint,
                sealed_response=view.selected_seal,
                sealed_manifest_fingerprint=seal_fingerprint,
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


def _successor_member(record_kind: str, schema_id: str, value: RecoveryDTO) -> RecoveryRecordMember:
    raw = value.canonical_bytes()
    digest = _sha(raw)
    return RecoveryRecordMember(
        owner=AGENT_LOOP_OWNER,
        record_kind=record_kind,
        schema_id=schema_id,
        record_id=schema_id + ":" + digest,
        canonical_record_bytes=raw,
        fingerprint=digest,
    )


def prepare_execution_successor(
    request: PrepareExecutionSuccessor,
) -> PreparedExecutionSuccessor | CallPreparationRejected:
    """Prepare one CLI successor batch; broker selection/authentication remains external."""
    try:
        request = PrepareExecutionSuccessor.model_validate_json(request.canonical_bytes())
        if not isinstance(request.fence, NonSchedulerFence):
            raise RecoveryHold("scheduler successor is outside the native CLI profile")
        _validate_cut(request.cut, allow_suspended=True)
        lineage = request.cut.complete_ordered_run_lineage
        current = lineage[-1]
        selected = request.original_suspension
        baseline, pair = selected.baseline, selected.pair
        if not isinstance(current, ExecutionRunRecord):
            raise RecoveryHold("successor requires the native v2 execution Run")
        if baseline.original_obligations:
            raise RecoveryHold("successor requires recovery of original obligations")
        if (
            current != request.run
            or _run_ref(current) != request.cut.current_run
            or current.state != "SUSPENDED"
            or current.worker_session != request.fence.worker_session_id
            or current.run_id != request.fence.run_id
            or current.head != request.fence.run_head
            or current.suspension_baseline != selected.pair.baseline
            or pair.suspended_run != _run_ref(current)
            or pair.baseline.subject_id != baseline.baseline_id
            or pair.predecessor_run != baseline.predecessor_run
            or baseline.run_id != current.run_id
            or baseline.activation_blocking_predicates
            or current.original_obligations != baseline.original_obligations
            or len(lineage) < 2
            or _run_ref(lineage[-2]) != baseline.predecessor_run
        ):
            raise RecoveryHold("successor source Run or frozen suspension differs")
        if selected.canonical_pair_bytes != pair.canonical_bytes():
            raise RecoveryIntegrityFault("selected suspension pair bytes differ")
        if request.command_id != f"execution-successor:{current.run_id}:{current.head}":
            raise RecoveryHold("successor command identity differs from suspended Run")
        if request.disposition_version != "1":
            raise RecoveryHold("unsupported native successor disposition")
        _validate_admitted_successor_source(request, scheduler_successor=False)
        expected_obligations, expected_no_retry = _expected_successor_inheritance(request)
        pending = tuple(
            value
            for value in request.cut.frontier.ordered_calls
            if value.kind == "READ_ONLY_RETRY_PENDING"
        )

        superseded_pending = current.model_copy(
            update={
                "state": "SUPERSEDED",
                "predecessor": current.head,
                "event": request.command_id,
                "head": "pending",
            }
        )
        superseded = superseded_pending.model_copy(
            update={"head": "loop:" + superseded_pending.digest()}
        )
        proposal = request.successor
        successor_pending = ExecutionRunRecord(
            tenant=proposal.tenant,
            principal=proposal.principal,
            run_id=proposal.run_id,
            state="CREATED",
            head="pending",
            predecessor=current.head,
            prompt=proposal.prompt,
            policy=proposal.policy,
            origin=proposal.origin,
            contour_head=proposal.contour_head,
            policy_head=proposal.policy_head,
            worker_session=proposal.worker_session,
            root_binding="NOT_APPLICABLE",
            turns=(),
            delivery_acceptance=None,
            suspension_baseline=None,
            original_obligations=current.original_obligations,
            no_retry_references=current.no_retry_references,
            event=request.command_id + ":created",
        )
        successor = successor_pending.model_copy(
            update={"head": "loop:" + successor_pending.digest()}
        )
        predecessor_ref = _run_ref(current)
        superseded_ref = _run_ref(superseded)
        successor_ref = _run_ref(successor)
        admitted = request.admitted_input
        assert admitted is not None
        changed = tuple(
            value
            for value in (
                admitted.custody,
                admitted.inbox,
                admitted.selected_decision,
                admitted.source_authentication,
            )
        )
        initialization = ExecutionSuccessorInitializationRecord(
            command_id=request.command_id,
            predecessor_run=predecessor_ref,
            successor_run=successor_ref,
            source_cut_fingerprint=request.cut.digest(),
            disposition_version=request.disposition_version,
            observation_frontier=request.cut.tenant_commit_sequence,
            current_bindings=request.cut.current_bindings,
            execution_fence=request.fence,
            original_obligations=expected_obligations,
            inherited_no_retry_boundaries=expected_no_retry,
            inherited_pending_branches=pending,
        )
        init_member = _successor_member(
            "SUCCESSOR_INITIALIZATION", SUCCESSOR_INITIALIZATION_SCHEMA, initialization
        )
        init_ref = CallSubjectHead(
            subject_id=successor.run_id,
            revision=Present(head=init_member.record_id, fingerprint=init_member.fingerprint),
        )
        edge = ExecutionSuccessorEdge(
            command_id=request.command_id,
            original_pair=selected.selected_pair,
            predecessor_before=predecessor_ref,
            predecessor_superseded=superseded_ref,
            successor_created=successor_ref,
            source_cut_fingerprint=request.cut.digest(),
            disposition_version=request.disposition_version,
            changed_binding_manifest=changed,
            complete_initialization=(init_ref,),
            original_obligations=expected_obligations,
            inherited_no_retry_boundaries=expected_no_retry,
            inherited_pending_branches=tuple(_pending_head(value) for value in pending),
            observation_frontier=request.cut.tenant_commit_sequence,
            fence=request.fence,
        )
        outputs = (
            superseded.canonical_bytes(),
            successor.canonical_bytes(),
            edge.canonical_bytes(),
            init_member.canonical_record_bytes,
        )
        return PreparedExecutionSuccessor(
            source_request_fingerprint=request.digest(),
            superseded_run=superseded,
            successor_run=successor,
            edge=edge,
            lineage_advance=NonSchedulerSuccessor(),
            complete_initialization_bytes=(init_member.canonical_record_bytes,),
            complete_batch_fingerprint=_sha(b"\0".join(outputs)),
        )
    except (ValueError, TypeError, AttributeError, IndexError) as error:
        return _rejected(request.command_id, error)


def classify_execution_successor(
    request: PrepareExecutionSuccessor,
    prepared: PreparedExecutionSuccessor,
) -> ExecutionRecoveryClassified:
    """Recompute the one registered CLI successor disposition from its full request."""
    request = PrepareExecutionSuccessor.model_validate_json(request.canonical_bytes())
    current = request.cut.complete_ordered_run_lineage[-1]
    baseline = request.original_suspension.baseline
    admitted = request.admitted_input
    if not isinstance(request.fence, NonSchedulerFence) or admitted is None:
        raise ValueError("successor classification requires the selected native CLI route")
    if prepare_execution_successor(request) != prepared:
        raise ValueError("successor classification differs from prepared owner result")
    if (
        baseline.bindings.objective != current.prompt
        or request.cut.current_bindings.objective != baseline.bindings.objective
        or admitted.normalized_prompt == baseline.bindings.objective
        or admitted.normalized_prompt != admitted.raw_input_bytes.decode("utf-8")
        or request.successor.prompt != admitted.normalized_prompt
    ):
        raise ValueError("selected CLI ingress does not prove a changed registered objective")
    expected_manifest = (
        admitted.custody,
        admitted.inbox,
        admitted.selected_decision,
        admitted.source_authentication,
    )
    if prepared.edge.changed_binding_manifest != expected_manifest:
        raise ValueError("successor changed-binding manifest differs from selected CLI ingress")
    return ExecutionRecoveryClassified(
        source_request_fingerprint=request.digest(),
        disposition="SUCCESSOR_REQUIRED",
        reason="AUTHENTICATED_CLI_OBJECTIVE_CHANGED",
    )


def prepare_execution_successor_start(
    request: PrepareExecutionSuccessorStart,
) -> PreparedExecutionSuccessorStart | CallPreparationRejected:
    """Start exactly one successor Turn while preserving predecessor obligations."""
    try:
        request = PrepareExecutionSuccessorStart.model_validate_json(request.canonical_bytes())
        source = request.source_request
        created = request.created_run
        edge = ExecutionSuccessorEdge.model_validate_json(request.edge_bytes)
        if edge.canonical_bytes() != request.edge_bytes:
            raise RecoveryIntegrityFault("successor edge bytes are not canonical")
        if len(request.initialization_bytes) != 1:
            raise RecoveryHold("native successor start requires one initialization record")
        initialization = ExecutionSuccessorInitializationRecord.model_validate_json(
            request.initialization_bytes[0]
        )
        if initialization.canonical_bytes() != request.initialization_bytes[0]:
            raise RecoveryIntegrityFault("successor initialization bytes are not canonical")
        if (
            created.state != "CREATED"
            or created.predecessor != source.run.head
            or edge.successor_created != _run_ref(created)
            or edge.original_pair != source.original_suspension.selected_pair
            or edge.source_cut_fingerprint != source.cut.digest()
            or initialization.successor_run != _run_ref(created)
            or initialization.predecessor_run != edge.predecessor_before
            or initialization.command_id != source.command_id
            or initialization.execution_fence != source.fence
            or request.command_id != f"execution-successor-start:{created.run_id}:{created.head}"
        ):
            raise RecoveryHold("successor start does not select the exact CREATED successor")
        from .execution_lifecycle import start_successor_execution_turn

        started = start_successor_execution_turn(created)
        raw = started.canonical_bytes()
        return PreparedExecutionSuccessorStart(
            source_request_fingerprint=request.digest(),
            run=started,
            complete_batch_fingerprint=_sha(raw),
        )
    except (ValueError, TypeError, AttributeError) as error:
        return _rejected(request.command_id, error)


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
        request_bytes = request.canonical_bytes()
        request = PrepareExecutionContinuation.model_validate_json(request_bytes)
        _validate_cut(request.cut)
        expected = _accounting(request.cut)
        if request.complete_accounting != expected:
            raise RecoveryHold("accounting is missing, extra, reordered, or from another cut")
        cut_fingerprint: str | None = None
        continuations = []
        for view, accounting in zip(request.cut.complete_ordered_responses, expected, strict=True):
            joined = sealed_call_accounting(
                view.seal.response_seal_id,
                view.seal.digest(),
                tuple(ref.subject_id for ref in view.seal.complete_ordered_initialized),
                view.ordered_calls,
            )
            closures, reductions = _continuation_closures(request.cut, joined)
            if cut_fingerprint is None:
                cut_fingerprint = request.cut.digest()
            preimage = (
                b"chiplog.execution.continuation-ready.v1\x00"
                + cut_fingerprint.encode()
                + accounting.canonical_bytes()
            )
            continuations.append(
                ContinuationReadyRecord(
                    readiness_id="continuation-ready:" + _sha(preimage),
                    source_cut_fingerprint=cut_fingerprint,
                    accounting=accounting,
                    complete_original_closures=closures,
                    complete_current_reductions=reductions,
                )
            )
        return PreparedExecutionContinuation(
            source_request_fingerprint=hashlib.sha256(request_bytes).hexdigest(),
            complete_continuations=tuple(continuations),
        )
    except (ValueError, TypeError) as error:
        return _rejected(request.command_id, error)


def prepare_execution_suspension(
    request: PrepareExecutionSuspension,
) -> PreparedExecutionSuspension | CallPreparationRejected:
    """Prepare the acyclic baseline → suspended Run → pair publication batch."""
    try:
        request = PrepareExecutionSuspension.model_validate_json(request.canonical_bytes())
        if not isinstance(request.fence, NonSchedulerFence):
            raise RecoveryHold("scheduler execution cannot use this suspension route")
        _validate_cut(request.cut)
        lineage = request.cut.complete_ordered_run_lineage
        if not lineage or any(run.root_binding != "NOT_APPLICABLE" for run in lineage):
            raise RecoveryHold("suspension requires an authenticated non-scheduler Run lineage")
        current = lineage[-1]
        if (
            current != request.run
            or _run_ref(current) != request.cut.current_run
            or current.state != "ACTIVE"
            or current.worker_session != request.fence.worker_session_id
            or current.run_id != request.fence.run_id
            or current.head != request.fence.run_head
        ):
            raise RecoveryHold("suspension Run or non-scheduler fence is stale")
        if current.original_obligations:
            raise RecoveryHold("suspension predecessor already carries original obligations")
        if request.activation_blocking_predicates:
            raise RecoveryHold("first suspension profile requires no activation blockers")

        suspension_obligations: tuple[OriginalObligationBinding, ...] = ()
        if len(request.cut.frontier.ordered_calls) != 1:
            raise RecoveryHold("suspension requires one exact selected call frontier")
        selected_call = request.cut.frontier.ordered_calls[0]
        if isinstance(selected_call.disposition, RecoveryRequiredResult):
            profiles_v5 = tuple(
                source for source in request.cut.complete_sources if source.schema_id == SCHEMA_V5
            )
            if len(profiles_v5) != 1:
                raise RecoveryHold("open-obligation suspension requires native profile V5")
            if selected_call.disposition.recovered_outcome.outcome.kind != "ABSENT":
                raise RecoveryHold("only an open original obligation can enter this suspension")
            suspension_obligations = (selected_call.disposition.obligation,)

        cut_fingerprint = request.cut.digest()
        baseline_preimage = (
            b"chiplog.execution.suspension-baseline.v2\0"
            + request.command_id.encode()
            + b"\0"
            + cut_fingerprint.encode()
        )
        baseline = ExecutionSuspensionBaseline(
            baseline_id="suspension-baseline:" + _sha(baseline_preimage),
            suspension_command_id=request.command_id,
            run_id=current.run_id,
            predecessor_run=_run_ref(current),
            source_cut_fingerprint=cut_fingerprint,
            frontier=request.cut.frontier,
            bindings=request.cut.current_bindings,
            original_obligations=suspension_obligations,
            activation_blocking_predicates=tuple(
                predicate.revision for predicate in request.activation_blocking_predicates
            ),
        )
        baseline_raw = baseline.canonical_bytes()
        baseline_digest = _sha(baseline_raw)
        baseline_ref = CallSubjectHead(
            subject_id=baseline.baseline_id,
            revision=Present(head=baseline.baseline_id, fingerprint=baseline_digest),
        )
        suspended = current.model_copy(
            update={
                "state": "SUSPENDED",
                "original_obligations": suspension_obligations,
                "predecessor": current.head,
                "suspension_baseline": baseline_ref,
                "event": request.command_id,
                "head": "pending",
            }
        )
        suspended = suspended.model_copy(update={"head": "loop:" + suspended.digest()})
        suspended_ref = _run_ref(suspended)
        pair = ExecutionSuspensionPair(
            suspension_command_id=request.command_id,
            source_cut_fingerprint=cut_fingerprint,
            predecessor_run=_run_ref(current),
            baseline=baseline_ref,
            suspended_run=suspended_ref,
        )
        records = (baseline_raw, suspended.canonical_bytes(), pair.canonical_bytes())
        batch_fingerprint = _sha(b"\0".join(records))
        return PreparedExecutionSuspension(
            source_request_fingerprint=request.digest(),
            baseline=baseline,
            run=suspended,
            pair=pair,
            complete_batch_fingerprint=batch_fingerprint,
        )
    except (ValueError, TypeError, AttributeError) as error:
        return _rejected(request.command_id, error)


def prepare_execution_resume(
    request: PrepareExecutionResume,
) -> PreparedExecutionResume | CallPreparationRejected:
    """Prepare equality-only same-Run resume over the selected suspension."""
    try:
        request = PrepareExecutionResume.model_validate_json(request.canonical_bytes())
        if not isinstance(request.fence, NonSchedulerFence):
            raise RecoveryHold("scheduler execution cannot use this resume route")
        _validate_cut(request.cut, allow_suspended=True)
        lineage = request.cut.complete_ordered_run_lineage
        current = lineage[-1]
        selected = request.original_suspension
        baseline = selected.baseline
        pair = selected.pair
        if not isinstance(current, ExecutionRunRecord):
            raise RecoveryHold("same-Run resume requires the selected V2 execution record")
        current_ref = _run_ref(current)
        if (
            current != request.run
            or current_ref != request.cut.current_run
            or current.state != "SUSPENDED"
            or current.worker_session != request.fence.worker_session_id
            or current.run_id != request.fence.run_id
            or current.head != request.fence.run_head
            or current.root_binding != "NOT_APPLICABLE"
            or current.suspension_baseline != pair.baseline
            or pair.suspended_run != current_ref
            or pair.predecessor_run != baseline.predecessor_run
            or baseline.run_id != current.run_id
            or baseline.activation_blocking_predicates
            or current.original_obligations != baseline.original_obligations
            or len(lineage) < 2
            or _run_ref(lineage[-2]) != baseline.predecessor_run
        ):
            raise RecoveryHold("resume Run, frozen baseline, or selected suspension differs")
        if request.disposition_version != "r14-same-run-equality.v1":
            raise RecoveryHold("unsupported same-Run disposition version")
        if request.command_id != f"execution-resume:{current.run_id}:{current.head}":
            raise RecoveryHold("resume command identity differs from selected suspended Run")
        if (
            request.cut.frontier.registry != baseline.frontier.registry
            or request.cut.current_bindings != baseline.bindings
        ):
            raise RecoveryHold("resume frontier registry or frozen bindings differ")
        if baseline.original_obligations:
            _validate_obligation_resume_successor(request.cut, baseline, current)
        elif request.cut.frontier != _expected_suspended_frontier(
            baseline.frontier,
            baseline.predecessor_run,
            current_ref,
            request.cut.tenant_commit_sequence,
        ):
            raise RecoveryHold("resume frontier is not the exact mandatory suspension successor")

        resumed = current.model_copy(
            update={
                "state": "ACTIVE",
                "predecessor": current.head,
                "event": request.command_id,
                "head": "pending",
            }
        )
        resumed = resumed.model_copy(update={"head": "loop:" + resumed.digest()})
        run_raw = resumed.canonical_bytes()
        return PreparedExecutionResume(
            source_request_fingerprint=request.digest(),
            original_pair=selected.selected_pair,
            run=resumed,
            complete_batch_fingerprint=_sha(run_raw),
        )
    except (ValueError, TypeError, AttributeError, IndexError) as error:
        return _rejected(request.command_id, error)


def classify_execution_resume_rejection(
    request: PrepareExecutionResume,
    rejected: CallPreparationRejected,
) -> ExecutionRecoveryClassified:
    """Describe a failed resume preparation without authorizing a transition."""
    if rejected.command_id != request.command_id:
        raise ValueError("resume rejection belongs to another command")
    if rejected.code == "HOLD":
        disposition = "RECOVERY_HOLD"
        reason = "RESUME_FRONTIER_UNSUPPORTED_OR_INCOMPLETE"
    else:
        disposition = "TERMINAL_RECOVERY_FAULT"
        reason = "RESUME_INTEGRITY_FAULT_OBSERVED"
    return ExecutionRecoveryClassified(
        source_request_fingerprint=request.digest(),
        disposition=disposition,
        reason=reason,
    )


def classify_execution_resume(
    request: PrepareExecutionResume,
    result: PreparedExecutionResume | CallPreparationRejected,
) -> ExecutionRecoveryClassified:
    """Classify the observed preparation outcome; never authorize its writer."""
    if isinstance(result, PreparedExecutionResume):
        if result.source_request_fingerprint != request.digest():
            raise ValueError("prepared resume belongs to another request")
        disposition = "SAME_RUN"
        reason = "EXACT_SAME_RUN_FRONTIER_PREPARED"
        return ExecutionRecoveryClassified(
            source_request_fingerprint=request.digest(),
            disposition=disposition,
            reason=reason,
        )
    return classify_execution_resume_rejection(request, result)
