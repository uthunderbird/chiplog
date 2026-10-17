"""Private model custody and current-authorized consumption of retained responses."""

from __future__ import annotations

import hashlib
import secrets
from typing import TYPE_CHECKING, Any

from chiplog.capabilities.agent_loop.contracts import LoopHold, LoopRejected
from chiplog.capabilities.agent_loop.execution_client_transitions import ExecutionRun, capture
from chiplog.capabilities.agent_loop.model_custody_contracts import CustodiedAttempt
from chiplog.capabilities.agent_loop.model_runtime_replacement import (
    prepare_fenced_model_replacement,
)
from chiplog.platform.postgres import Connection, Record, Snapshot

if TYPE_CHECKING:
    from chiplog.composition.postgres_execution import PostgresExecutionRuntime

_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_attempt_custody (
    attempt_id text PRIMARY KEY,
    emitted_head text NOT NULL,
    binding bytea NOT NULL,
    capability_hash bytea NOT NULL,
    state text NOT NULL CHECK (state IN ('NEVER_STARTED','STARTED','FENCED_OUT')),
    max_response_bytes bigint NOT NULL,
    response bytea,
    receipt text,
    CHECK ((response IS NULL) = (receipt IS NULL))
);
ALTER TABLE model_attempt_custody ADD COLUMN IF NOT EXISTS scheduler_lease bytea;
"""


class PostgresModelCustody:
    def __init__(self, runtime: PostgresExecutionRuntime) -> None:
        self._runtime = runtime
        self._capabilities: dict[str, tuple[bytes, str]] = {}
        with runtime._transaction() as (connection, trust):
            runtime._actor(connection, trust, runtime.peer)
            connection.execute(_SCHEMA)

    def register(self, connection: Connection, emitted: ExecutionRun) -> None:
        """Only called on the connection publishing this exact emitted Run."""
        attempt = emitted.turns[-1].attempts[-1]
        if (
            attempt.state != "EMITTED_OUTCOME_UNKNOWN"
            or emitted.tenant != self._runtime.tenant_id
            or emitted.principal != self._runtime.principal_id
        ):
            raise LoopRejected("model custody requires exact emitted authority")
        capability = secrets.token_bytes(32)
        self._capabilities[attempt.attempt_id] = capability, emitted.head
        connection.execute(
            "INSERT INTO model_attempt_custody "
            "(attempt_id,emitted_head,binding,capability_hash,state,"
            "max_response_bytes,scheduler_lease) "
            "VALUES (%s,%s,%s,%s,'NEVER_STARTED',%s,%s)",
            (
                attempt.attempt_id,
                emitted.head,
                attempt.canonical_bytes(),
                hashlib.sha256(capability).digest(),
                emitted.policy.max_response_bytes,
                self._runtime.scheduler_fence(
                    self._runtime.database._snapshot(connection), emitted.run_id
                ),
            ),
        )

    def _sender_row(self, connection: Connection, attempt: CustodiedAttempt) -> Any:
        capability = self._capabilities.get(attempt.attempt_id)
        row = connection.execute(
            "SELECT emitted_head,binding,capability_hash,state,max_response_bytes,"
            "response,receipt,scheduler_lease "
            "FROM model_attempt_custody WHERE attempt_id=%s FOR UPDATE",
            (attempt.attempt_id,),
        ).fetchone()
        if (
            capability is None
            or row is None
            or row[0] != capability[1]
            or bytes(row[1]) != attempt.canonical_bytes()
            or not secrets.compare_digest(bytes(row[2]), hashlib.sha256(capability[0]).digest())
        ):
            raise PermissionError("model custody sender binding differs")
        return row

    def start(self, attempt: CustodiedAttempt) -> None:
        runtime = self._runtime
        with runtime._transaction() as (connection, trust):
            runtime._actor(connection, trust, runtime.peer)
            row = self._sender_row(connection, attempt)
            if row[3] != "NEVER_STARTED":
                raise LoopHold("model transport already started or fenced; no repeated SEND")
            snapshot = runtime.database._snapshot(connection)
            runtime.assert_scheduler_fence(
                snapshot, attempt.manifest.run_id, None if row[7] is None else bytes(row[7])
            )
            runs = [run for run in runtime._runs(snapshot) if run.run_id == attempt.manifest.run_id]
            if (
                not runs
                or runs[-1].state != "ACTIVE"
                or runs[-1].head != row[0]
                or not runs[-1].turns
                or not runs[-1].turns[-1].attempts
                or runs[-1].turns[-1].attempts[-1].canonical_bytes() != attempt.canonical_bytes()
            ):
                raise LoopHold("model transport requires the selected ACTIVE emitted Run")
            connection.execute(
                "UPDATE model_attempt_custody SET state='STARTED' WHERE attempt_id=%s",
                (attempt.attempt_id,),
            )
        # A successful commit is the one-shot permit. Reading STARTED is never one.

    def retain(self, attempt: CustodiedAttempt, raw: bytes, receipt: str) -> None:
        runtime = self._runtime
        # An old sender can retain immutable facts after worker takeover; it
        # cannot select a Run or acquire another transport permit through here.
        with runtime.database.connect() as connection:
            runtime.database._check_identity(connection)
            row = self._sender_row(connection, attempt)
            if row[3] != "STARTED" or not receipt or len(raw) > row[4]:
                raise LoopRejected("model response lacks started custody or exceeds its bound")
            if row[5] is not None:
                if bytes(row[5]) != raw or row[6] != receipt:
                    raise LoopRejected("model custody already retains another response")
                return
            connection.execute(
                "UPDATE model_attempt_custody SET response=%s,receipt=%s WHERE attempt_id=%s",
                (raw, receipt, attempt.attempt_id),
            )

    def retained(self, emitted: ExecutionRun) -> tuple[bytes, str]:
        runtime = self._runtime
        attempt = emitted.turns[-1].attempts[-1]
        with runtime._transaction() as (connection, trust):
            runtime._actor(connection, trust, runtime.peer)
            row = connection.execute(
                "SELECT emitted_head,binding,state,response,receipt "
                "FROM model_attempt_custody WHERE attempt_id=%s",
                (attempt.attempt_id,),
            ).fetchone()
            if (
                row is None
                or row[0] != emitted.head
                or bytes(row[1]) != attempt.canonical_bytes()
                or row[2] != "STARTED"
                or row[3] is None
                or row[4] is None
            ):
                raise LoopHold("model outcome unknown; no retained response or resend authority")
            return bytes(row[3]), str(row[4])

    def fence_unstarted(self, connection: Connection, emitted: ExecutionRun) -> None:
        """Called only inside the current writer's replacement publication."""
        attempt = emitted.turns[-1].attempts[-1]
        row = connection.execute(
            "SELECT emitted_head,binding,state FROM model_attempt_custody "
            "WHERE attempt_id=%s FOR UPDATE",
            (attempt.attempt_id,),
        ).fetchone()
        if (
            row is None
            or row[0] != emitted.head
            or bytes(row[1]) != attempt.canonical_bytes()
            or row[2] != "NEVER_STARTED"
        ):
            raise LoopHold("model attempt may have started; replacement is forbidden")
        connection.execute(
            "UPDATE model_attempt_custody SET state='FENCED_OUT' WHERE attempt_id=%s",
            (attempt.attempt_id,),
        )


