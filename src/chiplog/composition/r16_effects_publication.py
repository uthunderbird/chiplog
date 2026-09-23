"""Actual authenticated preview adoption to one journal-first PlanEffect commit."""

from __future__ import annotations

import json
import os
import secrets
import time
from typing import TYPE_CHECKING

from chiplog.adapters.driven.effects_broker import validate_preparation_pair
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.effects.contracts import (
    EffectDenied,
    EffectPreparationRequest,
    PreparedEffectPublication,
    PublishPlanEffectCommand,
)
from chiplog.composition.r16_effects import R16EffectsProducer
from chiplog.composition.r16_effects_authority import R16PlanEffectAuthority
from chiplog.composition.r16_effects_inputs import (
    build_effect_request,
    canonical,
    capture_sources,
    digest,
    planning_records,
    require_initial_output,
)
from chiplog.composition.r16_effects_registry import HermeticPlanEffectRegistry
from chiplog.composition.r16_planning_reads import validate_plan_effect_records
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    BrokerPublicationResult,
    ExactRecordHead,
    ExactReplayQuery,
    NoSelectedDecision,
    ObservedAbsence,
    ObservedPresence,
    OwnerCommandBytes,
    OwnerRecordBytes,
    PlanEffectBatch,
    PublicationIdentity,
    PublicationRejected,
    WorkerAuthentication,
)
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortRejected
from chiplog.platform.owner_publications import BrokerPublicationCoordinator, source_commands

if TYPE_CHECKING:
    from chiplog.composition.r14_runtime import R14PlanningRuntime


