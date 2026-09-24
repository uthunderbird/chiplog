"""Closed owner-facing automatic scheduler seed wire; it grants no authority."""

from __future__ import annotations

import base64
import hashlib
from typing import Annotated, Literal, Protocol, Self

from pydantic import Field, TypeAdapter, model_validator

from .call_acceptance_contracts import CallAuthorityObservation, CallSubjectHead
from .recovery_contracts import (
    Absent,
    Digest,
    Identity,
    PreRootDecisionFence,
    Present,
    TrustedClockProofRef,
    UInt64,
)
from .scheduler_configuration import PolicyRevision, ScheduleRevision, policy_head, schedule_head
from .scheduler_contracts import (
    FullEligibilityEvidence,
    SchedulerCommandIdentity,
    SchedulerEligibilityBoundary,
    SchedulerIntervalBoundHead,
    StreamingEligibilityEvidence,
)
from .scheduler_execution_contracts import (
    IntervalExecutionCutV2,
    OverflowHoldPrimitiveV2,
    PreparedIntervalSeedBatchV2,
    PreparedOverflowPrimitiveFirstPublicationV2,
    PreparedPrimitiveFirstPublicationV2,
    ScheduledServiceApplicabilityV2,
    SchedulerExecutionDTO,
    SelectedScheduledMandateBudgetV1,
    SelectedScheduledSystemMandateV2,
)
from .scheduler_materialization import IntervalParentPrimitive

PUBLIC_REQUEST_SCHEMA_V2 = "chiplog.scheduler.automatic-cycle-request.v2"
CYCLE_CLOCK_OBSERVATION_SCHEMA_V2 = "chiplog.scheduler.cycle-clock-observation.v2"
SELECTED_CONFIGURATION_OBSERVATION_SCHEMA_V2 = (
    "chiplog.scheduler.selected-configuration-observation.v2"
)
CYCLE_SOURCE_OBSERVATION_SCHEMA_V2 = "chiplog.scheduler.cycle-source-observation.v2"
EXECUTION_SEED_REQUEST_SCHEMA_V2 = "chiplog.scheduler.execution-seed-request.v2"
EXECUTION_SEED_REJECTED_SCHEMA_V2 = "chiplog.scheduler.execution-seed-rejected.v2"
EXECUTE_INTERVAL_COMMAND_VERSION_V2 = "chiplog.scheduler.execute-interval.v2"
EXECUTION_PUBLICATION_PREPARATION_SCHEMA_V2 = "chiplog.scheduler.execution-interval-preparation.v2"
CANONICALIZATION_VERSION = "chiplog.scheduler.canonical.v1"
CYCLE_SOURCE_REGISTRY_VERSION_V2 = "chiplog.scheduler.cycle-source-registry.v2"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _present(domain: str, raw: bytes) -> Present:
    digest = _sha(raw)
    return Present(head=f"{domain}:{digest}", fingerprint=digest)


def _validate_selected_wrapper_bytes(
    *, raw: bytes, body: SchedulerExecutionDTO, reference: Present, label: str
) -> None:
    if raw != body.canonical_bytes() or _sha(raw) != reference.fingerprint:
        raise ValueError(f"selected {label} bytes or fingerprint differs")


class AutomaticSchedulerCycleRequestV2(SchedulerExecutionDTO):
    kind: Literal["REQUEST_AUTOMATIC_SCHEDULER_CYCLE_V2"] = "REQUEST_AUTOMATIC_SCHEDULER_CYCLE_V2"
    request_id: Identity
    delivery_id: Identity
    schedule_selector: Identity


def automatic_request_reference(request: AutomaticSchedulerCycleRequestV2) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=request.request_id,
        revision=_present("scheduler-cycle-request-v2", request.canonical_bytes()),
    )


def automatic_command_identity(
    request: AutomaticSchedulerCycleRequestV2,
) -> SchedulerCommandIdentity:
    digest = _sha(request.canonical_bytes())
    return SchedulerCommandIdentity(
        command_id=f"scheduler-execute-interval-v2:{digest}",
        schema_version=EXECUTE_INTERVAL_COMMAND_VERSION_V2,
        canonicalization_version=CANONICALIZATION_VERSION,
    )


def scheduler_execution_command_reference(identity: SchedulerCommandIdentity) -> CallSubjectHead:
    """Return the V2 command reference consumed by executable scheduler primitives."""

    return CallSubjectHead(
        subject_id=identity.command_id,
        revision=_present("scheduler-execution-command-v2", identity.canonical_bytes()),
    )


