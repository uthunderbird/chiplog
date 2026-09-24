"""Public inert builders for scheduler-execution V2 consumer tests."""

from __future__ import annotations

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallAuthorityObservation,
    CallSubjectHead,
)
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, EndpointSelection
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead,
    OriginSelection,
    ProviderRecipient,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    CoalescedSubject,
    ExecutionLineageBinding,
    FirstPublication,
    PhysicalRootBinding,
    PreRootDecisionFence,
    Present,
    TrustedClockProofRef,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    CoalescedDisposition,
    DueCoordinate,
    GenesisLease,
    MaterializationIdentity,
    MissedOccurrencePolicyHead,
    ScheduleDefinitionHead,
    SchedulerCommandIdentity,
    SchedulerEligibilityBoundary,
    SchedulerEligibilityManifest,
    SchedulerIntervalBound,
    SchedulerIntervalBoundHead,
    UndisposedOccurrence,
)
from chiplog.capabilities.agent_loop.scheduler_execution_contracts import (
    InitialRunAbsenceV2,
    IntervalExecutionCutV2,
    PreparedOrdinaryIntervalSeedBatchV2,
    PreparedPrimitiveFirstPublicationV2,
    ScheduledConfigurationSourceV2,
    ScheduledMandateBudgetV1,
    ScheduledMandateConsumptionBasisV1,
    ScheduledOccurrenceSeedV2,
    ScheduledServiceApplicabilityV2,
    ScheduledSystemMandateHorizonV2,
    ScheduledSystemMandateScopeV2,
    ScheduledSystemMandateV2,
    ScheduledToolPermissionV2,
    SelectedScheduledMandateBudgetV1,
    SelectedScheduledSystemMandateV2,
)
from chiplog.capabilities.agent_loop.scheduler_materialization import (
    IntervalParentPrimitive,
    ScheduledBatchPrimitiveDomainV1,
    SchedulerRunInputs,
)

DIGEST = "a" * 64


def present(name: str) -> Present:
    return Present(head=name, fingerprint=DIGEST)


def call_head(name: str) -> CallSubjectHead:
    return CallSubjectHead(subject_id=name, revision=present(name + ":head"))


def origin() -> OriginSelection:
    endpoint = ExactHead(identity="endpoint", head="endpoint/head", fingerprint=DIGEST)
    return OriginSelection(
        ingress_binding=ExactHead(identity="ingress", head="ingress/head", fingerprint=DIGEST),
        recipient=ProviderRecipient(
            provider_id="hermetic-local",
            account_id="account",
            recipient_id="principal",
            endpoint=endpoint,
            canonical_address=b"local://principal",
            credential_binding=ExactHead(
                identity="credential", head="credential/head", fingerprint=DIGEST
            ),
        ),
    )


def endpoint_selection() -> EndpointSelection:
    return EndpointSelection(
        kind="ORIGIN_EXACT",
        ingress_binding_head="ingress",
        endpoint_head="endpoint",
        endpoint_id="endpoint",
        provider="hermetic-local",
        recipient="principal",
        canonical_address="local://principal",
        credential_binding_head="credential",
    )


def clock() -> TrustedClockProofRef:
    return TrustedClockProofRef(
        proof_id="proof",
        proof_fingerprint=DIGEST,
        proof_version="v1",
        clock_contract_version="clock.v1",
        fence_fingerprint=DIGEST,
        command_id="command",
        command_payload_fingerprint=DIGEST,
        submission_id="submission",
    )


def source() -> CallAuthorityObservation:
    return CallAuthorityObservation(
        source_id="source",
        family="MANDATE",
        source=call_head("source"),
        generation="generation",
        frontier="frontier",
        canonical_value_base64="Ynl0ZXM=",
        observed_at_ns=1,
        valid_until_ns=2,
    )


def boundary() -> SchedulerEligibilityBoundary:
    coordinate = DueCoordinate(
        coordinate_policy_version="chiplog.scheduler.unix-ns.v1", canonical_coordinate="10"
    )
    return SchedulerEligibilityBoundary(
        schedule_definition_head=ScheduleDefinitionHead(
            schedule_id="schedule",
            schedule_revision="revision",
            head="schedule/head",
            fingerprint=DIGEST,
        ),
        missed_occurrence_policy_head=MissedOccurrencePolicyHead(
            policy_id="policy",
            policy_revision="revision",
            head="policy/head",
            fingerprint=DIGEST,
            kind="COALESCE",
        ),
        previous_due_boundary=coordinate,
        cutoff_due_coordinate=coordinate,
        enumeration_frontier="frontier",
        predecessor_interval=Absent(),
        canonicalization_version="chiplog.scheduler.canonical.v1",
    )


