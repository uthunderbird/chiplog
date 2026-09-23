"""Actual authenticated GENESIS adoption through the isolated owner and sole writer."""

from __future__ import annotations

import base64
import json
import os
import secrets
from typing import TYPE_CHECKING, Literal

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.agent_loop.scheduler_configuration import (
    ConfigurationSnapshot,
    policy_head,
    schedule_head,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import SchedulerContextRef
from chiplog.capabilities.agent_loop.scheduler_preparation import (
    BATCH_SCHEMA,
    BatchPreparationCut,
    ConfigurationGenesisCommand,
    ConfigurationPreparationRequest,
    ConfigurationPreparationSnapshot,
    PreparedSchedulerBatch,
    prepare_configuration,
)
from chiplog.composition.r15_scheduler_authority import (
    SchedulerFreshSources,
    SchedulerPublicationAuthority,
)
from chiplog.composition.r15_scheduler_history import configuration_snapshot, read_loop_snapshot
from chiplog.composition.r15_scheduler_registry import (
    GENESIS_SERVICE_IDENTITY,
    ConfigurationTransitionCommand,
    SchedulerConfigurationAdoption,
    SchedulerConfigurationDraft,
    SchedulerGenesisAdoption,
    SchedulerGenesisDraft,
    _canonical,
    _command_id,
    _digest,
    _frame,
    _policy_bytes,
    _reference,
    capture_generation_sources,
    command_from_draft,
    configuration_authority_head,
    configuration_authority_preimage,
    configuration_command_from_draft,
    configuration_command_id,
    configuration_policy_bytes,
    validate_configuration_adoption,
    validate_fresh_adoption,
)
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    BrokerPublicationResult,
    ExactRecordHead,
    ExactReplayQuery,
    JournalSelectedPublication,
    ObservedAbsence,
    ObservedHead,
    ObservedPresence,
    OwnerCommandBytes,
    OwnerRecordBytes,
    PublicationIdentity,
    PublicationRejected,
    SingleOwnerBatch,
    WorkerAuthentication,
)
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortRejected
from chiplog.platform.owner_publications import BrokerPublicationCoordinator, source_commands

if TYPE_CHECKING:
    from chiplog.composition.r7_planning import ObservedTrustCall
    from chiplog.composition.r14_runtime import R14PlanningRuntime

_APPLICABILITY_SCHEMA = "chiplog.scheduler.genesis-issuance.v1"
_PREPARATION_SCHEMA = "chiplog.scheduler.configuration-preparation.v1"
_CONFIGURATION_APPLICABILITY_SCHEMA = "chiplog.scheduler.configuration-issuance.v1"


async def _authenticate(runtime: R14PlanningRuntime, peer: str) -> ObservedTrustCall:
    if peer != "hermetic-ingress":
        raise LoopRejected("scheduler configuration requires registered CLI ingress")
    # Only this actual invocation enters our private authority. Neither API accepts
    # caller-supplied ObservedTrustCall, SchedulerContextRef or prepared output.
    observed = await runtime._observed_trust_call(
        "AUTHENTICATE",
        {
            "contour": "CLI",
            "credential_id": "hermetic-credential",
            "peer_credential": f"uid:{os.getuid()}",
            "session_id": "hermetic-session",
        },
    )
    capture_generation_sources(runtime, observed)
    return observed


async def preview_genesis(
    runtime: R14PlanningRuntime, peer: str, draft: SchedulerGenesisDraft
) -> ConfigurationGenesisCommand:
    observed = await _authenticate(runtime, peer)
    return command_from_draft(draft, capture_generation_sources(runtime, observed))


async def preview_configuration(
    runtime: R14PlanningRuntime, peer: str, draft: SchedulerConfigurationDraft
) -> ConfigurationTransitionCommand:
    observed = await _authenticate(runtime, peer)
    # Captured sources are issuer-owned; this dummy transport grants no invocation.
    authority = SchedulerPublicationAuthority(
        runtime,
        observed,
        SchedulerConfigurationAdoption(
            adoption_act_id=draft.adoption_act_id, command_bytes=b"preview"
        ),
    )
    sources = authority.capture_fresh()
    current = configuration_snapshot(sources.startup, draft.schedule_id)
    return configuration_command_from_draft(draft, current, sources.generations)


