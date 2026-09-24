"""Complete inert V2 scheduler-execution batch fixtures for consumer tests."""

from __future__ import annotations

import base64
import hashlib
from typing import Literal

from chiplog.capabilities.agent_loop.execution_transition_contracts import (
    CreateExecutionRun,
    ExecutionTransitionProposal,
)
from chiplog.capabilities.agent_loop.execution_transitions import prepare_execution_transition
from chiplog.capabilities.agent_loop.recovery_contracts import Absent
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    IntervalBranch,
    StreamingEligibilityEvidence,
)
from chiplog.capabilities.agent_loop.scheduler_execution_contracts import (
    ORDINARY_CHARGED_EDGES_V2,
    OVERFLOW_CHARGED_EDGES_V2,
    OVERFLOW_SAFETY_EDGES_V2,
    ChargedScheduledIntervalAccountingV2,
    CompleteSchedulerExecutionDependencyManifestV2,
    FinalizedIntervalResultV2,
    FinalizedOccurrenceCommitmentV2,
    FinalizedOverflowHoldV2,
    FinalizeScheduledIntervalV2,
    OverflowHoldPrimitiveV2,
    PreparedFinalizedScheduledIntervalV2,
    PreparedOrdinaryIntervalSeedBatchV2,
    PreparedOverflowPrimitiveFirstPublicationV2,
    PreparedOverflowSeedBatchV2,
    PreparedPrimitiveFirstPublicationV2,
    PreparedScheduledExecutionInitializationV2,
    PreparedScheduledIntervalExecutionOutputsV2,
    PreparedScheduledRunV2,
    PrepareScheduledIntervalExecutionsV2,
    ProposedScheduledMandateBudgetV1,
    ProposedScheduledMandateConsumptionV1,
    ProposedScheduledWholeEnvelopeV2,
    SafetyHoldNoDebitAccountingV2,
    ScheduledExecutionBindingV2,
    ScheduledMandateBudgetV1,
    ScheduledMandateConsumptionBasisV1,
    ScheduledWholeIntervalEnvelopeV2,
    ScheduledWholeIntervalMemberDescriptorV2,
)
from chiplog.capabilities.agent_loop.scheduler_materialization import SchedulerCanonicalMember
from tests.support.scheduler_execution import DIGEST, call_head, ordinary_seed_batch, present


def overflow_seed_batch() -> PreparedOverflowSeedBatchV2:
    ordinary = ordinary_seed_batch()
    source = ordinary.source
    assert isinstance(source, PreparedPrimitiveFirstPublicationV2)
    primitive = OverflowHoldPrimitiveV2(
        command=call_head("overflow-command"),
        boundary=source.primitive_parent.boundary,
        bound_head=source.primitive_parent.current_bound,
        exceeded_dimension="MANIFEST_BYTES",
        actual_value=101,
        limit=100,
        evidence=StreamingEligibilityEvidence(
            manifest_digest=DIGEST,
            member_count=3,
            first_member=Absent(),
            last_member=Absent(),
            order_contract_version="order.v1",
            enumeration_completeness_proof=present("enumeration"),
        ),
    )
    return PreparedOverflowSeedBatchV2(
        configuration_source=ordinary.configuration_source,
        source=PreparedOverflowPrimitiveFirstPublicationV2(
            overflow_primitive=primitive,
            overflow_primitive_reference=present("overflow"),
            canonical_overflow_primitive_bytes=primitive.canonical_bytes(),
        ),
        cut=ordinary.cut,
        selected_mandate=ordinary.selected_mandate,
        selected_budget_predecessor=ordinary.selected_budget_predecessor,
        occurrence_seeds=(),
        skipped_dispositions=(),
    )


