"""Canonical journal/anchor lifecycle for the shared owner publication writer.

Selected bytes can be recovered without a live owner. Fresh semantic admission
belongs to the registered broker authority; this module does not issue it.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import ClassVar, Literal, cast

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.architecture.r7_runtime import R14_PRODUCTION_MANIFEST
from chiplog.capabilities.agent_loop.contracts import DurableCompanion, LoopSnapshot, RunRecord
from chiplog.composition.r7_planning import _open_runtime
from chiplog.composition.r13_planning import R13PlanningRuntime
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.authority_reads import capture_authority_storage_state
from chiplog.platform.owner_decision_journal import (
    IndependentOwnerDecisionJournal,
    OwnerJournalIntegrityError,
)
from chiplog.platform.owner_publications import (
    OwnerPublicationPending,
    PreparedOwnerPublication,
    SelectedOwnerDecision,
)


class AnchoredOwnerDecisionJournal(IndependentOwnerDecisionJournal):
    """Private composition binding; accepting a request here grants no authority."""

    def __init__(self, runtime: R14PlanningRuntime) -> None:
        self._runtime = runtime
        super().__init__(
            IndependentTenantDecisionJournal(
                runtime._database.with_suffix(runtime._database.suffix + ".owners-journal")
            ),
            runtime._tenant_id,
        )

    def select(
        self, prepared: PreparedOwnerPublication, resulting_commitment: str
    ) -> SelectedOwnerDecision:
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
                raise ValueError("selected predecessor differs from physical/independent anchor")
            return super().select(prepared, resulting_commitment)
        except OwnerJournalIntegrityError, OwnerPublicationPending:
            raise
        except (OSError, RuntimeError, ValueError, TypeError) as error:
            raise OwnerJournalIntegrityError(
                "select_anchored", runtime._tenant_id, command
            ) from error

    def materialized(self, decision: SelectedOwnerDecision) -> None:
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
    _owner_journal: AnchoredOwnerDecisionJournal | None = None
    _database_identity: tuple[str, int, int]

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
        self._check_database_identity()
        if self._pending_owners() or self._pending() or self._pending_gate_publications():
            raise OwnerPublicationPending("selected publication must finish before fresh selection")

    def decide_publication(self, command: PhysicalPublicationCommand, commitment: str) -> None:
        self._require_no_pending()
        super().decide_publication(command, commitment)

    def decide(
        self,
        record: RunRecord,
        expected: LoopSnapshot,
        commitment: str,
        companions: tuple[DurableCompanion, ...],
    ) -> None:
        self._require_no_pending()
        super().decide(record, expected, commitment, companions)

    def _publication_decision_guard(
        self, proposal_bytes: bytes | None, resulting_commitment: str
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
        self._require_no_pending()
        return super()._publication_decision_guard(proposal_bytes, resulting_commitment)

    def _anchor_materialization(self, identity: str, predecessor: str, resulting: str) -> None:
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

    async def _recover_exact(
        self, command: PhysicalPublicationCommand, predecessor: str, resulting: str
    ) -> None:
        self._check_database_identity()
        actual, _ = capture_authority_storage_state(self._database)
        if self._commitment_journal.load(self._tenant_id) not in (predecessor, resulting):
            raise OwnerJournalIntegrityError(
                "recover_anchor", self._tenant_id, command.idempotency_key
            ) from ValueError("independent anchor differs from selected predecessor/result")
        if actual == predecessor:

            def guard() -> Literal["INDETERMINATE"] | None:
                self._check_database_identity()
                if capture_authority_storage_state(self._database)[0] != predecessor:
                    return "INDETERMINATE"
                return None

            def selected_bytes(commitment: str) -> None:
                if commitment != resulting:
                    raise OwnerJournalIntegrityError(
                        "recover_result", self._tenant_id, command.idempotency_key
                    ) from ValueError("exact selected records produce a different commitment")

            outcome = await self._appender.submit(
                replace(command, admission_guard=guard, decision_guard=selected_bytes)
            )
            if outcome.disposition not in ("COMMITTED", "REPLAY"):
                raise OwnerPublicationPending("selected recovery: " + outcome.disposition)
            actual, _ = capture_authority_storage_state(self._database)
        if actual != resulting:
            raise OwnerJournalIntegrityError(
                "recover_physical", self._tenant_id, command.idempotency_key
            ) from ValueError("physical commitment differs from complete selected result")

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
            )
            self._owner_decisions().materialized(decision)
        for entry in loops:
            identity = str(entry["operation_id"])
            predecessor, resulting = str(entry["predecessor"]), str(entry["resulting"])
            await self._recover_exact(self._publication(entry), predecessor, resulting)
            self._anchor_materialization(identity, predecessor, resulting)
            self._append_decision({"version": 1, "kind": "MATERIALIZED", "operation_id": identity})
        await self._recover_pending()

    async def _recover_pending(self) -> None:
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
                await self._recover_exact(command, entry["predecessor"], entry["resulting"])
                self._anchor_materialization(identity, entry["predecessor"], entry["resulting"])
                self._gate.materialized(identity)
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
        manifest=R14_PRODUCTION_MANIFEST,
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
