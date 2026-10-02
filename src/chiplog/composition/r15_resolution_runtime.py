"""One adopted overflow resolution through the isolated owner and sole writer."""

from __future__ import annotations

import base64
import json
import secrets
from typing import TYPE_CHECKING

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    FirstPublication,
    PreRootDecisionFence,
)
from chiplog.capabilities.agent_loop.scheduler_configuration import (
    policy_head,
    schedule_head,
    stream_definition,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    ResolveIntervalCommand,
    SchedulerCommandIdentity,
    SchedulerOverflowHold,
)
from chiplog.capabilities.agent_loop.scheduler_preparation import (
    BATCH_SCHEMA,
    BatchPreparationCut,
    IntervalPreparationRequest,
    IntervalPreparationSnapshot,
    PreparedSchedulerBatch,
    prepare_interval_request,
)
from chiplog.composition.r15_interval_service import (
    SERVICE_ISSUANCE_SCHEMA,
    selected_interval_progress,
    validate_service_interval_batch,
)
from chiplog.composition.r15_resolution_authority import ResolutionPublicationAuthority
from chiplog.composition.r15_resolution_contracts import (
    ResolutionIssuanceEvidence,
    ResolutionPolicy,
    ResolutionPolicyAdoption,
    ResolutionPolicyDraft,
    ResolutionPolicyPreview,
)
from chiplog.composition.r15_resolution_evidence import (
    ISSUANCE_SCHEMA,
    PREPARATION_SCHEMA,
    adopted,
    context_for,
    cut_proof,
    operator_proof,
    resolution_id,
    validate_resolution_batch,
)
from chiplog.composition.r15_scheduler_history import configuration_snapshot, read_loop_snapshot
from chiplog.composition.r15_scheduler_publication import _authenticate
from chiplog.composition.r15_scheduler_registry import _canonical, _digest, _frame, _reference
from chiplog.composition.r15_tick_evidence import ISSUANCE_SCHEMA as TICK_ISSUANCE_SCHEMA
from chiplog.composition.r15_tick_evidence import validate_tick_batch
from chiplog.composition.r15_tick_runtime import _source_cut
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    BrokerPublicationResult,
    ExactRecordHead,
    ExactReplayQuery,
    JournalSelectedPublication,
    ObservedPresence,
    OwnerCommandBytes,
    OwnerRecordBytes,
    PublicationIdentity,
    PublicationRejected,
    SingleOwnerBatch,
    WorkerAuthentication,
)
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortRejected
from chiplog.platform.owner_publications import BrokerPublicationCoordinator

if TYPE_CHECKING:
    from chiplog.composition.r15_scheduler_authority import SchedulerFreshSources
    from chiplog.composition.r15_scheduler_runtime import R15SchedulerRuntime


async def preview_resolution(
    runtime: R15SchedulerRuntime, peer: str, draft: ResolutionPolicyDraft
) -> ResolutionPolicyPreview:
    observed = await _authenticate(runtime, peer)
    authority = ResolutionPublicationAuthority(
        runtime,
        observed,
        ResolutionPolicyAdoption(adoption_act_id=draft.adoption_act_id, policy_bytes=b"preview"),
    )
    sources = authority.capture_fresh()
    current = configuration_snapshot(sources.startup, draft.schedule_id)
    hold = _active_hold(sources, draft.schedule_id)
    if current.schedule is None or current.policy is None or current.bound is None:
        raise LoopRejected("resolution configuration incomplete")
    if (
        current.bound.generation <= hold.bound_head.generation
        or current.bound.head_id == hold.bound_head.head_id
    ):
        raise LoopRejected("resolution requires successor interval bound")
    return ResolutionPolicyPreview(
        adoption_act_id=draft.adoption_act_id,
        schedule=schedule_head(current.schedule),
        missed_policy=policy_head(current.policy),
        successor_bound=current.bound,
        active_hold=hold.hold,
        held_boundary=hold.boundary,
        policy=ResolutionPolicy(),
    )