def _decode_request(raw: bytes) -> AutomaticSchedulerCycleRequestV2:
    value = AutomaticSchedulerCycleRequestV2.model_validate_json(raw)
    if value.canonical_bytes() != raw:
        raise ValueError("automatic scheduler request is not canonical")
    return value


class SchedulerCycleClockObservationV2(SchedulerExecutionDTO):
    schema_id: Literal["chiplog.scheduler.cycle-clock-observation.v2"] = (
        "chiplog.scheduler.cycle-clock-observation.v2"
    )
    source_id: Identity
    contract_version: Literal["chiplog.scheduler.coordinate-clock.v1"] = (
        "chiplog.scheduler.coordinate-clock.v1"
    )
    coordinate_codec: Literal["chiplog.scheduler.unix-ns.v1"] = "chiplog.scheduler.unix-ns.v1"
    deployment_profile: Identity
    implementation_fingerprint: Digest
    observation_id: Identity
    unix_ns: UInt64
    monotonic_ns: UInt64
    valid_until_monotonic_ns: UInt64
    proof: TrustedClockProofRef
    canonical_proof_bytes: bytes = Field(min_length=1)

    @model_validator(mode="after")
    def proof_and_window_are_exact(self) -> Self:
        if (
            self.monotonic_ns >= self.valid_until_monotonic_ns
            or _sha(self.canonical_proof_bytes) != self.proof.proof_fingerprint
        ):
            raise ValueError("clock proof bytes or monotonic validity window differs")
        return self


class SchedulerSelectedConfigurationObservationV2(SchedulerExecutionDTO):
    schema_id: Literal["chiplog.scheduler.selected-configuration-observation.v2"] = (
        "chiplog.scheduler.selected-configuration-observation.v2"
    )
    tenant_id: Identity
    database_id: Identity
    schedule: ScheduleRevision
    schedule_reference: Present
    canonical_schedule_bytes: bytes = Field(min_length=1)
    policy: PolicyRevision
    policy_reference: Present
    canonical_policy_bytes: bytes = Field(min_length=1)
    bound: SchedulerIntervalBoundHead
    bound_reference: Present
    canonical_bound_bytes: bytes = Field(min_length=1)
    active_hold: Annotated[Absent | Present, Field(discriminator="kind")]
    selected_decision: CallSubjectHead

    @model_validator(mode="after")
    def native_parts_are_exact(self) -> Self:
        schedule_ref = schedule_head(self.schedule)
        policy_ref = policy_head(self.policy)
        if (
            self.canonical_schedule_bytes != self.schedule.canonical_bytes()
            or self.schedule_reference
            != Present(head=schedule_ref.head, fingerprint=schedule_ref.fingerprint)
        ):
            raise ValueError("selected schedule native bytes or reference differs")
        if (
            self.canonical_policy_bytes != self.policy.canonical_bytes()
            or self.policy_reference
            != Present(head=policy_ref.head, fingerprint=policy_ref.fingerprint)
        ):
            raise ValueError("selected policy native bytes or reference differs")
        if (
            self.canonical_bound_bytes != self.bound.canonical_bytes()
            or self.bound_reference
            != Present(head=self.bound.head_id, fingerprint=_sha(self.canonical_bound_bytes))
        ):
            raise ValueError("selected bound native bytes or reference differs")
        if self.schedule.schedule_id != self.policy.schedule_id:
            raise ValueError("selected schedule and policy differ")
        return self


def selected_configuration_reference(
    configuration: SchedulerSelectedConfigurationObservationV2,
) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=configuration.schedule.schedule_id,
        revision=_present(
            "scheduler-selected-configuration-observation-v2", configuration.canonical_bytes()
        ),
    )


class _CycleSourceBody(SchedulerExecutionDTO):
    tenant_id: Identity
    database_id: Identity
    selected_reference: CallSubjectHead
    generation: Identity
    frontier: Identity
    observed_at_ns: UInt64
    valid_until_ns: UInt64

    @model_validator(mode="after")
    def time_window_is_nonempty(self) -> Self:
        if self.observed_at_ns >= self.valid_until_ns:
            raise ValueError("source observation validity window is empty")
        return self


class SchedulerCycleActorSourceV2(_CycleSourceBody):
    kind: Literal["SCHEDULER_CYCLE_SOURCE_ACTOR_V2"] = "SCHEDULER_CYCLE_SOURCE_ACTOR_V2"
    schema_id: Literal["chiplog.scheduler.cycle-source-actor.v2"] = (
        "chiplog.scheduler.cycle-source-actor.v2"
    )
    service_identity: Identity
    registration: CallSubjectHead
    credential: CallSubjectHead