def prepared_runs(count: int) -> tuple[PreparedScheduledRunV2, ...]:
    batch = ordinary_seed_batch(count)
    assert isinstance(batch.source, PreparedPrimitiveFirstPublicationV2)
    values: list[PreparedScheduledRunV2] = []
    for seed in batch.occurrence_seeds:
        inputs = seed.primitive.run_inputs
        command = CreateExecutionRun(
            command_id=f"create-{seed.ordinal}",
            tenant=inputs.tenant,
            principal=inputs.principal,
            run_id=seed.initial_run_absence.run_id,
            prompt=inputs.prompt,
            policy=inputs.policy,
            origin=batch.selected_mandate.mandate.scope.origin,
            contour_head=inputs.contour_head,
            policy_head=inputs.policy_head,
            worker_session=inputs.worker_session,
        )
        created = prepare_execution_transition(command)
        assert isinstance(created, ExecutionTransitionProposal)
        run = created.run
        initialization = PreparedScheduledExecutionInitializationV2(
            source_request_fingerprint=DIGEST,
            run=run,
            scheduled_binding=ScheduledExecutionBindingV2(
                service_identity="scheduler-service",
                mandate_head=batch.selected_mandate.mandate_head,
                primitive_parent=batch.source.primitive_parent_reference,
                materialization=seed.materialization,
                original_configuration=batch.configuration_source.configuration,
            ),
            proposal_fingerprint=DIGEST,
        )
        values.append(PreparedScheduledRunV2(command=command, initialization=initialization))
    return tuple(values)


def prepare_request(count: int) -> PrepareScheduledIntervalExecutionsV2:
    batch = ordinary_seed_batch(count)
    runs = prepared_runs(count)
    return PrepareScheduledIntervalExecutionsV2(
        seed_batch=batch, ordered_create_commands=tuple(item.command for item in runs)
    )


def charged_accounting(
    count: int, *, overflow: bool = False
) -> ChargedScheduledIntervalAccountingV2:
    batch = overflow_seed_batch() if overflow else ordinary_seed_batch(count)
    predecessor = batch.selected_budget_predecessor
    basis = ScheduledMandateConsumptionBasisV1(
        mandate_id=predecessor.budget.mandate_id,
        selected_predecessor=predecessor,
        primitive_command=call_head("overflow-primitive" if overflow else "interval-primitive"),
        primitive_fingerprint=DIGEST,
        delta_cycles=1,
        delta_runs=0 if overflow else count,
        delta_consequential_calls=0,
        accounting_policy_version="scheduler-accounting.v1",
    )
    successor_body = ScheduledMandateBudgetV1(
        mandate_id=predecessor.budget.mandate_id,
        generation=predecessor.budget.generation + 1,
        predecessor=predecessor.budget_head,
        cumulative_cycles=predecessor.budget.cumulative_cycles + 1,
        cumulative_runs=predecessor.budget.cumulative_runs + (0 if overflow else count),
        cumulative_consequential_calls=predecessor.budget.cumulative_consequential_calls,
        consumption_basis=basis,
    )
    successor = ProposedScheduledMandateBudgetV1(
        proposed_record_id="proposed-budget",
        canonical_budget_bytes=successor_body.canonical_bytes(),
        budget=successor_body,
    )
    return ChargedScheduledIntervalAccountingV2(
        proposed_consumption=ProposedScheduledMandateConsumptionV1(
            selected_predecessor=predecessor, successor=successor, basis=basis
        )
    )


