"""Canonical C-only automatic-scheduler seed fixtures."""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallAuthorityObservation,
    CallSubjectHead,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    FirstPublication,
    PreRootDecisionFence,
    Present,
    TrustedClockProofRef,
)
from chiplog.capabilities.agent_loop.scheduler_configuration import (
    FixedIntervalDefinition,
    PolicyRevision,
    ScheduleRevision,
    policy_head,
    schedule_head,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    FullEligibilityEvidence,
    SchedulerContextRef,
    SchedulerEligibilityManifest,
    StreamingEligibilityEvidence,
)
from chiplog.capabilities.agent_loop.scheduler_execution_contracts import (
    IntervalExecutionCutV2,
    ScheduledServiceApplicabilityV2,
)
from chiplog.capabilities.agent_loop.scheduler_seed_producer_contracts import (
    AutomaticSchedulerCycleRequestV2,
    PrepareSchedulerExecutionSeedV2,
    RetainedSchedulerCycleSourceObservationV2,
    SchedulerCycleActorSourceV2,
    SchedulerCycleApplicabilitySourceV2,
    SchedulerCycleBoundSourceV2,
    SchedulerCycleBudgetSourceV2,
    SchedulerCycleClockObservationV2,
    SchedulerCycleClockSourceV2,
    SchedulerCycleContourSourceV2,
    SchedulerCycleMandateSourceV2,
    SchedulerCycleMissedPolicySourceV2,
    SchedulerCyclePreRootSourceV2,
    SchedulerCycleScheduleSourceV2,
    SchedulerCycleSourceObservationV2,
    SchedulerCycleTrustSourceV2,
    SchedulerSelectedConfigurationObservationV2,
    automatic_command_identity,
    automatic_request_reference,
)
from tests.support.scheduler_execution import (
    DIGEST,
    bound_head,
    boundary,
    budget,
    mandate,
    ordinary_seed_batch,
    parent,
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _present(name: str, raw: bytes) -> Present:
    return Present(head=f"{name}:{_sha(raw)}", fingerprint=_sha(raw))


def _head(name: str) -> CallSubjectHead:
    raw = name.encode()
    return CallSubjectHead(subject_id=name, revision=_present(name, raw))


def public_request() -> AutomaticSchedulerCycleRequestV2:
    return AutomaticSchedulerCycleRequestV2(
        request_id="cycle-request", delivery_id="delivery", schedule_selector="schedule"
    )


def _clock_proof(command_id: str) -> tuple[TrustedClockProofRef, bytes]:
    raw = b"trusted-clock-proof"
    return (
        TrustedClockProofRef(
            proof_id="proof",
            proof_fingerprint=_sha(raw),
            proof_version="v1",
            clock_contract_version="chiplog.scheduler.coordinate-clock.v1",
            fence_fingerprint=DIGEST,
            command_id=command_id,
            command_payload_fingerprint=DIGEST,
            submission_id="submission",
        ),
        raw,
    )


def clock_observation() -> SchedulerCycleClockObservationV2:
    proof, raw = _clock_proof(automatic_command_identity(public_request()).command_id)
    return SchedulerCycleClockObservationV2(
        source_id="scheduler-cycle.clock",
        deployment_profile="test",
        implementation_fingerprint=DIGEST,
        observation_id="clock-observation",
        unix_ns=10,
        monotonic_ns=10,
        valid_until_monotonic_ns=20,
        proof=proof,
        canonical_proof_bytes=raw,
    )


def selected_configuration() -> SchedulerSelectedConfigurationObservationV2:
    parent_value = parent()
    run_inputs = parent_value.run_inputs
    schedule = ScheduleRevision(
        schedule_id="schedule",
        revision=0,
        previous=Absent(),
        definition=FixedIntervalDefinition(
            start_ns=0, period_ns=10, end_exclusive_ns="NO_END", run_inputs=run_inputs
        ),
        context=SchedulerContextRef(
            tenant_id="tenant",
            service_identity="scheduler-service",
            session_id="session",
            mandate_head="mandate",
            issuance_id="issuance",
            issuance_fingerprint=DIGEST,
        ),
        command_id="configuration-command",
    )
    policy = PolicyRevision(
        schedule_id="schedule",
        revision=0,
        previous=Absent(),
        policy="COALESCE",
        context=schedule.context,
        command_id="configuration-command",
    )
    bound = bound_head()
    from chiplog.capabilities.agent_loop.scheduler_configuration import policy_head, schedule_head

    return SchedulerSelectedConfigurationObservationV2(
        tenant_id="tenant",
        database_id="database",
        schedule=schedule,
        schedule_reference=Present(
            head=schedule_head(schedule).head, fingerprint=schedule_head(schedule).fingerprint
        ),
        canonical_schedule_bytes=schedule.canonical_bytes(),
        policy=policy,
        policy_reference=Present(
            head=policy_head(policy).head, fingerprint=policy_head(policy).fingerprint
        ),
        canonical_policy_bytes=policy.canonical_bytes(),
        bound=bound,
        bound_reference=Present(head=bound.head_id, fingerprint=_sha(bound.canonical_bytes())),
        canonical_bound_bytes=bound.canonical_bytes(),
        active_hold=Absent(),
        selected_decision=_head("selected-decision"),
    )


def _foreign_source() -> CallAuthorityObservation:
    return CallAuthorityObservation(
        source_id="foreign",
        family="EVIDENCE",
        source=_head("foreign"),
        generation="g",
        frontier="f",
        canonical_value_base64=base64.b64encode(b"foreign").decode(),
        observed_at_ns=1,
        valid_until_ns=2,
    )


def _row(source_id: str, family: Any, body: Any) -> CallAuthorityObservation:
    raw = body.canonical_bytes()
    return CallAuthorityObservation(
        source_id=source_id,
        family=family,
        source=body.selected_reference,
        generation=body.generation,
        frontier=body.frontier,
        canonical_value_base64=base64.b64encode(raw).decode(),
        observed_at_ns=body.observed_at_ns,
        valid_until_ns=body.valid_until_ns,
    )


def retained_source() -> RetainedSchedulerCycleSourceObservationV2:
    request = public_request()
    command = automatic_command_identity(request)
    configuration = selected_configuration()
    selected_mandate = mandate()
    scope = selected_mandate.mandate.scope.model_copy(
        update={
            "tenant_id": configuration.tenant_id,
            "service_identity": "scheduler-service",
            "schedule_head": CallSubjectHead(
                subject_id=configuration.schedule.schedule_id,
                revision=configuration.schedule_reference,
            ),
            "missed_policy_head": CallSubjectHead(
                subject_id=policy_head(configuration.policy).policy_id,
                revision=configuration.policy_reference,
            ),
            "bound_head": CallSubjectHead(
                subject_id=configuration.bound.head_id,
                revision=configuration.bound_reference,
            ),
        }
    )
    mandate_body = selected_mandate.mandate.model_copy(update={"scope": scope})
    mandate_raw = mandate_body.canonical_bytes()
    selected_mandate = selected_mandate.model_copy(
        update={
            "mandate": mandate_body,
            "canonical_mandate_bytes": mandate_raw,
            "mandate_head": _present("scheduler-system-mandate-v2", mandate_raw),
        }
    )
    selected_budget = budget(selected_mandate)
    selected_budget = selected_budget.model_copy(
        update={
            "budget_head": _present(
                "scheduler-mandate-budget-v1", selected_budget.canonical_budget_bytes
            )
        }
    )
    applicability = ScheduledServiceApplicabilityV2(
        service_identity="scheduler-service",
        current_registration=_head("registration"),
        current_authority=_head("authority"),
        current_runtime_applicability=_head("runtime"),
        current_revocation=Absent(),
        sources=(_foreign_source(),),
    )
    clock = clock_observation()
    selected_boundary = boundary().model_copy(
        update={
            "schedule_definition_head": schedule_head(configuration.schedule),
            "missed_occurrence_policy_head": policy_head(configuration.policy),
        }
    )
    fence = PreRootDecisionFence(
        command_id=command.command_id,
        disposition=FirstPublication(
            decision=Absent(), expected_canonical_absence_manifest="absence"
        ),
        scheduler_authority_head="scheduler",
        broker_generation="broker",
        runtime_generation="runtime",
    )
    common: dict[str, Any] = dict(
        tenant_id="tenant",
        database_id="database",
        generation="generation",
        frontier="frontier",
        observed_at_ns=1,
        valid_until_ns=2,
    )
    rows = (
        _row(
            "scheduler-cycle.actor",
            "ACTOR",
            SchedulerCycleActorSourceV2(
                **common,
                selected_reference=applicability.current_registration,
                service_identity=applicability.service_identity,
                registration=applicability.current_registration,
                credential=_head("credential"),
            ),
        ),
        _row(
            "scheduler-cycle.trust",
            "ACTOR",
            SchedulerCycleTrustSourceV2(
                **common, selected_reference=_head("trust"), trust_binding=_head("trust")
            ),
        ),
        _row(
            "scheduler-cycle.contour",
            "ACTOR",
            SchedulerCycleContourSourceV2(
                **common,
                selected_reference=_head("contour"),
                active_contour=_head("contour"),
                beneficiary_principal=selected_mandate.mandate.scope.beneficiary_principal,
            ),
        ),
        _row(
            "scheduler-cycle.mandate",
            "MANDATE",
            SchedulerCycleMandateSourceV2(
                **common,
                selected_reference=CallSubjectHead(
                    subject_id="mandate", revision=selected_mandate.mandate_head
                ),
                mandate_head=selected_mandate.mandate_head,
                canonical_mandate_bytes=selected_mandate.canonical_mandate_bytes,
                revocation=Absent(),
            ),
        ),
        _row(
            "scheduler-cycle.schedule",
            "POLICY",
            SchedulerCycleScheduleSourceV2(
                **common,
                selected_reference=CallSubjectHead(
                    subject_id=configuration.schedule.schedule_id,
                    revision=configuration.schedule_reference,
                ),
                schedule=configuration.schedule,
                schedule_reference=configuration.schedule_reference,
                canonical_schedule_bytes=configuration.canonical_schedule_bytes,
            ),
        ),
        _row(
            "scheduler-cycle.missed-policy",
            "POLICY",
            SchedulerCycleMissedPolicySourceV2(
                **common,
                selected_reference=CallSubjectHead(
                    subject_id=policy_head(configuration.policy).policy_id,
                    revision=configuration.policy_reference,
                ),
                policy=configuration.policy,
                policy_reference=configuration.policy_reference,
                canonical_policy_bytes=configuration.canonical_policy_bytes,
            ),
        ),
        _row(
            "scheduler-cycle.bound",
            "POLICY",
            SchedulerCycleBoundSourceV2(
                **common,
                selected_reference=CallSubjectHead(
                    subject_id=configuration.bound.head_id, revision=configuration.bound_reference
                ),
                bound=configuration.bound,
                bound_reference=configuration.bound_reference,
                canonical_bound_bytes=configuration.canonical_bound_bytes,
            ),
        ),
        _row(
            "scheduler-cycle.applicability",
            "APPLICABILITY",
            SchedulerCycleApplicabilitySourceV2(
                **common,
                selected_reference=applicability.current_runtime_applicability,
                registration=applicability.current_registration,
                authority=applicability.current_authority,
                runtime=applicability.current_runtime_applicability,
                revocation=Absent(),
            ),
        ),
        _row(
            "scheduler-cycle.clock",
            "APPLICABILITY",
            SchedulerCycleClockSourceV2(
                **common,
                selected_reference=CallSubjectHead(
                    subject_id=clock.observation_id,
                    revision=_present("clock-observation", b"clock"),
                ),
                observation_id=clock.observation_id,
                proof=clock.proof,
                canonical_proof_bytes=clock.canonical_proof_bytes,
                unix_ns=clock.unix_ns,
                broker_clock_observation=CallSubjectHead(
                    subject_id=clock.observation_id,
                    revision=_present("clock-observation", b"clock"),
                ),
            ),
        ),
        _row(
            "scheduler-cycle.pre-root",
            "APPLICABILITY",
            SchedulerCyclePreRootSourceV2(
                **common,
                selected_reference=CallSubjectHead(
                    subject_id=command.command_id, revision=_present("broker-cut", b"cut")
                ),
                fence=fence,
                tenant_commit_sequence=1,
                materialization_commitment=DIGEST,
                independent_journal_head=Absent(),
                broker_cut_observation=CallSubjectHead(
                    subject_id=command.command_id, revision=_present("broker-cut", b"cut")
                ),
            ),
        ),
        _row(
            "scheduler-cycle.budget",
            "MANDATE",
            SchedulerCycleBudgetSourceV2(
                **common,
                selected_reference=CallSubjectHead(
                    subject_id=selected_budget.budget.mandate_id,
                    revision=selected_budget.budget_head,
                ),
                budget_head=selected_budget.budget_head,
                canonical_budget_bytes=selected_budget.canonical_budget_bytes,
            ),
        ),
    )
    observation = SchedulerCycleSourceObservationV2(
        request_reference=automatic_request_reference(request),
        canonical_request_bytes=request.canonical_bytes(),
        command=command,
        configuration=configuration,
        selected_mandate=selected_mandate,
        selected_budget_predecessor=selected_budget,
        current_applicability=applicability,
        clock=clock,
        boundary=selected_boundary,
        pre_root_fence=fence,
        tenant_commit_sequence=1,
        materialization_commitment=DIGEST,
        independent_journal_head=Absent(),
        trust_binding=_head("trust"),
        active_contour=_head("contour"),
        authority_registry=_head("registry"),
        source_preimages=rows,
    )
    raw = observation.canonical_bytes()
    return RetainedSchedulerCycleSourceObservationV2(
        source_reference=CallSubjectHead(
            subject_id=command.command_id,
            revision=_present("scheduler-cycle-source-observation-v2", raw),
        ),
        canonical_source_bytes=raw,
        observation=observation,
    )


def cut(n: int = 0) -> IntervalExecutionCutV2:
    source = retained_source().observation
    base = ordinary_seed_batch(n).cut
    return base.model_copy(
        update={
            "tenant_id": "tenant",
            "database_id": "database",
            "tenant_commit_sequence": source.tenant_commit_sequence,
            "existing_materialization_commitment": source.materialization_commitment,
            "authority_registry": source.authority_registry,
            "sources": source.source_preimages,
            "pre_root_fence": source.pre_root_fence,
            "selected_mandate": source.selected_mandate,
            "selected_budget_predecessor": source.selected_budget_predecessor,
            "current_applicability": source.current_applicability,
        }
    )


def ordinary_request(n: int = 0) -> PrepareSchedulerExecutionSeedV2:
    source = retained_source()
    return PrepareSchedulerExecutionSeedV2(
        identity=source.observation.command,
        source=source,
        cut=cut(n),
        eligibility=FullEligibilityEvidence(
            manifest=SchedulerEligibilityManifest(members=(), fingerprint=DIGEST)
        ),
    )


def overflow_request(kind: str = "STREAMING_MANIFEST") -> PrepareSchedulerExecutionSeedV2:
    source = retained_source()
    current_cut = cut(0).model_copy(update={"ordered_initial_run_absences": ()})
    evidence: FullEligibilityEvidence | StreamingEligibilityEvidence
    if kind == "FULL_MANIFEST":
        evidence = FullEligibilityEvidence(
            manifest=SchedulerEligibilityManifest(members=(), fingerprint=DIGEST)
        )
    else:
        evidence = StreamingEligibilityEvidence(
            manifest_digest=DIGEST,
            member_count=1,
            first_member=Absent(),
            last_member=Absent(),
            order_contract_version="order",
            enumeration_completeness_proof=_present("proof", b"proof"),
        )
    return PrepareSchedulerExecutionSeedV2(
        identity=source.observation.command, source=source, cut=current_cut, eligibility=evidence
    )