class SchedulerCycleTrustSourceV2(_CycleSourceBody):
    kind: Literal["SCHEDULER_CYCLE_SOURCE_TRUST_V2"] = "SCHEDULER_CYCLE_SOURCE_TRUST_V2"
    schema_id: Literal["chiplog.scheduler.cycle-source-trust.v2"] = (
        "chiplog.scheduler.cycle-source-trust.v2"
    )
    trust_binding: CallSubjectHead


class SchedulerCycleContourSourceV2(_CycleSourceBody):
    kind: Literal["SCHEDULER_CYCLE_SOURCE_CONTOUR_V2"] = "SCHEDULER_CYCLE_SOURCE_CONTOUR_V2"
    schema_id: Literal["chiplog.scheduler.cycle-source-contour.v2"] = (
        "chiplog.scheduler.cycle-source-contour.v2"
    )
    active_contour: CallSubjectHead
    beneficiary_principal: Identity


class SchedulerCycleMandateSourceV2(_CycleSourceBody):
    kind: Literal["SCHEDULER_CYCLE_SOURCE_MANDATE_V2"] = "SCHEDULER_CYCLE_SOURCE_MANDATE_V2"
    schema_id: Literal["chiplog.scheduler.cycle-source-mandate.v2"] = (
        "chiplog.scheduler.cycle-source-mandate.v2"
    )
    mandate_head: Present
    mandate_schema: Literal["chiplog.scheduler.system-mandate.v2"] = (
        "chiplog.scheduler.system-mandate.v2"
    )
    canonical_mandate_bytes: bytes = Field(min_length=1)
    revocation: Absent


class SchedulerCycleScheduleSourceV2(_CycleSourceBody):
    kind: Literal["SCHEDULER_CYCLE_SOURCE_SCHEDULE_V2"] = "SCHEDULER_CYCLE_SOURCE_SCHEDULE_V2"
    schema_id: Literal["chiplog.scheduler.cycle-source-schedule.v2"] = (
        "chiplog.scheduler.cycle-source-schedule.v2"
    )
    schedule: ScheduleRevision
    schedule_reference: Present
    canonical_schedule_bytes: bytes = Field(min_length=1)
    native_schema: Literal["chiplog.scheduler.schedule-definition.v1"] = (
        "chiplog.scheduler.schedule-definition.v1"
    )


class SchedulerCycleMissedPolicySourceV2(_CycleSourceBody):
    kind: Literal["SCHEDULER_CYCLE_SOURCE_MISSED_POLICY_V2"] = (
        "SCHEDULER_CYCLE_SOURCE_MISSED_POLICY_V2"
    )
    schema_id: Literal["chiplog.scheduler.cycle-source-missed-policy.v2"] = (
        "chiplog.scheduler.cycle-source-missed-policy.v2"
    )
    policy: PolicyRevision
    policy_reference: Present
    canonical_policy_bytes: bytes = Field(min_length=1)
    native_schema: Literal["chiplog.scheduler.missed-policy.v1"] = (
        "chiplog.scheduler.missed-policy.v1"
    )


class SchedulerCycleBoundSourceV2(_CycleSourceBody):
    kind: Literal["SCHEDULER_CYCLE_SOURCE_BOUND_V2"] = "SCHEDULER_CYCLE_SOURCE_BOUND_V2"
    schema_id: Literal["chiplog.scheduler.cycle-source-bound.v2"] = (
        "chiplog.scheduler.cycle-source-bound.v2"
    )
    bound: SchedulerIntervalBoundHead
    bound_reference: Present
    canonical_bound_bytes: bytes = Field(min_length=1)
    native_schema: Literal["chiplog.scheduler.interval-bound.v1"] = (
        "chiplog.scheduler.interval-bound.v1"
    )


class SchedulerCycleApplicabilitySourceV2(_CycleSourceBody):
    kind: Literal["SCHEDULER_CYCLE_SOURCE_APPLICABILITY_V2"] = (
        "SCHEDULER_CYCLE_SOURCE_APPLICABILITY_V2"
    )
    schema_id: Literal["chiplog.scheduler.cycle-source-applicability.v2"] = (
        "chiplog.scheduler.cycle-source-applicability.v2"
    )
    registration: CallSubjectHead
    authority: CallSubjectHead
    runtime: CallSubjectHead
    revocation: Absent


class SchedulerCycleClockSourceV2(_CycleSourceBody):
    kind: Literal["SCHEDULER_CYCLE_SOURCE_CLOCK_V2"] = "SCHEDULER_CYCLE_SOURCE_CLOCK_V2"
    schema_id: Literal["chiplog.scheduler.cycle-source-clock.v2"] = (
        "chiplog.scheduler.cycle-source-clock.v2"
    )
    observation_id: Identity
    proof: TrustedClockProofRef
    canonical_proof_bytes: bytes = Field(min_length=1)
    unix_ns: UInt64
    broker_clock_observation: CallSubjectHead