def finalize_request(
    count: int, *, overflow: bool = False, no_debit: bool = False
) -> FinalizeScheduledIntervalV2:
    batch = overflow_seed_batch() if overflow else ordinary_seed_batch(count)
    accounting: ChargedScheduledIntervalAccountingV2 | SafetyHoldNoDebitAccountingV2
    branch: Literal["ORDINARY_CHARGED", "OVERFLOW_CHARGED", "OVERFLOW_SAFETY_NO_DEBIT"]
    if no_debit:
        accounting = SafetyHoldNoDebitAccountingV2(
            retained_selected_budget_predecessor=batch.selected_budget_predecessor,
            proposed_successor=Absent(),
            delta_cycles=0,
            delta_runs=0,
            delta_consequential_calls=0,
        )
        branch, edges = "OVERFLOW_SAFETY_NO_DEBIT", OVERFLOW_SAFETY_EDGES_V2
    elif overflow:
        accounting = charged_accounting(count, overflow=True)
        branch, edges = "OVERFLOW_CHARGED", OVERFLOW_CHARGED_EDGES_V2
    else:
        accounting = charged_accounting(count)
        branch, edges = "ORDINARY_CHARGED", ORDINARY_CHARGED_EDGES_V2
    return FinalizeScheduledIntervalV2(
        seed_batch=batch,
        ordered_prepared_runs=() if overflow else prepared_runs(count),
        accounting=accounting,
        dependency_manifest=CompleteSchedulerExecutionDependencyManifestV2(
            branch=branch, registered_edges=edges
        ),
    )


def finalized_result(count: int, *, overflow: bool = False) -> PreparedFinalizedScheduledIntervalV2:
    batch = overflow_seed_batch() if overflow else ordinary_seed_batch(count)
    outcome: FinalizedIntervalResultV2 | FinalizedOverflowHoldV2
    occurrences = (
        ()
        if overflow
        else tuple(
            FinalizedOccurrenceCommitmentV2(
                materialization=seed.materialization,
                initialized_run=present(f"initialized-{seed.ordinal}"),
                initialization=present(f"initialization-{seed.ordinal}"),
                reciprocal_run_root=present(f"reciprocal-{seed.ordinal}"),
                companion_manifest=present(f"companion-{seed.ordinal}"),
                finalized_members_fingerprint=DIGEST,
            )
            for seed in batch.occurrence_seeds
        )
    )
    if overflow:
        outcome = FinalizedOverflowHoldV2(
            hold=present("hold"), overflow_primitive=present("overflow")
        )
        envelope_branch: IntervalBranch | Literal["OVERFLOW_HOLD"] = "OVERFLOW_HOLD"
    else:
        assert isinstance(batch, PreparedOrdinaryIntervalSeedBatchV2)
        outcome = FinalizedIntervalResultV2(
            interval_result=present("result"), branch=batch.branch, resulting_boundary="20"
        )
        envelope_branch = batch.branch
    raw = outcome.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    member = SchedulerCanonicalMember(
        record_kind="scheduler.overflow-hold" if overflow else "scheduler.interval-result",
        record_id="owner-result:" + fingerprint,
        schema_id=(
            "chiplog.scheduler.overflow-hold.v2"
            if overflow
            else "chiplog.scheduler.interval-result.v2"
        ),
        canonical_base64=base64.b64encode(raw).decode(),
        fingerprint=fingerprint,
    )
    descriptor = ScheduledWholeIntervalMemberDescriptorV2(
        record_kind=member.record_kind,
        subject_id="command",
        record_id=member.record_id,
        schema_id=member.schema_id,
        fingerprint=member.fingerprint,
    )
    body = ScheduledWholeIntervalEnvelopeV2(
        tenant_id="tenant",
        interval_command=call_head("command"),
        branch=envelope_branch,
        primitive_reference=present("overflow") if overflow else present("parent"),
        selected_configuration=batch.configuration_source.configuration,
        selected_mandate=batch.selected_mandate.mandate_head,
        selected_applicability=call_head("applicability"),
        selected_budget_predecessor=batch.selected_budget_predecessor.budget_head,
        budget_successor=Absent(),
        selected_clock_cut_fingerprint=DIGEST,
        outcome=present("hold") if overflow else present("result"),
        ordered_non_envelope_members=(descriptor,),
        expected_ordinary_seed_count=0 if overflow else count,
        ordered_occurrence_materializations=()
        if overflow
        else tuple(seed.materialization for seed in batch.occurrence_seeds),
        resulting_boundary=None if overflow else "20",
        dependency_manifest_fingerprint=DIGEST,
    )
    envelope_bytes = body.canonical_bytes()
    whole = ProposedScheduledWholeEnvelopeV2(
        proposed_record_id="whole-envelope",
        body=body,
        canonical_envelope_bytes=envelope_bytes,
        external_reference=type(present("whole-envelope"))(
            head="whole-envelope", fingerprint=hashlib.sha256(envelope_bytes).hexdigest()
        ),
    )
    return PreparedFinalizedScheduledIntervalV2(
        source_request_fingerprint=DIGEST,
        seed_kind="OVERFLOW" if overflow else "ORDINARY",
        expected_seed_count=0 if overflow else count,
        finalized_occurrences=occurrences,
        outcome=outcome,
        complete_ordered_canonical_records=(member,),
        complete_ordered_member_subject_ids=("command",),
        complete_ordered_record_bytes=(raw, envelope_bytes),
        whole_envelope=whole,
        proposal_fingerprint=DIGEST,
    )