def _preparation(
    adoption: SchedulerGenesisAdoption | SchedulerConfigurationAdoption,
    sources: SchedulerFreshSources,
) -> tuple[ConfigurationPreparationRequest, dict[str, object]]:
    startup, generations = sources.startup, sources.generations
    operation: Literal[
        "scheduler.genesis",
        "scheduler.amend_schedule",
        "scheduler.amend_policy",
        "scheduler.replace_bound",
    ]
    if isinstance(adoption, SchedulerGenesisAdoption):
        validate_fresh_adoption(adoption, generations)
        current = ConfigurationSnapshot(
            schedule=None, policy=None, bound=None, active_hold=Absent()
        )
        operation = "scheduler.genesis"
        policy = json.loads(_policy_bytes())
    else:
        if len(startup.index.schedules) != 1:
            raise LoopRejected("configuration requires one registered schedule")
        current = configuration_snapshot(startup, startup.index.schedules[0].schedule_id)
        transition = validate_configuration_adoption(adoption, current, generations)
        if transition.kind == "AMEND_SCHEDULE":
            operation = "scheduler.amend_schedule"
        elif transition.kind == "AMEND_POLICY":
            operation = "scheduler.amend_policy"
        else:
            operation = "scheduler.replace_bound"
        policy = json.loads(configuration_policy_bytes())
    cut = startup.cut
    source_evidence = {
        "generations": json.loads(generations.evidence_bytes),
        "registry": json.loads(startup.registration.canonical_manifest),
        "cut": {
            "tenant": cut.tenant_id,
            "frontier": cut.tenant_frontier,
            "commitment": cut.materialization_commitment,
            "owner_journal_head": cut.independent_journal_head,
            "database_path": cut.physical_database_path,
            "database_device": cut.physical_device,
            "database_inode": cut.physical_inode,
        },
        "loop_entries": tuple(
            (identity, tenant, base64.b64encode(raw).decode())
            for identity, tenant, raw in sources.loop_entries
        ),
        "fence": (sources.fence_generation, sources.fence_frontier),
    }
    mandate = {
        "policy": policy,
        "adoption": adoption.model_dump(mode="json"),
        "authenticated_reference": json.loads(generations.evidence_bytes)["trust_reference"],
    }
    nonce = secrets.token_hex(32)
    issuance = {"nonce": nonce, "mandate": mandate, "sources": source_evidence}
    context = SchedulerContextRef(
        tenant_id=cut.tenant_id,
        service_identity=GENESIS_SERVICE_IDENTITY,
        session_id="hermetic-session",
        mandate_head=_reference("r15-genesis-mandate-v1", mandate),
        issuance_id="r15-genesis-context:" + nonce,
        issuance_fingerprint=_digest(_canonical(issuance)),
    )
    proof_preimage = {"context": context.model_dump(), "source_cut": source_evidence["cut"]}
    owner_cut = BatchPreparationCut(
        tenant_id=cut.tenant_id,
        tenant_frontier=cut.tenant_frontier,
        materialization_commitment=cut.materialization_commitment,
        registry_head=startup.registration.reference.head,
        registry_fingerprint=startup.registration.reference.fingerprint,
        submission_id="r15-genesis-submission:" + nonce,
        authorized_context=context,
        authorized_command_fingerprint=_digest(adoption.command_bytes),
        authority_proof=Present(
            head="r15-genesis-cut:" + nonce,
            fingerprint=_digest(_canonical(proof_preimage)),
        ),
    )
    request = ConfigurationPreparationRequest(
        operation=operation,
        context=context,
        snapshot=ConfigurationPreparationSnapshot(
            cut=owner_cut,
            current=current,
            authority_epoch=generations.authority_epoch,
            broker_generation=generations.broker_generation,
            runtime_graph_generation=generations.runtime_graph_generation,
            scheduler_authority_head=(
                context.mandate_head
                if isinstance(adoption, SchedulerGenesisAdoption)
                else configuration_authority_head(generations)
            ),
        ),
        command_bytes=adoption.command_bytes,
    )
    preimages: dict[str, object] = {"issuance": issuance, "cut_proof": proof_preimage}
    if isinstance(adoption, SchedulerConfigurationAdoption):
        preimages["configuration_authority"] = json.loads(
            configuration_authority_preimage(generations)
        )
    return request, preimages