class SchedulerCyclePreRootSourceV2(_CycleSourceBody):
    kind: Literal["SCHEDULER_CYCLE_SOURCE_PRE_ROOT_V2"] = "SCHEDULER_CYCLE_SOURCE_PRE_ROOT_V2"
    schema_id: Literal["chiplog.scheduler.cycle-source-pre-root.v2"] = (
        "chiplog.scheduler.cycle-source-pre-root.v2"
    )
    fence: PreRootDecisionFence
    tenant_commit_sequence: UInt64
    materialization_commitment: Digest
    independent_journal_head: Annotated[Absent | Present, Field(discriminator="kind")]
    broker_cut_observation: CallSubjectHead


class SchedulerCycleBudgetSourceV2(_CycleSourceBody):
    kind: Literal["SCHEDULER_CYCLE_SOURCE_BUDGET_V2"] = "SCHEDULER_CYCLE_SOURCE_BUDGET_V2"
    schema_id: Literal["chiplog.scheduler.cycle-source-budget.v2"] = (
        "chiplog.scheduler.cycle-source-budget.v2"
    )
    budget_head: Present
    budget_schema: Literal["chiplog.scheduler.mandate-budget.v1"] = (
        "chiplog.scheduler.mandate-budget.v1"
    )
    canonical_budget_bytes: bytes = Field(min_length=1)


type SchedulerCycleSourceRowV2 = Annotated[
    SchedulerCycleActorSourceV2
    | SchedulerCycleTrustSourceV2
    | SchedulerCycleContourSourceV2
    | SchedulerCycleMandateSourceV2
    | SchedulerCycleScheduleSourceV2
    | SchedulerCycleMissedPolicySourceV2
    | SchedulerCycleBoundSourceV2
    | SchedulerCycleApplicabilitySourceV2
    | SchedulerCycleClockSourceV2
    | SchedulerCyclePreRootSourceV2
    | SchedulerCycleBudgetSourceV2,
    Field(discriminator="kind"),
]
_ROW_ADAPTER: TypeAdapter[SchedulerCycleSourceRowV2] = TypeAdapter(SchedulerCycleSourceRowV2)
SOURCE_REGISTRY_V2: tuple[tuple[str, str, str, type[_CycleSourceBody]], ...] = (
    (
        "scheduler-cycle.actor",
        "ACTOR",
        "chiplog.scheduler.cycle-source-actor.v2",
        SchedulerCycleActorSourceV2,
    ),
    (
        "scheduler-cycle.trust",
        "ACTOR",
        "chiplog.scheduler.cycle-source-trust.v2",
        SchedulerCycleTrustSourceV2,
    ),
    (
        "scheduler-cycle.contour",
        "ACTOR",
        "chiplog.scheduler.cycle-source-contour.v2",
        SchedulerCycleContourSourceV2,
    ),
    (
        "scheduler-cycle.mandate",
        "MANDATE",
        "chiplog.scheduler.cycle-source-mandate.v2",
        SchedulerCycleMandateSourceV2,
    ),
    (
        "scheduler-cycle.schedule",
        "POLICY",
        "chiplog.scheduler.cycle-source-schedule.v2",
        SchedulerCycleScheduleSourceV2,
    ),
    (
        "scheduler-cycle.missed-policy",
        "POLICY",
        "chiplog.scheduler.cycle-source-missed-policy.v2",
        SchedulerCycleMissedPolicySourceV2,
    ),
    (
        "scheduler-cycle.bound",
        "POLICY",
        "chiplog.scheduler.cycle-source-bound.v2",
        SchedulerCycleBoundSourceV2,
    ),
    (
        "scheduler-cycle.applicability",
        "APPLICABILITY",
        "chiplog.scheduler.cycle-source-applicability.v2",
        SchedulerCycleApplicabilitySourceV2,
    ),
    (
        "scheduler-cycle.clock",
        "APPLICABILITY",
        "chiplog.scheduler.cycle-source-clock.v2",
        SchedulerCycleClockSourceV2,
    ),
    (
        "scheduler-cycle.pre-root",
        "APPLICABILITY",
        "chiplog.scheduler.cycle-source-pre-root.v2",
        SchedulerCyclePreRootSourceV2,
    ),
    (
        "scheduler-cycle.budget",
        "MANDATE",
        "chiplog.scheduler.cycle-source-budget.v2",
        SchedulerCycleBudgetSourceV2,
    ),
)


