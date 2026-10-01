"""Private broker authority for the one delivery-work generation-zero record."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import TYPE_CHECKING, Literal

from chiplog.capabilities.agent_loop.delivery_work_contracts import prepare_delivery_work_genesis
from chiplog.composition.h1_delivery_work_sources import (
    DeliveryWorkLocatorV1,
    H1DeliveryWorkSources,
)
from chiplog.platform._owner_publication_contracts import (
    DeliveryWorkGenesisBatchV1,
    ExactReplayQuery,
    PublicationRejected,
    RegisteredPublication,
)
from chiplog.platform._sqlite import (
    PhysicalPublicationCommand,
    PublicationVerificationMode,
    StoreAdmissionError,
    VerifiedOwnerPublication,
)
from chiplog.platform.delivery_work_genesis_binding_contracts import DeliveryWorkGenesisBindingV1
from chiplog.platform.owner_decision_journal import canonical_owner_publication_bytes
from chiplog.platform.owner_publications import PreparedOwnerPublication, SelectedOwnerDecision
from chiplog.platform.publication_inspection import (
    ExpectedPublicationRecord,
    PublicationExpectation,
    inspect_publication,
)

if TYPE_CHECKING:
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime


class DeliveryWorkGenesisAuthority:
    """Authenticates fresh genesis from one selected V4 source and its CAS cut."""

    def __init__(self) -> None:
        self._runtime: CommonCliExecutionRuntime | None = None
        self._prepared: dict[str, PreparedOwnerPublication] = {}

    def _bind_installed_runtime(self, runtime: CommonCliExecutionRuntime) -> None:
        from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime

        if self._runtime is not None or type(runtime) is not CommonCliExecutionRuntime:
            raise RuntimeError("delivery-work genesis runtime binding is unavailable")
        if type(getattr(runtime, "_h1_delivery_work_sources", None)) is not H1DeliveryWorkSources:
            raise RuntimeError("delivery-work selected source is not mounted")
        self._runtime = runtime

    def _source_matches(self, request: DeliveryWorkGenesisBatchV1) -> bool:
        runtime = self._runtime
        if runtime is None:
            return False
        source = runtime._h1_delivery_work_sources.read_absent(  # type: ignore[attr-defined]
            DeliveryWorkLocatorV1(request.selected_source.intent_id)
        )
        return (
            source.tenant_id == request.identity.tenant_id
            and source.decision_id == request.selected_decision_id
            and source.decision_fingerprint == request.selected_decision_fingerprint
            and source.owner_source() == request.selected_source
        )

    async def prepare(
        self, request: RegisteredPublication
    ) -> PreparedOwnerPublication | PublicationRejected:
        if type(request) is not DeliveryWorkGenesisBatchV1 or not self._source_matches(request):
            return PublicationRejected(
                kind="DENIED",
                tenant_id=request.identity.tenant_id,
                command_id=request.identity.command_id,
                reason="delivery-work genesis selected source differs",
            )
        expected = prepare_delivery_work_genesis(request.selected_source)
        if request.complete_records[0].canonical_bytes != expected.canonical_bytes():
            return PublicationRejected(
                kind="DENIED",
                tenant_id=request.identity.tenant_id,
                command_id=request.identity.command_id,
                reason="delivery-work genesis derivation differs",
            )
        binding = DeliveryWorkGenesisBindingV1(
            selected_decision_id=request.selected_decision_id,
            selected_decision_fingerprint=request.selected_decision_fingerprint,
            selected_source=request.selected_source,
            request_digest=hashlib.sha256(canonical_owner_publication_bytes(request)).hexdigest(),
        )
        prepared = PreparedOwnerPublication(
            request=request,
            issuance_id="delivery-work-genesis:" + request.selected_decision_id,
            fence_generation="r6",
            fence_frontier=0,
            predecessor_commitment=request.expected.expected_materialization_commitment,
            delivery_work_genesis_binding=binding,
        )
        self._prepared[request.identity.command_id] = prepared
        return prepared

    def authenticate_replay(self, query: ExactReplayQuery) -> PublicationRejected | None:
        if query.operation != "agent_loop.delivery_work.genesis.v1":
            return PublicationRejected(
                kind="DENIED",
                tenant_id=query.identity.tenant_id,
                command_id=query.identity.command_id,
                reason="delivery-work genesis replay operation differs",
            )
        return None

    def check_prepared(
        self, prepared: PreparedOwnerPublication
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
        request = prepared.request
        if (
            type(request) is not DeliveryWorkGenesisBatchV1
            or self._prepared.get(request.identity.command_id) != prepared
        ):
            return "DENIED"
        # The physical resolver performs the current subject CAS on its writer
        # connection.  Do not open a second reader while that transaction waits.
        return None

    def materialization_state(
        self, decision: SelectedOwnerDecision
    ) -> Literal["ABSENT", "EXACT_PREFIX", "COMPLETE", "CONFLICT"]:
        request = decision.prepared.request
        runtime = self._runtime
        if type(request) is not DeliveryWorkGenesisBatchV1 or runtime is None:
            return "CONFLICT"
        try:
            with runtime._authority_gate().hold():
                connection = runtime._appender._materializer._connection  # type: ignore[attr-defined]
                record = request.complete_records[0]
                expectation = PublicationExpectation(
                    request.identity.tenant_id,
                    request.operation,
                    request.identity.command_id,
                    request.identity.command_fingerprint,
                    request.expected.tenant_frontier,
                    (
                        ExpectedPublicationRecord(
                            record.record_id,
                            record.owner,
                            record.schema_id,
                            record.canonical_bytes,
                            record.fingerprint,
                        ),
                    ),
                )
                connection.execute("BEGIN")
                try:
                    return inspect_publication(
                        connection, expectation, decision.tenant_commit_sequence
                    )
                finally:
                    connection.rollback()
        except sqlite3.Error, OSError, RuntimeError, TypeError, ValueError:
            return "CONFLICT"

    def check_selected_predecessor(self, decision: SelectedOwnerDecision) -> bool:
        request = decision.prepared.request
        runtime = self._runtime
        if type(request) is not DeliveryWorkGenesisBatchV1 or runtime is None:
            return False
        try:
            connection = runtime._appender._materializer._connection  # type: ignore[attr-defined]
            if not connection.in_transaction:
                return False
            record = request.complete_records[0]
            expectation = PublicationExpectation(
                request.identity.tenant_id,
                request.operation,
                request.identity.command_id,
                request.identity.command_fingerprint,
                request.expected.tenant_frontier,
                (
                    ExpectedPublicationRecord(
                        record.record_id,
                        record.owner,
                        record.schema_id,
                        record.canonical_bytes,
                        record.fingerprint,
                    ),
                ),
            )
            return (
                inspect_publication(connection, expectation, decision.tenant_commit_sequence)
                == "ABSENT"
            )
        except sqlite3.Error, OSError, RuntimeError, TypeError, ValueError:
            return False

    def verify(
        self,
        command: PhysicalPublicationCommand,
        mode: PublicationVerificationMode,
        connection: sqlite3.Connection,
    ) -> VerifiedOwnerPublication:
        runtime = self._runtime
        if runtime is None or command.operation_kind != "agent_loop.delivery_work.genesis.v1":
            raise StoreAdmissionError("delivery-work genesis authority is unavailable")
        selected = runtime._owner_decisions().lookup(command.tenant_id, command.idempotency_key)
        prepared = self._prepared.get(command.idempotency_key)
        request = None if selected is None else selected.prepared.request
        if request is None and prepared is not None:
            request = prepared.request
        if type(request) is not DeliveryWorkGenesisBatchV1 or (
            selected is not None and selected.prepared.request != request
        ):
            raise StoreAdmissionError("delivery-work genesis selected decision differs")
        record = request.complete_records[0]
        if (
            command.tenant_id != request.identity.tenant_id
            or command.idempotency_key != request.identity.command_id
            or command.request_fingerprint != request.identity.command_fingerprint
            or command.expected_head != request.expected.tenant_frontier
            or len(command.records) != 1
            or command.records[0].record_id != record.record_id
            or command.records[0].owner != record.owner
            or command.records[0].schema_id != record.schema_id
            or command.records[0].canonical_bytes != record.canonical_bytes
            or command.records[0].fingerprint != record.fingerprint
        ):
            raise StoreAdmissionError("delivery-work genesis physical projection differs")
        reserved = (record.record_id, record.record_id.removesuffix("g0") + "g1")
        rows = connection.execute(
            "SELECT record_id FROM records WHERE tenant_id=? AND record_id IN (?, ?)",
            (command.tenant_id, *reserved),
        ).fetchall()
        if mode == "ABSENT" and rows:
            raise StoreAdmissionError("delivery-work genesis subject is occupied")
        if mode in ("PRESELECT", "PRECOMMIT", "REPLAY") and tuple(rows) != ((record.record_id,),):
            raise StoreAdmissionError("delivery-work genesis subject membership differs")
        if mode in ("PRECOMMIT", "REPLAY") and selected is None:
            raise StoreAdmissionError("delivery-work genesis has no selected decision")
        binding = (
            None if selected is None else selected.prepared.delivery_work_genesis_binding
        ) or (None if prepared is None else prepared.delivery_work_genesis_binding)
        expected_binding = DeliveryWorkGenesisBindingV1(
            selected_decision_id=request.selected_decision_id,
            selected_decision_fingerprint=request.selected_decision_fingerprint,
            selected_source=request.selected_source,
            request_digest=hashlib.sha256(canonical_owner_publication_bytes(request)).hexdigest(),
        )
        if binding != expected_binding:
            raise StoreAdmissionError("delivery-work genesis binding is absent")
        return VerifiedOwnerPublication.from_command(
            command,
            binding_fingerprint=binding.request_digest,
            selected_identity=None if selected is None else selected.decision_id,
            selected_fingerprint=None if selected is None else selected.decision_fingerprint,
            expected_resulting=None if selected is None else selected.resulting_commitment,
            expected_commit_sequence=None if selected is None else selected.tenant_commit_sequence,
        )