def _manifest(request: ConfigurationPreparationRequest) -> AuthoritativeReadManifest:
    cut = request.snapshot.cut
    heads: tuple[ObservedHead, ...]
    if request.operation == "scheduler.genesis":
        command = ConfigurationGenesisCommand.model_validate_json(request.command_bytes)
        heads = tuple(
            ObservedAbsence(owner="agent_loop", record_kind=kind, subject_id=command.schedule_id)
            for kind in (
                "scheduler.schedule-definition",
                "scheduler.missed-policy",
                "scheduler.interval-bound",
            )
        )
    else:
        current = request.snapshot.current
        if current.schedule is None or current.policy is None or current.bound is None:
            raise LoopRejected("configuration manifest requires complete current heads")
        schedule = schedule_head(current.schedule)
        policy = policy_head(current.policy)
        subject = current.schedule.schedule_id
        heads = tuple(
            ObservedPresence(
                head=ExactRecordHead(
                    owner="agent_loop",
                    record_kind=kind,
                    subject_id=subject,
                    record_id=identity,
                    fingerprint=fingerprint,
                )
            )
            for kind, identity, fingerprint in (
                ("scheduler.schedule-definition", schedule.head, schedule.fingerprint),
                ("scheduler.missed-policy", policy.head, policy.fingerprint),
                (
                    "scheduler.interval-bound",
                    current.bound.head_id,
                    _digest(current.bound.canonical_bytes()),
                ),
            )
        )
        hold: ObservedHead = (
            ObservedAbsence(owner="agent_loop", record_kind="overflow-hold", subject_id=subject)
            if current.active_hold.kind == "ABSENT"
            else ObservedPresence(
                head=ExactRecordHead(
                    owner="agent_loop",
                    record_kind="overflow-hold",
                    subject_id=subject,
                    record_id=current.active_hold.head,
                    fingerprint=current.active_hold.fingerprint,
                )
            )
        )
        heads += (hold,)
    values = {
        "tenant_id": cut.tenant_id,
        "tenant_frontier": cut.tenant_frontier,
        "expected_materialization_commitment": cut.materialization_commitment,
        "registry_head": cut.registry_head,
        "registry_fingerprint": cut.registry_fingerprint,
        "ordered_heads": [head.model_dump(mode="json") for head in heads],
    }
    return AuthoritativeReadManifest(
        tenant_id=cut.tenant_id,
        tenant_frontier=cut.tenant_frontier,
        expected_materialization_commitment=cut.materialization_commitment,
        registry_head=cut.registry_head,
        registry_fingerprint=cut.registry_fingerprint,
        ordered_heads=heads,
        complete_manifest_fingerprint=_digest(_canonical(values)),
    )


def _verified_result(
    runtime: R14PlanningRuntime, result: BrokerPublicationResult
) -> BrokerPublicationResult:
    if isinstance(result, JournalSelectedPublication):
        # This is historical equality and complete membership, not current owner
        # preparation. It also prevents a materialized marker alone from succeeding.
        read_loop_snapshot(runtime)
    return result


async def publish_genesis(
    runtime: R14PlanningRuntime, peer: str, adoption: SchedulerGenesisAdoption
) -> BrokerPublicationResult:
    return await _publish(
        runtime, peer, SchedulerGenesisAdoption.model_validate_json(adoption.canonical_bytes())
    )