def decode_scheduler_cycle_source_row(
    observation: CallAuthorityObservation,
) -> SchedulerCycleSourceRowV2:
    entry = next((item for item in SOURCE_REGISTRY_V2 if item[0] == observation.source_id), None)
    if entry is None or observation.family != entry[1]:
        raise ValueError("unknown or wrong-family scheduler cycle source")
    try:
        raw = base64.b64decode(observation.canonical_value_base64, validate=True)
        body = _ROW_ADAPTER.validate_json(raw)
    except Exception as error:
        raise ValueError(
            "scheduler cycle source bytes do not use its registered decoder"
        ) from error
    if (
        not isinstance(body, entry[3])
        or body.schema_id != entry[2]
        or body.canonical_bytes() != raw
    ):
        raise ValueError("scheduler cycle source is noncanonical or hybrid")
    if (
        observation.source != body.selected_reference
        or observation.generation != body.generation
        or observation.frontier != body.frontier
        or observation.observed_at_ns != body.observed_at_ns
        or observation.valid_until_ns != body.valid_until_ns
    ):
        raise ValueError("scheduler cycle source envelope differs from body")
    return body


class SchedulerCycleSourceObservationV2(SchedulerExecutionDTO):
    schema_id: Literal["chiplog.scheduler.cycle-source-observation.v2"] = (
        "chiplog.scheduler.cycle-source-observation.v2"
    )
    request_reference: CallSubjectHead
    request_schema: Literal["chiplog.scheduler.automatic-cycle-request.v2"] = (
        "chiplog.scheduler.automatic-cycle-request.v2"
    )
    canonical_request_bytes: bytes = Field(min_length=1)
    command: SchedulerCommandIdentity
    configuration: SchedulerSelectedConfigurationObservationV2
    selected_mandate: SelectedScheduledSystemMandateV2
    selected_budget_predecessor: SelectedScheduledMandateBudgetV1
    current_applicability: ScheduledServiceApplicabilityV2
    clock: SchedulerCycleClockObservationV2
    boundary: SchedulerEligibilityBoundary
    pre_root_fence: PreRootDecisionFence
    tenant_commit_sequence: UInt64
    materialization_commitment: Digest
    independent_journal_head: Annotated[Absent | Present, Field(discriminator="kind")]
    trust_binding: CallSubjectHead
    active_contour: CallSubjectHead
    authority_registry: CallSubjectHead
    source_preimages: tuple[CallAuthorityObservation, ...] = Field(min_length=11, max_length=11)

    @model_validator(mode="after")
    def has_closed_selected_projection(self) -> Self:
        request = _decode_request(self.canonical_request_bytes)
        if self.request_reference != automatic_request_reference(
            request
        ) or self.command != automatic_command_identity(request):
            raise ValueError("public request/reference/command differs")
        if self.pre_root_fence.command_id != self.command.command_id:
            raise ValueError("pre-root fence differs from automatic command")
        _validate_selected_wrapper_bytes(
            raw=self.selected_mandate.canonical_mandate_bytes,
            body=self.selected_mandate.mandate,
            reference=self.selected_mandate.mandate_head,
            label="mandate",
        )
        _validate_selected_wrapper_bytes(
            raw=self.selected_budget_predecessor.canonical_budget_bytes,
            body=self.selected_budget_predecessor.budget,
            reference=self.selected_budget_predecessor.budget_head,
            label="budget",
        )
        scope = self.selected_mandate.mandate.scope
        schedule_ref = schedule_head(self.configuration.schedule)
        policy_ref = policy_head(self.configuration.policy)
        expected_schedule = CallSubjectHead(
            subject_id=self.configuration.schedule.schedule_id,
            revision=Present(head=schedule_ref.head, fingerprint=schedule_ref.fingerprint),
        )
        expected_policy = CallSubjectHead(
            subject_id=policy_ref.policy_id,
            revision=Present(head=policy_ref.head, fingerprint=policy_ref.fingerprint),
        )
        expected_bound = CallSubjectHead(
            subject_id=self.configuration.bound.head_id,
            revision=self.configuration.bound_reference,
        )
        if (
            self.boundary.schedule_definition_head != schedule_ref
            or self.boundary.missed_occurrence_policy_head != policy_ref
            or scope.schedule_head != expected_schedule
            or scope.missed_policy_head != expected_policy
            or scope.bound_head != expected_bound
            or scope.tenant_id != self.configuration.tenant_id
            or scope.service_identity != self.current_applicability.service_identity
            or self.configuration.schedule.context.tenant_id != self.configuration.tenant_id
            or self.configuration.policy.context.tenant_id != self.configuration.tenant_id
            or self.configuration.schedule.context.service_identity != scope.service_identity
            or self.configuration.policy.context.service_identity != scope.service_identity
        ):
            raise ValueError("boundary, mandate scope, or native configuration context differs")
        if (
            self.clock.proof.command_id != self.command.command_id
            or self.clock.coordinate_codec != scope.horizon.coordinate_codec
            or self.boundary.previous_due_boundary.coordinate_policy_version
            != self.clock.coordinate_codec
            or self.boundary.cutoff_due_coordinate.coordinate_policy_version
            != self.clock.coordinate_codec
            or self.clock.unix_ns >= scope.horizon.expires_at_unix_ns
        ):
            raise ValueError("clock command, codec, or immutable expiry differs")
        if (
            self.selected_budget_predecessor.budget.mandate_id != scope.mandate_id
            or self.selected_budget_predecessor.budget.consumption_basis.mandate_id
            != scope.mandate_id
        ):
            raise ValueError("selected budget mandate lineage differs")
        if (
            self.configuration.active_hold.kind != "ABSENT"
            or self.current_applicability.current_revocation.kind != "ABSENT"
        ):
            raise ValueError("fresh automatic source cannot retain a hold or revocation")
        if any(
            item.source_id.startswith("scheduler-cycle.")
            for item in self.current_applicability.sources
        ):
            raise ValueError("applicability cannot recursively embed scheduler-cycle sources")
        rows = tuple(decode_scheduler_cycle_source_row(item) for item in self.source_preimages)
        if tuple((item.source_id, item.family) for item in self.source_preimages) != tuple(
            (item[0], item[1]) for item in SOURCE_REGISTRY_V2
        ):
            raise ValueError("scheduler cycle sources must use exact registered order")
        (
            actor,
            trust,
            contour,
            mandate,
            schedule,
            policy,
            bound,
            applicability,
            clock,
            pre_root,
            budget,
        ) = rows
        assert (
            isinstance(actor, SchedulerCycleActorSourceV2)
            and isinstance(trust, SchedulerCycleTrustSourceV2)
            and isinstance(contour, SchedulerCycleContourSourceV2)
            and isinstance(mandate, SchedulerCycleMandateSourceV2)
            and isinstance(schedule, SchedulerCycleScheduleSourceV2)
            and isinstance(policy, SchedulerCycleMissedPolicySourceV2)
            and isinstance(bound, SchedulerCycleBoundSourceV2)
            and isinstance(applicability, SchedulerCycleApplicabilitySourceV2)
            and isinstance(clock, SchedulerCycleClockSourceV2)
            and isinstance(pre_root, SchedulerCyclePreRootSourceV2)
            and isinstance(budget, SchedulerCycleBudgetSourceV2)
        )
        if any(
            row.tenant_id != self.configuration.tenant_id
            or row.database_id != self.configuration.database_id
            for row in rows
        ):
            raise ValueError("source rows differ from selected configuration tenant/database")
        if (
            actor.service_identity != self.current_applicability.service_identity
            or actor.registration != self.current_applicability.current_registration
            or actor.selected_reference != actor.registration
            or trust.trust_binding != self.trust_binding
            or trust.selected_reference != trust.trust_binding
            or contour.active_contour != self.active_contour
            or contour.selected_reference != contour.active_contour
            or contour.beneficiary_principal
            != self.selected_mandate.mandate.scope.beneficiary_principal
        ):
            raise ValueError("actor/trust/contour source join differs")
        if (
            mandate.mandate_head != self.selected_mandate.mandate_head
            or mandate.canonical_mandate_bytes != self.selected_mandate.canonical_mandate_bytes
            or mandate.selected_reference.subject_id != scope.mandate_id
            or mandate.selected_reference.revision != mandate.mandate_head
            or schedule.schedule != self.configuration.schedule
            or schedule.schedule_reference != self.configuration.schedule_reference
            or schedule.canonical_schedule_bytes != self.configuration.canonical_schedule_bytes
            or schedule.selected_reference.subject_id != schedule.schedule.schedule_id
            or schedule.selected_reference.revision != schedule.schedule_reference
            or policy.policy != self.configuration.policy
            or policy.policy_reference != self.configuration.policy_reference
            or policy.canonical_policy_bytes != self.configuration.canonical_policy_bytes
            or policy.selected_reference.subject_id != policy_head(policy.policy).policy_id
            or policy.selected_reference.revision != policy.policy_reference
            or bound.bound != self.configuration.bound
            or bound.bound_reference != self.configuration.bound_reference
            or bound.canonical_bound_bytes != self.configuration.canonical_bound_bytes
            or bound.selected_reference.subject_id != bound.bound.head_id
            or bound.selected_reference.revision != bound.bound_reference
        ):
            raise ValueError("selected mandate or native configuration source join differs")
        if (
            applicability.registration != self.current_applicability.current_registration
            or applicability.authority != self.current_applicability.current_authority
            or applicability.runtime != self.current_applicability.current_runtime_applicability
            or applicability.selected_reference != applicability.runtime
            or clock.observation_id != self.clock.observation_id
            or clock.proof != self.clock.proof
            or clock.canonical_proof_bytes != self.clock.canonical_proof_bytes
            or clock.unix_ns != self.clock.unix_ns
            or clock.selected_reference != clock.broker_clock_observation
            or clock.selected_reference.subject_id != clock.observation_id
            or pre_root.fence != self.pre_root_fence
            or pre_root.tenant_commit_sequence != self.tenant_commit_sequence
            or pre_root.materialization_commitment != self.materialization_commitment
            or pre_root.independent_journal_head != self.independent_journal_head
            or pre_root.selected_reference != pre_root.broker_cut_observation
            or pre_root.selected_reference.subject_id != self.command.command_id
            or budget.budget_head != self.selected_budget_predecessor.budget_head
            or budget.canonical_budget_bytes
            != self.selected_budget_predecessor.canonical_budget_bytes
            or budget.selected_reference.subject_id
            != self.selected_budget_predecessor.budget.mandate_id
            or budget.selected_reference.revision != budget.budget_head
        ):
            raise ValueError("applicability/clock/cut/budget source join differs")
        return self