async def recover_model_response(
    runtime: PostgresExecutionRuntime, peer: str, run_id: str, expected_head: str
) -> ExecutionRun:
    async with runtime._lane:
        snapshot = runtime._snapshot_for_peer(peer)
        runs = runtime._runs(snapshot)
        sources = [row for row in runs if row.run_id == run_id and row.head == expected_head]
        if (
            len(sources) != 1
            or sources[0].event != "ModelAttemptEmitted"
            or sources[0].turns[-1].attempts[-1].state != "EMITTED_OUTCOME_UNKNOWN"
        ):
            raise LoopRejected("recovery requires the exact emitted model Run")
        emitted = sources[0]
        batches = [
            batch
            for batch in snapshot.batches
            if batch.operation == "execution.emit"
            and any(record.record_id == emitted.head for record in batch.records)
        ]
        if len(batches) != 1:
            raise LoopRejected("model recovery emission publication is absent")
        key = batches[0].idempotency_key
        replay = runtime._replay_run(snapshot, "execution.capture", key)
        if replay is not None:
            return replay
        selected = [row for row in runs if row.run_id == run_id][-1]
        if selected != emitted:
            raise LoopRejected("stale selected model recovery attempt")
        raw, receipt = runtime._model_custody.retained(emitted)
        recovered = capture(emitted, raw, receipt)

        def guard(current: Snapshot) -> None:
            latest = [row for row in runtime._runs(current) if row.run_id == run_id]
            if not latest or latest[-1] != emitted:
                raise LoopRejected("model recovery selected generation changed")

        await runtime.publish_execution_batch(
            peer,
            "execution.capture",
            key,
            snapshot.head,
            (
                Record(
                    recovered.head, "agent_loop", recovered.schema_id, recovered.canonical_bytes()
                ),
            ),
            guard,
        )
        return recovered


