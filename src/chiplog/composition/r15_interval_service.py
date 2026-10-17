"""Selected interval progress shared by automatic decisions and hold resolution.

This is a read-only projection of an already authenticated scheduler startup cut.
The caller must obtain that cut through SchedulerPublicationAuthority.capture_fresh.
It issues no authority and does not choose an interval cutoff.
"""

from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import Field

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    FirstPublication,
    PreRootDecisionFence,
    Present,
)
from chiplog.capabilities.agent_loop.scheduler_configuration import (
    ConfigurationSnapshot,
    DisposedOccurrence,
    policy_head,
    schedule_head,
    stream_definition,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    DecideIntervalCommand,
    DueCoordinate,
    ScheduledIntervalDecision,
    SchedulerCommandIdentity,
    SchedulerContextRef,
    SchedulerEligibilityBoundary,
    SchedulerIntervalResolutionDecision,
)
from chiplog.capabilities.agent_loop.scheduler_preparation import (
    BatchPreparationCut,
    IntervalPreparationRequest,
    IntervalPreparationSnapshot,
)
from chiplog.composition.r15_scheduler_authority import (
    SchedulerFreshSources,
    SchedulerPublicationAuthority,
)
from chiplog.composition.r15_scheduler_history import configuration_snapshot
from chiplog.composition.r15_scheduler_registry import _canonical, _digest, _reference
from chiplog.composition.r15_tick_contracts import TickClockObservation, TickDTO, TickSourceCut
from chiplog.composition.r15_tick_evidence import cut_proof
from chiplog.composition.scheduler_source_registry import AdmittedSchedulerStartup
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    BrokerPublicationResult,
    ExactReplayQuery,
    InvocationProofRef,
    JournalSelectedPublication,
    OwnerCommandBytes,
    OwnerRecordBytes,
    PublicationIdentity,
    PublicationRejected,
    SingleOwnerBatch,
    WorkerAuthentication,
)
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortRejected
from chiplog.platform.owner_publications import (
    BrokerPublicationCoordinator,
    PreparedOwnerPublication,
)

SERVICE_ISSUANCE_SCHEMA = "chiplog.scheduler.interval-service-issuance.v3"


class IntervalServiceIssuanceEvidence(TickDTO):
    """Original signed service request, selected mandate and complete owner frames."""

    schema_id: str = SERVICE_ISSUANCE_SCHEMA
    act_id: str = Field(min_length=1)
    schedule_id: str = Field(min_length=1)
    selected_mandate_bytes: bytes = Field(min_length=1)
    selected_policy: Present
    execution_identity_bytes: bytes = Field(min_length=1)
    signed_message: bytes = Field(min_length=1)
    signature: bytes = Field(min_length=64, max_length=64)
    trust_request_frame: bytes = Field(min_length=1)
    trust_response_frame: bytes = Field(min_length=1)
    trust_observation_bytes: bytes = Field(min_length=1)
    generation_preimages: bytes = Field(min_length=1)
    source_cut: TickSourceCut
    clock: TickClockObservation
    issuance_nonce: str = Field(min_length=1)
    preparation: IntervalPreparationRequest
    owner_request_frame: bytes = Field(min_length=1)
    owner_response_frame: bytes = Field(min_length=1)


def service_interval_id(act_id: str, principal_id: str) -> str:
    """Stable replay key; it deliberately excludes live heads and clock readings."""
    return _reference(
        "r15-service-interval-act-v3",
        ("hermetic-tenant", principal_id, "scheduler.decide_interval", act_id),
    )


def service_context_for(
    mandate: Any,
    policy: Present,
    execution: Any,
    signed_message: bytes,
    signature: bytes,
    generations: bytes,
    source_cut: TickSourceCut,
    clock: TickClockObservation,
    nonce: str,
    session_id: str,
) -> SchedulerContextRef:
    mandate_value = mandate.model_dump(mode="json")
    execution_value = execution.model_dump(mode="json")
    return SchedulerContextRef(
        tenant_id=source_cut.tenant_id,
        service_identity=execution.service_identity,
        session_id=session_id,
        mandate_head=mandate.mandate_head.head,
        issuance_id="r15-service-context:" + nonce,
        issuance_fingerprint=_digest(
            _canonical(
                {
                    "mandate": mandate_value,
                    "policy": policy.model_dump(mode="json"),
                    "identity": execution_value,
                    "signed_message": base64.b64encode(signed_message).decode(),
                    "signature": base64.b64encode(signature).decode(),
                    "generations": json.loads(generations),
                    "source_cut": source_cut.model_dump(mode="json"),
                    "clock": clock.model_dump(mode="json"),
                    "nonce": nonce,
                }
            )
        ),
    )