async def publish_plan_effect(
    runtime: R14PlanningRuntime,
    peer: str,
    display_id: str,
    display_digest: str,
    adoption_act_id: str,
) -> BrokerPublicationResult:
    """No caller-supplied authority or prepared object enters this entrypoint."""
    if peer != "hermetic-ingress":
        raise LoopRejected("effect adoption requires the registered CLI ingress")
    observed = await runtime._observed_trust_call(
        "AUTHENTICATE",
        {
            "contour": "CLI",
            "credential_id": "hermetic-credential",
            "peer_credential": f"uid:{os.getuid()}",
            "session_id": "hermetic-session",
        },
    )
    authority = R16PlanEffectAuthority(runtime)
    journal = runtime._owner_decisions()
    coordinator = BrokerPublicationCoordinator(runtime._appender, authority, journal)
    command_id = "plan-effect:" + digest(
        canonical((runtime._tenant_id, display_id, adoption_act_id))
    )
    with runtime._authority_gate().hold():
        if runtime._trust_observation_guard(observed) is not None:
            raise LoopRejected("effect invocation authentication changed or denied")
        displays = tuple(d for d in runtime._displays() if d.display_id == display_id)
        if len(displays) != 1 or (
            displays[0].display_digest,
            displays[0].adoption_act_id,
            displays[0].principal,
        ) != (display_digest, adoption_act_id, "hermetic-principal"):
            raise LoopRejected("effect adoption differs from exact original display")
        selected = journal.lookup(runtime._tenant_id, command_id)
        if selected is not None:
            original = selected.prepared.request
            if not isinstance(original, PlanEffectBatch):
                raise LoopRejected("selected identity belongs to another operation")
            preparation = EffectPreparationRequest.model_validate_json(
                original.effects_command.canonical_bytes
            )
            command = PublishPlanEffectCommand.model_validate_json(preparation.command_bytes)
            act = command.intent.authority.act
            if (
                preparation.canonical_bytes() != original.effects_command.canonical_bytes
                or act.kind != "EXACT_PROPOSAL_ADOPTION"
                or act.display_digest != display_digest
                or act.adoption.subject_id != adoption_act_id
                or command.intent.authority.principal_id != displays[0].principal
                or command.intent.authority.tenant_id != runtime._tenant_id
            ):
                raise LoopRejected("selected effect differs from original adopted operation")
            invocation = authority.issue_invocation(
                original.identity, source_commands(original), observed
            )
            replay = coordinator.lookup_exact(
                ExactReplayQuery(
                    identity=original.identity,
                    operation=original.operation,
                    current_invocation=invocation,
                    original_commands=source_commands(original),
                )
            )
            if isinstance(replay, NoSelectedDecision):
                raise LoopRejected("selected effect did not resolve to exact replay")
            if not isinstance(replay, PublicationRejected) or replay.kind != "HOLD":
                return replay
            if authority.materialization_state(selected) != "ABSENT":
                return replay
    if selected is not None:
        return await coordinator.recover_selected(runtime._tenant_id, command_id)

    adoption = await R16EffectsProducer(runtime).adopt_effect(
        peer,
        display_id,
        display_digest,
        adoption_act_id,
    )
    registry = HermeticPlanEffectRegistry()
    with runtime._authority_gate().hold():
        sources = capture_sources(runtime, adoption, registry)
        request = build_effect_request(command_id, adoption, sources, registry)
        callee = sources.sessions[1]
        caller = BrokerSession(
            tenant_id=runtime._tenant_id,
            broker_epoch=callee.broker_epoch,
            generation_id=callee.generation_id,
            owner_id="broker",
            session_id=f"broker:{callee.generation_id}",
        )
        sent = PublicPortCall(
            operation_id="effects.prepare_transition",
            request_id="plan-effect:" + secrets.token_hex(24),
            caller=caller,
            callee=callee,
            schema_id="chiplog.effects.prepare.v1",
            canonical_payload=request.canonical_bytes(),
            budget=CallBudget(
                remaining_calls=1,
                remaining_depth=1,
                policy_version=1,
                absolute_deadline_ns=request.current.authority.valid_until_ns,
            ),
        )
    response = await runtime._supervisor.runtime().call(sent)
    with runtime._authority_gate().hold():
        if capture_sources(runtime, adoption, registry) != sources:
            raise LoopRejected("effect sources changed during owner preparation")
        if (
            response.request_id != sent.request_id
            or response.responder != sent.callee
            or time.monotonic_ns() >= sent.budget.absolute_deadline_ns
        ):
            raise LoopRejected("effects owner response identity or deadline differs")
        if isinstance(response, PublicPortRejected):
            raise LoopRejected("effects preparation rejected: " + response.failure.reason)
        if response.schema_id != "chiplog.effects.prepared-publication.v1":
            raise LoopRejected("effects owner response schema differs")
        if "disposition" in json.loads(response.canonical_payload):
            denied = EffectDenied.model_validate_json(response.canonical_payload)
            if (
                denied.canonical_bytes() != response.canonical_payload
                or denied.command_id != command_id
            ):
                raise LoopRejected("effects owner denial differs from invocation")
            return PublicationRejected(
                kind="HOLD",
                tenant_id=runtime._tenant_id,
                command_id=command_id,
                reason=denied.reason,
            )
        prepared = PreparedEffectPublication.model_validate_json(response.canonical_payload)
        if prepared.canonical_bytes() != response.canonical_payload:
            raise LoopRejected("effects owner output is noncanonical")
        validate_preparation_pair(request, prepared)
        require_initial_output(request, prepared)
        command = PublishPlanEffectCommand.model_validate_json(request.command_bytes)
        if (
            prepared.exact_companion_manifest != command.complete_publication_manifest
            or prepared.record.snapshot.intent != command.intent
            or prepared.record.kind != "PLAN_EFFECT_PUBLISHED"
        ):
            raise LoopRejected("effects owner substituted intent or planning companions")
        records = (
            *planning_records(adoption),
            OwnerRecordBytes(
                owner="effects",
                record_kind="effects." + prepared.record.kind,
                record_id=prepared.record.record.head,
                schema_id="chiplog.effects.record.v1",
                canonical_bytes=prepared.record.canonical_bytes(),
                fingerprint=digest(prepared.record.canonical_bytes()),
            ),
        )
        commands = (
            OwnerCommandBytes(
                owner="planning",
                schema_id="chiplog.planning.public.create.v2",
                canonical_bytes=adoption.planning.request_bytes,
                fingerprint=digest(adoption.planning.request_bytes),
            ),
            OwnerCommandBytes(
                owner="effects",
                schema_id="chiplog.effects.prepare.v1",
                canonical_bytes=request.canonical_bytes(),
                fingerprint=digest(request.canonical_bytes()),
            ),
        )
        identity = PublicationIdentity(
            tenant_id=runtime._tenant_id,
            command_id=command_id,
            command_fingerprint=digest(
                canonical(
                    {
                        "domain": "chiplog.plan-effect.publication.v1",
                        "tenant": runtime._tenant_id,
                        "display": display_id,
                        "digest": display_digest,
                        "act": adoption_act_id,
                        "commands": commands,
                    }
                )
            ),
            canonicalization_version="chiplog.owner-publication.v1",
        )
        invocation = authority.issue_invocation(identity, commands, adoption.observed_trust)
        worker = sources.cut.worker
        if worker is None:
            raise LoopRejected("current worker missing")
        worker_bytes = worker.fence.canonical_bytes()
        heads: tuple[ObservedAbsence | ObservedPresence, ...] = (
            ObservedPresence(
                head=ExactRecordHead(
                    owner="agent_loop",
                    record_kind="Run",
                    subject_id=worker.run.run_id,
                    record_id=worker.run.head,
                    fingerprint=digest(worker.run.canonical_bytes()),
                )
            ),
            *(
                ObservedPresence(
                    head=ExactRecordHead(
                        owner="effects",
                        record_kind=row.kind,
                        subject_id=row.snapshot.intent.intent_id,
                        record_id=row.record.head,
                        fingerprint=row.record.fingerprint,
                    )
                )
                for row in request.expected.records
            ),
            ObservedAbsence(
                owner="effects",
                record_kind="ExternalActionIntent",
                subject_id=command.intent.intent_id,
            ),
        )
        batch = PlanEffectBatch(
            identity=identity,
            authentication=WorkerAuthentication(
                invocation=invocation,
                applicability_schema="chiplog.effects.worker-fence.v1",
                applicability_bytes=worker_bytes,
                applicability_fingerprint=digest(worker_bytes),
            ),
            expected=AuthoritativeReadManifest(
                tenant_id=runtime._tenant_id,
                tenant_frontier=sources.cut.tenant_frontier,
                expected_materialization_commitment=sources.cut.materialization_commitment,
                registry_head=registry.reference.head,
                registry_fingerprint=registry.reference.fingerprint,
                ordered_heads=heads,
                complete_manifest_fingerprint=digest(canonical((sources, heads))),
            ),
            planning_command=commands[0],
            effects_command=commands[1],
            complete_records=records,
            complete_batch_fingerprint=digest(canonical(records)),
        )
        validate_plan_effect_records(batch, runtime._tenant_id, sources.cut.tenant_frontier + 1)
        authority.register_prepared(batch, adoption, sources, sent, response)
    return await coordinator.commit(batch)
