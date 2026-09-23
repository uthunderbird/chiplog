"""Canonical authenticated denial orchestration and explicit v7 runtime assembly."""

from __future__ import annotations

import os
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.adapters.driven.loop_prompts import OwnedStaticPrompts
from chiplog.adapters.driven.loop_sqlite import SQLiteLoopStore
from chiplog.architecture.r7_runtime import R16_PRODUCTION_MANIFEST
from chiplog.capabilities.agent_loop.application import AgentLoop
from chiplog.capabilities.agent_loop.contracts import EndpointSelection, LoopRejected, LoopSnapshot
from chiplog.capabilities.effects.contracts import EffectDenied, PreparedEffectPublication
from chiplog.composition.r7_planning import _open_runtime
from chiplog.composition.r13_workspace import R13Workspace
from chiplog.composition.r14_runtime import R14PlanningRuntime
from chiplog.composition.r16_denial_authority import DenialAuthority
from chiplog.composition.r16_denial_history import (
    PREPARATION_SCHEMA,
    denial_manifest,
    denial_record,
    retained_ingress,
    validate_denial_batch,
    validate_selected_denials,
)
from chiplog.composition.r16_denial_inputs import (
    build_denial_request,
    capture_denial,
    require_invocation,
)
from chiplog.composition.r16_denial_registry import DenialIngress, denial_command_id
from chiplog.composition.r16_effects_inputs import canonical, digest
from chiplog.platform._owner_publication_contracts import (
    BrokerPublicationResult,
    ExactReplayQuery,
    NoSelectedDecision,
    OwnerCommandBytes,
    PublicationIdentity,
    PublicationRejected,
    SingleOwnerBatch,
    WorkerAuthentication,
)
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortRejected
from chiplog.platform.owner_publications import BrokerPublicationCoordinator


async def publish_denial(
    runtime: R14PlanningRuntime, peer: str, worker_run_id: str, ingress: DenialIngress
) -> BrokerPublicationResult:
    if peer != "hermetic-ingress":
        raise LoopRejected("denial requires registered CLI ingress")
    observed = await runtime._observed_trust_call(
        "AUTHENTICATE",
        {
            "contour": "CLI",
            "credential_id": "hermetic-credential",
            "peer_credential": f"uid:{os.getuid()}",
            "session_id": "hermetic-session",
        },
    )
    authority = DenialAuthority(runtime)
    journal = runtime._owner_decisions()
    coordinator = BrokerPublicationCoordinator(runtime._appender, authority, journal)
    command_id = denial_command_id(runtime._tenant_id, "hermetic-principal", ingress.act_id)
    with runtime._authority_gate().hold():
        require_invocation(runtime, observed)
        selected_history = journal.snapshot()
        validate_selected_denials(selected_history)
        selected = journal.lookup(runtime._tenant_id, command_id)
        if selected is not None:
            batch = selected.prepared.request
            if (
                not isinstance(batch, SingleOwnerBatch)
                or batch.command.schema_id != PREPARATION_SCHEMA
            ):
                raise LoopRejected("selected act belongs to another operation")
            from chiplog.capabilities.effects.denial_contracts import DenialPreparationRequest

            original = DenialPreparationRequest.model_validate_json(batch.command.canonical_bytes)
            if retained_ingress(original) != ingress:
                return PublicationRejected(
                    kind="CONFLICT",
                    tenant_id=runtime._tenant_id,
                    command_id=command_id,
                    reason="act identity already names different exact denial inputs",
                )
            proof = authority.invocation(batch.identity, batch.command, observed)
            result = coordinator.lookup_exact(
                ExactReplayQuery(
                    identity=batch.identity,
                    operation="effects.before_send",
                    current_invocation=proof,
                    original_commands=(batch.command,),
                )
            )
            if isinstance(result, NoSelectedDecision):
                raise LoopRejected("selected denial disappeared during exact replay")
            if not isinstance(result, PublicationRejected) or result.kind != "HOLD":
                return result
            if authority.materialization_state(selected) != "ABSENT":
                return result
    if selected is not None:
        return await coordinator.recover_selected(runtime._tenant_id, command_id)

    with runtime._authority_gate().hold():
        captured = capture_denial(runtime, observed, worker_run_id)
        request = build_denial_request(ingress, captured, observed)
        callee = captured.sessions[0]
        sent = PublicPortCall(
            operation_id="effects.prepare_denial",
            request_id="denial:" + secrets.token_hex(24),
            caller=BrokerSession(
                tenant_id=runtime._tenant_id,
                broker_epoch=callee.broker_epoch,
                generation_id=callee.generation_id,
                owner_id="broker",
                session_id=f"broker:{callee.generation_id}",
            ),
            callee=callee,
            schema_id=PREPARATION_SCHEMA,
            canonical_payload=request.canonical_bytes(),
            budget=CallBudget(
                remaining_calls=1,
                remaining_depth=1,
                policy_version=1,
                absolute_deadline_ns=request.command.authority.valid_until_ns,
            ),
        )
    returned = await runtime._supervisor.runtime().call(sent)
    with runtime._authority_gate().hold():
        if capture_denial(runtime, observed, worker_run_id) != captured:
            raise LoopRejected("denying sources changed during owner preparation")
        if (
            returned.request_id != sent.request_id
            or returned.responder != sent.callee
            or time.monotonic_ns() >= sent.budget.absolute_deadline_ns
        ):
            raise LoopRejected("denying owner response identity or lease differs")
        if isinstance(returned, PublicPortRejected):
            raise LoopRejected("denying owner rejected: " + returned.failure.reason)
        if returned.schema_id != "chiplog.effects.before-send-prepared-publication.v2":
            raise LoopRejected("denying owner response schema differs")
        import json

        if "disposition" in json.loads(returned.canonical_payload):
            denied = EffectDenied.model_validate_json(returned.canonical_payload)
            if (
                denied.command_id != command_id
                or denied.canonical_bytes() != returned.canonical_payload
            ):
                raise LoopRejected("denying owner denial differs from invocation")
            return PublicationRejected(
                kind="HOLD",
                tenant_id=runtime._tenant_id,
                command_id=command_id,
                reason=denied.reason,
            )
        prepared = PreparedEffectPublication.model_validate_json(returned.canonical_payload)
        if prepared.canonical_bytes() != returned.canonical_payload:
            raise LoopRejected("noncanonical denying owner output")
        command = OwnerCommandBytes(
            owner="effects",
            schema_id=PREPARATION_SCHEMA,
            canonical_bytes=request.canonical_bytes(),
            fingerprint=digest(request.canonical_bytes()),
        )
        identity = PublicationIdentity(
            tenant_id=runtime._tenant_id,
            command_id=command_id,
            command_fingerprint=command.fingerprint,
            canonicalization_version="chiplog.owner-publication.v1",
        )
        proof = authority.invocation(identity, command, observed)
        fence = request.current.fence.canonical_bytes()
        record = denial_record(prepared)
        batch = SingleOwnerBatch(
            operation="effects.before_send",
            identity=identity,
            authentication=WorkerAuthentication(
                invocation=proof,
                applicability_schema="chiplog.effects.worker-fence.v1",
                applicability_bytes=fence,
                applicability_fingerprint=digest(fence),
            ),
            expected=denial_manifest(request),
            command=command,
            complete_records=(record,),
            complete_batch_fingerprint=digest(canonical((record,))),
        )
        validate_denial_batch(batch, request.expected.records)
        authority.register(batch, captured, sent, returned)
    return await coordinator.commit(batch)