class RetainedSchedulerCycleSourceObservationV2(SchedulerExecutionDTO):
    schema_id: Literal["chiplog.scheduler.cycle-source-observation.v2"] = (
        "chiplog.scheduler.cycle-source-observation.v2"
    )
    source_reference: CallSubjectHead
    canonical_source_bytes: bytes = Field(min_length=1)
    observation: SchedulerCycleSourceObservationV2

    @model_validator(mode="after")
    def body_and_external_reference_are_exact(self) -> Self:
        if (
            self.canonical_source_bytes != self.observation.canonical_bytes()
            or self.source_reference.subject_id != self.observation.command.command_id
            or self.source_reference.revision
            != _present("scheduler-cycle-source-observation-v2", self.canonical_source_bytes)
        ):
            raise ValueError("retained scheduler source bytes or reference differs")
        return self


class SchedulerCycleSourceQueryV2(SchedulerExecutionDTO):
    tenant_id: Identity
    database_id: Identity
    command_id: Identity
    selected_cut: IntervalExecutionCutV2
    registry_version: Literal["chiplog.scheduler.cycle-source-registry.v2"] = (
        "chiplog.scheduler.cycle-source-registry.v2"
    )


class SchedulerCycleSourceReaderPortV2(Protocol):
    async def read_registered_cycle_sources(
        self, query: SchedulerCycleSourceQueryV2
    ) -> tuple[CallAuthorityObservation, ...]: ...


