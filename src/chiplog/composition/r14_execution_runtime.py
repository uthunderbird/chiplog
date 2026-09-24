"""Hermetic executable initialization through the canonical journal and sole writer."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import ClassVar, Literal, cast

from pydantic import TypeAdapter

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.adapters.driven.execution_prompts import render_execution_prompt
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.adapters.driven.r9_fence import CONVERSATION_OWNER
from chiplog.architecture.r7_runtime import R14_EXECUTION_LIFECYCLE_PRODUCTION_MANIFEST
from chiplog.capabilities.agent_loop.contracts import (
    BudgetPolicy,
    DisclosureLabel,
    LoopRejected,
    VisibilityMember,
)
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead,
    OriginSelection,
    ProviderRecipient,
)
from chiplog.capabilities.agent_loop.domain import join_labels
from chiplog.capabilities.agent_loop.execution_contracts import (
    ExecutionRunRecord,
    ExecutionVisibilityManifest,
)
from chiplog.capabilities.agent_loop.execution_initialization_contracts import (
    ExecutionInitializationResult,
    PreparedExecutionInitialization,
    PrepareInboxExecution,
)
from chiplog.capabilities.agent_loop.execution_preparation import execution_context_label
from chiplog.capabilities.agent_loop.execution_transition_contracts import (
    AccumulateExecutionVisibility,
    ActivateExecutionRun,
    CaptureExecutionResponse,
    CreateExecutionRun,
    EmitExecutionAttempt,
    ExecutionTransitionProposal,
    ExecutionTransitionRequest,
    ExecutionTransitionResult,
    PrepareExecutionRequest,
    StartInitialExecutionTurn,
)
from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
    RECOVERY_FRONTIER_REGISTRY_SCHEMA,
)
from chiplog.composition.r16_dispatch_registry import ResourceObservation
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess

from .r7_planning import ObservedTrustCall, _open_runtime
from .r13_workspace import R13Workspace, _history
from .r14_execution_fanout_contracts import EXECUTION_RUN_SCHEMA
from .r14_execution_inbox_records import (
    RetainedInboxExecutionInitialization,
    inbox_initialization_command,
)
from .r14_execution_transition_records import (
    ExecutionHistorySnapshot,
    RetainedExecutionTransition,
    transition_command,
)
from .r14_loop_history import read_execution_history
from .r14_runtime import R14PlanningRuntime


class R14ExecutionRuntime(R14PlanningRuntime):
    _record_schema_variants: ClassVar[tuple[tuple[str, str], ...]] = (
        *R14PlanningRuntime._record_schema_variants,
        ("agent_loop", EXECUTION_RUN_SCHEMA),
        ("agent_loop", RECOVERY_FRONTIER_REGISTRY_SCHEMA),
    )
    _execution_lane: asyncio.Lock
    _execution_model: HermeticModel

    def _bind_appender(self) -> None:
        super()._bind_appender()
        self._execution_lane = asyncio.Lock()

    def _check_execution_actor(self, observed: ObservedTrustCall) -> None:
        self._check_database_identity()
        raw = observed.result.reference_bytes
        if self._trust_observation_guard(observed) is not None or raw is None:
            raise LoopRejected("execution caller authentication unavailable or stale")
        value = json.loads(raw)
        if (
            self._tenant_id,
            value.get("tenant_id"),
            value.get("principal_id"),
            value.get("contour"),
            value.get("source_head"),
            value.get("peer_credential"),
        ) != (
            "hermetic-tenant",
            "hermetic-tenant",
            "hermetic-principal",
            "CLI",
            "local",
            f"uid:{os.getuid()}",
        ):
            raise LoopRejected("caller outside registered hermetic execution ingress")

    async def _execution_actor(self, peer: str) -> ObservedTrustCall:
        if peer != "hermetic-ingress":
            raise LoopRejected("unregistered execution ingress")
        observed = await self._observed_trust_call(
            "AUTHENTICATE",
            {
                "contour": "CLI",
                "credential_id": "hermetic-credential",
                "peer_credential": f"uid:{os.getuid()}",
                "session_id": "hermetic-session",
            },
        )
        with self._authority_gate().hold():
            self._check_execution_actor(observed)
        return observed

    def _execution_origin(self, run_id: str, prompt: str) -> OriginSelection:
        entries = [entry for entry in _history(self) if entry.entry_id == run_id + "/ingress"]
        if (
            len(entries) != 1
            or entries[0].accepted_bytes != prompt.encode()
            or entries[0].role != "principal"
        ):
            raise LoopRejected("execution origin lacks exact accepted principal ingress")
        rows = [
            row
            for row in self._appender._materializer.guarded_records(self._tenant_id, "r6", 0)
            if row[1] == entries[0].entry_id and row[2] == CONVERSATION_OWNER
        ]
        if len(rows) != 1 or not isinstance(rows[0][4], bytes):
            raise LoopRejected("execution origin physical record differs")

        def registered(identity: str, head: str) -> ExactHead:
            return ExactHead(
                identity=identity, head=head, fingerprint=hashlib.sha256(head.encode()).hexdigest()
            )

        return OriginSelection(
            ingress_binding=ExactHead(
                identity=entries[0].entry_id,
                head=entries[0].entry_id,
                fingerprint=hashlib.sha256(rows[0][4]).hexdigest(),
            ),
            recipient=ProviderRecipient(
                provider_id="hermetic-local",
                account_id="hermetic-tenant",
                recipient_id="hermetic-principal",
                endpoint=registered("hermetic-local", "hermetic-endpoint-v1"),
                canonical_address=b"local://hermetic-principal",
                credential_binding=registered("hermetic-credential", "hermetic-v1"),
            ),
        )

    async def _publish_initial_transition(
        self,
        peer: str,
        request: ExecutionTransitionRequest,
        *,
        expected_snapshot: ExecutionHistorySnapshot | None = None,
    ) -> ExecutionRunRecord:
        observed = await self._execution_actor(peer)
        with self._authority_gate().hold():
            self._check_execution_actor(observed)
            snapshot = read_execution_history(self)
            if expected_snapshot is not None and snapshot != expected_snapshot:
                raise LoopRejected("execution visibility source cut changed")
            run_id = (
                request.run_id if isinstance(request, CreateExecutionRun) else request.run.run_id
            )
            lineage = [run for run in snapshot.records if run.run_id == run_id]
            if isinstance(request, CreateExecutionRun):
                if (
                    lineage
                    or request.tenant != self._tenant_id
                    or request.principal != "hermetic-principal"
                    or request.origin != self._execution_origin(request.run_id, request.prompt)
                ):
                    raise LoopRejected("execution creation source or identity differs")
                worker = request.worker_session
            else:
                if (
                    not lineage
                    or lineage[-1] != request.run
                    or request.run.principal != "hermetic-principal"
                ):
                    raise LoopRejected("execution predecessor is stale or foreign")
                worker = request.run.worker_session
            if worker != self.current_worker():
                raise LoopRejected("execution worker is stale")
            predecessor = self._commitment_journal.load(self._tenant_id)
            if predecessor is None:
                raise LoopRejected("execution anchor absent")
            engine = self._supervisor.runtime()
            callee = engine.session("agent_loop")
            caller = BrokerSession(
                tenant_id=self._tenant_id,
                broker_epoch=callee.broker_epoch,
                generation_id=callee.generation_id,
                owner_id="broker",
                session_id=f"broker:{callee.generation_id}",
            )
            deadline = observed.request.budget.absolute_deadline_ns
        sent = PublicPortCall(
            operation_id="agent_loop.prepare_execution_transition",
            request_id="execution:" + secrets.token_hex(16),
            caller=caller,
            callee=callee,
            schema_id="chiplog.execution.transition-request.v2",
            canonical_payload=request.canonical_bytes(),
            budget=CallBudget(
                remaining_calls=1,
                remaining_depth=1,
                policy_version=1,
                absolute_deadline_ns=deadline,
            ),
        )
        returned = await engine.call(sent)
        if (
            not isinstance(returned, PublicPortSuccess)
            or returned.request_id != sent.request_id
            or returned.responder != callee
            or returned.schema_id != "chiplog.execution.transition-result.v2"
        ):
            raise LoopRejected("execution owner exchange rejected or changed identity")
        adapter: TypeAdapter[ExecutionTransitionResult] = TypeAdapter(ExecutionTransitionResult)
        proposal = adapter.validate_json(returned.canonical_payload)
        if (
            not isinstance(proposal, ExecutionTransitionProposal)
            or proposal.canonical_bytes() != returned.canonical_payload
        ):
            raise LoopRejected("execution owner rejected command or changed canonical bytes")
        evidence = RetainedExecutionTransition(
            request=request,
            proposal=proposal,
            expected_head=snapshot.tenant_head,
            predecessor_commitment=predecessor,
            expected_snapshot_fingerprint=snapshot.digest(),
            caller=caller,
            callee=callee,
            request_id=sent.request_id,
            deadline_ns=deadline,
        )
        command = transition_command(evidence)

        def guard() -> Literal["STALE"] | None:
            with self._authority_gate().hold():
                try:
                    self._check_execution_actor(observed)
                    if (
                        read_execution_history(self) != snapshot
                        or self.current_worker() != worker
                        or engine.session("agent_loop") != callee
                        or time.monotonic_ns() >= deadline
                        or self._commitment_journal.load(self._tenant_id) != predecessor
                        or transition_command(evidence) != command
                    ):
                        return "STALE"
                    if (
                        isinstance(request, CreateExecutionRun)
                        and self._execution_origin(request.run_id, request.prompt) != request.origin
                    ):
                        return "STALE"
                except LoopRejected:
                    return "STALE"
                return None

        def decide(resulting: str) -> None:
            with self._authority_gate().hold():
                self._require_no_pending()
                self._append_decision(
                    {
                        "version": 1,
                        "kind": "DECIDED",
                        "operation_id": command.idempotency_key,
                        "operation_kind": command.operation_kind,
                        "expected_head": command.expected_head,
                        "fingerprint": command.request_fingerprint,
                        "predecessor": predecessor,
                        "resulting": resulting,
                        "records": [
                            {
                                "record_id": row.record_id,
                                "owner": row.owner,
                                "schema": row.schema_id,
                                "payload": base64.b64encode(row.canonical_bytes).decode(),
                                "digest": row.fingerprint,
                            }
                            for row in command.records
                        ],
                        "execution_transition": evidence.canonical_bytes().decode(),
                    }
                )

        result = await self._appender.submit(
            replace(command, admission_guard=guard, decision_guard=decide)
        )
        if result.disposition not in ("COMMITTED", "REPLAY"):
            raise LoopRejected("execution publication " + result.disposition)
        self._finish_decision(command.idempotency_key)
        current = read_execution_history(self)
        if proposal.run not in current.records:
            raise LoopRejected("selected execution output absent after publication")
        return proposal.run

    async def _publish_selected_inbox_initialization(
        self,
        peer: str,
        request: PrepareInboxExecution,
        *,
        driver_request_bytes: bytes,
        driver_request_fingerprint: str,
        dispatch_observation: ResourceObservation,
        source_guard: Callable[[], bool],
    ) -> ExecutionRunRecord:
        """Publish a native Create from an independently reread selected inbox."""
        observed = await self._execution_actor(peer)
        with self._authority_gate().hold():
            self._check_execution_actor(observed)
            snapshot = read_execution_history(self)
            if (
                not source_guard()
                or any(run.run_id == request.create.run_id for run in snapshot.records)
                or request.cut.tenant_commit_sequence != snapshot.tenant_head
                or request.cut.worker_session_id != self.current_worker()
            ):
                raise LoopRejected("selected inbox source, Run absence, or worker differs")
            predecessor = self._commitment_journal.load(self._tenant_id)
            if predecessor is None:
                raise LoopRejected("execution anchor absent")
            engine = self._supervisor.runtime()
            callee = engine.session("agent_loop")
            caller = BrokerSession(
                tenant_id=self._tenant_id,
                broker_epoch=callee.broker_epoch,
                generation_id=callee.generation_id,
                owner_id="broker",
                session_id=f"broker:{callee.generation_id}",
            )
            deadline = observed.request.budget.absolute_deadline_ns
        sent = PublicPortCall(
            operation_id="agent_loop.initialize_inbox.v1",
            request_id="inbox-initialization:" + secrets.token_hex(16),
            caller=caller,
            callee=callee,
            schema_id="chiplog.execution.inbox-initialization.v1",
            canonical_payload=request.canonical_bytes(),
            budget=CallBudget(
                remaining_calls=1,
                remaining_depth=1,
                policy_version=1,
                absolute_deadline_ns=deadline,
            ),
        )
        returned = await engine.call(sent)
        if (
            not isinstance(returned, PublicPortSuccess)
            or returned.request_id != sent.request_id
            or returned.responder != callee
            or returned.schema_id != "chiplog.execution.inbox-initialization-result.v1"
        ):
            raise LoopRejected("inbox initialization owner exchange rejected or changed identity")
        result: ExecutionInitializationResult = TypeAdapter(
            ExecutionInitializationResult
        ).validate_json(returned.canonical_payload)
        if (
            not isinstance(result, PreparedExecutionInitialization)
            or result.canonical_bytes() != returned.canonical_payload
            or result.source_request_fingerprint != request.digest()
            or result.run.run_id != request.create.run_id
        ):
            raise LoopRejected("inbox initialization owner rejected selected Create")
        fields = (
            dispatch_observation.grant_bytes,
            dispatch_observation.credential_bytes,
            dispatch_observation.endpoint_bytes,
            dispatch_observation.clock_epoch,
            dispatch_observation.signature,
        )
        evidence = RetainedInboxExecutionInitialization(
            driver_request_bytes=driver_request_bytes,
            driver_request_fingerprint=driver_request_fingerprint,
            dispatch_grant_bytes=fields[0],
            dispatch_credential_bytes=fields[1],
            dispatch_endpoint_bytes=fields[2],
            dispatch_clock_epoch=fields[3],
            dispatch_signature=fields[4],
            request=request,
            proposal=result,
            expected_head=snapshot.tenant_head,
            predecessor_commitment=predecessor,
            caller=caller,
            callee=callee,
            request_id=sent.request_id,
            deadline_ns=deadline,
        )
        command = inbox_initialization_command(evidence)

        def guard() -> Literal["STALE"] | None:
            with self._authority_gate().hold():
                try:
                    self._check_execution_actor(observed)
                    if (
                        not source_guard()
                        or read_execution_history(self) != snapshot
                        or self.current_worker() != request.cut.worker_session_id
                        or engine.session("agent_loop") != callee
                        or time.monotonic_ns() >= deadline
                        or self._commitment_journal.load(self._tenant_id) != predecessor
                        or inbox_initialization_command(evidence) != command
                    ):
                        return "STALE"
                except LoopRejected, ValueError:
                    return "STALE"
                return None

        def decide(resulting: str) -> None:
            with self._authority_gate().hold():
                self._require_no_pending()
                self._append_decision(
                    {
                        "version": 1,
                        "kind": "DECIDED",
                        "operation_id": command.idempotency_key,
                        "operation_kind": command.operation_kind,
                        "expected_head": command.expected_head,
                        "fingerprint": command.request_fingerprint,
                        "predecessor": predecessor,
                        "resulting": resulting,
                        "records": [
                            {
                                "record_id": row.record_id,
                                "owner": row.owner,
                                "schema": row.schema_id,
                                "payload": base64.b64encode(row.canonical_bytes).decode(),
                                "digest": row.fingerprint,
                            }
                            for row in command.records
                        ],
                        "inbox_initialization": evidence.canonical_bytes().decode(),
                    }
                )

        published = await self._appender.submit(
            replace(command, admission_guard=guard, decision_guard=decide)
        )
        if published.disposition not in ("COMMITTED", "REPLAY"):
            raise LoopRejected("inbox initialization publication " + published.disposition)
        self._finish_decision(command.idempotency_key)
        current = read_execution_history(self)
        if result.run not in current.records:
            raise LoopRejected("selected inbox Run absent after publication")
        return result.run

    async def capture_execution(
        self, peer: str, run_id: str, expected_head: str
    ) -> ExecutionRunRecord:
        """Run the first prepared Turn once; an emitted unknown never retries here."""
        async with self._execution_lane:
            observed = await self._execution_actor(peer)
            with self._authority_gate().hold():
                snapshot = read_execution_history(self)
                lineage = [run for run in snapshot.records if run.run_id == run_id]
                if (
                    not lineage
                    or not isinstance(lineage[-1], ExecutionRunRecord)
                    or lineage[-1].head != expected_head
                ):
                    raise LoopRejected("missing exact execution Run")
                started = lineage[-1]
                if (
                    started.event != "TurnStarted"
                    or len(started.turns) != 1
                    or started.turns[-1].state != "PREPARING"
                    or started.turns[-1].attempts
                    or started.worker_session != self.current_worker()
                ):
                    raise LoopRejected("capture requires fresh initial Turn; unknown cannot retry")
            workspace_port = R13Workspace(self)
            workspace = await workspace_port.context(started)
            with self._authority_gate().hold():
                # Preparing workspace policy may publish before the actual read.
                # Bind the issued read cut, never silently refresh a stale context.
                issued = workspace_port._issued
                if issued is None or issued._issued is None:
                    raise LoopRejected("execution workspace lacks issued read")
                context = issued.proposal_context(issued._issued)
                snapshot = read_execution_history(self)
                current_lineage = [row for row in snapshot.records if row.run_id == run_id]
                if (
                    context.model_dump_json() != workspace.content
                    or context.batch.context.snapshot_frontier != snapshot.tenant_head
                    or not current_lineage
                    or current_lineage[-1] != started
                ):
                    raise LoopRejected("execution workspace or Run cut changed")
            content = json.dumps(
                {"prompt": started.prompt, "workspace": workspace.content, "prior_turns": []},
                sort_keys=True,
                separators=(",", ":"),
            )
            artifact = await render_execution_prompt(content)
            restricted = join_labels((execution_context_label(started), workspace.label))
            members = (
                workspace,
                VisibilityMember(
                    record_id=started.turns[-1].turn_id + "/context",
                    revision_head=started.head,
                    content=content,
                    provenance_head=started.head,
                    label_head=started.contour_head,
                    label=restricted,
                    producer="agent_loop",
                    surface="context",
                ),
                VisibilityMember(
                    record_id=artifact.prompt_id + "/" + artifact.version,
                    revision_head=artifact.content_hash,
                    content=artifact.rendered,
                    provenance_head=artifact.content_hash,
                    label_head=started.policy_head,
                    label=restricted,
                    producer="promptstrings",
                    surface="prompt",
                ),
                VisibilityMember(
                    record_id=started.turns[-1].turn_id + "/schema",
                    revision_head=artifact.digest(),
                    content=artifact.response_schema_json,
                    provenance_head=artifact.digest(),
                    label_head=started.policy_head,
                    label=DisclosureLabel(value="UNRESTRICTED", allowed_endpoints=()),
                    producer="agent_loop",
                    surface="schema",
                ),
            )
            accumulated = await self._publish_initial_transition(
                peer,
                AccumulateExecutionVisibility(
                    command_id="visibility:" + started.head, run=started, members=members
                ),
                expected_snapshot=snapshot,
            )
            manifest = ExecutionVisibilityManifest(
                tenant=started.tenant,
                principal=started.principal,
                contour_head=started.contour_head,
                run_id=started.run_id,
                turn_id=started.turns[-1].turn_id,
                generation=0,
                worker_session=started.worker_session,
                members=members,
                joined_label=join_labels(tuple(member.label for member in members)),
                artifact=artifact,
            )
            prepared = await self._publish_initial_transition(
                peer,
                PrepareExecutionRequest(
                    command_id="prepare:" + accumulated.head, run=accumulated, manifest=manifest
                ),
            )
            emitted = await self._publish_initial_transition(
                peer, EmitExecutionAttempt(command_id="emit:" + prepared.head, run=prepared)
            )
            with self._authority_gate().hold():
                self._check_execution_actor(observed)
                current = read_execution_history(self)
                selected = [run for run in current.records if run.run_id == run_id]
                if (
                    not selected
                    or selected[-1] != emitted
                    or self.current_worker() != emitted.worker_session
                    or self._supervisor.runtime()._realized_leaves["model"]
                    is not self._execution_model
                    or self._pending()
                ):
                    raise LoopRejected("selected emission or registered model changed")
            raw, receipt = await self._execution_model.invoke(emitted.turns[-1].attempts[-1])
            return await self._publish_initial_transition(
                peer,
                CaptureExecutionResponse(
                    command_id="capture:" + emitted.head, run=emitted, raw=raw, receipt=receipt
                ),
            )

    async def create_execution(
        self, peer: str, run_id: str, prompt: str, policy: BudgetPolicy
    ) -> ExecutionRunRecord:
        async with self._execution_lane:
            await self._execution_actor(peer)
            with self._authority_gate().hold():
                if any(run.run_id == run_id for run in read_execution_history(self).records):
                    raise LoopRejected("execution Run identity already exists")
            await R13Workspace(self).ingest(run_id, prompt)
            with self._authority_gate().hold():
                request = CreateExecutionRun(
                    command_id="create:" + run_id,
                    tenant=self._tenant_id,
                    principal="hermetic-principal",
                    run_id=run_id,
                    prompt=prompt,
                    policy=policy,
                    origin=self._execution_origin(run_id, prompt),
                    contour_head="hermetic-contour-v1",
                    policy_head="hermetic-policy-v1",
                    worker_session=self.current_worker(),
                )
            return await self._publish_initial_transition(peer, request)

    async def seal_execution(
        self, peer: str, run_id: str, expected_head: str
    ) -> ExecutionRunRecord:
        from .r14_execution_fanout import publish_execution_fanout

        async with self._execution_lane:
            return await publish_execution_fanout(self, peer, run_id, expected_head)

    async def seal_execution_complete(
        self, peer: str, run_id: str, expected_head: str
    ) -> ExecutionRunRecord:
        """Select the versioned registry companion for an eligible zero-call Complete."""
        from .r14_execution_fanout import publish_execution_fanout

        async with self._execution_lane:
            return await publish_execution_fanout(
                self, peer, run_id, expected_head, complete_registry=True
            )

    async def begin_execution(
        self, peer: str, run_id: str, expected_head: str
    ) -> ExecutionRunRecord:
        async with self._execution_lane:
            await self._execution_actor(peer)
            snapshot = read_execution_history(self)
            lineage = [run for run in snapshot.records if run.run_id == run_id]
            if (
                not lineage
                or not isinstance(lineage[-1], ExecutionRunRecord)
                or lineage[-1].head != expected_head
            ):
                raise LoopRejected("missing exact execution Run")
            active = await self._publish_initial_transition(
                peer, ActivateExecutionRun(command_id="activate:" + expected_head, run=lineage[-1])
            )
            return await self._publish_initial_transition(
                peer, StartInitialExecutionTurn(command_id="start:" + active.head, run=active)
            )


@asynccontextmanager
async def open_execution_runtime(
    database: Path, *, responses: tuple[bytes, ...] = ()
) -> AsyncIterator[R14ExecutionRuntime]:
    model = HermeticModel(responses)
    async with _open_runtime(
        database,
        tenant_id="hermetic-tenant",
        operator_secret=b"r13-hermetic-only",
        runtime_type=R14ExecutionRuntime,
        manifest=R14_EXECUTION_LIFECYCLE_PRODUCTION_MANIFEST,
        extra_leaves={
            "model": model,
            "effects_transport": HermeticEffectsProvider(
                receipt_key=b"hermetic-unissued", scenarios=()
            ),
        },
    ) as opened:
        runtime = cast(R14ExecutionRuntime, opened)
        runtime._execution_model = model
        model.session = runtime
        if runtime._trust.verify() is None:
            await runtime.bootstrap(
                database_instance_id="hermetic-database",
                principal_id="hermetic-principal",
                credential_id="hermetic-credential",
                session_id="hermetic-session",
                token="hermetic-bootstrap",
            )
        yield runtime