async def replace_unstarted_model_attempt(
    runtime: PostgresExecutionRuntime, peer: str, run_id: str, expected_head: str
) -> ExecutionRun:
    async with runtime._lane:
        snapshot = runtime._snapshot_for_peer(peer)
        key = run_id + ":replacement:" + expected_head
        replay = runtime._replay_run(snapshot, "execution.emit", key)
        if replay is not None:
            return runtime._replay_run(snapshot, "execution.capture", key) or replay
        runs = [row for row in runtime._runs(snapshot) if row.run_id == run_id]
        if not runs or runs[-1].head != expected_head:
            raise LoopRejected("stale exact model replacement Run")
        original = runs[-1]
        if original.root_binding != "NOT_APPLICABLE":
            raise LoopHold("scheduler model replacement requires scheduler admission")
        if not any(
            batch.operation == "execution.emit"
            and any(record.record_id == original.head for record in batch.records)
            for batch in snapshot.batches
        ):
            raise LoopRejected("model replacement emission publication is absent")
        prepared, emitted = prepare_fenced_model_replacement(original, runtime.current_worker())

        def guard(current: Snapshot) -> None:
            selected = [row for row in runtime._runs(current) if row.run_id == run_id]
            if not selected or selected[-1] != original:
                raise LoopRejected("model replacement selected generation changed")

        def stage(connection: Connection) -> None:
            runtime._model_custody.fence_unstarted(connection, original)
            runtime._model_custody.register(connection, emitted)

        result = await runtime.publish_execution_batch(
            peer,
            "execution.emit",
            key,
            snapshot.head,
            tuple(
                Record(row.head, "agent_loop", row.schema_id, row.canonical_bytes())
                for row in (prepared, emitted)
            ),
            guard,
            transaction_action=stage,
        )
        if result.disposition != "COMMITTED":
            selected = runtime._snapshot_for_peer(peer)
            replay = runtime._replay_run(selected, "execution.emit", key)
            if replay is None:
                raise LoopHold("model replacement publication was not committed")
            return runtime._replay_run(selected, "execution.capture", key) or replay
        raw, receipt = await runtime.model.invoke(emitted.turns[-1].attempts[-1])
        recovered = capture(emitted, raw, receipt)
        latest = runtime._snapshot_for_peer(peer)

        def capture_guard(current: Snapshot) -> None:
            selected = [row for row in runtime._runs(current) if row.run_id == run_id]
            if not selected or selected[-1] != emitted:
                raise LoopRejected("replacement response selected generation changed")

        await runtime.publish_execution_batch(
            peer,
            "execution.capture",
            key,
            latest.head,
            (
                Record(
                    recovered.head, "agent_loop", recovered.schema_id, recovered.canonical_bytes()
                ),
            ),
            capture_guard,
        )
        return recovered