def primitive_parent_reference(parent: IntervalParentPrimitive) -> Present:
    return _present("scheduler-parent-v1", parent.canonical_bytes())


def validate_prepared_primitive_parent(
    source: PreparedPrimitiveFirstPublicationV2, *, expected_kind: Literal["ORDINARY", "RESOLUTION"]
) -> IntervalParentPrimitive:
    parent = source.primitive_parent
    if (
        source.canonical_primitive_parent_bytes != parent.canonical_bytes()
        or source.primitive_parent_reference != primitive_parent_reference(parent)
        or parent.kind != expected_kind
    ):
        raise ValueError("prepared primitive parent bytes, reference, or kind differs")
    if expected_kind == "ORDINARY" and (
        parent.original_hold.kind != "ABSENT"
        or parent.operator_proof.kind != "ABSENT"
        or parent.original_bound != parent.current_bound
    ):
        raise ValueError("ordinary primitive parent must retain absent hold/proof and equal bounds")
    if expected_kind == "RESOLUTION" and (
        parent.original_hold.kind != "PRESENT"
        or parent.operator_proof.kind != "PRESENT"
        or parent.original_bound == parent.current_bound
        or parent.current_bound.generation <= parent.original_bound.generation
        or parent.current_bound.head_id == parent.original_bound.head_id
    ):
        raise ValueError("resolution primitive parent differs from successor-bound protocol")
    return parent


def overflow_primitive_reference(
    primitive: PreparedOverflowPrimitiveFirstPublicationV2 | object,
) -> Present:
    value = (
        primitive.overflow_primitive
        if isinstance(primitive, PreparedOverflowPrimitiveFirstPublicationV2)
        else primitive
    )
    assert hasattr(value, "canonical_bytes")
    return _present("scheduler-overflow-primitive-v2", value.canonical_bytes())