def require_mandate_members(request: IntervalPreparationRequest, mandate: Any) -> None:
    """Independently cover full enumeration, including streaming overflow evidence."""
    from chiplog.capabilities.agent_loop.scheduler_configuration import (
        definition_enumeration,
        definition_members,
    )
    from chiplog.capabilities.agent_loop.scheduler_domain import coordinate_value

    command = DecideIntervalCommand.model_validate_json(request.command_bytes)
    plan = definition_enumeration(
        request.snapshot.configuration, command.boundary, request.snapshot.disposed
    )
    for member in definition_members(plan):
        due = coordinate_value(member.due_coordinate)
        if not mandate.first_due_ns <= due <= mandate.last_due_ns:
            raise LoopRejected("service interval includes due member outside mandate horizon")


def validate_service_interval_batch(batch: SingleOwnerBatch) -> IntervalServiceIssuanceEvidence:
    """Verify retained service and owner bytes before historical read/recovery.

    The source registry additionally joins the selected policy and mandate with
    its preceding administrative journal decision. This verifier cannot turn a
    merely constructible evidence object into a fresh authority.
    """
    from chiplog.capabilities.agent_loop.scheduler_preparation import prepare_interval_request
    from chiplog.composition.r15_interval_policy import SelectedIntervalMandateV3
    from chiplog.composition.r15_interval_service_auth import (
        IntervalExecutionAuthority,
        verify_interval_signature,
    )
    from chiplog.composition.r15_tick_clock_v1 import _clock_source

    if (
        batch.operation != "scheduler.decide_interval"
        or not isinstance(batch.authentication, WorkerAuthentication)
        or batch.authentication.applicability_schema != SERVICE_ISSUANCE_SCHEMA
        or batch.complete_batch_fingerprint
        != _digest(
            _canonical(batch.model_dump(mode="json", exclude={"complete_batch_fingerprint"}))
        )
    ):
        raise ValueError("service interval envelope differs")
    auth = batch.authentication
    evidence = IntervalServiceIssuanceEvidence.model_validate_json(auth.applicability_bytes)
    if (
        evidence.canonical_bytes() != auth.applicability_bytes
        or auth.applicability_fingerprint != _digest(auth.applicability_bytes)
    ):
        raise ValueError("service interval issuance evidence differs")
    mandate = SelectedIntervalMandateV3.model_validate_json(evidence.selected_mandate_bytes)
    execution = IntervalExecutionAuthority.model_validate_json(evidence.execution_identity_bytes)
    if (
        mandate.canonical_bytes() != evidence.selected_mandate_bytes
        or execution.canonical_bytes() != evidence.execution_identity_bytes
        or mandate.mandate.service_identity != execution.service_identity
        or mandate.mandate.tenant_id != batch.identity.tenant_id
        or mandate.mandate.schedule.schedule_id != evidence.schedule_id
    ):
        raise ValueError("service interval mandate or identity differs")
    verify_interval_signature(execution, evidence.signed_message, evidence.signature)
    signed = evidence.signed_message
    if not signed.startswith(b"chiplog.r15.interval-service.decide.v3\x00"):
        raise ValueError("service interval signed domain differs")
    signed_body = json.loads(signed.split(b"\x00", 1)[1])
    intent = signed_body.get("intent", {})
    challenge = signed_body.get("challenge", {})
    if (
        intent.get("schedule_id") != evidence.schedule_id
        or intent.get("act_id") != evidence.act_id
        or intent.get("policy") != evidence.selected_policy.model_dump(mode="json")
        or intent.get("authority") != _digest(execution.canonical_bytes())
        or intent.get("principal") != execution.principal_id
        or intent.get("service") != execution.service_identity
        or intent.get("tenant") != mandate.mandate.tenant_id
        or intent.get("session") != execution.session_id
        or not isinstance(challenge.get("nonce"), str)
        or not challenge["nonce"]
        or challenge.get("expires_monotonic_ns", 0) <= evidence.clock.reading.monotonic_ns
    ):
        raise ValueError("service interval signed intent differs")
    generations = json.loads(evidence.generation_preimages)
    authentication = generations["authentication"]
    worker = generations["broker_generation_preimage"]["agent_session"]
    if (
        evidence.trust_request_frame != _canonical(authentication["request"])
        or evidence.trust_response_frame != _canonical(authentication["response"])
        or evidence.trust_observation_bytes != _canonical(authentication["observation"])
    ):
        raise ValueError("service interval trust frames differ")
    trust_request, trust_response = authentication["request"], authentication["response"]
    trust_result = json.loads(base64.b64decode(authentication["result_base64"], validate=True))
    trust_reference = generations["trust_reference"]
    if (
        trust_request["operation_id"] != "deployment_trust.authenticate"
        or trust_response["request_id"] != trust_request["request_id"]
        or trust_response["responder"] != trust_request["callee"]
        or base64.b64decode(trust_response["canonical_payload_base64"], validate=True)
        != base64.b64decode(authentication["result_base64"], validate=True)
        or trust_result["disposition"] != "VALID"
        or json.loads(base64.b64decode(trust_result["reference_bytes"], validate=True))
        != trust_reference
        or (
            trust_reference["tenant_id"],
            trust_reference["principal_id"],
            trust_reference["contour"],
            trust_reference["source_head"],
        )
        != ("hermetic-tenant", "hermetic-principal", "CLI", "local")
    ):
        raise ValueError("service interval original trust decision differs")
    clock, request = evidence.clock, evidence.preparation
    if (
        clock.source != _clock_source(clock.source.source_id)
        or clock.broker_epoch != str(worker["broker_epoch"])
        or clock.broker_session != worker["session_id"]
        or auth.invocation.broker_epoch != str(worker["broker_epoch"])
        or auth.invocation.broker_session != worker["session_id"]
        or auth.invocation.runtime_generation != worker["generation_id"]
        or auth.invocation.operation_subject != batch.identity.command_id
        or challenge.get("broker_epoch") != worker["broker_epoch"]
        or challenge.get("generation") != worker["generation_id"]
        or clock.reading.monotonic_ns >= clock.valid_until_monotonic_ns
        or clock.valid_until_monotonic_ns
        > authentication["request"]["budget"]["absolute_deadline_ns"]
        or request.operation != batch.operation
        or batch.identity.command_id != service_interval_id(evidence.act_id, execution.principal_id)
        or batch.identity.command_fingerprint != _digest(request.command_bytes)
    ):
        raise ValueError("service interval clock, operation or replay identity differs")
    command = DecideIntervalCommand.model_validate_json(request.command_bytes)
    require_mandate_members(request, mandate.mandate)
    config = request.snapshot.configuration
    if config.schedule is None or config.policy is None or config.bound is None:
        raise ValueError("service interval original configuration incomplete")
    enumeration_body = {
        "cut": evidence.source_cut.model_dump(mode="json"),
        "schedule": schedule_head(config.schedule).model_dump(mode="json"),
        "policy": policy_head(config.policy).model_dump(mode="json"),
        "bound": config.bound.model_dump(mode="json"),
        "previous": request.snapshot.previous_due_boundary.model_dump(mode="json"),
        "predecessor": request.snapshot.predecessor_interval.model_dump(mode="json"),
    }
    frontier = _reference("r15-service-enumeration-v3", enumeration_body)
    proof = Present(head=frontier, fingerprint=_digest(_canonical(enumeration_body)))
    expected_context = service_context_for(
        mandate,
        evidence.selected_policy,
        execution,
        evidence.signed_message,
        evidence.signature,
        evidence.generation_preimages,
        evidence.source_cut,
        clock,
        evidence.issuance_nonce,
        execution.session_id,
    )
    if (
        command.canonical_bytes() != request.command_bytes
        or request.context != expected_context
        or request.snapshot.cut.authorized_context != expected_context
        or command.publication_fence.scheduler_authority_head != mandate.mandate_head.head
        or command.identity.command_id != batch.identity.command_id
        or command.boundary.schedule_definition_head != mandate.mandate.schedule
        or command.boundary.missed_occurrence_policy_head != mandate.mandate.missed_policy
        or command.bound_head != mandate.mandate.bound
        or command.boundary.cutoff_due_coordinate.canonical_coordinate != str(clock.reading.unix_ns)
        or command.boundary.enumeration_frontier != frontier
        or request.snapshot.enumeration_frontier != frontier
        or request.snapshot.enumeration_proof != proof
        or command.boundary.previous_due_boundary != request.snapshot.previous_due_boundary
        or command.boundary.predecessor_interval != request.snapshot.predecessor_interval
        or command.boundary.schedule_definition_head != schedule_head(config.schedule)
        or command.boundary.missed_occurrence_policy_head != policy_head(config.policy)
        or command.bound_head != config.bound
        or request.snapshot.cut.authorized_command_fingerprint != _digest(request.command_bytes)
        or request.snapshot.cut.authority_proof != cut_proof(request.context, evidence.source_cut)
        or request.snapshot.cut.tenant_frontier != evidence.source_cut.tenant_frontier
        or request.snapshot.cut.materialization_commitment
        != evidence.source_cut.materialization_commitment
        or request.snapshot.cut.submission_id != "r15-service-submission:" + evidence.issuance_nonce
    ):
        raise ValueError("service interval original command differs")
    from chiplog.composition.r15_tick_runtime import _manifest

    if batch.expected != _manifest(request):
        raise ValueError("service interval exact read manifest differs")
    if auth.invocation.issuance_fingerprint != _digest(
        _canonical(
            {
                "nonce": auth.invocation.issuance_id,
                "identity": batch.identity.model_dump(mode="json"),
                "command": batch.command.model_dump(mode="json"),
                "evidence": auth.applicability_fingerprint,
            }
        )
    ):
        raise ValueError("service interval invocation commitment differs")
    prepared = prepare_interval_request(request)
    owner_sent, owner_response = (
        json.loads(evidence.owner_request_frame),
        json.loads(evidence.owner_response_frame),
    )
    if (
        owner_sent.get("operation_id") != "scheduler.prepare_interval"
        or base64.b64decode(owner_sent["canonical_payload_base64"], validate=True)
        != request.canonical_bytes()
        or base64.b64decode(owner_response["canonical_payload_base64"], validate=True)
        != prepared.canonical_bytes()
        or batch.command.canonical_bytes != request.canonical_bytes()
        or tuple(
            (row.record_kind, row.record_id, row.schema_id, row.canonical_bytes, row.fingerprint)
            for row in batch.complete_records
        )
        != tuple(
            (
                row.record_kind,
                row.record_id,
                row.schema_id,
                base64.b64decode(row.canonical_base64, validate=True),
                row.fingerprint,
            )
            for row in prepared.records
        )
    ):
        raise ValueError("service interval complete owner output differs")
    return evidence


