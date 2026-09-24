"""V2 scheduler execution wires; construction never grants publication authority.

These contracts deliberately keep the V1 interval decision and execution-initialization
unions closed.  They describe proposed V2 members only; broker selection, mandate
issuance/revocation, CAS, authentication, and whole-batch publication are separate
Phase-C work.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Annotated, Literal, Protocol

from pydantic import ConfigDict, Field, model_validator

from .call_acceptance_contracts import CallAuthorityObservation, CallSubjectHead
from .contracts import BudgetPolicy
from .delivery_contracts import OriginSelection
from .execution_run_versions import CreateExecutionRunVersion, ExecutionRun
from .recovery_contracts import (
    Absent,
    Digest,
    ExecutionLineageBinding,
    ExecutionLineageSubject,
    Identity,
    PhysicalRootBinding,
    PreRootDecisionFence,
    Present,
    RecoveryDTO,
    TrustedClockProofRef,
    UInt64,
)
from .scheduler_contracts import (
    FullEligibilityEvidence,
    GenesisLease,
    IntervalBranch,
    MaterializationIdentity,
    OccurrenceDisposition,
    SchedulerEligibilityBoundary,
    SchedulerIntervalBoundHead,
    StreamingEligibilityEvidence,
)
from .scheduler_materialization import (
    IntervalParentPrimitive,
    ScheduledBatchPrimitiveDomainV1,
    SchedulerCanonicalMember,
)


class SchedulerExecutionDTO(RecoveryDTO):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, ser_json_bytes="base64", val_json_bytes="base64"
    )


class ScheduledSystemMandateHorizonV2(SchedulerExecutionDTO):
    coordinate_codec: Literal["chiplog.scheduler.unix-ns.v1"]
    inclusion: Literal["CLOSED_INCLUSIVE"] = "CLOSED_INCLUSIVE"
    first_eligible_due_coordinate: Identity
    last_eligible_due_coordinate: Identity
    issuance_clock_proof: TrustedClockProofRef
    expires_at_unix_ns: UInt64
    max_cycles: UInt64
    max_runs: UInt64
    max_consequential_calls: UInt64


class ScheduledToolPermissionV2(SchedulerExecutionDTO):
    tool_name: Identity
    tool_version: Identity
    schema_id: Identity
    consequential: bool


class ScheduledSystemMandateScopeV2(SchedulerExecutionDTO):
    tenant_id: Identity
    mandate_id: Identity
    service_identity: Identity
    beneficiary_principal: Identity
    schedule_head: CallSubjectHead
    missed_policy_head: CallSubjectHead
    bound_head: CallSubjectHead
    prompt_template_fingerprint: Digest
    run_budget_policy: BudgetPolicy
    permitted_tools: tuple[ScheduledToolPermissionV2, ...]
    origin: OriginSelection
    delivery_scope: CallSubjectHead
    recipient_scope: CallSubjectHead
    disclosure_scope: CallSubjectHead
    operations: tuple[
        Literal["scheduler.execute_interval", "INITIALIZED_CONSEQUENTIAL_CALL"], ...
    ] = Field(min_length=1)
    issuance_generation: UInt64
    horizon: ScheduledSystemMandateHorizonV2


class ScheduledSystemMandateV2(SchedulerExecutionDTO):
    kind: Literal["SCHEDULED_SYSTEM_MANDATE_V2"] = "SCHEDULED_SYSTEM_MANDATE_V2"
    scope: ScheduledSystemMandateScopeV2


class SelectedScheduledSystemMandateV2(SchedulerExecutionDTO):
    """Exact selected mandate record, kept outside its canonical body."""

    mandate_head: Present
    schema_id: Literal["chiplog.scheduler.system-mandate.v2"] = (
        "chiplog.scheduler.system-mandate.v2"
    )
    canonical_mandate_bytes: bytes = Field(min_length=1)
    mandate: ScheduledSystemMandateV2
    issuance_observation: CallSubjectHead


def mandate_is_fresh_at(horizon: ScheduledSystemMandateHorizonV2, now_unix_ns: UInt64) -> bool:
    """Pure exclusive expiry comparison; caller-provided time remains unauthenticated."""

    return now_unix_ns < horizon.expires_at_unix_ns


class ScheduledMandateRevocationV1(SchedulerExecutionDTO):
    kind: Literal["SCHEDULED_MANDATE_REVOCATION_V1"] = "SCHEDULED_MANDATE_REVOCATION_V1"
    mandate_id: Identity
    mandate_head: Present
    predecessor: Annotated[Absent | Present, Field(discriminator="kind")]
    revocation_observation: CallSubjectHead


class ScheduledMandateConsumptionBasisV1(SchedulerExecutionDTO):
    mandate_id: Identity
    selected_predecessor: Annotated[
        Absent | SelectedScheduledMandateBudgetV1, Field(discriminator="kind")
    ]
    primitive_command: CallSubjectHead
    primitive_fingerprint: Digest
    delta_cycles: UInt64
    delta_runs: UInt64
    delta_consequential_calls: UInt64
    accounting_policy_version: Identity


class ScheduledMandateBudgetV1(SchedulerExecutionDTO):
    kind: Literal["SCHEDULED_MANDATE_BUDGET_V1"] = "SCHEDULED_MANDATE_BUDGET_V1"
    mandate_id: Identity
    generation: UInt64
    predecessor: Annotated[Absent | Present, Field(discriminator="kind")]
    cumulative_cycles: UInt64
    cumulative_runs: UInt64
    cumulative_consequential_calls: UInt64
    consumption_basis: ScheduledMandateConsumptionBasisV1


class SelectedScheduledMandateBudgetV1(SchedulerExecutionDTO):
    """Exact selected budget record; the body never commits to its own head."""

    kind: Literal["SELECTED_SCHEDULED_MANDATE_BUDGET_V1"] = "SELECTED_SCHEDULED_MANDATE_BUDGET_V1"
    budget_head: Present
    schema_id: Literal["chiplog.scheduler.mandate-budget.v1"] = (
        "chiplog.scheduler.mandate-budget.v1"
    )
    canonical_budget_bytes: bytes = Field(min_length=1)
    budget: ScheduledMandateBudgetV1


class ProposedScheduledMandateBudgetV1(SchedulerExecutionDTO):
    """Proposed body/reference; it has not been selected as a new CAS head."""

    proposed_record_id: Identity
    canonical_budget_bytes: bytes = Field(min_length=1)
    budget: ScheduledMandateBudgetV1


class ProposedScheduledMandateConsumptionV1(SchedulerExecutionDTO):
    kind: Literal["PROPOSED_SCHEDULED_MANDATE_CONSUMPTION_V1"] = (
        "PROPOSED_SCHEDULED_MANDATE_CONSUMPTION_V1"
    )
    selected_predecessor: SelectedScheduledMandateBudgetV1
    successor: ProposedScheduledMandateBudgetV1
    basis: ScheduledMandateConsumptionBasisV1

    @model_validator(mode="after")
    def binds_one_selected_budget_lineage(self) -> ProposedScheduledMandateConsumptionV1:
        mandate_id = self.selected_predecessor.budget.mandate_id
        if mandate_id != self.successor.budget.mandate_id or mandate_id != self.basis.mandate_id:
            raise ValueError("consumption proposal must retain one mandate lineage")
        if self.basis.selected_predecessor != self.selected_predecessor:
            raise ValueError("consumption basis must retain the exact selected predecessor")
        return self


class ScheduledServiceApplicabilityV2(SchedulerExecutionDTO):
    service_identity: Identity
    current_registration: CallSubjectHead
    current_authority: CallSubjectHead
    current_runtime_applicability: CallSubjectHead
    current_revocation: Annotated[Absent | Present, Field(discriminator="kind")]
    sources: tuple[CallAuthorityObservation, ...] = Field(min_length=1)


class InitialRunAbsenceV2(SchedulerExecutionDTO):
    ordinal: UInt64
    run_id: Identity
    root_id: Identity
    subject: ExecutionLineageSubject
    expected_run: Absent


class IntervalExecutionCutV2(SchedulerExecutionDTO):
    tenant_id: Identity
    database_id: Identity
    tenant_commit_sequence: UInt64
    existing_materialization_commitment: Digest
    broker_session_id: Identity
    broker_generation: Identity
    runtime_generation: Identity
    authority_registry: CallSubjectHead
    sources: tuple[CallAuthorityObservation, ...] = Field(min_length=1)
    pre_root_fence: PreRootDecisionFence
    selected_mandate: SelectedScheduledSystemMandateV2
    current_applicability: ScheduledServiceApplicabilityV2
    selected_budget_predecessor: SelectedScheduledMandateBudgetV1
    complete_pre_root_absence_manifest: Present
    ordered_initial_run_absences: tuple[InitialRunAbsenceV2, ...]


class ScheduledConfigurationSourceV2(SchedulerExecutionDTO):
    configuration: CallSubjectHead
    configuration_schema: Identity
    canonical_configuration_bytes: bytes = Field(min_length=1)


class PreparedPrimitiveFirstPublicationV2(SchedulerExecutionDTO):
    kind: Literal["PREPARED_PRIMITIVE_FIRST_PUBLICATION"] = "PREPARED_PRIMITIVE_FIRST_PUBLICATION"
    primitive_parent: IntervalParentPrimitive
    primitive_parent_reference: Present
    canonical_primitive_parent_bytes: bytes = Field(min_length=1)


class SelectedExactDecisionV2(SchedulerExecutionDTO):
    kind: Literal["SELECTED_EXACT_DECISION"] = "SELECTED_EXACT_DECISION"
    selected_decision: Present
    canonical_selected_decision_bytes: bytes = Field(min_length=1)
    canonical_selected_result_bytes: bytes = Field(min_length=1)
    exact_prefix_materialization: Present
    selected_debit: Annotated[Absent | Present, Field(discriminator="kind")]
    canonical_selected_debit_bytes: Annotated[bytes, Field(min_length=1)] | None = None

    @model_validator(mode="after")
    def selected_debit_bytes_match_marker(self) -> SelectedExactDecisionV2:
        if isinstance(self.selected_debit, Present) != (
            self.canonical_selected_debit_bytes is not None
        ):
            raise ValueError("selected debit marker and retained bytes differ")
        return self


IntervalExecutionSourceV2 = Annotated[
    PreparedPrimitiveFirstPublicationV2 | SelectedExactDecisionV2, Field(discriminator="kind")
]


class ScheduledOccurrenceSeedV2(SchedulerExecutionDTO):
    ordinal: UInt64
    primitive: ScheduledBatchPrimitiveDomainV1
    materialization: MaterializationIdentity
    lineage: ExecutionLineageBinding
    physical_epoch: Present
    physical_selector: PhysicalRootBinding
    genesis_lease: GenesisLease
    dispositions: tuple[OccurrenceDisposition, ...] = Field(min_length=1)
    initial_run_absence: InitialRunAbsenceV2


class PreparedOrdinaryIntervalSeedBatchV2(SchedulerExecutionDTO):
    kind: Literal["PREPARED_ORDINARY_INTERVAL_SEED_BATCH_V2"] = (
        "PREPARED_ORDINARY_INTERVAL_SEED_BATCH_V2"
    )
    configuration_source: ScheduledConfigurationSourceV2
    source: IntervalExecutionSourceV2
    branch: IntervalBranch
    occurrence_seeds: tuple[ScheduledOccurrenceSeedV2, ...]
    skipped_dispositions: tuple[OccurrenceDisposition, ...]
    cut: IntervalExecutionCutV2
    selected_mandate: SelectedScheduledSystemMandateV2
    selected_budget_predecessor: SelectedScheduledMandateBudgetV1

    @model_validator(mode="after")
    def seed_absences_match_common_cut(self) -> PreparedOrdinaryIntervalSeedBatchV2:
        if str(self.branch) == "OVERFLOW_HOLD":
            raise ValueError("ordinary seed batch cannot represent an overflow hold")
        if self.selected_mandate != self.cut.selected_mandate:
            raise ValueError("seed batch mandate differs from common cut")
        if self.selected_budget_predecessor != self.cut.selected_budget_predecessor:
            raise ValueError("seed batch budget predecessor differs from common cut")
        if tuple(seed.initial_run_absence for seed in self.occurrence_seeds) != (
            self.cut.ordered_initial_run_absences
        ):
            raise ValueError("occurrence seeds must enumerate the exact common-cut absences")
        return self


class OverflowHoldPrimitiveV2(SchedulerExecutionDTO):
    command: CallSubjectHead
    boundary: SchedulerEligibilityBoundary
    bound_head: SchedulerIntervalBoundHead
    exceeded_dimension: Literal["MEMBER_COUNT", "MANIFEST_BYTES", "SERIALIZED_BATCH_BYTES"]
    actual_value: UInt64
    limit: UInt64
    evidence: Annotated[
        FullEligibilityEvidence | StreamingEligibilityEvidence, Field(discriminator="kind")
    ]


class PreparedOverflowPrimitiveFirstPublicationV2(SchedulerExecutionDTO):
    """Overflow first publication has no ordinary interval-parent preimage."""

    kind: Literal["PREPARED_OVERFLOW_PRIMITIVE_FIRST_PUBLICATION"] = (
        "PREPARED_OVERFLOW_PRIMITIVE_FIRST_PUBLICATION"
    )
    overflow_primitive: OverflowHoldPrimitiveV2
    overflow_primitive_reference: Present
    canonical_overflow_primitive_bytes: bytes = Field(min_length=1)


class SelectedExactOverflowDecisionV2(SchedulerExecutionDTO):
    kind: Literal["SELECTED_EXACT_OVERFLOW_DECISION"] = "SELECTED_EXACT_OVERFLOW_DECISION"
    selected_decision: Present
    canonical_selected_decision_bytes: bytes = Field(min_length=1)
    canonical_selected_result_bytes: bytes = Field(min_length=1)
    exact_prefix_materialization: Present
    selected_debit: Annotated[Absent | Present, Field(discriminator="kind")]
    canonical_selected_debit_bytes: Annotated[bytes, Field(min_length=1)] | None = None

    @model_validator(mode="after")
    def selected_debit_bytes_match_marker(self) -> SelectedExactOverflowDecisionV2:
        if isinstance(self.selected_debit, Present) != (
            self.canonical_selected_debit_bytes is not None
        ):
            raise ValueError("selected debit marker and retained bytes differ")
        return self


OverflowExecutionSourceV2 = Annotated[
    PreparedOverflowPrimitiveFirstPublicationV2 | SelectedExactOverflowDecisionV2,
    Field(discriminator="kind"),
]


class PreparedOverflowSeedBatchV2(SchedulerExecutionDTO):
    kind: Literal["PREPARED_OVERFLOW_SEED_BATCH_V2"] = "PREPARED_OVERFLOW_SEED_BATCH_V2"
    configuration_source: ScheduledConfigurationSourceV2
    source: OverflowExecutionSourceV2
    cut: IntervalExecutionCutV2
    selected_mandate: SelectedScheduledSystemMandateV2
    selected_budget_predecessor: SelectedScheduledMandateBudgetV1
    occurrence_seeds: tuple[()]
    skipped_dispositions: tuple[()]

    @model_validator(mode="after")
    def overflow_uses_its_exact_empty_common_cut(self) -> PreparedOverflowSeedBatchV2:
        if self.cut.ordered_initial_run_absences != ():
            raise ValueError("overflow seed batch requires an empty initial-Run absence manifest")
        if self.selected_mandate != self.cut.selected_mandate:
            raise ValueError("overflow seed batch mandate differs from common cut")
        if self.selected_budget_predecessor != self.cut.selected_budget_predecessor:
            raise ValueError("overflow seed batch budget predecessor differs from common cut")
        return self


PreparedIntervalSeedBatchV2 = Annotated[
    PreparedOrdinaryIntervalSeedBatchV2 | PreparedOverflowSeedBatchV2, Field(discriminator="kind")
]


class PrepareScheduledIntervalExecutionsV2(SchedulerExecutionDTO):
    kind: Literal["PREPARE_SCHEDULED_INTERVAL_EXECUTIONS_V2"] = (
        "PREPARE_SCHEDULED_INTERVAL_EXECUTIONS_V2"
    )
    seed_batch: PreparedOrdinaryIntervalSeedBatchV2
    ordered_create_commands: tuple[CreateExecutionRunVersion, ...]

    @model_validator(mode="after")
    def commands_match_seed_absences(self) -> PrepareScheduledIntervalExecutionsV2:
        if tuple(command.run_id for command in self.ordered_create_commands) != tuple(
            seed.initial_run_absence.run_id for seed in self.seed_batch.occurrence_seeds
        ):
            raise ValueError("create commands must match ordered scheduled seed runs")
        return self


class ScheduledExecutionBindingV2(SchedulerExecutionDTO):
    kind: Literal["SCHEDULER_MATERIALIZATION_V2"] = "SCHEDULER_MATERIALIZATION_V2"
    service_identity: Identity
    mandate_head: Present
    primitive_parent: Present
    materialization: MaterializationIdentity
    original_configuration: CallSubjectHead


class PreparedScheduledExecutionInitializationV2(SchedulerExecutionDTO):
    kind: Literal["PREPARED_SCHEDULED_EXECUTION_INITIALIZATION_V2"] = (
        "PREPARED_SCHEDULED_EXECUTION_INITIALIZATION_V2"
    )
    source_request_fingerprint: Digest
    run: ExecutionRun
    scheduled_binding: ScheduledExecutionBindingV2
    proposal_fingerprint: Digest


class PreparedScheduledRunV2(SchedulerExecutionDTO):
    command: CreateExecutionRunVersion
    initialization: PreparedScheduledExecutionInitializationV2

    @model_validator(mode="after")
    def command_matches_run(self) -> PreparedScheduledRunV2:
        if self.command.run_id != self.initialization.run.run_id:
            raise ValueError("prepared Run does not match create command")
        expected_schema = {
            "CREATE_EXECUTION_RUN_V2": "chiplog.agent-loop.execution-record.v2",
            "CREATE_EXECUTION_RUN_V3": "chiplog.agent-loop.execution-record.v3",
        }[self.command.kind]
        if self.initialization.run.schema_id != expected_schema:
            raise ValueError("prepared Run command and Run version differ")
        return self


class PreparedScheduledIntervalExecutionOutputsV2(SchedulerExecutionDTO):
    kind: Literal["PREPARED_SCHEDULED_INTERVAL_EXECUTION_OUTPUTS_V2"] = (
        "PREPARED_SCHEDULED_INTERVAL_EXECUTION_OUTPUTS_V2"
    )
    source_request_fingerprint: Digest
    ordered_runs: tuple[PreparedScheduledRunV2, ...]
    proposal_fingerprint: Digest


class ChargedScheduledIntervalAccountingV2(SchedulerExecutionDTO):
    kind: Literal["CHARGED"] = "CHARGED"
    proposed_consumption: ProposedScheduledMandateConsumptionV1


class SafetyHoldNoDebitAccountingV2(SchedulerExecutionDTO):
    kind: Literal["SAFETY_HOLD_NO_DEBIT"] = "SAFETY_HOLD_NO_DEBIT"
    retained_selected_budget_predecessor: SelectedScheduledMandateBudgetV1
    proposed_successor: Absent
    delta_cycles: int = Field(ge=0, le=0)
    delta_runs: int = Field(ge=0, le=0)
    delta_consequential_calls: int = Field(ge=0, le=0)

    @model_validator(mode="after")
    def deltas_are_actual_zero_integers(self) -> SafetyHoldNoDebitAccountingV2:
        if any(type(value) is not int for value in self.deltas):
            raise ValueError("safety-hold deltas must be integer zeroes")
        return self

    @property
    def deltas(self) -> tuple[int, int, int]:
        return (self.delta_cycles, self.delta_runs, self.delta_consequential_calls)


ScheduledIntervalAccountingV2 = Annotated[
    ChargedScheduledIntervalAccountingV2 | SafetyHoldNoDebitAccountingV2,
    Field(discriminator="kind"),
]


class FinalizedOccurrenceCommitmentV2(SchedulerExecutionDTO):
    materialization: MaterializationIdentity
    initialized_run: Present
    initialization: Present
    reciprocal_run_root: Present
    companion_manifest: Present
    finalized_members_fingerprint: Digest


class FinalizedIntervalResultV2(SchedulerExecutionDTO):
    kind: Literal["FINALIZED_INTERVAL_RESULT_V2"] = "FINALIZED_INTERVAL_RESULT_V2"
    interval_result: Present
    branch: IntervalBranch
    resulting_boundary: Identity


class FinalizedOverflowHoldV2(SchedulerExecutionDTO):
    kind: Literal["FINALIZED_OVERFLOW_HOLD_V2"] = "FINALIZED_OVERFLOW_HOLD_V2"
    hold: Present
    overflow_primitive: Present


FinalizedScheduledIntervalOutcomeV2 = Annotated[
    FinalizedIntervalResultV2 | FinalizedOverflowHoldV2, Field(discriminator="kind")
]


class ScheduledWholeIntervalMemberDescriptorV2(SchedulerExecutionDTO):
    owner: Literal["agent_loop"] = "agent_loop"
    record_kind: Identity
    subject_id: Identity
    record_id: Identity
    schema_id: Identity
    fingerprint: Digest


class ScheduledWholeIntervalEnvelopeV2(SchedulerExecutionDTO):
    """Canonical WHOLE body; it excludes its own physical record descriptor."""

    schema_id: Literal["chiplog.scheduler.whole-envelope.v2"] = (
        "chiplog.scheduler.whole-envelope.v2"
    )
    domain_version: Literal["scheduler-whole-envelope-v2"] = "scheduler-whole-envelope-v2"
    tenant_id: Identity
    interval_command: CallSubjectHead
    branch: IntervalBranch | Literal["OVERFLOW_HOLD"]
    primitive_reference: Present
    selected_configuration: CallSubjectHead
    selected_mandate: Present
    selected_applicability: CallSubjectHead
    selected_budget_predecessor: Present
    budget_successor: Annotated[Absent | Present, Field(discriminator="kind")]
    selected_clock_cut_fingerprint: Digest
    outcome: Present
    ordered_non_envelope_members: tuple[ScheduledWholeIntervalMemberDescriptorV2, ...] = Field(
        min_length=1
    )
    expected_ordinary_seed_count: UInt64
    ordered_occurrence_materializations: tuple[MaterializationIdentity, ...]
    resulting_boundary: Identity | None
    dependency_manifest_fingerprint: Digest

    @model_validator(mode="after")
    def body_has_no_duplicate_or_self_descriptor(self) -> ScheduledWholeIntervalEnvelopeV2:
        ids = tuple(item.record_id for item in self.ordered_non_envelope_members)
        if len(ids) != len(set(ids)) or any(
            item.schema_id == self.schema_id for item in self.ordered_non_envelope_members
        ):
            raise ValueError("WHOLE body cannot contain duplicate or self-envelope descriptors")
        if self.branch == "OVERFLOW_HOLD" and (
            self.expected_ordinary_seed_count != 0
            or self.ordered_occurrence_materializations != ()
            or self.resulting_boundary is not None
        ):
            raise ValueError("overflow WHOLE body has no ordinary members or resulting boundary")
        return self


class ProposedScheduledWholeEnvelopeV2(SchedulerExecutionDTO):
    proposed_record_id: Identity
    body: ScheduledWholeIntervalEnvelopeV2
    canonical_envelope_bytes: bytes = Field(min_length=1)
    external_reference: Present

    @model_validator(mode="after")
    def bytes_are_exact_body(self) -> ProposedScheduledWholeEnvelopeV2:
        if self.canonical_envelope_bytes != self.body.canonical_bytes():
            raise ValueError("proposed WHOLE bytes differ from canonical body")
        if (
            hashlib.sha256(self.canonical_envelope_bytes).hexdigest()
            != self.external_reference.fingerprint
        ):
            raise ValueError("proposed WHOLE reference fingerprint differs from canonical bytes")
        if self.proposed_record_id != self.external_reference.head:
            raise ValueError("proposed WHOLE record ID differs from external reference")
        return self


class SelectedScheduledWholeEnvelopeV2(SchedulerExecutionDTO):
    selected_reference: Present
    schema_id: Literal["chiplog.scheduler.whole-envelope.v2"] = (
        "chiplog.scheduler.whole-envelope.v2"
    )
    canonical_envelope_bytes: bytes = Field(min_length=1)
    body: ScheduledWholeIntervalEnvelopeV2

    @model_validator(mode="after")
    def selected_bytes_are_exact_body(self) -> SelectedScheduledWholeEnvelopeV2:
        if self.canonical_envelope_bytes != self.body.canonical_bytes():
            raise ValueError("selected WHOLE bytes differ from canonical body")
        if (
            hashlib.sha256(self.canonical_envelope_bytes).hexdigest()
            != self.selected_reference.fingerprint
        ):
            raise ValueError("selected WHOLE reference fingerprint differs from canonical bytes")
        return self


ORDINARY_CHARGED_EDGES_V2: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("selected_configuration", ()),
    ("selected_mandate", ()),
    ("current_applicability", ("selected_mandate",)),
    ("selected_budget", ("selected_mandate",)),
    ("trusted_clock", ()),
    (
        "interval_cut",
        ("selected_configuration", "current_applicability", "selected_budget", "trusted_clock"),
    ),
    ("primitive", ("interval_cut",)),
    ("primitive_identity", ("primitive",)),
    ("occurrence_primitive", ("primitive_identity",)),
    ("stable_ids", ("occurrence_primitive",)),
    ("genesis_lineage_epoch_selector", ("stable_ids",)),
    ("scheduled_binding_and_run", ("genesis_lineage_epoch_selector",)),
    ("initialization", ("scheduled_binding_and_run",)),
    ("reciprocal_and_companion", ("initialization",)),
    ("finalized_occurrences", ("reciprocal_and_companion",)),
    ("budget_basis", ("selected_budget", "primitive_identity")),
    ("budget_successor", ("budget_basis",)),
    ("result", ("finalized_occurrences", "budget_successor")),
    ("whole_envelope", ("result", "budget_successor")),
)
OVERFLOW_CHARGED_EDGES_V2: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("selected_configuration", ()),
    ("selected_mandate", ()),
    ("current_applicability", ("selected_mandate",)),
    ("selected_budget", ("selected_mandate",)),
    ("trusted_clock", ()),
    (
        "interval_cut",
        ("selected_configuration", "current_applicability", "selected_budget", "trusted_clock"),
    ),
    ("primitive", ("interval_cut",)),
    ("primitive_identity", ("primitive",)),
    ("budget_basis", ("selected_budget", "primitive_identity")),
    ("budget_successor", ("budget_basis",)),
    ("result", ("primitive_identity", "budget_successor")),
    ("whole_envelope", ("result", "budget_successor")),
)
OVERFLOW_SAFETY_EDGES_V2: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("selected_configuration", ()),
    ("selected_mandate", ()),
    ("current_applicability", ("selected_mandate",)),
    ("selected_budget", ("selected_mandate",)),
    ("trusted_clock", ()),
    (
        "interval_cut",
        ("selected_configuration", "current_applicability", "selected_budget", "trusted_clock"),
    ),
    ("primitive", ("interval_cut",)),
    ("primitive_identity", ("primitive",)),
    ("budget_no_debit", ("selected_budget", "primitive_identity")),
    ("result", ("primitive_identity", "budget_no_debit")),
    ("whole_envelope", ("result", "budget_no_debit")),
)


class CompleteSchedulerExecutionDependencyManifestV2(SchedulerExecutionDTO):
    kind: Literal["SCHEDULER_EXECUTION_DEPENDENCY_MANIFEST_V2"] = (
        "SCHEDULER_EXECUTION_DEPENDENCY_MANIFEST_V2"
    )
    branch: Literal["ORDINARY_CHARGED", "OVERFLOW_CHARGED", "OVERFLOW_SAFETY_NO_DEBIT"]
    registered_edges: tuple[tuple[Identity, tuple[Identity, ...]], ...]

    @model_validator(mode="after")
    def requires_exact_registered_edges(self) -> CompleteSchedulerExecutionDependencyManifestV2:
        expected = {
            "ORDINARY_CHARGED": ORDINARY_CHARGED_EDGES_V2,
            "OVERFLOW_CHARGED": OVERFLOW_CHARGED_EDGES_V2,
            "OVERFLOW_SAFETY_NO_DEBIT": OVERFLOW_SAFETY_EDGES_V2,
        }[self.branch]
        if self.registered_edges != expected:
            raise ValueError("dependency manifest must use its exact registered edge set")
        return self


class FinalizeScheduledIntervalV2(SchedulerExecutionDTO):
    kind: Literal["FINALIZE_SCHEDULED_INTERVAL_V2"] = "FINALIZE_SCHEDULED_INTERVAL_V2"
    seed_batch: PreparedIntervalSeedBatchV2
    ordered_prepared_runs: tuple[PreparedScheduledRunV2, ...]
    accounting: ScheduledIntervalAccountingV2
    dependency_manifest: CompleteSchedulerExecutionDependencyManifestV2

    @model_validator(mode="after")
    def request_shape_matches_exact_seed_batch(self) -> FinalizeScheduledIntervalV2:
        ordinary = isinstance(self.seed_batch, PreparedOrdinaryIntervalSeedBatchV2)
        expected_runs = tuple(
            seed.initial_run_absence.run_id for seed in self.seed_batch.occurrence_seeds
        )
        if tuple(item.command.run_id for item in self.ordered_prepared_runs) != expected_runs:
            raise ValueError("finalize Run outputs must match exact ordered seed Run IDs")
        if ordinary:
            if not isinstance(self.accounting, ChargedScheduledIntervalAccountingV2):
                raise ValueError("ordinary seed batch requires charged accounting")
            if self.dependency_manifest.branch != "ORDINARY_CHARGED":
                raise ValueError(
                    "ordinary seed batch requires ordinary charged dependency manifest"
                )
        else:
            expected_branch = (
                "OVERFLOW_CHARGED"
                if isinstance(self.accounting, ChargedScheduledIntervalAccountingV2)
                else "OVERFLOW_SAFETY_NO_DEBIT"
            )
            if self.dependency_manifest.branch != expected_branch:
                raise ValueError("overflow seed batch accounting and dependency branch differ")
            if isinstance(self.accounting, SafetyHoldNoDebitAccountingV2) and (
                self.accounting.retained_selected_budget_predecessor
                != self.seed_batch.selected_budget_predecessor
            ):
                raise ValueError("safety hold must retain the selected budget predecessor")
        if isinstance(self.accounting, ChargedScheduledIntervalAccountingV2):
            proposal = self.accounting.proposed_consumption
            expected_delta = (1, len(expected_runs) if ordinary else 0, 0)
            actual_delta = (
                proposal.basis.delta_cycles,
                proposal.basis.delta_runs,
                proposal.basis.delta_consequential_calls,
            )
            if actual_delta != expected_delta:
                raise ValueError("charged accounting must use exact (1, N, 0) seed deltas")
            if proposal.selected_predecessor != self.seed_batch.selected_budget_predecessor:
                raise ValueError("charged accounting must use the seed selected budget predecessor")
        return self


class PreparedFinalizedScheduledIntervalV2(SchedulerExecutionDTO):
    kind: Literal["PREPARED_FINALIZED_SCHEDULED_INTERVAL_V2"] = (
        "PREPARED_FINALIZED_SCHEDULED_INTERVAL_V2"
    )
    source_request_fingerprint: Digest
    seed_kind: Literal["ORDINARY", "OVERFLOW"]
    expected_seed_count: UInt64
    finalized_occurrences: tuple[FinalizedOccurrenceCommitmentV2, ...]
    outcome: FinalizedScheduledIntervalOutcomeV2
    complete_ordered_canonical_records: tuple[SchedulerCanonicalMember, ...] = Field(min_length=1)
    complete_ordered_member_subject_ids: tuple[Identity, ...]
    complete_ordered_record_bytes: tuple[bytes, ...] = Field(min_length=1)
    whole_envelope: ProposedScheduledWholeEnvelopeV2
    proposal_fingerprint: Digest

    @model_validator(mode="after")
    def finalization_shape_is_closed(self) -> PreparedFinalizedScheduledIntervalV2:
        if len(self.finalized_occurrences) != self.expected_seed_count:
            raise ValueError("finalization outputs must match exact seed cardinality")
        if self.seed_kind == "ORDINARY":
            if not isinstance(self.outcome, FinalizedIntervalResultV2):
                raise ValueError("ordinary seed batch requires an interval result")
        else:
            if not isinstance(self.outcome, FinalizedOverflowHoldV2):
                raise ValueError("overflow seed batch requires an overflow hold")
            if self.expected_seed_count != 0 or self.finalized_occurrences != ():
                raise ValueError("overflow finalization must have zero seed outputs")
        if len(self.complete_ordered_member_subject_ids) != len(
            self.complete_ordered_canonical_records
        ):
            raise ValueError("WHOLE output subjects must match non-envelope membership")
        if (
            len(self.complete_ordered_record_bytes)
            != len(self.complete_ordered_canonical_records) + 1
        ):
            raise ValueError("WHOLE output bytes must match complete descriptor membership")
        descriptors = self.whole_envelope.body.ordered_non_envelope_members
        if len(descriptors) != len(self.complete_ordered_canonical_records):
            raise ValueError("WHOLE body descriptors must match non-envelope membership")
        record_ids = tuple(item.record_id for item in self.complete_ordered_canonical_records)
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("WHOLE complete membership cannot contain duplicate records")
        for member, subject_id, descriptor, raw in zip(
            self.complete_ordered_canonical_records,
            self.complete_ordered_member_subject_ids,
            descriptors,
            self.complete_ordered_record_bytes[:-1],
            strict=True,
        ):
            if base64.b64decode(member.canonical_base64, validate=True) != raw:
                raise ValueError("WHOLE member bytes differ from canonical descriptor bytes")
            if hashlib.sha256(raw).hexdigest() != member.fingerprint:
                raise ValueError("WHOLE member fingerprint differs from canonical bytes")
            if (
                descriptor.owner != "agent_loop"
                or descriptor.subject_id != subject_id
                or descriptor.record_kind != member.record_kind
                or descriptor.record_id != member.record_id
                or descriptor.schema_id != member.schema_id
                or descriptor.fingerprint != member.fingerprint
            ):
                raise ValueError("WHOLE body descriptor differs from complete member")
        if self.complete_ordered_record_bytes[-1] != self.whole_envelope.canonical_envelope_bytes:
            raise ValueError("WHOLE envelope bytes must be the final batch member")
        body = self.whole_envelope.body
        if body.expected_ordinary_seed_count != self.expected_seed_count:
            raise ValueError("WHOLE seed count differs from finalized result")
        if (
            tuple(item.materialization for item in self.finalized_occurrences)
            != body.ordered_occurrence_materializations
        ):
            raise ValueError("WHOLE materializations differ from finalized occurrences")
        if isinstance(self.outcome, FinalizedIntervalResultV2):
            if (
                body.branch != self.outcome.branch
                or body.outcome != self.outcome.interval_result
                or body.resulting_boundary != self.outcome.resulting_boundary
            ):
                raise ValueError("WHOLE branch or outcome differs from finalized interval result")
        elif (
            body.branch != "OVERFLOW_HOLD"
            or body.outcome != self.outcome.hold
            or body.primitive_reference != self.outcome.overflow_primitive
        ):
            raise ValueError("WHOLE branch or outcome differs from finalized overflow hold")
        return self


def _validate_selected_replay_members(
    records: tuple[SchedulerCanonicalMember, ...],
    subject_ids: tuple[Identity, ...],
    selected_whole: SelectedScheduledWholeEnvelopeV2,
    *,
    expected_branch: IntervalBranch | Literal["OVERFLOW_HOLD"],
) -> None:
    """Check retained physical members against a fixed selected WHOLE body.

    This establishes byte and descriptor coherence only.  Selection remains a
    responsibility of the mounted authoritative reader.
    """

    body = selected_whole.body
    if body.branch != expected_branch:
        raise ValueError("selected WHOLE branch differs from exact replay kind")
    if len(subject_ids) != len(records):
        raise ValueError("selected WHOLE subjects must match selected membership")
    if len(body.ordered_non_envelope_members) != len(records):
        raise ValueError("selected WHOLE descriptors must match selected membership")
    record_ids = tuple(item.record_id for item in records)
    if len(record_ids) != len(set(record_ids)):
        raise ValueError("selected WHOLE membership cannot contain duplicate records")
    for member, subject_id, descriptor in zip(
        records,
        subject_ids,
        body.ordered_non_envelope_members,
        strict=True,
    ):
        raw = base64.b64decode(member.canonical_base64, validate=True)
        if hashlib.sha256(raw).hexdigest() != member.fingerprint or (
            descriptor.owner != "agent_loop"
            or descriptor.subject_id != subject_id
            or descriptor.record_kind != member.record_kind
            or descriptor.record_id != member.record_id
            or descriptor.schema_id != member.schema_id
            or descriptor.fingerprint != member.fingerprint
        ):
            raise ValueError("selected WHOLE descriptor differs from selected member")


class ScheduledIntervalExactReplayV2(SchedulerExecutionDTO):
    kind: Literal["SCHEDULED_INTERVAL_EXACT_REPLAY_V2"] = "SCHEDULED_INTERVAL_EXACT_REPLAY_V2"
    source: SelectedExactDecisionV2
    complete_selected_records: tuple[SchedulerCanonicalMember, ...] = Field(min_length=1)
    complete_selected_member_subject_ids: tuple[Identity, ...]
    selected_whole_envelope: SelectedScheduledWholeEnvelopeV2

    @model_validator(mode="after")
    def selected_members_match_exact_whole_body(self) -> ScheduledIntervalExactReplayV2:
        if self.selected_whole_envelope.body.branch == "OVERFLOW_HOLD":
            raise ValueError("ordinary exact replay cannot retain an overflow WHOLE")
        _validate_selected_replay_members(
            self.complete_selected_records,
            self.complete_selected_member_subject_ids,
            self.selected_whole_envelope,
            expected_branch=self.selected_whole_envelope.body.branch,
        )
        from .scheduler_outcome_record_contracts import validate_selected_scheduler_replay_native

        validate_selected_scheduler_replay_native(self)
        return self


class ScheduledOverflowIntervalExactReplayV2(SchedulerExecutionDTO):
    kind: Literal["SCHEDULED_OVERFLOW_INTERVAL_EXACT_REPLAY_V2"] = (
        "SCHEDULED_OVERFLOW_INTERVAL_EXACT_REPLAY_V2"
    )
    source: SelectedExactOverflowDecisionV2
    complete_selected_records: tuple[SchedulerCanonicalMember, ...] = Field(min_length=1)
    complete_selected_member_subject_ids: tuple[Identity, ...]
    selected_whole_envelope: SelectedScheduledWholeEnvelopeV2

    @model_validator(mode="after")
    def selected_members_match_exact_overflow_whole_body(
        self,
    ) -> ScheduledOverflowIntervalExactReplayV2:
        _validate_selected_replay_members(
            self.complete_selected_records,
            self.complete_selected_member_subject_ids,
            self.selected_whole_envelope,
            expected_branch="OVERFLOW_HOLD",
        )
        from .scheduler_outcome_record_contracts import validate_selected_scheduler_replay_native

        validate_selected_scheduler_replay_native(self)
        return self


ScheduledExecutionExactReplayV2 = Annotated[
    ScheduledIntervalExactReplayV2 | ScheduledOverflowIntervalExactReplayV2,
    Field(discriminator="kind"),
]


class ScheduledExecutionPreparationRejectedV2(SchedulerExecutionDTO):
    kind: Literal["SCHEDULED_EXECUTION_PREPARATION_REJECTED_V2"] = (
        "SCHEDULED_EXECUTION_PREPARATION_REJECTED_V2"
    )
    command_id: Identity
    code: Literal[
        "MANDATE_BUDGET_EXHAUSTED",
        "DENIED",
        "STALE",
        "CONFLICT",
        "UNSUPPORTED",
        "INTEGRITY_FAULT",
        "HOLD",
    ]
    reason: Identity


ScheduledIntervalPreparationResultV2 = Annotated[
    PreparedScheduledIntervalExecutionOutputsV2
    | ScheduledExecutionPreparationRejectedV2
    | ScheduledExecutionExactReplayV2,
    Field(discriminator="kind"),
]


class SchedulerExecutionPreparationPort(Protocol):
    async def prepare_scheduled_interval_executions(
        self, request: PrepareScheduledIntervalExecutionsV2
    ) -> ScheduledIntervalPreparationResultV2: ...

    async def finalize_scheduled_interval(
        self, request: FinalizeScheduledIntervalV2
    ) -> PreparedFinalizedScheduledIntervalV2 | ScheduledExecutionPreparationRejectedV2: ...
