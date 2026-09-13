"""R13 broker composition: isolated owner and independently decided event publications."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal, cast

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.adapters.driven.loop_sqlite import OWNER, SCHEMA
from chiplog.adapters.driven.r9_fence import CONVERSATION_OWNER, CONVERSATION_SCHEMA, SCREEN_SINK
from chiplog.architecture.r7_runtime import R13_PRODUCTION_MANIFEST
from chiplog.capabilities.agent_loop.contracts import (
    DurableCompanion,
    LocalPlanningReceipt,
    LoopRejected,
    LoopSnapshot,
    ProposalDisplay,
    RunRecord,
    TransitionRequest,
)
from chiplog.composition.r7_planning import _open_runtime
from chiplog.composition.r8 import R8PlanningRuntime
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.authority_reads import capture_authority_storage_state
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortRejected

if TYPE_CHECKING:
    from chiplog.composition.r13_planning import R13PlanningRuntime


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class R13Runtime(R8PlanningRuntime):
    _planning_read_variant: ClassVar[
        Literal["PLANNING_PUBLICATIONS", "PLANNING_PUBLICATIONS_R13"]
    ] = "PLANNING_PUBLICATIONS_R13"
    _record_contracts: ClassVar[dict[str, str]] = {
        **R8PlanningRuntime._record_contracts,
        OWNER: SCHEMA,
        CONVERSATION_OWNER: CONVERSATION_SCHEMA,
        "evidence_journal": "chiplog.evidence_journal.record.v1",
        "workspace_policy": "chiplog.workspace.policy.v1",
    }
    _derivative_contracts: ClassVar[tuple[str, ...]] = (SCREEN_SINK,)
    _loop_journal: IndependentTenantDecisionJournal | None = None
    _replaying_publication = False

    def _bind_appender(self) -> None:
        self._appender.bind_publication_observer(self)

    def decide_publication(self, command: PhysicalPublicationCommand, commitment: str) -> None:
        if self._replaying_publication:
            return
        if (
            command.tenant_id != self._tenant_id
            or command.operation_kind not in ("workspace.policy", "conversation.accept")
            or self._pending()
        ):
            raise LoopRejected("unregistered auxiliary publication or pending decision")
        predecessor = self._commitment_journal.load(self._tenant_id)
        if predecessor is None:
            raise LoopRejected("missing independent predecessor")
        self._append_decision(
            {
                "version": 1,
                "kind": "DECIDED",
                "operation_id": command.idempotency_key,
                "operation_kind": command.operation_kind,
                "expected_head": command.expected_head,
                "fingerprint": command.request_fingerprint,
                "predecessor": predecessor,
                "resulting": commitment,
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
            }
        )

    def publication_committed(self, command: PhysicalPublicationCommand) -> None:
        if not self._replaying_publication:
            self._finish_decision(command.idempotency_key)

    def refresh_derivative_observation(self) -> None:
        actual, observation = capture_authority_storage_state(self._database)
        if actual != self._commitment_journal.load(self._tenant_id):
            raise LoopRejected("workspace read changed authoritative state")
        state = self._read_ledger.current_state(self._tenant_id)
        if state.file_wal_observation != observation:
            self._read_ledger.invalidate_storage_mutation(
                self._tenant_id, state.fingerprint(), actual, observation
            )

    def _loop_decisions(self) -> IndependentTenantDecisionJournal:
        if self._loop_journal is None:
            self._loop_journal = IndependentTenantDecisionJournal(
                self._database.with_suffix(self._database.suffix + ".loop-journal")
            )
        return self._loop_journal

    def _pending(self) -> tuple[dict[str, object], ...]:
        pending: dict[str, dict[str, object]] = {}
        for _, _, payload in self._loop_decisions().entries():
            entry = json.loads(payload)
            if not isinstance(entry, dict) or entry.get("version") != 1:
                raise LoopRejected("unknown loop decision journal entry")
            operation = entry.get("operation_id")
            if not isinstance(operation, str):
                raise LoopRejected("invalid loop decision identity")
            if entry.get("kind") == "DISPLAY":
                display = ProposalDisplay.model_validate_json(entry["display"])
                if display.display_id != operation or display.tenant != self._tenant_id:
                    raise LoopRejected("display physical identity mismatch")
                continue
            if entry.get("kind") == "DECIDED":
                if operation in pending:
                    raise LoopRejected("rival pending loop publication")
                pending[operation] = entry
            elif entry.get("kind") == "MATERIALIZED":
                if operation not in pending:
                    raise LoopRejected("orphaned loop materialization")
                del pending[operation]
            else:
                raise LoopRejected("unknown loop publication disposition")
        return tuple(pending.values())

    def _append_decision(self, value: object) -> None:
        journal = self._loop_decisions()
        entries = journal.entries()
        journal.append(_canonical(value), entries[-1][0] if entries else None)

    async def _prepare_startup(self) -> None:
        for pending in self._pending():
            actual, _ = capture_authority_storage_state(self._database)
            if actual == pending["predecessor"]:
                self._replaying_publication = True
                try:
                    result = await self._appender.submit(self._publication(pending))
                finally:
                    self._replaying_publication = False
                if result.disposition not in ("COMMITTED", "REPLAY"):
                    raise LoopRejected("decided loop publication recovery " + result.disposition)
                actual, _ = capture_authority_storage_state(self._database)
            if actual != pending["resulting"]:
                raise LoopRejected("loop decision does not match actual materialization")
            self._commitment_journal.commit(self._tenant_id, actual)
            self._append_decision(
                {"version": 1, "kind": "MATERIALIZED", "operation_id": pending["operation_id"]}
            )
        await super()._prepare_startup()

    def _publication(self, entry: dict[str, object]) -> PhysicalPublicationCommand:
        rows = cast(list[dict[str, str]], entry["records"])
        return PhysicalPublicationCommand(
            tenant_id=self._tenant_id,
            operation_kind=str(entry["operation_kind"]),
            idempotency_key=str(entry["operation_id"]),
            request_fingerprint=str(entry["fingerprint"]),
            expected_head=cast(int, entry["expected_head"]),
            fence_generation="r6",
            expected_fence_frontier=0,
            minimum_fence_frontier=0,
            records=tuple(
                PhysicalRecord(
                    row["record_id"],
                    row["owner"],
                    row["schema"],
                    base64.b64decode(row["payload"], validate=True),
                    row["digest"],
                )
                for row in rows
            ),
        )

    def validate(self, previous: RunRecord | None, proposed: RunRecord) -> None:
        if proposed.event in (
            "ModelAttemptPrepared",
            "ModelAttemptEmitted",
            "ModelResponseCaptured",
            "ModelResponseRejected",
            "ModelResponseReceived",
            "CompleteAcceptance",
            "ModelAttemptReplacedWithoutExposure",
        ):
            attempt = proposed.turns[-1].attempts[proposed.turns[-1].selector]
            if attempt.worker_session != self.current_worker():
                raise LoopRejected("stale owner generation/session; recovery HOLD")
        if (
            proposed.event in ("PlanningReceiptObserved", "CompleteAcceptance")
            and proposed.planning_receipts
        ):
            snapshot = json.loads(self._snapshot())
            for receipt in proposed.planning_receipts:
                matches = [
                    row for row in snapshot["commands"] if row["command_id"] == receipt.command_id
                ]
                if (
                    len(matches) != 1
                    or _canonical(matches[0]["result"]).decode() != receipt.canonical_result
                ):
                    raise LoopRejected("receipt is not the exact committed owner result")
                self._validate_receipt(receipt)
        runtime = self._supervisor.runtime()
        callee = runtime.session("agent_loop")
        request = TransitionRequest(previous=previous, proposed=proposed)
        result = runtime.call_sync(
            PublicPortCall(
                operation_id="agent_loop.validate_transition",
                request_id="loop:" + secrets.token_hex(16),
                caller=BrokerSession(
                    tenant_id=self._tenant_id,
                    broker_epoch=callee.broker_epoch,
                    generation_id=callee.generation_id,
                    owner_id="broker",
                    session_id=f"broker:{callee.generation_id}",
                ),
                callee=callee,
                schema_id="chiplog.agent-loop.transition.v1",
                canonical_payload=request.canonical_bytes(),
                budget=CallBudget(
                    remaining_calls=1,
                    remaining_depth=1,
                    absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
                    policy_version=1,
                ),
            )
        )
        if (
            isinstance(result, PublicPortRejected)
            or result.canonical_payload != proposed.canonical_bytes()
        ):
            raise LoopRejected("isolated loop owner rejected exact transition")

    def current_worker(self) -> str:
        session = self._supervisor.runtime().session("agent_loop")
        return f"{session.broker_epoch}:{session.generation_id}:{session.session_id}"

    def _validate_receipt(self, receipt: LocalPlanningReceipt) -> None:
        raise LoopRejected("receipt authority requires registered planning bridge")

    def companions(self, record: RunRecord) -> tuple[DurableCompanion, ...]:
        from chiplog.composition.r13_workspace import acceptance_companions

        return acceptance_companions(self, record)

    def decide(
        self,
        record: RunRecord,
        expected: LoopSnapshot,
        commitment: str,
        companions: tuple[DurableCompanion, ...],
    ) -> None:
        if self._pending():
            raise LoopRejected("pending durable decision requires materialization before new work")
        payload = record.canonical_bytes()
        if companions != self.companions(record):
            raise LoopRejected("conversation acceptance closure changed inside transaction")
        predecessor = self._commitment_journal.load(self._tenant_id)
        if predecessor is None:
            raise LoopRejected("missing independent authoritative commitment")
        self._append_decision(
            {
                "version": 1,
                "kind": "DECIDED",
                "operation_id": record.head,
                "operation_kind": "agent_loop",
                "expected_head": expected.tenant_head,
                "fingerprint": hashlib.sha256(payload).hexdigest(),
                "predecessor": predecessor,
                "resulting": commitment,
                "records": [
                    {
                        "record_id": record.head,
                        "owner": OWNER,
                        "schema": SCHEMA,
                        "payload": base64.b64encode(payload).decode(),
                        "digest": hashlib.sha256(payload).hexdigest(),
                    },
                    *(
                        {
                            "record_id": item.record_id,
                            "owner": item.owner,
                            "schema": item.schema_id,
                            "payload": item.payload_base64,
                            "digest": hashlib.sha256(
                                base64.b64decode(item.payload_base64, validate=True)
                            ).hexdigest(),
                        }
                        for item in companions
                    ),
                ],
            }
        )

    def committed(self, record: RunRecord) -> None:
        self._finish_decision(record.head)

    def _finish_decision(self, operation_id: str) -> None:
        pending = self._pending()
        actual, observation = capture_authority_storage_state(self._database)
        if not pending:
            if self._commitment_journal.load(self._tenant_id) != actual:
                raise LoopRejected("replayed loop publication has unknown current commitment")
            return
        if (
            len(pending) != 1
            or pending[0]["operation_id"] != operation_id
            or pending[0]["resulting"] != actual
        ):
            raise LoopRejected("loop commit differs from independent decision")
        self._commitment_journal.commit(self._tenant_id, actual)
        state = self._read_ledger.current_state(self._tenant_id)
        if state.materialization_commitment != actual or state.file_wal_observation != observation:
            self._read_ledger.invalidate_storage_mutation(
                self._tenant_id, state.fingerprint(), actual, observation
            )
        self._append_decision({"version": 1, "kind": "MATERIALIZED", "operation_id": operation_id})


@asynccontextmanager
async def open_r13_runtime(
    database: Path, *, model: HermeticModel | None = None
) -> AsyncIterator[R13PlanningRuntime]:
    from chiplog.composition.r13_planning import R13PlanningRuntime

    async with _open_runtime(
        database,
        tenant_id="hermetic-tenant",
        operator_secret=b"r13-hermetic-only",
        runtime_type=R13PlanningRuntime,
        manifest=R13_PRODUCTION_MANIFEST,
        extra_leaves={"model": model if model is not None else HermeticModel()},
    ) as runtime:
        result = cast(R13PlanningRuntime, runtime)
        if result._trust.verify() is None:
            await result.bootstrap(
                database_instance_id="hermetic-database",
                principal_id="hermetic-principal",
                credential_id="hermetic-credential",
                session_id="hermetic-session",
                token="hermetic-bootstrap",
            )
        yield result