if TYPE_CHECKING:
    from chiplog.composition.r15_interval_service_auth import IntervalServiceSession
    from chiplog.composition.r15_scheduler_runtime import R15SchedulerRuntime


class ServiceIntervalPublicationAuthority(SchedulerPublicationAuthority):
    """Private journal issuer for one signed automatic interval act."""

    def __init__(
        self,
        runtime: R15SchedulerRuntime,
        session: IntervalServiceSession,
        schedule_id: str,
        act_id: str,
        signature: bytes,
    ) -> None:
        self._runtime = runtime
        self._observed = session.observed
        self._session = session
        self._schedule_id = schedule_id
        self._act_id = act_id
        self._signature = signature
        self._is_genesis = False
        self._operations = ("scheduler.decide_interval",)
        self._command_id = service_interval_id(act_id, session.authority.principal_id)
        self._invocations = {}
        self._sources = []
        self._prepared = []
        self._originals: list[tuple[PublicationIdentity, OwnerCommandBytes]] = []
        self._evidence_fingerprints: dict[str, str] = {}
        self._clock_observation: TickClockObservation | None = None
        self._tick_clock = runtime._tick_clock
        self._policy_sources: SchedulerFreshSources | None = None

    def retain_original(self, batch: SingleOwnerBatch) -> None:
        evidence = validate_service_interval_batch(batch)
        if evidence.act_id != self._act_id or evidence.schedule_id != self._schedule_id:
            raise LoopRejected("service interval selected act differs")
        self._originals.append((batch.identity, batch.command))
        self._evidence_fingerprints[batch.command.fingerprint] = _digest(evidence.canonical_bytes())

    def retain_request(
        self, identity: PublicationIdentity, command: OwnerCommandBytes, evidence_bytes: bytes
    ) -> None:
        request = IntervalPreparationRequest.model_validate_json(command.canonical_bytes)
        if (
            identity.command_id != self._command_id
            or identity.command_fingerprint != _digest(request.command_bytes)
            or command.fingerprint != _digest(command.canonical_bytes)
            or command.schema_id != "chiplog.scheduler.interval-preparation.v1"
            or request.operation != "scheduler.decide_interval"
        ):
            raise LoopRejected("service interval private request binding differs")
        self._originals.append((identity, command))
        self._evidence_fingerprints[command.fingerprint] = _digest(evidence_bytes)

    def issue_invocation(
        self, identity: PublicationIdentity, command: OwnerCommandBytes
    ) -> InvocationProofRef:
        from chiplog.composition.r15_scheduler_registry import capture_generation_sources

        if (identity, command) not in self._originals:
            raise LoopRejected("service interval invocation lacks retained original")
        session = capture_generation_sources(self._runtime, self._observed).agent_session
        nonce = secrets.token_hex(32)
        proof = InvocationProofRef(
            issuance_id=nonce,
            issuance_fingerprint=_digest(
                _canonical(
                    {
                        "nonce": nonce,
                        "identity": identity.model_dump(mode="json"),
                        "command": command.model_dump(mode="json"),
                        "evidence": self._evidence_fingerprints[command.fingerprint],
                    }
                )
            ),
            broker_epoch=str(session.broker_epoch),
            broker_session=session.session_id,
            runtime_generation=session.generation_id,
            operation_subject=identity.command_id,
        )
        self._invocations[nonce] = (proof, identity, command, "scheduler.decide_interval")
        return proof

    def _matches_original(self, request: SingleOwnerBatch) -> bool:
        evidence = validate_service_interval_batch(request)
        return evidence.act_id == self._act_id and evidence.schedule_id == self._schedule_id

    def check_clock(self) -> None:
        original = self._clock_observation
        if original is None:
            raise LoopRejected("service interval clock observation absent")
        from chiplog.composition.r15_tick_clock_v1 import registered_clock_source

        runtime = cast("R15SchedulerRuntime", self._runtime)
        if runtime._tick_clock is not self._tick_clock:
            raise LoopRejected("service interval clock instance changed")
        if registered_clock_source(runtime._tick_clock) != original.source:
            raise LoopRejected("service interval clock source changed")
        now = runtime._tick_clock.observe()
        if (
            now.unix_ns < original.reading.unix_ns
            or now.monotonic_ns < original.reading.monotonic_ns
            or now.monotonic_ns >= original.valid_until_monotonic_ns
        ):
            raise LoopRejected("service interval clock regressed or expired")

    def check_prepared(
        self, prepared: PreparedOwnerPublication
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
        failure = super().check_prepared(prepared)
        if failure is not None:
            return failure
        try:
            self.check_clock()
            from chiplog.composition.r15_interval_service_auth import (
                verify_interval_service_request,
            )

            assert self._clock_observation is not None
            assert self._policy_sources is not None
            verify_interval_service_request(
                cast("R15SchedulerRuntime", self._runtime),
                self._session,
                self._schedule_id,
                self._act_id,
                self._signature,
                self._policy_sources,
                self._clock_observation,
            )
        except OSError, RuntimeError, ValueError, TypeError, KeyError:
            return "STALE"
        return None


@dataclass(frozen=True)
class SelectedIntervalCut:
    """Authenticated caller-captured cut; descriptive and never an authority token."""

    sources: SchedulerFreshSources
    configuration: ConfigurationSnapshot
    previous_due_boundary: DueCoordinate
    predecessor_interval: Absent | Present
    disposed: tuple[DisposedOccurrence, ...]


def capture_selected_interval_cut(
    authority: SchedulerPublicationAuthority, schedule_id: str
) -> SelectedIntervalCut:
    """Use the existing authority gate, journal and source registry for one read cut."""
    sources = authority.capture_fresh()
    configuration = configuration_snapshot(sources.startup, schedule_id)
    if (
        configuration.schedule is None
        or configuration.policy is None
        or configuration.bound is None
    ):
        raise LoopRejected("interval requires complete selected configuration")
    previous, predecessor, disposed = selected_interval_progress(sources.startup, schedule_id)
    return SelectedIntervalCut(sources, configuration, previous, predecessor, disposed)


def build_service_interval_request(
    selected: SelectedIntervalCut,
    source_cut: TickSourceCut,
    clock: TickClockObservation,
    context: SchedulerContextRef,
    command_id: str,
    issuance_nonce: str,
) -> IntervalPreparationRequest:
    """Prepare a complete owner input from one selected cut and broker-issued context.

    The caller must verify service signature and selected bounded mandate before
    calling. This pure builder grants no authority; writer admission repeats the
    exact cut, clock and mandate checks before selecting the batch.
    """
    config = selected.configuration
    assert config.schedule is not None and config.policy is not None and config.bound is not None
    if config.active_hold.kind != "ABSENT":
        raise LoopRejected("automatic interval cannot bypass active overflow hold")
    schedule, policy = schedule_head(config.schedule), policy_head(config.policy)
    enumeration_body = {
        "cut": source_cut.model_dump(mode="json"),
        "schedule": schedule.model_dump(mode="json"),
        "policy": policy.model_dump(mode="json"),
        "bound": config.bound.model_dump(mode="json"),
        "previous": selected.previous_due_boundary.model_dump(mode="json"),
        "predecessor": selected.predecessor_interval.model_dump(mode="json"),
    }
    frontier = _reference("r15-service-enumeration-v3", enumeration_body)
    proof = Present(head=frontier, fingerprint=_digest(_canonical(enumeration_body)))
    boundary = SchedulerEligibilityBoundary(
        schedule_definition_head=schedule,
        missed_occurrence_policy_head=policy,
        previous_due_boundary=selected.previous_due_boundary,
        cutoff_due_coordinate=DueCoordinate(
            coordinate_policy_version=clock.source.coordinate_codec,
            canonical_coordinate=str(clock.reading.unix_ns),
        ),
        enumeration_frontier=frontier,
        predecessor_interval=selected.predecessor_interval,
        canonicalization_version="chiplog.scheduler.canonical.v1",
    )
    measured = stream_definition(config, boundary, selected.disposed, proof)
    fence = PreRootDecisionFence(
        command_id=command_id,
        disposition=FirstPublication(
            decision=Absent(),
            expected_canonical_absence_manifest=_reference(
                "r15-service-absence-v3", source_cut.model_dump(mode="json")
            ),
        ),
        scheduler_authority_head=context.mandate_head,
        broker_generation=selected.sources.generations.broker_generation,
        runtime_generation=selected.sources.generations.runtime_graph_generation,
    )
    command = DecideIntervalCommand(
        identity=SchedulerCommandIdentity(
            command_id=command_id,
            schema_version="chiplog.scheduler.decide-interval.v1",
            canonicalization_version="chiplog.scheduler.canonical.v1",
        ),
        boundary=boundary,
        bound_head=config.bound,
        manifest=measured.evidence.manifest
        if measured.evidence.kind == "FULL_MANIFEST"
        else measured.evidence,
        publication_fence=fence,
    )
    registry = selected.sources.startup.registration.reference
    cut = BatchPreparationCut(
        tenant_id=source_cut.tenant_id,
        tenant_frontier=source_cut.tenant_frontier,
        materialization_commitment=source_cut.materialization_commitment,
        registry_head=registry.head,
        registry_fingerprint=registry.fingerprint,
        submission_id="r15-service-submission:" + issuance_nonce,
        authorized_context=context,
        authorized_command_fingerprint=_digest(command.canonical_bytes()),
        authority_proof=cut_proof(context, source_cut),
    )
    return IntervalPreparationRequest(
        operation="scheduler.decide_interval",
        context=context,
        command_bytes=command.canonical_bytes(),
        snapshot=IntervalPreparationSnapshot(
            cut=cut,
            configuration=config,
            disposed=selected.disposed,
            previous_due_boundary=selected.previous_due_boundary,
            predecessor_interval=selected.predecessor_interval,
            enumeration_frontier=frontier,
            enumeration_proof=proof,
            active_hold=None,
            publication_fence=fence,
            operator_proof=None,
        ),
    )


async def publish_service_interval(
    runtime: R15SchedulerRuntime,
    session: IntervalServiceSession,
    schedule_id: str,
    act_id: str,
    signature: bytes,
) -> BrokerPublicationResult:
    """One signed service act through isolated owner and selected physical writer."""
    from chiplog.capabilities.agent_loop.scheduler_preparation import (
        BATCH_SCHEMA,
        PreparedSchedulerBatch,
        prepare_interval_request,
    )
    from chiplog.composition.r15_interval_service_auth import (
        verify_interval_service_replay,
        verify_interval_service_request,
    )
    from chiplog.composition.r15_scheduler_registry import _frame
    from chiplog.composition.r15_tick_clock_v1 import registered_clock_source
    from chiplog.composition.r15_tick_runtime import _manifest, _source_cut

    authority = ServiceIntervalPublicationAuthority(
        runtime, session, schedule_id, act_id, signature
    )
    command_id = authority._command_id
    journal = runtime._owner_decisions()
    coordinator = BrokerPublicationCoordinator(runtime._appender, authority, journal)
    with runtime._authority_gate().hold():
        selected = journal.lookup(runtime._tenant_id, command_id)
        if selected is not None:
            original = selected.prepared.request
            if not isinstance(original, SingleOwnerBatch):
                raise LoopRejected("selected service interval envelope differs")
            evidence = validate_service_interval_batch(original)
            current = verify_interval_service_replay(
                runtime, session, schedule_id, act_id, signature
            )
            original_identity = json.loads(evidence.execution_identity_bytes)
            if (
                evidence.act_id != act_id
                or evidence.schedule_id != schedule_id
                or evidence.selected_policy != current.policy
                or original_identity != current.execution_identity.model_dump(mode="json")
            ):
                return PublicationRejected(
                    kind="CONFLICT",
                    tenant_id=runtime._tenant_id,
                    command_id=command_id,
                    reason="changed exact service interval intent",
                )
            authority.retain_original(original)
            invocation = authority.issue_invocation(original.identity, original.command)
            replay = coordinator.lookup_exact(
                ExactReplayQuery(
                    identity=original.identity,
                    operation=original.operation,
                    current_invocation=invocation,
                    original_commands=(original.command,),
                )
            )
            if isinstance(replay, JournalSelectedPublication):
                runtime._loop_snapshot()
                return replay
            if not isinstance(replay, PublicationRejected):
                raise LoopRejected("selected service interval replay unresolved")
            if replay.kind != "HOLD" or authority.materialization_state(selected) != "ABSENT":
                return replay
    if selected is not None:
        result = await coordinator.recover_selected(runtime._tenant_id, command_id)
        if isinstance(result, JournalSelectedPublication):
            runtime._loop_snapshot()
        return result
    selected_cut = capture_selected_interval_cut(authority, schedule_id)
    sources = selected_cut.sources
    source_cut = _source_cut(sources)
    agent_session = sources.generations.agent_session
    source = registered_clock_source(runtime._tick_clock)
    reading = runtime._tick_clock.observe()
    clock = TickClockObservation(
        source=source,
        observation_id="service-interval-clock:" + secrets.token_hex(24),
        broker_epoch=str(agent_session.broker_epoch),
        broker_session=agent_session.session_id,
        reading=reading,
        valid_until_monotonic_ns=min(
            reading.monotonic_ns + 30_000_000_000,
            session.expires_monotonic_ns,
            session.observed.request.budget.absolute_deadline_ns,
        ),
    )
    authority._clock_observation = clock
    service = verify_interval_service_request(
        runtime, session, schedule_id, act_id, signature, sources, clock
    )
    authority._policy_sources = sources
    nonce = secrets.token_hex(32)
    context = service_context_for(
        service.mandate,
        service.policy,
        service.execution_identity,
        service.signed_message,
        signature,
        sources.generations.evidence_bytes,
        source_cut,
        clock,
        nonce,
        session.session_id,
    )
    request = build_service_interval_request(
        selected_cut, source_cut, clock, context, command_id, nonce
    )
    require_mandate_members(request, service.mandate.mandate)
    sent = PublicPortCall(
        operation_id="scheduler.prepare_interval",
        request_id="service-interval:" + nonce,
        caller=BrokerSession(
            tenant_id=runtime._tenant_id,
            broker_epoch=agent_session.broker_epoch,
            generation_id=agent_session.generation_id,
            owner_id="broker",
            session_id=f"broker:{agent_session.generation_id}",
        ),
        callee=agent_session,
        schema_id="chiplog.scheduler.interval-preparation.v1",
        canonical_payload=request.canonical_bytes(),
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            policy_version=1,
            absolute_deadline_ns=session.observed.request.budget.absolute_deadline_ns,
        ),
    )
    response = await runtime._supervisor.runtime().call(sent)
    with runtime._authority_gate().hold():
        if authority.capture_fresh() != sources:
            raise LoopRejected("service interval source cut changed during owner preparation")
        authority.check_clock()
        verify_interval_service_request(
            runtime, session, schedule_id, act_id, signature, sources, clock
        )
        if isinstance(response, PublicPortRejected):
            raise LoopRejected("service interval owner rejected: " + response.failure.reason)
        if (
            response.request_id != sent.request_id
            or response.responder != sent.callee
            or response.schema_id != BATCH_SCHEMA
            or response.canonical_payload != prepare_interval_request(request).canonical_bytes()
        ):
            raise LoopRejected("service interval owner output differs")
        prepared = PreparedSchedulerBatch.model_validate_json(response.canonical_payload)
        generations = json.loads(sources.generations.evidence_bytes)
        evidence = IntervalServiceIssuanceEvidence(
            act_id=act_id,
            schedule_id=schedule_id,
            selected_mandate_bytes=service.mandate.canonical_bytes(),
            selected_policy=service.policy,
            execution_identity_bytes=service.execution_identity.canonical_bytes(),
            signed_message=service.signed_message,
            signature=signature,
            trust_request_frame=_canonical(generations["authentication"]["request"]),
            trust_response_frame=_canonical(generations["authentication"]["response"]),
            trust_observation_bytes=_canonical(generations["authentication"]["observation"]),
            generation_preimages=sources.generations.evidence_bytes,
            source_cut=source_cut,
            clock=clock,
            issuance_nonce=nonce,
            preparation=request,
            owner_request_frame=_canonical(_frame(sent)),
            owner_response_frame=_canonical(_frame(response)),
        )
        identity = PublicationIdentity(
            tenant_id=runtime._tenant_id,
            command_id=command_id,
            command_fingerprint=_digest(request.command_bytes),
            canonicalization_version="chiplog.owner-publication.v1",
        )
        owner_command = OwnerCommandBytes(
            owner="agent_loop",
            schema_id="chiplog.scheduler.interval-preparation.v1",
            canonical_bytes=request.canonical_bytes(),
            fingerprint=_digest(request.canonical_bytes()),
        )
        authority.retain_request(identity, owner_command, evidence.canonical_bytes())
        auth = WorkerAuthentication(
            invocation=authority.issue_invocation(identity, owner_command),
            applicability_schema=SERVICE_ISSUANCE_SCHEMA,
            applicability_bytes=evidence.canonical_bytes(),
            applicability_fingerprint=_digest(evidence.canonical_bytes()),
        )
        records = tuple(
            OwnerRecordBytes(
                owner="agent_loop",
                record_kind=row.record_kind,
                record_id=row.record_id,
                schema_id=row.schema_id,
                canonical_bytes=base64.b64decode(row.canonical_base64, validate=True),
                fingerprint=row.fingerprint,
            )
            for row in prepared.records
        )
        expected: AuthoritativeReadManifest = _manifest(request)
        values = dict(
            kind="SINGLE_OWNER",
            operation=request.operation,
            identity=identity.model_dump(mode="json"),
            authentication=auth.model_dump(mode="json"),
            expected=expected.model_dump(mode="json"),
            command=owner_command.model_dump(mode="json"),
            complete_records=[row.model_dump(mode="json") for row in records],
        )
        batch = SingleOwnerBatch(
            operation=request.operation,
            identity=identity,
            authentication=auth,
            expected=expected,
            command=owner_command,
            complete_records=records,
            complete_batch_fingerprint=_digest(_canonical(values)),
        )
        validate_service_interval_batch(batch)
        authority.register_prepared(batch, sources)
    result = await coordinator.commit(batch)
    if isinstance(result, JournalSelectedPublication):
        runtime._loop_snapshot()
    return result