class R16PlanningRuntime(R14PlanningRuntime):
    """Explicit v7 assembly retains its legacy Run publication/read profile."""

    def _loop_snapshot(self) -> LoopSnapshot:
        return SQLiteLoopStore(self._database, self._appender, self._tenant_id, "r6").snapshot()


@asynccontextmanager
async def open_r16_runtime(
    database: Path, *, model: HermeticModel | None = None
) -> AsyncIterator[R16PlanningRuntime]:
    async with _open_runtime(
        database,
        tenant_id="hermetic-tenant",
        operator_secret=b"r13-hermetic-only",
        runtime_type=R16PlanningRuntime,
        manifest=R16_PRODUCTION_MANIFEST,
        extra_leaves={"model": model if model is not None else HermeticModel()},
    ) as opened:
        runtime = cast(R16PlanningRuntime, opened)
        if runtime._trust.verify() is None:
            await runtime.bootstrap(
                database_instance_id="hermetic-database",
                principal_id="hermetic-principal",
                credential_id="hermetic-credential",
                session_id="hermetic-session",
                token="hermetic-bootstrap",
            )
        yield runtime


@asynccontextmanager
async def open_r16_loop(
    database: Path, *, responses: tuple[bytes, ...]
) -> AsyncIterator[AgentLoop]:
    model = HermeticModel(responses)
    async with open_r16_runtime(database, model=model) as runtime:
        model.session = runtime
        origin = EndpointSelection(
            kind="ORIGIN_EXACT",
            ingress_binding_head="hermetic-ingress-v1",
            endpoint_head="hermetic-endpoint-v1",
            endpoint_id="hermetic-local",
            provider="hermetic-local",
            recipient="hermetic-principal",
            canonical_address="local://hermetic-principal",
            credential_binding_head="hermetic-v1",
        )
        yield AgentLoop(
            SQLiteLoopStore(
                database, runtime._appender, runtime._tenant_id, "r6", authority=runtime
            ),
            model,
            OwnedStaticPrompts(),
            tenant=runtime._tenant_id,
            principal="hermetic-principal",
            origin=origin,
            contour_head="hermetic-contour-v1",
            policy_head="hermetic-policy-v1",
            worker_session=runtime.current_worker(),
            planning=runtime,
            workspace=R13Workspace(runtime),
            session=runtime,
        )
