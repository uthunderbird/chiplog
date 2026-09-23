"""One explicitly adopted tick through real clock, isolated owner and sole writer."""

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
    Present,
)
from chiplog.capabilities.agent_loop.scheduler_configuration import (
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
    SchedulerEligibilityBoundary,
)
from chiplog.capabilities.agent_loop.scheduler_preparation import (
    BATCH_SCHEMA,
    BatchPreparationCut,
    IntervalPreparationRequest,
    IntervalPreparationSnapshot,
    PreparedSchedulerBatch,
    prepare_interval_request,
)
from chiplog.composition.r15_scheduler_history import configuration_snapshot, read_loop_snapshot
from chiplog.composition.r15_scheduler_publication import _authenticate
from chiplog.composition.r15_scheduler_registry import (
    _canonical,
    _digest,
    _frame,
    _reference,
)
from chiplog.composition.r15_tick_authority import TickPublicationAuthority
from chiplog.composition.r15_tick_clock_v1 import registered_clock_source
from chiplog.composition.r15_tick_contracts import (
    TickClockObservation,
    TickIssuanceEvidence,
    TickPolicyAdoption,
    TickPolicyDraft,
    TickPolicyPreview,
    TickSourceCut,
)
from chiplog.composition.r15_tick_evidence import (
    ISSUANCE_SCHEMA,
    PREPARATION_SCHEMA,
    adopted,
    context_for,
    cut_proof,
    enumeration,
    tick_id,
    tick_policy,
    validate_tick_batch,
)
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    BrokerPublicationResult,
    ExactRecordHead,
    ExactReplayQuery,
    JournalSelectedPublication,
    ObservedAbsence,
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


async def preview_tick(
    runtime: R15SchedulerRuntime, peer: str, draft: TickPolicyDraft
) -> TickPolicyPreview:
    observed = await _authenticate(runtime, peer)
    # A preview creates neither tick permission nor private clock observation.
    authority = TickPublicationAuthority(
        runtime,
        observed,
        TickPolicyAdoption(adoption_act_id=draft.adoption_act_id, policy_bytes=b"preview"),
    )
    sources = authority.capture_fresh()
    current = configuration_snapshot(sources.startup, draft.schedule_id)
    if current.schedule is None or current.policy is None or current.bound is None:
        raise LoopRejected("tick configuration incomplete")
    return TickPolicyPreview(
        schema_id="chiplog.scheduler.tick-policy-adoption.v1",
        adoption_act_id=draft.adoption_act_id,
        delivery_id=draft.delivery_id,
        schedule=schedule_head(current.schedule),
        missed_policy=policy_head(current.policy),
        bound=current.bound,
        policy=tick_policy(registered_clock_source(runtime._tick_clock)),
    )


def _source_cut(sources: SchedulerFreshSources) -> TickSourceCut:
    cut = sources.startup.cut
    if cut.independent_journal_head is None:
        raise LoopRejected("tick requires selected configuration journal")
    return TickSourceCut(
        tenant_id=cut.tenant_id,
        database_id="hermetic-database",
        database_path=cut.physical_database_path,
        database_device=cut.physical_device,
        database_inode=cut.physical_inode,
        tenant_frontier=cut.tenant_frontier,
        materialization_commitment=cut.materialization_commitment,
        independent_journal_head=cut.independent_journal_head,
        deletion_fence_generation=sources.fence_generation,
        deletion_fence_frontier=sources.fence_frontier,
    )


def _progress(
    sources: SchedulerFreshSources, preview: TickPolicyPreview
) -> tuple[DueCoordinate, Absent | Present, tuple[DisposedOccurrence, ...]]:
    """Project exhaustive admitted history; historical validator has already checked it."""
    genesis = [
        item
        for item in sources.startup.index.registered_genesis
        if item[0] == preview.schedule.schedule_id
    ]
    if len(genesis) != 1:
        raise LoopRejected("tick requires one registered initial boundary")
    previous = genesis[0][1]
    predecessor: Absent | Present = Absent()
    disposed: list[DisposedOccurrence] = []
    for batch in sources.startup.cut.selected:
        for record in batch.records:
            if record.record_kind != "interval-result":
                continue
            result = ScheduledIntervalDecision.model_validate_json(
                base64.b64decode(record.canonical_base64, validate=True)
            )
            boundary = result.boundary
            if boundary.schedule_definition_head.schedule_id != preview.schedule.schedule_id:
                continue
            previous, predecessor = result.resulting_boundary, result.decision
            if boundary.schedule_definition_head != preview.schedule:
                continue
            for disposition in result.dispositions:
                member = disposition.occurrence
                exact_disposition = [
                    record
                    for record in batch.records
                    if record.record_kind == "occurrence-disposition"
                    and json.loads(base64.b64decode(record.canonical_base64, validate=True))
                    == disposition.model_dump(mode="json")
                ]
                if len(exact_disposition) != 1:
                    raise LoopRejected("disposed occurrence lacks its exact selected record")
                selected_disposition = exact_disposition[0]
                disposed.append(
                    DisposedOccurrence(
                        occurrence_id=member.occurrence_id,
                        due_coordinate=member.due_coordinate,
                        disposition=Present(
                            head=selected_disposition.record_id,
                            fingerprint=selected_disposition.fingerprint,
                        ),
                    )
                )
    return previous, predecessor, tuple(disposed)