def bound_head() -> SchedulerIntervalBoundHead:
    return SchedulerIntervalBoundHead(
        head_id="bound",
        predecessor=Absent(),
        generation=0,
        bound=SchedulerIntervalBound(
            max_member_count=10, max_manifest_bytes=100, max_serialized_batch_bytes=1000
        ),
        owner_id="scheduler",
        authority_epoch="epoch",
        broker_generation="broker",
        runtime_graph_generation="runtime",
        canonicalization_version="chiplog.scheduler.canonical.v1",
    )


def subject() -> CoalescedSubject:
    return CoalescedSubject(aggregate_id="aggregate", manifest_fingerprint=DIGEST)


def mandate() -> SelectedScheduledSystemMandateV2:
    scope = ScheduledSystemMandateScopeV2(
        tenant_id="tenant",
        mandate_id="mandate",
        service_identity="scheduler-service",
        beneficiary_principal="principal",
        schedule_head=call_head("schedule"),
        missed_policy_head=call_head("policy"),
        bound_head=call_head("bound"),
        prompt_template_fingerprint=DIGEST,
        run_budget_policy=BudgetPolicy(),
        permitted_tools=(
            ScheduledToolPermissionV2(
                tool_name="propose_intent",
                tool_version="1",
                schema_id="tool.v1",
                consequential=False,
            ),
        ),
        origin=origin(),
        delivery_scope=call_head("delivery"),
        recipient_scope=call_head("recipient"),
        disclosure_scope=call_head("disclosure"),
        operations=("scheduler.execute_interval",),
        issuance_generation=0,
        horizon=ScheduledSystemMandateHorizonV2(
            coordinate_codec="chiplog.scheduler.unix-ns.v1",
            first_eligible_due_coordinate="10",
            last_eligible_due_coordinate="20",
            issuance_clock_proof=clock(),
            expires_at_unix_ns=100,
            max_cycles=3,
            max_runs=3,
            max_consequential_calls=0,
        ),
    )
    body = ScheduledSystemMandateV2(
        scope=scope,
    )
    return SelectedScheduledSystemMandateV2(
        mandate_head=present("mandate/head"),
        canonical_mandate_bytes=body.canonical_bytes(),
        mandate=body,
        issuance_observation=call_head("issuance"),
    )


def budget(
    value: SelectedScheduledSystemMandateV2 | None = None,
) -> SelectedScheduledMandateBudgetV1:
    value = value or mandate()
    basis = ScheduledMandateConsumptionBasisV1(
        mandate_id=value.mandate.scope.mandate_id,
        selected_predecessor=Absent(),
        primitive_command=call_head("primitive"),
        primitive_fingerprint=DIGEST,
        delta_cycles=0,
        delta_runs=0,
        delta_consequential_calls=0,
        accounting_policy_version="scheduler-accounting.v1",
    )
    body = ScheduledMandateBudgetV1(
        mandate_id=value.mandate.scope.mandate_id,
        generation=0,
        predecessor=Absent(),
        cumulative_cycles=0,
        cumulative_runs=0,
        cumulative_consequential_calls=0,
        consumption_basis=basis,
    )
    return SelectedScheduledMandateBudgetV1(
        budget_head=present("budget/head"),
        canonical_budget_bytes=body.canonical_bytes(),
        budget=body,
    )


def parent() -> IntervalParentPrimitive:
    return IntervalParentPrimitive(
        kind="ORDINARY",
        command=SchedulerCommandIdentity(
            command_id="command", schema_version="v1", canonicalization_version="v1"
        ),
        boundary=boundary(),
        current_bound=bound_head(),
        original_bound=bound_head(),
        original_hold=Absent(),
        operator_proof=Absent(),
        manifest=SchedulerEligibilityManifest(members=(), fingerprint=DIGEST),
        branch="BOUNDARY_ONLY_NO_WORK",
        run_inputs=SchedulerRunInputs(
            tenant="tenant",
            principal="principal",
            prompt="prompt",
            policy=BudgetPolicy(),
            origin=endpoint_selection(),
            contour_head="contour",
            policy_head="policy",
            worker_session="worker",
            authority_epoch="epoch",
        ),
    )