async def publish_configuration(
    runtime: R14PlanningRuntime, peer: str, adoption: SchedulerConfigurationAdoption
) -> BrokerPublicationResult:
    return await _publish(
        runtime,
        peer,
        SchedulerConfigurationAdoption.model_validate_json(adoption.canonical_bytes()),
    )


async def _publish(
    runtime: R14PlanningRuntime,
    peer: str,
    adoption: SchedulerGenesisAdoption | SchedulerConfigurationAdoption,
) -> BrokerPublicationResult:
    genesis = isinstance(adoption, SchedulerGenesisAdoption)
    policy = json.loads(_policy_bytes() if genesis else configuration_policy_bytes())
    applicability_schema = _APPLICABILITY_SCHEMA if genesis else _CONFIGURATION_APPLICABILITY_SCHEMA
    command_id = (_command_id if genesis else configuration_command_id)(adoption.adoption_act_id)
    allowed_operations = (
        ("scheduler.genesis",)
        if genesis
        else ("scheduler.amend_schedule", "scheduler.amend_policy", "scheduler.replace_bound")
    )
    observed = await _authenticate(runtime, peer)
    authority = SchedulerPublicationAuthority(runtime, observed, adoption)
    journal = runtime._owner_decisions()
    coordinator = BrokerPublicationCoordinator(runtime._appender, authority, journal)
    identity = PublicationIdentity(
        tenant_id=runtime._tenant_id,
        command_id=command_id,
        command_fingerprint=_digest(adoption.command_bytes),
        canonicalization_version="chiplog.owner-publication.v1",
    )
    with runtime._authority_gate().hold():
        selected = journal.lookup(runtime._tenant_id, identity.command_id)
        if selected is not None:
            original = selected.prepared.request
            if (
                not isinstance(original, SingleOwnerBatch)
                or original.operation not in allowed_operations
                or not isinstance(original.authentication, WorkerAuthentication)
                or original.authentication.applicability_schema != applicability_schema
            ):
                raise LoopRejected("selected scheduler identity has an unregistered envelope")
            evidence = json.loads(original.authentication.applicability_bytes)
            saved = ConfigurationPreparationRequest.model_validate_json(
                original.command.canonical_bytes
            )
            if (
                original.identity != identity
                or saved.command_bytes != adoption.command_bytes
                or evidence["adoption"] != adoption.model_dump(mode="json")
                or evidence["policy"] != policy
            ):
                return PublicationRejected(
                    kind="CONFLICT",
                    tenant_id=runtime._tenant_id,
                    command_id=identity.command_id,
                    reason="changed exact scheduler adoption",
                )
            if not genesis:
                preimages = evidence["preimages"]
                authority_preimage = preimages["configuration_authority"]
                old_sources = preimages["issuance"]["sources"]["generations"]
                old_agent = old_sources["broker_generation_preimage"]["agent_session"]
                old_worker = (
                    f"{old_agent['broker_epoch']}:{old_agent['generation_id']}:"
                    f"{old_agent['session_id']}"
                )
                expected_authority = {
                    "policy": policy,
                    "authenticated_reference": old_sources["trust_reference"],
                    "authority_epoch": saved.snapshot.authority_epoch,
                    "broker_generation": saved.snapshot.broker_generation,
                    "runtime_graph_generation": saved.snapshot.runtime_graph_generation,
                    "worker_session": old_worker,
                }
                if (
                    authority_preimage != expected_authority
                    or saved.snapshot.scheduler_authority_head
                    != _reference("r15-configuration-authority-v1", authority_preimage)
                    or saved.operation != original.operation
                ):
                    raise LoopRejected("selected configuration authority preimage differs")
            invocation = authority.issue_invocation(original.identity, original.command)
            query = ExactReplayQuery(
                identity=original.identity,
                operation=original.operation,
                current_invocation=invocation,
                original_commands=source_commands(original),
            )
            failure = authority.authenticate_replay(query)
            if failure is not None:
                return failure
            replay = coordinator.lookup_exact(query)
            if isinstance(replay, JournalSelectedPublication):
                return _verified_result(runtime, replay)
            if not isinstance(replay, PublicationRejected):
                raise LoopRejected("selected scheduler decision has no exact replay")
            # The coordinator authenticates again; a later denial must remain
            # terminal even when the selected physical batch is still absent.
            if replay.kind != "HOLD":
                return replay
            if authority.materialization_state(selected) != "ABSENT":
                return replay
    if selected is not None:
        return _verified_result(
            runtime, await coordinator.recover_selected(runtime._tenant_id, identity.command_id)
        )

    sources = authority.capture_fresh()
    request, preimages = _preparation(adoption, sources)
    callee = sources.generations.agent_session
    sent = PublicPortCall(
        operation_id="scheduler.prepare_configuration",
        request_id="scheduler-genesis:" + secrets.token_hex(24),
        caller=BrokerSession(
            tenant_id=runtime._tenant_id,
            broker_epoch=callee.broker_epoch,
            generation_id=callee.generation_id,
            owner_id="broker",
            session_id=f"broker:{callee.generation_id}",
        ),
        callee=callee,
        schema_id=_PREPARATION_SCHEMA,
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
            raise LoopRejected("scheduler sources changed during owner preparation")
        if response.request_id != sent.request_id or response.responder != sent.callee:
            raise LoopRejected("scheduler owner response identity differs")
        if isinstance(response, PublicPortRejected):
            raise LoopRejected("scheduler owner rejected preparation: " + response.failure.reason)
        if response.schema_id != BATCH_SCHEMA:
            raise LoopRejected("scheduler owner response schema differs")
        prepared = PreparedSchedulerBatch.model_validate_json(response.canonical_payload)
        if (
            prepared.canonical_bytes() != response.canonical_payload
            or prepare_configuration(request).canonical_bytes() != response.canonical_payload
        ):
            raise LoopRejected("scheduler output differs from complete original owner preparation")
        owner_command = OwnerCommandBytes(
            owner="agent_loop",
            schema_id=_PREPARATION_SCHEMA,
            canonical_bytes=sent.canonical_payload,
            fingerprint=_digest(sent.canonical_payload),
        )
        invocation = authority.issue_invocation(identity, owner_command)
        applicability = _canonical(
            {
                "schema": applicability_schema,
                "policy": policy,
                "adoption": adoption.model_dump(mode="json"),
                "preimages": preimages,
                "owner_request": _frame(sent),
                "owner_response": _frame(response),
            }
        )
        records = tuple(
            OwnerRecordBytes(
                owner="agent_loop",
                record_kind=member.record_kind,
                record_id=member.record_id,
                schema_id=member.schema_id,
                canonical_bytes=base64.b64decode(member.canonical_base64, validate=True),
                fingerprint=member.fingerprint,
            )
            for member in prepared.records
        )
        authentication = WorkerAuthentication(
            invocation=invocation,
            applicability_schema=applicability_schema,
            applicability_bytes=applicability,
            applicability_fingerprint=_digest(applicability),
        )
        expected = _manifest(request)
        values = {
            "kind": "SINGLE_OWNER",
            "operation": request.operation,
            "identity": identity.model_dump(mode="json"),
            "authentication": authentication.model_dump(mode="json"),
            "expected": expected.model_dump(mode="json"),
            "command": owner_command.model_dump(mode="json"),
            "complete_records": [record.model_dump(mode="json") for record in records],
        }
        batch = SingleOwnerBatch(
            operation=request.operation,
            identity=identity,
            authentication=authentication,
            expected=expected,
            command=owner_command,
            complete_records=records,
            complete_batch_fingerprint=_digest(_canonical(values)),
        )
        authority.register_prepared(batch, sources)
    return _verified_result(runtime, await coordinator.commit(batch))