def _request(
    adoption: TickPolicyAdoption,
    sources: SchedulerFreshSources,
    clock: TickClockObservation,
    nonce: str,
) -> IntervalPreparationRequest:
    preview = adopted(adoption)
    current = configuration_snapshot(sources.startup, preview.schedule.schedule_id)
    if current.schedule is None or current.policy is None or current.bound is None:
        raise LoopRejected("tick configuration incomplete")
    if (schedule_head(current.schedule), policy_head(current.policy), current.bound) != (
        preview.schedule,
        preview.missed_policy,
        preview.bound,
    ):
        raise LoopRejected("tick adopted configuration is stale")
    if current.active_hold.kind != "ABSENT":
        raise LoopRejected("tick cannot bypass active overflow hold")
    source_cut = _source_cut(sources)
    context = context_for(adoption, sources.generations.evidence_bytes, source_cut, clock, nonce)
    previous, predecessor, disposed = _progress(sources, preview)
    frontier, proof = enumeration(source_cut, preview)
    boundary = SchedulerEligibilityBoundary(
        schedule_definition_head=preview.schedule,
        missed_occurrence_policy_head=preview.missed_policy,
        previous_due_boundary=previous,
        cutoff_due_coordinate=DueCoordinate(
            coordinate_policy_version=clock.source.coordinate_codec,
            canonical_coordinate=str(clock.reading.unix_ns),
        ),
        enumeration_frontier=frontier,
        predecessor_interval=predecessor,
        canonicalization_version="chiplog.scheduler.canonical.v1",
    )
    measured = stream_definition(current, boundary, disposed, proof)
    fence = PreRootDecisionFence(
        command_id=tick_id(adoption.adoption_act_id),
        disposition=FirstPublication(
            decision=Absent(),
            expected_canonical_absence_manifest=_reference(
                "r15-tick-absence-v1", source_cut.model_dump(mode="json")
            ),
        ),
        scheduler_authority_head=context.mandate_head,
        broker_generation=sources.generations.broker_generation,
        runtime_generation=sources.generations.runtime_graph_generation,
    )
    command = DecideIntervalCommand(
        identity=SchedulerCommandIdentity(
            command_id=fence.command_id,
            schema_version="chiplog.scheduler.decide-interval.v1",
            canonicalization_version="chiplog.scheduler.canonical.v1",
        ),
        boundary=boundary,
        bound_head=preview.bound,
        manifest=measured.evidence.manifest
        if measured.evidence.kind == "FULL_MANIFEST"
        else measured.evidence,
        publication_fence=fence,
    )
    registry = sources.startup.registration.reference
    cut = BatchPreparationCut(
        tenant_id=source_cut.tenant_id,
        tenant_frontier=source_cut.tenant_frontier,
        materialization_commitment=source_cut.materialization_commitment,
        registry_head=registry.head,
        registry_fingerprint=registry.fingerprint,
        submission_id="r15-tick-submission:" + nonce,
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
            configuration=current,
            disposed=disposed,
            previous_due_boundary=previous,
            predecessor_interval=predecessor,
            enumeration_frontier=frontier,
            enumeration_proof=proof,
            active_hold=None,
            publication_fence=fence,
            operator_proof=None,
        ),
    )