def _active_hold(sources: SchedulerFreshSources, schedule_id: str) -> SchedulerOverflowHold:
    holds = tuple(
        hold
        for hold in sources.startup.index.active_holds
        if hold.boundary.schedule_definition_head.schedule_id == schedule_id
    )
    if len(holds) != 1:
        raise LoopRejected("resolution requires one active overflow hold")
    return holds[0]


def _original_hold_request(
    sources: SchedulerFreshSources, hold_id: str
) -> IntervalPreparationRequest:
    matching = [
        historical.selection.prepared.request
        for historical in sources.startup.cut.historical_requests
        if historical.selection.prepared.request.operation == "scheduler.decide_interval"
        and any(
            record.record_id == hold_id
            for record in historical.selection.prepared.request.complete_records
        )
    ]
    if len(matching) != 1:
        raise LoopRejected("active hold lacks one selected original interval")
    selected = matching[0]
    if not isinstance(selected.authentication, WorkerAuthentication):
        raise LoopRejected("active hold original interval authentication differs")
    schema = selected.authentication.applicability_schema
    if schema == TICK_ISSUANCE_SCHEMA:
        validate_tick_batch(selected)
    elif schema == SERVICE_ISSUANCE_SCHEMA:
        validate_service_interval_batch(selected)
    else:
        raise LoopRejected("active hold has unknown original interval issuance")
    return IntervalPreparationRequest.model_validate_json(selected.command.canonical_bytes)


def _request(
    adoption: ResolutionPolicyAdoption,
    sources: SchedulerFreshSources,
    nonce: str,
) -> IntervalPreparationRequest:
    preview = adopted(adoption)
    current = configuration_snapshot(sources.startup, preview.schedule.schedule_id)
    hold = _active_hold(sources, preview.schedule.schedule_id)
    if current.schedule is None or current.policy is None or current.bound is None:
        raise LoopRejected("resolution configuration incomplete")
    if (
        schedule_head(current.schedule) != preview.schedule
        or policy_head(current.policy) != preview.missed_policy
        or current.bound != preview.successor_bound
        or hold.hold != preview.active_hold
        or hold.boundary != preview.held_boundary
        or current.active_hold != hold.hold
    ):
        raise LoopRejected("resolution adopted heads or active hold changed")
    original = _original_hold_request(sources, hold.hold.head)
    previous, predecessor, disposed = selected_interval_progress(
        sources.startup, preview.schedule.schedule_id
    )
    boundary = hold.boundary
    if (
        boundary.previous_due_boundary != previous
        or boundary.predecessor_interval != predecessor
        or original.snapshot.enumeration_frontier != boundary.enumeration_frontier
    ):
        raise LoopRejected("held boundary differs from current selected progress")
    proof = original.snapshot.enumeration_proof
    measured = stream_definition(current, boundary, disposed, proof)
    if measured.evidence.kind != "FULL_MANIFEST":
        raise LoopRejected("successor bound still cannot hold complete manifest")
    cut = _source_cut(sources)
    context = context_for(adoption, sources.generations.evidence_bytes, cut, nonce)
    fence = PreRootDecisionFence(
        command_id=resolution_id(adoption.adoption_act_id),
        disposition=FirstPublication(
            decision=Absent(),
            expected_canonical_absence_manifest=_reference(
                "r15-resolution-absence-v1",
                {"cut": cut.model_dump(mode="json"), "hold": hold.hold.model_dump(mode="json")},
            ),
        ),
        scheduler_authority_head=context.mandate_head,
        broker_generation=_reference(
            "r15-broker-generation-v1",
            json.loads(sources.generations.evidence_bytes)["broker_generation_preimage"],
        ),
        runtime_generation=_reference(
            "r15-runtime-graph-v1",
            json.loads(sources.generations.evidence_bytes)["runtime_graph_preimage"],
        ),
    )
    command = ResolveIntervalCommand(
        identity=SchedulerCommandIdentity(
            command_id=fence.command_id,
            schema_version="chiplog.scheduler.resolve-interval.v1",
            canonicalization_version="chiplog.scheduler.canonical.v1",
        ),
        active_hold=hold,
        successor_bound=current.bound,
        boundary=boundary,
        complete_manifest=measured.evidence.manifest,
        operator_proof=operator_proof(adoption, context),
        publication_fence=fence,
    )
    registry = sources.startup.registration.reference
    owner_cut = BatchPreparationCut(
        tenant_id=cut.tenant_id,
        tenant_frontier=cut.tenant_frontier,
        materialization_commitment=cut.materialization_commitment,
        registry_head=registry.head,
        registry_fingerprint=registry.fingerprint,
        submission_id="r15-resolution-submission:" + nonce,
        authorized_context=context,
        authorized_command_fingerprint=_digest(command.canonical_bytes()),
        authority_proof=cut_proof(context, cut),
    )
    return IntervalPreparationRequest(
        operation="scheduler.resolve_interval",
        context=context,
        command_bytes=command.canonical_bytes(),
        snapshot=IntervalPreparationSnapshot(
            cut=owner_cut,
            configuration=current,
            disposed=disposed,
            previous_due_boundary=previous,
            predecessor_interval=predecessor,
            enumeration_frontier=boundary.enumeration_frontier,
            enumeration_proof=proof,
            active_hold=hold,
            publication_fence=fence,
            operator_proof=command.operator_proof,
        ),
    )


