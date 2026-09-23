"""Canonical journal/anchor lifecycle for the shared owner publication writer.

Selected bytes can be recovered without a live owner. Fresh semantic admission
belongs to the registered broker authority; this module does not issue it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, closing
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, cast

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.architecture.r7_runtime import R14_FANOUT_PRODUCTION_MANIFEST
from chiplog.capabilities.agent_loop.contracts import DurableCompanion, LoopSnapshot, RunRecord
from chiplog.composition.r7_planning import _open_runtime
from chiplog.composition.r13_planning import R13PlanningRuntime
from chiplog.composition.r14_cancellation_contracts import (
    CANCELLATION_SCHEMA,
    NOT_EXECUTED_SCHEMA,
    CancelCallSubmission,
)
from chiplog.composition.r14_fanout_contracts import INITIALIZED_SCHEMA, SEAL_SCHEMA
from chiplog.platform._owner_publication_contracts import BrokerPublicationResult
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.authority_reads import (
    capture_authority_snapshot_commitment,
    capture_authority_storage_state,
)
from chiplog.platform.owner_decision_journal import (
    IndependentOwnerDecisionJournal,
    OwnerJournalIntegrityError,
    OwnerJournalSnapshot,
)
from chiplog.platform.owner_publications import (
    OwnerPublicationPending,
    PreparedOwnerPublication,
    SelectedOwnerDecision,
)
from chiplog.platform.publication_readback import inspect_publication

if TYPE_CHECKING:
    from chiplog.composition.r16_denial_registry import DenialIngress


class AnchoredOwnerDecisionJournal(IndependentOwnerDecisionJournal):
    """Private composition binding; accepting a request here grants no authority."""

    def __init__(self, runtime: R14PlanningRuntime) -> None:
        self._runtime = runtime
        self._snapshot_cache: (
            tuple[tuple[tuple[str, str | None, bytes], ...], OwnerJournalSnapshot] | None
        ) = None
        super().__init__(
            IndependentTenantDecisionJournal.for_authority_bundle(
                runtime._database.with_suffix(runtime._database.suffix + ".owners-journal"),
                authority_gate=runtime._authority_gate(),
            ),
            runtime._tenant_id,
        )

    def snapshot(self) -> OwnerJournalSnapshot:
        # Reuse only immutable semantic decoding. Every call still authenticates
        # the complete physical journal, key, chain and independent anchored head.
        with self._runtime._authority_gate().hold():
            try:
                entries = self._raw.entries()
                cached = self._snapshot_cache
                if cached is not None and cached[0] == entries:
                    return cached[1]
                snapshot = super().snapshot()
                if self._raw.entries() != entries or snapshot.head != (
                    entries[-1][0] if entries else None
                ):
                    raise ValueError("owner journal changed across semantic snapshot")
                self._snapshot_cache = (entries, snapshot)
                return snapshot
            except OwnerJournalIntegrityError:
                raise
            except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
                raise OwnerJournalIntegrityError(
                    "snapshot_anchored", self._runtime._tenant_id, "journal"
                ) from error

    def select(
        self, prepared: PreparedOwnerPublication, resulting_commitment: str
    ) -> SelectedOwnerDecision:
        with self._runtime._authority_gate().hold():
            runtime = self._runtime
            command = prepared.request.identity.command_id
            try:
                if self.lookup(runtime._tenant_id, command) is not None:
                    return super().select(prepared, resulting_commitment)
                runtime._require_no_pending()
                runtime._check_database_identity()
                anchored = runtime._commitment_journal.load(runtime._tenant_id)
                actual, _ = capture_authority_storage_state(runtime._database)
                if anchored != prepared.predecessor_commitment or actual != anchored:
                    raise ValueError(
                        "selected predecessor differs from physical/independent anchor"
                    )
                return super().select(prepared, resulting_commitment)
            except OwnerJournalIntegrityError, OwnerPublicationPending:
                raise
            except (OSError, RuntimeError, ValueError, TypeError) as error:
                raise OwnerJournalIntegrityError(
                    "select_anchored", runtime._tenant_id, command
                ) from error

    def materialized(self, decision: SelectedOwnerDecision) -> None:
        with self._runtime._authority_gate().hold():
            runtime = self._runtime
            command = decision.prepared.request.identity.command_id
            try:
                snapshot = self.snapshot()
                if self.lookup(runtime._tenant_id, command) != decision:
                    raise ValueError("unknown or changed selected decision")
                if command in snapshot.materialized_command_ids:
                    # A historical replay must never rewind a later authority anchor.
                    super().materialized(decision)
                    return
                pending = tuple(
                    item
                    for item in snapshot.decisions
                    if item.prepared.request.identity.command_id
                    not in snapshot.materialized_command_ids
                )
                if pending != (decision,) or snapshot.decisions[-1] != decision:
                    raise ValueError("materialization does not match unique selected tail")
                if runtime._pending() or runtime._pending_gate_publications():
                    raise ValueError("competing independently selected journal families")
                runtime._anchor_materialization(
                    command, decision.prepared.predecessor_commitment, decision.resulting_commitment
                )
                super().materialized(decision)
            except OwnerJournalIntegrityError:
                raise
            except (OSError, RuntimeError, ValueError, TypeError) as error:
                raise OwnerJournalIntegrityError(
                    "materialize_anchored", runtime._tenant_id, command
                ) from error


class R14PlanningRuntime(R13PlanningRuntime):
    """One physical writer and one pending publication across all journal families."""

    _record_contracts: ClassVar[dict[str, str]] = {
        **R13PlanningRuntime._record_contracts,
        "effects": "chiplog.effects.record.v1",
    }
    _record_schema_variants: ClassVar[tuple[tuple[str, str], ...]] = (
        *R13PlanningRuntime._record_schema_variants,
        ("agent_loop", SEAL_SCHEMA),
        ("agent_loop", INITIALIZED_SCHEMA),
        ("agent_loop", CANCELLATION_SCHEMA),
        ("agent_loop", NOT_EXECUTED_SCHEMA),
    )
    _owner_journal: AnchoredOwnerDecisionJournal | None = None
    _database_identity: tuple[str, int, int]

    async def cancel_call(self, peer: str, submission: CancelCallSubmission) -> RunRecord:
        from chiplog.composition.r14_cancellation import cancel_call

        return await cancel_call(self, peer, submission)

    async def publish_effect(
        self, peer: str, display_id: str, digest: str, adoption_act_id: str
    ) -> BrokerPublicationResult:
        from chiplog.composition.r16_effects_publication import publish_plan_effect

        return await publish_plan_effect(self, peer, display_id, digest, adoption_act_id)

    async def dispose_effect(
        self, peer: str, worker_run_id: str, ingress: DenialIngress
    ) -> BrokerPublicationResult:
        from chiplog.composition.r16_denial_publication import publish_denial

        return await publish_denial(self, peer, worker_run_id, ingress)

    def _verified_publications(
        self, owner_id: Literal["planning", "projections"]
    ) -> tuple[Any, ...]:
        from chiplog.adapters.driven.planning_sqlite import PlanningProjectionIntegrityError
        from chiplog.composition.r16_planning_reads import (
            merge_planning_publications,
            read_plan_effect_publications,
        )

        with self._authority_gate().hold():
            legacy = super()._verified_publications(owner_id)
            effects, frontier = read_plan_effect_publications(self)
            if self._verified_tenant_frontier != frontier:
                raise PlanningProjectionIntegrityError(
                    "planning subsets have different authenticated frontiers"
                )
            self._verified_tenant_frontier = frontier
            return merge_planning_publications(legacy, effects)

    def _bind_appender(self) -> None:
        path = self._database.resolve(strict=True)
        metadata = path.stat()
        self._database_identity = (str(path), metadata.st_dev, metadata.st_ino)
        super()._bind_appender()

    def _check_database_identity(self) -> None:
        path = self._database.resolve(strict=True)
        metadata = path.stat()
        if (str(path), metadata.st_dev, metadata.st_ino) != self._database_identity:
            raise ValueError("runtime physical database identity changed")

    def _owner_decisions(self) -> AnchoredOwnerDecisionJournal:
        with self._authority_gate().hold():
            if self._owner_journal is None:
                self._owner_journal = AnchoredOwnerDecisionJournal(self)
            return self._owner_journal

    def _pending_owners(self) -> tuple[SelectedOwnerDecision, ...]:
        snapshot = self._owner_decisions().snapshot()
        return tuple(
            item
            for item in snapshot.decisions
            if item.prepared.request.identity.command_id not in snapshot.materialized_command_ids
        )

    def _pending_gate_publications(self) -> tuple[tuple[str, bytes], ...]:
        self._configure_gate()
        assert self._gate is not None
        return tuple((identity, payload) for identity, payload in self._gate.pending() if payload)

    def _require_no_pending(self) -> None:
        with self._authority_gate().hold():
            self._check_database_identity()
            if self._pending_owners() or self._pending() or self._pending_gate_publications():
                raise OwnerPublicationPending(
                    "selected publication must finish before fresh selection"
                )

    def decide_publication(self, command: PhysicalPublicationCommand, commitment: str) -> None:
        with self._authority_gate().hold():
            self._require_no_pending()
            super().decide_publication(command, commitment)

    def decide(
        self,
        record: RunRecord,
        expected: LoopSnapshot,
        commitment: str,
        companions: tuple[DurableCompanion, ...],
    ) -> None:
        with self._authority_gate().hold():
            self._require_no_pending()
            super().decide(record, expected, commitment, companions)

    def _publication_decision_guard(
        self, proposal_bytes: bytes | None, resulting_commitment: str
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
        with self._authority_gate().hold():
            self._require_no_pending()
            return super()._publication_decision_guard(proposal_bytes, resulting_commitment)

    def _anchor_materialization(self, identity: str, predecessor: str, resulting: str) -> None:
        with self._authority_gate().hold():
            self._check_database_identity()
            actual, observation = capture_authority_storage_state(self._database)
            anchored = self._commitment_journal.load(self._tenant_id)
            if actual != resulting or anchored not in (predecessor, resulting):
                raise OwnerJournalIntegrityError(
                    "anchor_materialization", self._tenant_id, identity
                ) from ValueError("physical or anchored commitment differs from selected decision")
            state = self._read_ledger.current_state(self._tenant_id)
            if state.materialization_commitment not in (predecessor, resulting):
                raise OwnerJournalIntegrityError(
                    "anchor_read_ledger", self._tenant_id, identity
                ) from ValueError("read ledger commitment differs from selected predecessor/result")
            self._commitment_journal.commit(self._tenant_id, resulting)
            if (
                state.materialization_commitment != resulting
                or state.file_wal_observation != observation
            ):
                self._read_ledger.invalidate_storage_mutation(
                    self._tenant_id, state.fingerprint(), resulting, observation
                )

    def _loop_snapshot(self) -> LoopSnapshot:
        from chiplog.composition.r14_loop_history import read_loop_snapshot

        return read_loop_snapshot(self)

    def _selected_physical_state(
        self, command: PhysicalPublicationCommand
    ) -> tuple[str, str, str | None]:
        """Fresh gated cut; never join a caller's potentially stale workspace snapshot."""
        with self._authority_gate().hold():
            self._check_database_identity()
            with closing(
                sqlite3.connect(
                    self._database.resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None
                )
            ) as connection:
                connection.execute("BEGIN")
                actual = capture_authority_snapshot_commitment(connection, self._tenant_id)
                state = inspect_publication(connection, command, command.expected_head + 1)
                anchored = self._commitment_journal.load(self._tenant_id)
                self._check_database_identity()
                return state, actual, anchored

    def _loop_decision_materialized(
        self, operation_id: str, expected: dict[str, object] | None = None
    ) -> bool:
        with self._authority_gate().hold():
            if not super()._loop_decision_materialized(operation_id, expected):
                return False
            entries = [json.loads(payload) for _, _, payload in self._loop_decisions().entries()]
            entry = next(
                item
                for item in entries
                if item.get("kind") == "DECIDED" and item.get("operation_id") == operation_id
            )
            state, actual, anchored = self._selected_physical_state(self._publication(entry))
            if state != "COMPLETE" or actual != anchored:
                raise OwnerJournalIntegrityError(
                    "recover_history_physical", self._tenant_id, operation_id
                ) from ValueError("selected history or current physical anchor differs")
            return True

    async def _recover_exact(
        self,
        command: PhysicalPublicationCommand,
        predecessor: str,
        resulting: str,
        *,
        is_materialized: Callable[[], bool],
    ) -> None:
        def classify() -> str:
            with self._authority_gate().hold():
                historical = is_materialized()
                state, actual, anchored = self._selected_physical_state(command)
                if historical:
                    valid = state == "COMPLETE" and actual == anchored
                elif state == "ABSENT":
                    valid = actual == predecessor and anchored == predecessor
                else:
                    valid = (
                        state == "COMPLETE"
                        and actual == resulting
                        and anchored in (predecessor, resulting)
                    )
                if not valid:
                    raise OwnerJournalIntegrityError(
                        "recover_physical", self._tenant_id, command.idempotency_key
                    ) from ValueError("selected membership or physical/independent anchor differs")
                return state

        if classify() == "COMPLETE":
            return

        def guard() -> Literal["INDETERMINATE"] | None:
            return None if classify() == "ABSENT" else "INDETERMINATE"

        def selected_bytes(commitment: str) -> None:
            if commitment != resulting:
                raise OwnerJournalIntegrityError(
                    "recover_result", self._tenant_id, command.idempotency_key
                ) from ValueError("exact selected records produce a different commitment")

        outcome = await self._appender.submit(
            replace(command, admission_guard=guard, decision_guard=selected_bytes)
        )
        # REPLAY bypasses writer guards. Re-read history and exact physical membership,
        # allowing a different process to have completed and advanced the anchor.
        if classify() != "COMPLETE":
            raise OwnerPublicationPending("selected recovery: " + outcome.disposition)

    def _owner_decision_materialized(self, decision: SelectedOwnerDecision) -> bool:
        with self._authority_gate().hold():
            journal = self._owner_decisions()
            command = decision.prepared.request.identity.command_id
            if journal.lookup(self._tenant_id, command) != decision:
                raise OwnerJournalIntegrityError("recover_history", self._tenant_id, command)
            return command in journal.snapshot().materialized_command_ids

    def _finish_decision(
        self, operation_id: str, *, expected: dict[str, object] | None = None
    ) -> None:
        with self._authority_gate().hold():
            if self._loop_decision_materialized(operation_id, expected):
                return
            if self._pending_owners() or self._pending_gate_publications():
                raise OwnerPublicationPending(
                    "competing journal families during loop materialization"
                )
            super()._finish_decision(operation_id, expected=expected)

    def _finish_gate_decision(self, operation_id: str, execution: bytes) -> None:
        with self._authority_gate().hold():
            if self._gate_decision_materialized(operation_id, execution):
                return
            if self._pending_owners() or self._pending():
                raise OwnerPublicationPending(
                    "competing journal families during gate materialization"
                )
            super()._finish_gate_decision(operation_id, execution)

    @staticmethod
    def _owner_command(decision: SelectedOwnerDecision) -> PhysicalPublicationCommand:
        prepared, request = decision.prepared, decision.prepared.request
        return PhysicalPublicationCommand(
            tenant_id=request.identity.tenant_id,
            operation_kind=request.operation,
            idempotency_key=request.identity.command_id,
            request_fingerprint=request.identity.command_fingerprint,
            expected_head=request.expected.tenant_frontier,
            fence_generation=prepared.fence_generation,
            expected_fence_frontier=prepared.fence_frontier,
            minimum_fence_frontier=prepared.fence_frontier,
            records=tuple(
                PhysicalRecord(
                    row.record_id, row.owner, row.schema_id, row.canonical_bytes, row.fingerprint
                )
                for row in request.complete_records
            ),
        )

    async def _prepare_startup(self) -> None:
        with self._authority_gate().hold():
            from chiplog.composition.r14_loop_history import (
                validate_selected_cancellations,
                validate_selected_executions,
            )
            from chiplog.composition.r16_denial_history import validate_selected_denials

            validate_selected_cancellations(self)
            validate_selected_executions(self)
            validate_selected_denials(self._owner_decisions().snapshot())
            owners, loops, gates = (
                self._pending_owners(),
                self._pending(),
                self._pending_gate_publications(),
            )
            if len(owners) + len(loops) + len(gates) > 1:
                raise OwnerJournalIntegrityError(
                    "startup_pending", self._tenant_id, "journal-families"
                ) from ValueError("more than one independently selected publication is pending")
        for decision in owners:
            await self._recover_exact(
                self._owner_command(decision),
                decision.prepared.predecessor_commitment,
                decision.resulting_commitment,
                is_materialized=partial(self._owner_decision_materialized, decision),
            )
            self._owner_decisions().materialized(decision)
        for entry in loops:
            identity = str(entry["operation_id"])
            predecessor, resulting = str(entry["predecessor"]), str(entry["resulting"])
            await self._recover_exact(
                self._publication(entry),
                predecessor,
                resulting,
                is_materialized=partial(self._loop_decision_materialized, identity, entry),
            )
            self._finish_decision(identity, expected=entry)
        await self._recover_pending()

    async def _recover_pending(self) -> None:
        with self._authority_gate().hold():
            gates = self._pending_gate_publications()
            if gates and (self._pending_owners() or self._pending() or len(gates) != 1):
                raise OwnerJournalIntegrityError(
                    "recover_pending", self._tenant_id, "journal-families"
                ) from ValueError("gate recovery conflicts with another selected publication")
        assert self._gate is not None
        for identity, raw in gates:
            try:
                entry = json.loads(raw)
                if (
                    set(entry) != {"version", "predecessor", "resulting", "proposal"}
                    or entry["version"] != 1
                ):
                    raise ValueError("unknown selected gate materialization")
                proposal = json.loads(base64.b64decode(entry["proposal"], validate=True))
                command = PhysicalPublicationCommand(
                    tenant_id=self._tenant_id,
                    operation_kind="planning.create_intention_line",
                    idempotency_key=identity,
                    request_fingerprint=proposal["request_fingerprint"],
                    expected_head=proposal["commit_sequence"] - 1,
                    fence_generation="r6",
                    expected_fence_frontier=0,
                    minimum_fence_frontier=0,
                    records=tuple(
                        PhysicalRecord(
                            row["record_id"],
                            "planning",
                            "chiplog.planning.record.v1",
                            base64.b64decode(row["canonical_bytes"], validate=True),
                            hashlib.sha256(
                                base64.b64decode(row["canonical_bytes"], validate=True)
                            ).hexdigest(),
                        )
                        for row in proposal["records"]
                    ),
                )
                await self._recover_exact(
                    command,
                    entry["predecessor"],
                    entry["resulting"],
                    is_materialized=partial(self._gate_decision_materialized, identity, raw),
                )
                self._finish_gate_decision(identity, raw)
            except OwnerJournalIntegrityError, OwnerPublicationPending:
                raise
            except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
                raise OwnerJournalIntegrityError(
                    "recover_gate", self._tenant_id, identity
                ) from error


@asynccontextmanager
async def open_r14_runtime(
    database: Path, *, model: HermeticModel | None = None
) -> AsyncIterator[R14PlanningRuntime]:
    async with _open_runtime(
        database,
        tenant_id="hermetic-tenant",
        operator_secret=b"r13-hermetic-only",
        runtime_type=R14PlanningRuntime,
        manifest=R14_FANOUT_PRODUCTION_MANIFEST,
        extra_leaves={"model": model if model is not None else HermeticModel()},
    ) as opened:
        runtime = cast(R14PlanningRuntime, opened)
        if runtime._trust.verify() is None:
            await runtime.bootstrap(
                database_instance_id="hermetic-database",
                principal_id="hermetic-principal",
                credential_id="hermetic-credential",
                session_id="hermetic-session",
                token="hermetic-bootstrap",
            )
        yield runtime