def validate_prepared_overflow_primitive(
    source: PreparedOverflowPrimitiveFirstPublicationV2,
) -> OverflowHoldPrimitiveV2:
    primitive = source.overflow_primitive
    if (
        source.canonical_overflow_primitive_bytes != primitive.canonical_bytes()
        or source.overflow_primitive_reference != overflow_primitive_reference(source)
        or primitive.actual_value <= primitive.limit
    ):
        raise ValueError("overflow primitive bytes, reference, or overflow inequality differs")
    return primitive


def validate_prepared_overflow_primitive_source_join(
    source: RetainedSchedulerCycleSourceObservationV2,
    prepared: PreparedOverflowPrimitiveFirstPublicationV2,
) -> OverflowHoldPrimitiveV2:
    """Verify that a prepared overflow primitive belongs to its retained cycle source."""

    try:
        retained_source = RetainedSchedulerCycleSourceObservationV2.model_validate_json(
            source.canonical_bytes()
        )
    except ValueError as error:
        raise ValueError("prepared overflow retained source carrier is invalid") from error
    primitive = validate_prepared_overflow_primitive(prepared)
    if primitive.command != scheduler_execution_command_reference(
        retained_source.observation.command
    ):
        raise ValueError("prepared overflow primitive source command reference differs")
    return primitive


class PrepareSchedulerExecutionSeedV2(SchedulerExecutionDTO):
    kind: Literal["PREPARE_SCHEDULER_EXECUTION_SEED_V2"] = "PREPARE_SCHEDULER_EXECUTION_SEED_V2"
    schema_id: Literal["chiplog.scheduler.execution-seed-request.v2"] = (
        "chiplog.scheduler.execution-seed-request.v2"
    )
    identity: SchedulerCommandIdentity
    source: RetainedSchedulerCycleSourceObservationV2
    cut: IntervalExecutionCutV2
    eligibility: Annotated[
        FullEligibilityEvidence | StreamingEligibilityEvidence, Field(discriminator="kind")
    ]

    @model_validator(mode="after")
    def fresh_cut_and_source_are_one_exact_projection(self) -> Self:
        source = self.source.observation
        if (
            self.identity != source.command
            or self.cut.tenant_id != source.configuration.tenant_id
            or self.cut.database_id != source.configuration.database_id
            or self.cut.tenant_commit_sequence != source.tenant_commit_sequence
            or self.cut.existing_materialization_commitment != source.materialization_commitment
            or self.cut.authority_registry != source.authority_registry
            or self.cut.pre_root_fence != source.pre_root_fence
            or self.cut.selected_mandate != source.selected_mandate
            or self.cut.selected_budget_predecessor != source.selected_budget_predecessor
            or self.cut.current_applicability != source.current_applicability
            or self.cut.sources != source.source_preimages
        ):
            raise ValueError("seed cut differs from retained selected source")
        if (
            self.cut.pre_root_fence.disposition.kind != "FIRST_PUBLICATION"
            or self.cut.pre_root_fence.disposition.decision.kind != "ABSENT"
        ):
            raise ValueError("seed preparation accepts first publication only")
        return self


class PreparedSchedulerExecutionSeedV2(SchedulerExecutionDTO):
    kind: Literal["PREPARED_SCHEDULER_EXECUTION_SEED_V2"] = "PREPARED_SCHEDULER_EXECUTION_SEED_V2"
    source_request_fingerprint: Digest
    seed_batch: PreparedIntervalSeedBatchV2
    proposal_fingerprint: Digest


class SchedulerExecutionSeedRejectedV2(SchedulerExecutionDTO):
    kind: Literal["SCHEDULER_EXECUTION_SEED_REJECTED_V2"] = "SCHEDULER_EXECUTION_SEED_REJECTED_V2"
    schema_id: Literal["chiplog.scheduler.execution-seed-rejected.v2"] = (
        "chiplog.scheduler.execution-seed-rejected.v2"
    )
    command_id: Identity
    code: Literal[
        "MANDATE_BUDGET_EXHAUSTED",
        "AUTHORITY_DENIED",
        "CLOCK_UNVERIFIABLE",
        "STALE",
        "INTEGRITY_HOLD",
        "INVALID_INPUT",
        "UNSUPPORTED",
    ]
    reason: Identity


class SchedulerExecutionSeedPort(Protocol):
    async def prepare_seed(
        self, request: PrepareSchedulerExecutionSeedV2
    ) -> PreparedSchedulerExecutionSeedV2 | SchedulerExecutionSeedRejectedV2: ...
