"""Native PostgreSQL execution: authenticated transactions and emission-before-SEND."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Literal, Protocol

from chiplog.adapters.driven.execution_prompts import (
    render_execution_prompt,
    render_history_execution_prompt,
)
from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    InitializedCallRecord,
    SealedResponseRecord,
)
from chiplog.capabilities.agent_loop.call_acceptance_preparation import call_subject_id
from chiplog.capabilities.agent_loop.contracts import (
    BudgetPolicy,
    LoopHold,
    LoopRejected,
    SchedulerRootReference,
    WorkerSessionPort,
)
from chiplog.capabilities.agent_loop.delivery_contracts import OriginSelection
from chiplog.capabilities.agent_loop.execution_client_contracts import (
    ConversationEntryV1,
    NativeCallStateV1,
)
from chiplog.capabilities.agent_loop.execution_client_fanout import seal
from chiplog.capabilities.agent_loop.execution_client_transitions import (
    ExecutionAttempt,
    ExecutionRun,
    begin,
    capture,
    continue_turn,
    create,
    current,
    emit,
    headed,
    prepare,
)
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_history_contracts import ExecutionRunRecordV3
from chiplog.capabilities.agent_loop.model_custody_contracts import ModelAttemptCustody
from chiplog.capabilities.agent_loop.readonly_history_tool_contracts import ConversationHistoryQuery
from chiplog.capabilities.agent_loop.readonly_runtime_contracts import (
    HistorySnapshotSelectionV2,
    ReadOnlyLineageV2,
)
from chiplog.capabilities.deployment_trust._model import DeploymentTrustService
from chiplog.composition.postgres_model_recovery import PostgresModelCustody
from chiplog.composition.postgres_planning import PostgresPlanningRuntime
from chiplog.platform.deployment_gate import DeploymentGateRequest
from chiplog.platform.postgres import (
    Batch,
    Connection,
    PostgresIntegrityError,
    PostgresServer,
    PublicationResult,
    Record,
    Snapshot,
    UserDatabase,
    transient_read_boundary,
)

if TYPE_CHECKING:
    from chiplog.capabilities.agent_loop.completion_client_contracts import (
        NativeCompletionAcceptanceV1,
        NativeDeliveryClaimV1,
        NativeDeliveryIntentV1,
        NativeDeliveryReceiptV1,
        NativeDeliverySendV1,
    )
    from chiplog.composition.postgres_effects import PostgresEffectsBinding
    from chiplog.composition.postgres_scheduler import PostgresScheduler

RUN_SCHEMAS = {"chiplog.agent-loop.execution-record.v2", "chiplog.agent-loop.execution-record.v3"}
INITIALIZED_SCHEMA = "chiplog.agent-loop.initialized-call.v1"
SEAL_SCHEMA = "chiplog.agent-loop.response-seal.v1"


class ExecutionModelPort(Protocol):
    session: WorkerSessionPort | None
    custody: ModelAttemptCustody | None

    async def invoke(self, attempt: ExecutionAttempt) -> tuple[bytes, str]: ...


def _run_record(run: ExecutionRun) -> Record:
    return Record(run.head, "agent_loop", run.schema_id, run.canonical_bytes())


class PostgresExecutionRuntime(PostgresPlanningRuntime):
    def __init__(
        self,
        database: UserDatabase,
        tenant: str,
        operator_secret: bytes,
        *,
        principal_id: str,
        credential_id: str,
        session_id: str,
        model: ExecutionModelPort,
        origin: OriginSelection,
        peer: str = "local-cli",
        effects_binding: PostgresEffectsBinding | None = None,
        authenticate_entitlement: Callable[[bytes, bytes], bool] | None = None,
    ) -> None:
        super().__init__(database, tenant, operator_secret, authenticate_entitlement)
        self.effects_binding = effects_binding
        self.scheduler: PostgresScheduler | None = None
        if model.session is not None:
            raise LoopRejected("model adapter already bound; provide a fresh adapter per runtime")
        self.tenant_id, self.principal_id = tenant, principal_id
        self.credential_id, self.session_id, self.peer = credential_id, session_id, peer
        self.model, self.origin = model, origin
        self._token = secrets.token_hex(24)
        self._worker = "postgres:" + principal_id + ":" + credential_id + ":" + session_id
        self._lane = asyncio.Lock()
        with self._transaction() as (connection, trust):
            self._context(trust, principal_id, credential_id, session_id)
            connection.execute("UPDATE user_state SET worker=%s", (self._token,))
        model.session = self
        self._model_custody = PostgresModelCustody(self)
        model.custody = self._model_custody

    def _actor(self, connection: Connection, trust: DeploymentTrustService, peer: str) -> None:
        if peer != self.peer:
            raise PermissionError("execution peer does not match authenticated endpoint")
        self._context(trust, self.principal_id, self.credential_id, self.session_id)
        row = connection.execute("SELECT worker FROM user_state").fetchone()
        if row != (self._token,):
            raise LoopHold("stale execution worker; open the current client")

    def current_worker(self) -> str:
        with self._transaction() as (connection, trust):
            self._actor(connection, trust, self.peer)
            return self._worker

    def scheduler_fence(self, snapshot: Snapshot, run_id: str) -> bytes | None:
        runs = [run for run in self._runs(snapshot) if run.run_id == run_id]
        if not runs:
            raise LoopRejected("scheduler action Run is absent")
        run = runs[-1]
        if not isinstance(run.root_binding, SchedulerRootReference):
            return None
        if self.scheduler is None:
            raise LoopHold("scheduled action requires its mounted scheduler")
        return self.scheduler.lease_fence(snapshot, run).canonical_bytes()

    def assert_scheduler_fence(
        self, snapshot: Snapshot, run_id: str, expected: bytes | None
    ) -> None:
        if self.scheduler_fence(snapshot, run_id) != expected:
            raise LoopHold("scheduler action lease fence changed")

    def snapshot(self) -> Snapshot:
        return self._snapshot_for_peer(self.peer)

    def _snapshot_for_peer(self, peer: str) -> Snapshot:
        with self._transaction() as (connection, trust):
            self._actor(connection, trust, peer)
            return self.database._snapshot(connection)

    def _runs(self, snapshot: Snapshot) -> tuple[ExecutionRun, ...]:
        found: list[ExecutionRun] = []
        latest: dict[str, ExecutionRun] = {}
        record_id = "<enumeration>"
        try:
            for batch in snapshot.batches:
                for record in batch.records:
                    if record.schema_id not in RUN_SCHEMAS:
                        continue
                    record_id = record.record_id
                    record_type = (
                        ExecutionRunRecordV3
                        if record.schema_id.endswith(".v3")
                        else ExecutionRunRecord
                    )
                    run = current(record_type.model_validate_json(record.canonical_bytes))
                    previous = latest.get(run.run_id)
                    if (
                        record.owner != "agent_loop"
                        or record_id != run.head
                        or run.tenant != self.tenant_id
                        or run.principal != self.principal_id
                        or run.canonical_bytes() != record.canonical_bytes
                        or run.predecessor != (None if previous is None else previous.head)
                        or (previous is not None and run.root_binding != previous.root_binding)
                    ):
                        raise ValueError("execution record identity or ancestry mismatch")
                    latest[run.run_id] = run
                    found.append(run)
            return tuple(found)
        except (ValueError, TypeError, KeyError) as error:
            raise PostgresIntegrityError(
                f"operation=execution.snapshot tenant={self.tenant_id} record_id={record_id}"
            ) from error

    def execution_record(self, run_id: str) -> ExecutionRun:
        rows = [run for run in self._runs(self.snapshot()) if run.run_id == run_id]
        if not rows:
            raise LoopRejected("execution Run is absent")
        return rows[-1]

    async def publish_execution_batch(
        self,
        peer: str,
        operation: str,
        idempotency_key: str,
        expected_head: int,
        records: tuple[Record, ...],
        validate: Callable[[Snapshot], None] | None = None,
        *,
        transaction_action: Callable[[Connection], None] | None = None,
    ) -> PublicationResult:
        if not records or len({record.record_id for record in records}) != len(records):
            raise ValueError("execution batch must be nonempty and unique")
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    [
                        record.record_id,
                        record.owner,
                        record.schema_id,
                        hashlib.sha256(record.canonical_bytes).hexdigest(),
                    ]
                    for record in records
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        with self._transaction() as (connection, trust):
            self._actor(connection, trust, peer)
            snapshot = self.database._snapshot(connection)
            previous = next(
                (
                    batch
                    for batch in snapshot.batches
                    if batch.operation == operation and batch.idempotency_key == idempotency_key
                ),
                None,
            )
            if previous is not None:
                if previous.fingerprint != fingerprint or previous.records != records:
                    raise LoopRejected("execution batch identity conflict")
                return PublicationResult("REPLAY", previous.sequence)
            if snapshot.head != expected_head:
                raise LoopRejected("stale execution tenant head")
            if validate is not None:
                validate(snapshot)
            selected = {run.run_id: run for run in self._runs(snapshot)}
            checked: set[str] = set()
            for item in records:
                if item.schema_id not in RUN_SCHEMAS:
                    continue
                kind = (
                    ExecutionRunRecordV3 if item.schema_id.endswith(".v3") else ExecutionRunRecord
                )
                proposed = current(kind.model_validate_json(item.canonical_bytes))
                source = selected.get(proposed.run_id)
                if source is not None:
                    if proposed.root_binding != source.root_binding:
                        raise LoopRejected("execution scheduler root binding changed")
                    if (
                        source.run_id not in checked
                        and operation not in {"execution.capture", "execution.call-outcome"}
                    ):
                        self.scheduler_fence(snapshot, source.run_id)
                        checked.add(source.run_id)
                elif isinstance(proposed.root_binding, SchedulerRootReference):
                    if self.scheduler is None:
                        raise LoopHold("scheduled initialization requires its mounted scheduler")
                    candidate = Snapshot(
                        snapshot.head + 1,
                        (
                            *snapshot.batches,
                            Batch(
                                operation, idempotency_key, fingerprint, snapshot.head + 1, records
                            ),
                        ),
                    )
                    lineage = self.scheduler.lineage(candidate, proposed.root_binding.root_id)
                    root = proposed.root_binding
                    if (
                        lineage.lineage.initial_run_id != proposed.run_id
                        or root.initial_run_id != proposed.run_id
                        or root.root_fingerprint != lineage.lineage.root_fingerprint
                        or root.subject_canonical_base64
                        != base64.b64encode(lineage.lineage.subject.canonical_bytes()).decode()
                    ):
                        raise LoopRejected("scheduled initialization root differs")
            if transaction_action is not None:
                transaction_action(connection)
            return self.database.append(
                connection, operation, idempotency_key, fingerprint, records, snapshot.head
            )

    def _replay_run(self, snapshot: Snapshot, operation: str, key: str) -> ExecutionRun | None:
        batch = next(
            (
                batch
                for batch in snapshot.batches
                if batch.operation == operation and batch.idempotency_key == key
            ),
            None,
        )
        if batch is None:
            return None
        # A batch may begin after prior Run records. Decode the selected final
        # member using the already validated full history instead of a new root.
        selected = {record.record_id for record in batch.records if record.schema_id in RUN_SCHEMAS}
        matches = [run for run in self._runs(snapshot) if run.head in selected]
        if not matches:
            raise PostgresIntegrityError("selected execution batch has no Run")
        return matches[-1]

    def prepare_execution_records(
        self,
        snapshot: Snapshot,
        *,
        run_id: str,
        prompt: str,
        policy: BudgetPolicy,
        origin: OriginSelection,
        worker: str,
        root_binding: Literal["NOT_APPLICABLE"] | SchedulerRootReference = "NOT_APPLICABLE",
        execution_version: Literal["v2", "v3"] = "v3",
        history_offset: int = 0,
    ) -> tuple[ExecutionRun, tuple[Record, ...]]:
        """Prepare companions; caller authenticates and atomically publishes the same cut.

        This performs no I/O and grants no authority. Scheduler callers verify
        the complete selected root/lineage; ingress callers verify their source.
        """
        if history_offset < 0:
            raise ValueError("history offset must be nonnegative")
        if any(row.run_id == run_id for row in self._runs(snapshot)):
            raise LoopRejected("execution Run already exists")
        run = create(
            version=execution_version,
            tenant=self.tenant_id,
            principal=self.principal_id,
            run_id=run_id,
            prompt=prompt,
            policy=policy,
            origin=origin,
            worker=worker,
            root_binding=root_binding,
        )
        entries = self._history(snapshot, snapshot.head, self.principal_id)
        entry = ConversationEntryV1(
            entry_id=run_id + "/ingress",
            tenant=self.tenant_id,
            principal=self.principal_id,
            ordinal=len(entries) + history_offset + 1,
            role="principal",
            text=prompt,
        )
        return run, (
            _run_record(run),
            Record(entry.entry_id, "conversation", entry.schema_id, entry.canonical_bytes()),
        )

    async def create_execution(
        self,
        peer: str,
        run_id: str,
        prompt: str,
        policy: BudgetPolicy,
        *,
        execution_version: Literal["v2", "v3"] = "v3",
    ) -> ExecutionRun:
        async with self._lane:
            snapshot = self._snapshot_for_peer(peer)
            run = create(
                version=execution_version,
                tenant=self.tenant_id,
                principal=self.principal_id,
                run_id=run_id,
                prompt=prompt,
                policy=policy,
                origin=self.origin,
                worker=self.current_worker(),
            )
            rows = [row for row in self._runs(snapshot) if row.run_id == run_id]
            if rows:
                if rows[0] != run:
                    raise LoopRejected("execution Run identity conflict")
                return rows[0]
            entries = self._history(snapshot, snapshot.head, self.principal_id)
            entry = ConversationEntryV1(
                entry_id=run_id + "/ingress",
                tenant=self.tenant_id,
                principal=self.principal_id,
                ordinal=len(entries) + 1,
                role="principal",
                text=prompt,
            )

            def guard(current_snapshot: Snapshot) -> None:
                if any(row.run_id == run_id for row in self._runs(current_snapshot)):
                    raise LoopRejected("execution Run already exists")

            await self.publish_execution_batch(
                peer,
                "execution.create",
                run_id,
                snapshot.head,
                (
                    _run_record(run),
                    Record(
                        entry.entry_id, "conversation", entry.schema_id, entry.canonical_bytes()
                    ),
                ),
                guard,
            )
            return run

    async def begin_execution(self, peer: str, run_id: str, expected_head: str) -> ExecutionRun:
        async with self._lane:
            snapshot = self._snapshot_for_peer(peer)
            key = run_id + ":" + expected_head
            prior = self._replay_run(snapshot, "execution.begin", key)
            if prior is not None:
                return prior
            run = self.execution_record(run_id)
            if run.head != expected_head:
                raise LoopRejected("stale exact execution Run")
            active, started = begin(run)
            await self.publish_execution_batch(
                peer,
                "execution.begin",
                key,
                snapshot.head,
                (_run_record(active), _run_record(started)),
            )
            return started

    async def capture_execution(self, peer: str, run_id: str, expected_head: str) -> ExecutionRun:
        async with self._lane:
            snapshot = self._snapshot_for_peer(peer)
            key = run_id + ":" + expected_head
            prior = self._replay_run(snapshot, "execution.capture", key)
            if prior is not None:
                return prior
            if any(
                batch.operation == "execution.emit" and batch.idempotency_key == key
                for batch in snapshot.batches
            ):
                raise LoopHold("model outcome unknown; blind resend forbidden")
            run = self.execution_record(run_id)
            if run.head != expected_head or run.worker_session != self.current_worker():
                raise LoopRejected("stale exact execution Run or worker")
            context = self._execution_context(snapshot, run)
            artifact = (
                await render_history_execution_prompt(context)
                if isinstance(run, ExecutionRunRecordV3)
                else await render_execution_prompt(context)
            )
            accumulated, prepared = prepare(run, artifact, context)
            emitted = emit(prepared)
            await self.publish_execution_batch(
                peer,
                "execution.emit",
                key,
                snapshot.head,
                tuple(_run_record(row) for row in (accumulated, prepared, emitted)),
                transaction_action=lambda connection: self._model_custody.register(
                    connection, emitted
                ),
            )
            self.current_worker()
            raw, receipt = await self.model.invoke(emitted.turns[-1].attempts[-1])
            captured = capture(emitted, raw, receipt)
            latest = self.snapshot()
            if self.execution_record(run_id) != emitted:
                raise LoopHold(
                    "model response retained externally; execution advanced before capture"
                )
            await self.publish_execution_batch(
                peer, "execution.capture", key, latest.head, (_run_record(captured),)
            )
            return captured

    async def suspend_execution(self, peer: str, run_id: str, expected_head: str) -> ExecutionRun:
        from chiplog.composition.postgres_run_recovery import suspend_execution

        return await suspend_execution(self, peer, run_id, expected_head)

    async def resume_execution(self, peer: str, run_id: str, expected_head: str) -> ExecutionRun:
        from chiplog.composition.postgres_run_recovery import resume_execution

        return await resume_execution(self, peer, run_id, expected_head)

    async def abort_execution(self, peer: str, run_id: str, expected_head: str) -> ExecutionRun:
        from chiplog.composition.postgres_run_recovery import abort_execution

        return await abort_execution(self, peer, run_id, expected_head)

    async def accept_completion(
        self, peer: str, run_id: str, expected_head: str
    ) -> NativeCompletionAcceptanceV1:
        from chiplog.composition.postgres_completion import accept_completion

        return await accept_completion(self, peer, run_id, expected_head)

    def delivery_intents(self, peer: str, run_id: str) -> tuple[NativeDeliveryIntentV1, ...]:
        from chiplog.capabilities.agent_loop.completion_client_contracts import (
            NativeCompletionAcceptanceV1,
            NativeDeliveryIntentV1,
        )
        from chiplog.composition.postgres_completion import rows

        snapshot = self._snapshot_for_peer(peer)
        heads = {
            value.head
            for value in rows(self, snapshot, NativeCompletionAcceptanceV1)
            if value.run_id == run_id
        }
        return tuple(
            value
            for value in rows(self, snapshot, NativeDeliveryIntentV1)
            if value.acceptance_head in heads
        )

    async def commit_delivery_send(
        self, peer: str, delivery_id: str, request: DeploymentGateRequest
    ) -> NativeDeliverySendV1:
        from chiplog.composition.postgres_completion import commit_delivery_send

        return await commit_delivery_send(self, peer, delivery_id, request)

    async def emit_delivery(self, peer: str, delivery_id: str) -> NativeDeliveryClaimV1:
        from chiplog.composition.postgres_completion import emit_delivery

        return await emit_delivery(self, peer, delivery_id)

    async def reconcile_delivery(
        self, peer: str, delivery_id: str
    ) -> NativeDeliveryReceiptV1 | None:
        from chiplog.composition.postgres_completion import reconcile_delivery

        return await reconcile_delivery(self, peer, delivery_id)

    async def replace_unstarted_model_attempt(
        self, peer: str, run_id: str, expected_head: str
    ) -> ExecutionRun:
        from chiplog.composition.postgres_model_recovery import replace_unstarted_model_attempt

        return await replace_unstarted_model_attempt(self, peer, run_id, expected_head)

    async def recover_model_response(
        self, peer: str, run_id: str, expected_emitted_head: str
    ) -> ExecutionRun:
        from chiplog.composition.postgres_model_recovery import recover_model_response

        return await recover_model_response(self, peer, run_id, expected_emitted_head)

    async def seal_execution(self, peer: str, run_id: str, expected_head: str) -> ExecutionRun:
        async with self._lane:
            snapshot = self._snapshot_for_peer(peer)
            key = run_id + ":" + expected_head
            prior = self._replay_run(snapshot, "execution.seal", key)
            if prior is not None:
                return prior
            run = self.execution_record(run_id)
            if run.head != expected_head:
                raise LoopRejected("stale exact captured Run")
            sealed, response, calls, states = seal(run)
            records = (
                _run_record(sealed),
                Record(
                    response.response_seal_id, "agent_loop", SEAL_SCHEMA, response.canonical_bytes()
                ),
                *(
                    Record(
                        call.original_call_id,
                        "agent_loop",
                        INITIALIZED_SCHEMA,
                        call.canonical_bytes(),
                    )
                    for call in calls
                ),
                *(
                    Record(state.head, "agent_loop", state.schema_id, state.canonical_bytes())
                    for state in states
                ),
            )
            await self.publish_execution_batch(peer, "execution.seal", key, snapshot.head, records)
            return sealed

    def initialized_calls(self) -> tuple[InitializedCallRecord, ...]:
        return self._initialized(self.snapshot())

    async def execute_readonly_call(
        self,
        peer: str,
        original_call_id: str,
        *,
        expected_counter_head: str | None = None,
    ) -> ReadOnlyLineageV2:
        from chiplog.composition.postgres_readonly import execute_readonly_call

        return await execute_readonly_call(
            self, peer, original_call_id, expected_counter_head=expected_counter_head
        )

    def _initialized(self, snapshot: Snapshot) -> tuple[InitializedCallRecord, ...]:
        rows = []
        record_id = "<enumeration>"
        try:
            for batch in snapshot.batches:
                for record in batch.records:
                    if record.schema_id != INITIALIZED_SCHEMA:
                        continue
                    record_id = record.record_id
                    call = InitializedCallRecord.model_validate_json(record.canonical_bytes)
                    if (
                        record.owner != "agent_loop"
                        or call.original_call_id != record_id
                        or call.original_call_id != call_subject_id(call.call.original)
                        or call.call.original.tenant_id != self.tenant_id
                        or call.canonical_bytes() != record.canonical_bytes
                    ):
                        raise ValueError("initialized call identity mismatch")
                    rows.append(call)
            return tuple(rows)
        except (ValueError, TypeError, KeyError) as error:
            raise PostgresIntegrityError(
                f"operation=execution.calls tenant={self.tenant_id} record_id={record_id}"
            ) from error

    def _call_states(self, snapshot: Snapshot) -> dict[str, NativeCallStateV1]:
        latest: dict[str, NativeCallStateV1] = {}
        initialized = {call.original_call_id: call for call in self._initialized(snapshot)}
        for batch in snapshot.batches:
            for record in batch.records:
                if record.schema_id != "chiplog.agent-loop.native-call-state.v1":
                    continue
                state = NativeCallStateV1.model_validate_json(record.canonical_bytes)
                previous = latest.get(state.original_call_id)
                original = initialized.get(state.original_call_id)
                if (
                    original is None
                    or record.record_id != state.head
                    or state.head
                    != "call-state:" + state.model_copy(update={"head": "pending"}).digest()
                    or state.predecessor != (None if previous is None else previous.head)
                ):
                    raise PostgresIntegrityError("native call state identity or ancestry mismatch")
                decoded = original
                if (
                    state.initialized.subject_id != decoded.original_call_id
                    or state.initialized.revision.head != "record:" + decoded.digest()
                    or state.initialized.revision.fingerprint != decoded.digest()
                    or state.run_id != decoded.call.original.original_run_id
                ):
                    raise PostgresIntegrityError(
                        "native call state differs from original initialization"
                    )
                latest[state.original_call_id] = state
        return latest

    def call_state(self, original_call_id: str) -> NativeCallStateV1:
        state = self._call_states(self.snapshot()).get(original_call_id)
        if state is None:
            raise LoopRejected("original call is absent")
        return state

    async def accept_execution_call(
        self,
        peer: str,
        original_call_id: str,
        authority_act_id: str,
        *,
        expected_head: str | None = None,
    ) -> NativeCallStateV1:
        return await self._choose_call_branch(
            peer, original_call_id, authority_act_id, "ACCEPTED", expected_head
        )

    async def cancel_execution_call(
        self,
        peer: str,
        original_call_id: str,
        authority_act_id: str,
        *,
        expected_head: str | None = None,
    ) -> NativeCallStateV1:
        return await self._choose_call_branch(
            peer, original_call_id, authority_act_id, "CANCELLED", expected_head
        )

    async def _choose_call_branch(
        self,
        peer: str,
        original_call_id: str,
        authority_act_id: str,
        branch: Literal["ACCEPTED", "CANCELLED"],
        expected_head: str | None,
    ) -> NativeCallStateV1:
        if not authority_act_id:
            raise LoopRejected("explicit authority act is required")
        async with self._lane:
            snapshot = self._snapshot_for_peer(peer)
            state = self._call_states(snapshot).get(original_call_id)
            if state is None:
                raise LoopRejected("original call is absent")
            original = next(
                call
                for call in self._initialized(snapshot)
                if call.original_call_id == original_call_id
            )
            if original.call.classification == "CONSEQUENTIAL" and branch == "ACCEPTED":
                raise LoopRejected("self effect acceptance requires exact preview adoption")
            replay = next(
                (
                    selected
                    for selected in (
                        NativeCallStateV1.model_validate_json(record.canonical_bytes)
                        for batch in snapshot.batches
                        for record in batch.records
                        if record.schema_id == "chiplog.agent-loop.native-call-state.v1"
                    )
                    if selected.original_call_id == original_call_id
                    and selected.state == branch
                    and selected.authority_act_id == authority_act_id
                ),
                None,
            )
            if replay is not None:
                return replay
            if original.call.classification == "READ_ONLY" and branch == "ACCEPTED":
                raise LoopRejected("read-only acceptance belongs to the shared lineage executor")
            if original.call.classification == "READ_ONLY" and any(
                lineage.original_call_id == original_call_id and lineage.attempts
                for lineage in (
                    ReadOnlyLineageV2.model_validate_json(record.canonical_bytes)
                    for batch in snapshot.batches
                    for record in batch.records
                    if record.schema_id == "chiplog.readonly.lineage.v2"
                )
            ):
                raise LoopRejected("read-only acceptance/cancellation branch already selected")
            if state.state == branch and state.authority_act_id == authority_act_id:
                return state
            if expected_head is not None and state.head != expected_head:
                raise LoopRejected("stale exact original-call state")
            if state.state != "INITIALIZED":
                raise LoopRejected("original-call acceptance/cancellation branch already selected")
            selected = headed(
                state.model_copy(
                    update={
                        "state": branch,
                        "predecessor": state.head,
                        "authority_act_id": authority_act_id,
                        "result_base64": base64.b64encode(b"NOT_EXECUTED").decode()
                        if branch == "CANCELLED"
                        else None,
                    }
                ),
                "call-state:",
            )
            await self.publish_execution_batch(
                peer,
                "execution.call-branch",
                original_call_id,
                snapshot.head,
                (
                    Record(
                        selected.head, "agent_loop", selected.schema_id, selected.canonical_bytes()
                    ),
                ),
            )
            return selected

    async def record_execution_call_outcome(
        self,
        peer: str,
        original_call_id: str,
        expected_head: str,
        *,
        outcome: Literal["SUCCEEDED", "FAILED_DEFINITE", "OUTCOME_UNKNOWN"],
        result: bytes,
    ) -> NativeCallStateV1:
        async with self._lane:
            snapshot = self._snapshot_for_peer(peer)
            state = self._call_states(snapshot).get(original_call_id)
            if state is None:
                raise LoopRejected("original call is absent")
            original = next(
                call
                for call in self._initialized(snapshot)
                if call.original_call_id == original_call_id
            )
            if original.call.classification == "CONSEQUENTIAL":
                raise LoopRejected("self effect outcome requires authenticated resolver")
            encoded = base64.b64encode(result).decode()
            replay = next(
                (
                    selected
                    for selected in (
                        NativeCallStateV1.model_validate_json(record.canonical_bytes)
                        for batch in snapshot.batches
                        for record in batch.records
                        if record.schema_id == "chiplog.agent-loop.native-call-state.v1"
                    )
                    if selected.original_call_id == original_call_id
                    and selected.predecessor == expected_head
                    and selected.outcome == outcome
                    and selected.result_base64 == encoded
                ),
                None,
            )
            if replay is not None:
                return replay
            if state.outcome == outcome and state.result_base64 == encoded:
                return state
            if state.head != expected_head or state.state != "ACCEPTED":
                raise LoopRejected("outcome requires exact accepted original call")
            if state.outcome == "OUTCOME_UNKNOWN":
                raise LoopHold(
                    "original open obligation requires resolver closure; "
                    "direct terminal flag denied"
                )
            selected = headed(
                state.model_copy(
                    update={
                        "predecessor": state.head,
                        "state": "ACCEPTED" if outcome == "OUTCOME_UNKNOWN" else "TERMINAL",
                        "outcome": outcome,
                        "result_base64": encoded,
                    }
                ),
                "call-state:",
            )
            await self.publish_execution_batch(
                peer,
                "execution.call-outcome",
                state.head,
                snapshot.head,
                (
                    Record(
                        selected.head, "agent_loop", selected.schema_id, selected.canonical_bytes()
                    ),
                ),
            )
            return selected

    def _closed_call_results(
        self,
        snapshot: Snapshot,
        run: ExecutionRun,
        turn_index: int,
        *,
        pending_readonly_call: str | None = None,
    ) -> tuple[dict[str, object], ...]:
        from chiplog.composition.postgres_readonly_successor import selected_turn_origin

        original_run_id = selected_turn_origin(self, snapshot, run, turn_index)
        turn = run.turns[turn_index]
        if turn.response_seal is None or turn.initialized_calls is None:
            raise LoopHold("Turn has no complete selected response seal")
        selected_seals = [
            SealedResponseRecord.model_validate_json(record.canonical_bytes)
            for batch in snapshot.batches
            for record in batch.records
            if record.schema_id == SEAL_SCHEMA and record.record_id == turn.response_seal.subject_id
        ]
        if (
            len(selected_seals) != 1
            or selected_seals[0].digest() != turn.response_seal.revision.fingerprint
            or selected_seals[0].complete_ordered_initialized != turn.initialized_calls
        ):
            raise PostgresIntegrityError("selected response seal membership differs")
        initialized = {call.original_call_id: call for call in self._initialized(snapshot)}
        states = self._call_states(snapshot)
        readonly = {
            lineage.original_call_id: lineage
            for lineage in (
                ReadOnlyLineageV2.model_validate_json(record.canonical_bytes)
                for batch in snapshot.batches
                for record in batch.records
                if record.schema_id == "chiplog.readonly.lineage.v2"
            )
        }
        results = []
        for reference in turn.initialized_calls:
            call = initialized.get(reference.subject_id)
            state = states.get(reference.subject_id)
            if (
                call is None
                or state is None
                or call.digest() != reference.revision.fingerprint
                or call.call.original.original_run_id != original_run_id
                or call.call.original.original_turn_id != turn.turn_id
            ):
                raise PostgresIntegrityError("selected seal original-call identity differs")
            result = b""
            if state.state == "CANCELLED":
                result = base64.b64decode(state.result_base64 or "", validate=True)
            elif call.call.classification == "READ_ONLY":
                if call.original_call_id == pending_readonly_call:
                    continue
                lineage = readonly.get(call.original_call_id)
                if lineage is None or not lineage.terminal:
                    raise LoopHold("selected read-only call remains pending")
                if (
                    lineage.selection.tenant_id != self.tenant_id
                    or lineage.selection.principal_id != self.principal_id
                ):
                    raise PermissionError("selected read-only lineage belongs to another user")
                outcome = lineage.attempts[-1].outcome
                assert outcome is not None
                result = outcome.value
            elif state.external_intent_id is not None:
                from chiplog.composition.postgres_effects import current_effect_result

                if self.effects_binding is None:
                    raise LoopHold("original effect provider registration unavailable")
                result = current_effect_result(
                    self, self.effects_binding, snapshot, call.original_call_id
                )
            elif state.state != "TERMINAL":
                raise LoopHold("selected consequential call or original obligation remains pending")
            else:
                result = base64.b64decode(state.result_base64 or "", validate=True)
            try:
                output: object = result.decode("utf-8")
            except UnicodeDecodeError:
                output = {"encoding": "base64", "value": base64.b64encode(result).decode()}
            results.append(
                {
                    "original_call_id": call.original_call_id,
                    "call_id": call.call.original.model_call_label,
                    "result": output,
                }
            )
        return tuple(results)

    def assert_all_selected_seals_closed(self, snapshot: Snapshot, run: ExecutionRun) -> None:
        """Check every retained seal against current state in the supplied CAS snapshot."""
        for index in range(len(run.turns)):
            if run.turns[index].response_seal is not None:
                self._closed_call_results(snapshot, run, index)

    def _execution_context(self, snapshot: Snapshot, run: ExecutionRun) -> str:
        from chiplog.composition.postgres_completion import provider_queries

        return json.dumps(
            {
                "prompt": run.prompt,
                "current_queries": [
                    query.model_dump(mode="json") for query in provider_queries(self, snapshot, run)
                ],
                "prior_turns": [
                    {
                        "turn_id": run.turns[index].turn_id,
                        "calls": self._closed_call_results(snapshot, run, index),
                    }
                    for index in range(len(run.turns) - 1)
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    async def continue_execution(self, peer: str, run_id: str, expected_head: str) -> ExecutionRun:
        async with self._lane:
            snapshot = self._snapshot_for_peer(peer)
            key = run_id + ":" + expected_head
            prior = self._replay_run(snapshot, "execution.continue", key)
            if prior is not None:
                return prior
            rows = [row for row in self._runs(snapshot) if row.run_id == run_id]
            if not rows or rows[-1].head != expected_head:
                raise LoopRejected("stale exact continuation Run")
            run = rows[-1]
            self.assert_all_selected_seals_closed(snapshot, run)
            continued = continue_turn(run)
            await self.publish_execution_batch(
                peer, "execution.continue", key, snapshot.head, (_run_record(continued),)
            )
            return continued

    async def retry_readonly_in_successor(
        self,
        peer: str,
        original_call_id: str,
        expected_pending_head: str,
        expected_predecessor_run_head: str,
        successor_run_id: str,
    ) -> ReadOnlyLineageV2:
        from chiplog.composition.postgres_readonly_successor import retry_readonly_in_successor

        return await retry_readonly_in_successor(
            self,
            peer,
            original_call_id,
            expected_pending_head,
            expected_predecessor_run_head,
            successor_run_id,
        )

    def _history(
        self, snapshot: Snapshot, frontier: int, principal: str
    ) -> tuple[ConversationEntryV1, ...]:
        if principal != self.principal_id or not 0 <= frontier <= snapshot.head:
            raise PermissionError("foreign principal or invalid history frontier")
        rows = []
        record_id = "<enumeration>"
        try:
            for batch in snapshot.batches:
                if batch.sequence > frontier:
                    continue
                for record in batch.records:
                    if record.schema_id != "chiplog.conversation.entry.pg.v1":
                        continue
                    record_id = record.record_id
                    row = ConversationEntryV1.model_validate_json(record.canonical_bytes)
                    if (
                        record.owner != "conversation"
                        or row.entry_id != record_id
                        or row.canonical_bytes() != record.canonical_bytes
                    ):
                        raise ValueError("conversation record identity mismatch")
                    rows.append(row)
        except (ValueError, TypeError, KeyError) as error:
            raise PostgresIntegrityError(
                f"operation=history.read tenant={self.tenant_id} record_id={record_id}"
            ) from error
        if any(row.tenant != self.tenant_id or row.principal != principal for row in rows) or tuple(
            row.ordinal for row in rows
        ) != tuple(range(1, len(rows) + 1)):
            raise PostgresIntegrityError("conversation identity or ordinal mismatch")
        return tuple(rows)

    def read_user_history_at(
        self, frontier: int, principal: str
    ) -> tuple[ConversationEntryV1, ...]:
        return self._history(self.snapshot(), frontier, principal)

    def _history_selection(self, snapshot: Snapshot, frontier: int) -> HistorySnapshotSelectionV2:
        rows = self._history(snapshot, frontier, self.principal_id)
        payload = json.dumps(
            {
                "tenant": self.tenant_id,
                "principal": self.principal_id,
                "frontier": frontier,
                "entries": [row.model_dump() for row in rows],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return HistorySnapshotSelectionV2(
            snapshot_id="history:" + hashlib.sha256(payload).hexdigest(),
            tenant_id=self.tenant_id,
            principal_id=self.principal_id,
            frontier=frontier,
        )

    def _history_cursor(self, cursor: str) -> tuple[int, int, str]:
        try:
            prefix, frontier, ordinal, digest = cursor.split(":")
            if (
                prefix != "pg-history"
                or not frontier.isdecimal()
                or not ordinal.isdecimal()
                or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
            ):
                raise ValueError("invalid cursor")
            return int(frontier), int(ordinal), "history:" + digest
        except (ValueError, TypeError) as error:
            raise LoopRejected("invalid stable history cursor") from error

    def select_history_snapshot(
        self,
        peer: str,
        query: ConversationHistoryQuery,
    ) -> HistorySnapshotSelectionV2:
        snapshot = self._snapshot_for_peer(peer)
        frontier = snapshot.head
        if query.after_cursor is not None:
            frontier, _ordinal, identity = self._history_cursor(query.after_cursor)
            selection = self._history_selection(snapshot, frontier)
            if selection.snapshot_id != identity:
                raise LoopRejected("history cursor belongs to another user or snapshot")
            return selection
        return self._history_selection(snapshot, frontier)

    def read_history_snapshot(
        self,
        peer: str,
        selection: HistorySnapshotSelectionV2,
        query: ConversationHistoryQuery,
    ) -> bytes:
        with transient_read_boundary(self.tenant_id):
            return self._read_history_snapshot(peer, selection, query)

    def _read_history_snapshot(
        self,
        peer: str,
        selection: HistorySnapshotSelectionV2,
        query: ConversationHistoryQuery,
    ) -> bytes:
        snapshot = self._snapshot_for_peer(peer)
        if (
            selection.tenant_id != self.tenant_id
            or selection.principal_id != self.principal_id
            or self._history_selection(snapshot, selection.frontier) != selection
        ):
            raise PermissionError("history selection differs from authenticated user snapshot")
        rows = self._history(snapshot, selection.frontier, self.principal_id)
        after = 0
        if query.after_cursor is not None:
            frontier, after, identity = self._history_cursor(query.after_cursor)
            if (
                frontier != selection.frontier
                or identity != selection.snapshot_id
                or after > len(rows)
            ):
                raise LoopRejected("history cursor differs from selected snapshot")
        page = rows[after : after + query.limit]
        last = after + len(page)
        next_cursor = (
            None
            if last >= len(rows)
            else (
                f"pg-history:{selection.frontier}:{last}:{selection.snapshot_id.removeprefix('history:')}"
            )
        )
        return json.dumps(
            {
                "snapshot_id": selection.snapshot_id,
                "frontier": selection.frontier,
                "entries": [
                    {
                        "entry_id": row.entry_id,
                        "sequence": row.ordinal,
                        "role": row.role,
                        "text": row.text,
                    }
                    for row in page
                ],
                "next_cursor": next_cursor,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()


@asynccontextmanager
async def open_postgres_execution_runtime(
    *,
    tenant_id: str,
    principal_id: str,
    credential_id: str,
    session_id: str,
    operator_secret: bytes,
    model: ExecutionModelPort,
    origin: OriginSelection,
    peer: str = "local-cli",
    effects_binding: PostgresEffectsBinding | None = None,
    authenticate_entitlement: Callable[[bytes, bytes], bool] | None = None,
) -> AsyncIterator[PostgresExecutionRuntime]:
    """Primary native PostgreSQL client factory, also exposed by chiplog.client."""
    database = PostgresServer.from_environment().user_database(tenant_id)
    database.provision()
    yield PostgresExecutionRuntime(
        database,
        tenant_id,
        operator_secret,
        principal_id=principal_id,
        credential_id=credential_id,
        session_id=session_id,
        model=model,
        origin=origin,
        peer=peer,
        effects_binding=effects_binding,
        authenticate_entitlement=authenticate_entitlement,
    )