def _manifest(request: IntervalPreparationRequest) -> AuthoritativeReadManifest:
    cut, config = request.snapshot.cut, request.snapshot.configuration
    if (
        config.schedule is None
        or config.policy is None
        or config.bound is None
        or request.snapshot.active_hold is None
    ):
        raise LoopRejected("resolution read manifest lacks current heads")
    schedule, policy = schedule_head(config.schedule), policy_head(config.policy)
    hold = request.snapshot.active_hold
    ordered = tuple(
        ObservedPresence(
            head=ExactRecordHead(
                owner="agent_loop",
                record_kind=kind,
                subject_id=schedule.schedule_id,
                record_id=identity,
                fingerprint=fingerprint,
            )
        )
        for kind, identity, fingerprint in (
            ("scheduler.schedule-definition", schedule.head, schedule.fingerprint),
            ("scheduler.missed-policy", policy.head, policy.fingerprint),
            (
                "scheduler.interval-bound",
                config.bound.head_id,
                _digest(config.bound.canonical_bytes()),
            ),
            ("overflow-hold", hold.hold.head, hold.hold.fingerprint),
        )
    )
    values = dict(
        tenant_id=cut.tenant_id,
        tenant_frontier=cut.tenant_frontier,
        expected_materialization_commitment=cut.materialization_commitment,
        registry_head=cut.registry_head,
        registry_fingerprint=cut.registry_fingerprint,
        ordered_heads=[head.model_dump(mode="json") for head in ordered],
    )
    return AuthoritativeReadManifest.model_validate(
        {
            **values,
            "ordered_heads": ordered,
            "complete_manifest_fingerprint": _digest(_canonical(values)),
        }
    )