def finalized_result_with_two_non_envelope_members() -> PreparedFinalizedScheduledIntervalV2:
    """A valid result whose two ordered members make ordering mutations observable."""

    result = finalized_result(1)
    additional_bytes = b"scheduler-finalization-companion-v2"
    additional_member = SchedulerCanonicalMember(
        record_kind="scheduler.finalization-companion",
        record_id="companion:" + hashlib.sha256(additional_bytes).hexdigest(),
        schema_id="chiplog.scheduler.finalization-companion.v2",
        canonical_base64=base64.b64encode(additional_bytes).decode(),
        fingerprint=hashlib.sha256(additional_bytes).hexdigest(),
    )
    additional_descriptor = ScheduledWholeIntervalMemberDescriptorV2(
        record_kind=additional_member.record_kind,
        subject_id="companion",
        record_id=additional_member.record_id,
        schema_id=additional_member.schema_id,
        fingerprint=additional_member.fingerprint,
    )
    body = result.whole_envelope.body.model_copy(
        update={
            "ordered_non_envelope_members": (
                *result.whole_envelope.body.ordered_non_envelope_members,
                additional_descriptor,
            )
        }
    )
    envelope_bytes = body.canonical_bytes()
    whole = ProposedScheduledWholeEnvelopeV2(
        proposed_record_id="whole-envelope",
        body=body,
        canonical_envelope_bytes=envelope_bytes,
        external_reference=type(result.whole_envelope.external_reference)(
            head="whole-envelope", fingerprint=hashlib.sha256(envelope_bytes).hexdigest()
        ),
    )
    return PreparedFinalizedScheduledIntervalV2(
        source_request_fingerprint=result.source_request_fingerprint,
        seed_kind=result.seed_kind,
        expected_seed_count=result.expected_seed_count,
        finalized_occurrences=result.finalized_occurrences,
        outcome=result.outcome,
        complete_ordered_canonical_records=(
            *result.complete_ordered_canonical_records,
            additional_member,
        ),
        complete_ordered_member_subject_ids=(
            *result.complete_ordered_member_subject_ids,
            "companion",
        ),
        complete_ordered_record_bytes=(
            result.complete_ordered_record_bytes[0],
            additional_bytes,
            envelope_bytes,
        ),
        whole_envelope=whole,
        proposal_fingerprint=result.proposal_fingerprint,
    )


def zero_work_finalized_result() -> PreparedFinalizedScheduledIntervalV2:
    """A nonempty finalization output for an ordinary interval with no occurrences."""

    return finalized_result(0)


def streamed_overflow_finalized_result() -> PreparedFinalizedScheduledIntervalV2:
    """A nonempty finalization output for a streaming overflow hold."""

    return finalized_result(0, overflow=True)


def preparation_outputs(count: int) -> PreparedScheduledIntervalExecutionOutputsV2:
    return PreparedScheduledIntervalExecutionOutputsV2(
        source_request_fingerprint=DIGEST,
        ordered_runs=prepared_runs(count),
        proposal_fingerprint=DIGEST,
    )