def ordinary_seed_batch(count: int = 0) -> PreparedOrdinaryIntervalSeedBatchV2:
    selected_mandate = mandate()
    selected_budget = budget(selected_mandate)
    current_subject = subject()
    absences = tuple(
        InitialRunAbsenceV2(
            ordinal=index,
            run_id=f"run-{index}",
            root_id=f"root-{index}",
            subject=current_subject,
            expected_run=Absent(),
        )
        for index in range(count)
    )
    cut = IntervalExecutionCutV2(
        tenant_id="tenant",
        database_id="database",
        tenant_commit_sequence=1,
        existing_materialization_commitment=DIGEST,
        broker_session_id="broker-session",
        broker_generation="broker",
        runtime_generation="runtime",
        authority_registry=call_head("registry"),
        sources=(source(),),
        pre_root_fence=PreRootDecisionFence(
            command_id="command",
            disposition=FirstPublication(
                decision=Absent(), expected_canonical_absence_manifest="absence"
            ),
            scheduler_authority_head="scheduler",
            broker_generation="broker",
            runtime_generation="runtime",
        ),
        selected_mandate=selected_mandate,
        current_applicability=ScheduledServiceApplicabilityV2(
            service_identity="scheduler-service",
            current_registration=call_head("registration"),
            current_authority=call_head("authority"),
            current_runtime_applicability=call_head("applicability"),
            current_revocation=Absent(),
            sources=(source(),),
        ),
        selected_budget_predecessor=selected_budget,
        complete_pre_root_absence_manifest=present("absence"),
        ordered_initial_run_absences=absences,
    )
    parent_value = parent()
    seeds = tuple(
        ScheduledOccurrenceSeedV2(
            ordinal=index,
            primitive=ScheduledBatchPrimitiveDomainV1(
                parent_decision=present("parent"),
                command=parent_value.command,
                boundary=parent_value.boundary,
                bound_head=parent_value.current_bound,
                manifest=parent_value.manifest,
                subject=current_subject,
                initial_run_id=f"run-{index}",
                root_id=f"root-{index}",
                root_fingerprint=DIGEST,
                run_inputs=parent_value.run_inputs,
            ),
            materialization=MaterializationIdentity(
                materialization_id=f"materialization-{index}", primitive_domain_fingerprint=DIGEST
            ),
            lineage=ExecutionLineageBinding(
                root_id=f"root-{index}",
                subject=current_subject,
                root_fingerprint=DIGEST,
                lineage_head=f"lineage-{index}",
                initial_run_id=f"run-{index}",
                current_run_id=f"run-{index}",
                schedule_id="schedule",
                schedule_revision="revision",
                policy_revision="revision",
            ),
            physical_epoch=present(f"epoch-{index}"),
            physical_selector=PhysicalRootBinding(
                selector_id=f"selector-{index}",
                selector_head=f"selector-head-{index}",
                selector_version=0,
                current_epoch_id=f"epoch-{index}",
                current_epoch_head=f"epoch-head-{index}",
            ),
            genesis_lease=GenesisLease(lease_head=f"lease-{index}"),
            dispositions=(
                CoalescedDisposition(
                    kind="COALESCED_INTO",
                    occurrence=UndisposedOccurrence(
                        occurrence_id=f"occurrence-{index}",
                        schedule_id="schedule",
                        schedule_revision="revision",
                        due_coordinate=boundary().cutoff_due_coordinate,
                        undisposed_head=f"undisposed-{index}",
                        undisposed_fingerprint=DIGEST,
                    ),
                    aggregate_id="aggregate",
                    manifest_fingerprint=DIGEST,
                    materialization=MaterializationIdentity(
                        materialization_id=f"materialization-{index}",
                        primitive_domain_fingerprint=DIGEST,
                    ),
                ),
            ),
            initial_run_absence=absences[index],
        )
        for index in range(count)
    )
    return PreparedOrdinaryIntervalSeedBatchV2(
        configuration_source=ScheduledConfigurationSourceV2(
            configuration=call_head("configuration"),
            configuration_schema="configuration.v1",
            canonical_configuration_bytes=b"configuration",
        ),
        source=PreparedPrimitiveFirstPublicationV2(
            primitive_parent=parent_value,
            primitive_parent_reference=present("parent"),
            canonical_primitive_parent_bytes=parent_value.canonical_bytes(),
        ),
        branch="BOUNDARY_ONLY_NO_WORK" if count == 0 else "COALESCE_SINGLE",
        occurrence_seeds=seeds,
        skipped_dispositions=(),
        cut=cut,
        selected_mandate=selected_mandate,
        selected_budget_predecessor=selected_budget,
    )