async def publish_resolution(
    runtime: R15SchedulerRuntime, peer: str, adoption: ResolutionPolicyAdoption
) -> BrokerPublicationResult:
    adoption = ResolutionPolicyAdoption.model_validate_json(adoption.canonical_bytes())
    observed = await _authenticate(runtime, peer)
    authority = ResolutionPublicationAuthority(runtime, observed, adoption)
    journal = runtime._owner_decisions()
    coordinator = BrokerPublicationCoordinator(runtime._appender, authority, journal)
    command_id = resolution_id(adoption.adoption_act_id)
    with runtime._authority_gate().hold():
        selected = journal.lookup(runtime._tenant_id, command_id)
        if selected is not None:
            original = selected.prepared.request
            if not isinstance(original, SingleOwnerBatch):
                raise LoopRejected("resolution selected envelope is not single owner")
            evidence = validate_resolution_batch(original)
            if evidence.adoption != adoption:
                return PublicationRejected(
                    kind="CONFLICT",
                    tenant_id=runtime._tenant_id,
                    command_id=command_id,
                    reason="changed resolution adoption",
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
                read_loop_snapshot(runtime)
                return replay
            if not isinstance(replay, PublicationRejected):
                raise LoopRejected("selected resolution has no replay interpretation")
            if replay.kind != "HOLD" or authority.materialization_state(selected) != "ABSENT":
                return replay
    if selected is not None:
        result = await coordinator.recover_selected(runtime._tenant_id, command_id)
        if isinstance(result, JournalSelectedPublication):
            read_loop_snapshot(runtime)
        return result
    sources = authority.capture_fresh()
    nonce = secrets.token_hex(32)
    request = _request(adoption, sources, nonce)
    session = sources.generations.agent_session
    sent = PublicPortCall(
        operation_id="scheduler.prepare_interval",
        request_id="resolution:" + nonce,
        caller=BrokerSession(
            tenant_id=runtime._tenant_id,
            broker_epoch=session.broker_epoch,
            generation_id=session.generation_id,
            owner_id="broker",
            session_id=f"broker:{session.generation_id}",
        ),
        callee=session,
        schema_id=PREPARATION_SCHEMA,
        canonical_payload=request.canonical_bytes(),
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            policy_version=1,
            absolute_deadline_ns=observed.request.budget.absolute_deadline_ns,
        ),
    )
    response = await runtime._supervisor.runtime().call(sent)
    with runtime._authority_gate().hold():
        if authority.capture_fresh() != sources:
            raise LoopRejected("resolution source cut changed during owner preparation")
        if isinstance(response, PublicPortRejected):
            raise LoopRejected("resolution owner rejected: " + response.failure.reason)
        if (
            response.request_id != sent.request_id
            or response.responder != sent.callee
            or response.schema_id != BATCH_SCHEMA
            or response.canonical_payload != prepare_interval_request(request).canonical_bytes()
        ):
            raise LoopRejected("resolution owner response differs from complete preparation")
        prepared = PreparedSchedulerBatch.model_validate_json(response.canonical_payload)
        evidence = ResolutionIssuanceEvidence(
            adoption=adoption,
            trust_preimages=_canonical(
                json.loads(sources.generations.evidence_bytes)["authentication"]
            ),
            generation_preimages=sources.generations.evidence_bytes,
            source_cut=_source_cut(sources),
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
            schema_id=PREPARATION_SCHEMA,
            canonical_bytes=request.canonical_bytes(),
            fingerprint=_digest(request.canonical_bytes()),
        )
        authority.retain_request(identity, owner_command, evidence.canonical_bytes())
        invocation = authority.issue_invocation(identity, owner_command)
        auth = WorkerAuthentication(
            invocation=invocation,
            applicability_schema=ISSUANCE_SCHEMA,
            applicability_bytes=evidence.canonical_bytes(),
            applicability_fingerprint=_digest(evidence.canonical_bytes()),
        )
        records = tuple(
            OwnerRecordBytes(
                owner="agent_loop",
                record_kind=item.record_kind,
                record_id=item.record_id,
                schema_id=item.schema_id,
                canonical_bytes=base64.b64decode(item.canonical_base64, validate=True),
                fingerprint=item.fingerprint,
            )
            for item in prepared.records
        )
        expected = _manifest(request)
        values = dict(
            kind="SINGLE_OWNER",
            operation=request.operation,
            identity=identity.model_dump(mode="json"),
            authentication=auth.model_dump(mode="json"),
            expected=expected.model_dump(mode="json"),
            command=owner_command.model_dump(mode="json"),
            complete_records=[record.model_dump(mode="json") for record in records],
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
        validate_resolution_batch(batch)
        authority.register_prepared(batch, sources)
    result = await coordinator.commit(batch)
    if isinstance(result, JournalSelectedPublication):
        read_loop_snapshot(runtime)
    return result