def _manifest(request: IntervalPreparationRequest) -> AuthoritativeReadManifest:
    cut, config = request.snapshot.cut, request.snapshot.configuration
    assert config.schedule is not None and config.policy is not None and config.bound is not None
    schedule, policy = schedule_head(config.schedule), policy_head(config.policy)
    heads = tuple(
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
        )
    )
    ordered = (
        *heads,
        ObservedAbsence(
            owner="agent_loop", record_kind="overflow-hold", subject_id=schedule.schedule_id
        ),
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


async def publish_tick(
    runtime: R15SchedulerRuntime, peer: str, adoption: TickPolicyAdoption
) -> BrokerPublicationResult:
    adoption = TickPolicyAdoption.model_validate_json(adoption.canonical_bytes())
    observed = await _authenticate(runtime, peer)
    authority = TickPublicationAuthority(runtime, observed, adoption)
    journal = runtime._owner_decisions()
    coordinator = BrokerPublicationCoordinator(runtime._appender, authority, journal)
    with runtime._authority_gate().hold():
        selected = journal.lookup(runtime._tenant_id, tick_id(adoption.adoption_act_id))
        if selected is not None:
            original = selected.prepared.request
            if not isinstance(original, SingleOwnerBatch):
                raise LoopRejected("tick selected envelope is not single owner")
            evidence = validate_tick_batch(original)
            if evidence.adoption != adoption:
                return PublicationRejected(
                    kind="CONFLICT",
                    tenant_id=runtime._tenant_id,
                    command_id=original.identity.command_id,
                    reason="changed exact tick adoption",
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
                raise LoopRejected("selected tick has no replay interpretation")
            if replay.kind != "HOLD" or authority.materialization_state(selected) != "ABSENT":
                return replay
    if selected is not None:
        result = await coordinator.recover_selected(
            runtime._tenant_id, tick_id(adoption.adoption_act_id)
        )
        if isinstance(result, JournalSelectedPublication):
            read_loop_snapshot(runtime)
        return result
    preview = adopted(adoption)
    source = registered_clock_source(runtime._tick_clock)
    if preview.policy.clock_source != source:
        raise LoopRejected("tick adopted clock source changed")
    sources = authority.capture_fresh()
    session = sources.generations.agent_session
    reading = runtime._tick_clock.observe()
    clock = TickClockObservation(
        source=source,
        observation_id="tick-clock:" + secrets.token_hex(24),
        broker_epoch=str(session.broker_epoch),
        broker_session=session.session_id,
        reading=reading,
        valid_until_monotonic_ns=min(
            reading.monotonic_ns + 30_000_000_000, observed.request.budget.absolute_deadline_ns
        ),
    )
    authority._clock_observation = clock
    nonce = secrets.token_hex(32)
    request = _request(adoption, sources, clock, nonce)
    sent = PublicPortCall(
        operation_id="scheduler.prepare_interval",
        request_id="tick:" + nonce,
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
            raise LoopRejected("tick source cut changed during owner preparation")
        authority.check_clock()
        if isinstance(response, PublicPortRejected):
            raise LoopRejected("tick owner rejected: " + response.failure.reason)
        if (
            response.request_id != sent.request_id
            or response.responder != sent.callee
            or response.schema_id != BATCH_SCHEMA
            or response.canonical_payload != prepare_interval_request(request).canonical_bytes()
        ):
            raise LoopRejected("tick owner response differs from complete original preparation")
        prepared = PreparedSchedulerBatch.model_validate_json(response.canonical_payload)
        generations = json.loads(sources.generations.evidence_bytes)
        evidence = TickIssuanceEvidence(
            schema_id="chiplog.scheduler.tick-issuance.v1",
            adoption=adoption,
            registered_policy=preview.policy,
            trust_request_frame=_canonical(generations["authentication"]["request"]),
            trust_response_frame=_canonical(generations["authentication"]["response"]),
            trust_observation_bytes=_canonical(generations["authentication"]["observation"]),
            generation_preimages=sources.generations.evidence_bytes,
            source_cut=_source_cut(sources),
            clock=clock,
            issuance_nonce=nonce,
            preparation=request,
            owner_request_frame=_canonical(_frame(sent)),
            owner_response_frame=_canonical(_frame(response)),
        )
        identity = PublicationIdentity(
            tenant_id=runtime._tenant_id,
            command_id=tick_id(adoption.adoption_act_id),
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
                record_kind=r.record_kind,
                record_id=r.record_id,
                schema_id=r.schema_id,
                canonical_bytes=base64.b64decode(r.canonical_base64, validate=True),
                fingerprint=r.fingerprint,
            )
            for r in prepared.records
        )
        expected = _manifest(request)
        values = dict(
            kind="SINGLE_OWNER",
            operation=request.operation,
            identity=identity.model_dump(mode="json"),
            authentication=auth.model_dump(mode="json"),
            expected=expected.model_dump(mode="json"),
            command=owner_command.model_dump(mode="json"),
            complete_records=[r.model_dump(mode="json") for r in records],
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
        validate_tick_batch(batch)
        authority.register_prepared(batch, sources)
    result = await coordinator.commit(batch)
    if isinstance(result, JournalSelectedPublication):
        read_loop_snapshot(runtime)
    return result