def parse_selected_interval_result(
    raw: bytes,
) -> ScheduledIntervalDecision | SchedulerIntervalResolutionDecision:
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise LoopRejected("selected interval result is not an object")
    kind = body.get("kind")
    if kind == "INTERVAL_DECISION":
        return ScheduledIntervalDecision.model_validate_json(raw)
    if kind == "INTERVAL_RESOLUTION":
        return SchedulerIntervalResolutionDecision.model_validate_json(raw)
    raise LoopRejected("selected interval result kind is unknown")


def selected_interval_progress(
    startup: AdmittedSchedulerStartup, schedule_id: str
) -> tuple[DueCoordinate, Absent | Present, tuple[DisposedOccurrence, ...]]:
    """Return exact boundary, predecessor and disposed heads from selected history."""
    genesis = [item for item in startup.index.registered_genesis if item[0] == schedule_id]
    if len(genesis) != 1:
        raise LoopRejected("interval requires one registered initial boundary")
    previous = genesis[0][1]
    predecessor: Absent | Present = Absent()
    disposed: list[DisposedOccurrence] = []
    for batch in startup.cut.selected:
        for record in batch.records:
            if record.record_kind != "interval-result":
                continue
            result = parse_selected_interval_result(
                base64.b64decode(record.canonical_base64, validate=True)
            )
            boundary = result.boundary
            if boundary.schedule_definition_head.schedule_id != schedule_id:
                continue
            if (
                boundary.previous_due_boundary != previous
                or boundary.predecessor_interval != predecessor
            ):
                raise LoopRejected("selected interval predecessor chain differs")
            previous, predecessor = result.resulting_boundary, result.decision
            for disposition in result.dispositions:
                member = disposition.occurrence
                exact = [
                    selected
                    for selected in batch.records
                    if selected.record_kind == "occurrence-disposition"
                    and json.loads(base64.b64decode(selected.canonical_base64, validate=True))
                    == disposition.model_dump(mode="json")
                ]
                if len(exact) != 1:
                    raise LoopRejected("disposed occurrence lacks exact selected record")
                selected = exact[0]
                disposed.append(
                    DisposedOccurrence(
                        occurrence_id=member.occurrence_id,
                        due_coordinate=member.due_coordinate,
                        disposition=Present(
                            head=selected.record_id,
                            fingerprint=selected.fingerprint,
                        ),
                    )
                )
    return previous, predecessor, tuple(disposed)
