"""Private broker authority for the first selected delivery-work claim."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from chiplog.capabilities.agent_loop.delivery_work_contracts import (
    DELIVERY_WORK_OWNER,
    DELIVERY_WORK_SCHEMA,
    prepare_delivery_work_claim,
    prepare_delivery_work_claim_projection,
    prepare_delivery_work_genesis,
)
from chiplog.composition.h1_delivery_work_claim_sources import (
    H1DeliveryWorkClaimSources,
    _H1DeliveryWorkClaimCapture,
)
from chiplog.platform._owner_publication_contracts import (
    DeliveryWorkClaimBatchV1,
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
from chiplog.platform.delivery_work_claim_binding_contracts import (
    DeliveryWorkClaimantClockBindingV1,
    DeliveryWorkClaimBindingV1,
    validate_delivery_work_claim_binding,
)
from chiplog.platform.owner_decision_journal import canonical_owner_publication_bytes
from chiplog.platform.owner_publications import PreparedOwnerPublication, SelectedOwnerDecision
from chiplog.platform.publication_inspection import (
    ExpectedPublicationRecord,
    PublicationExpectation,
    inspect_publication,
)

if TYPE_CHECKING:
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime


_OPERATION = "agent_loop.delivery_work.claim.v1"


@dataclass(frozen=True, slots=True)
class _RegisteredClaim:
    """One private capture identity, irrevocably bound to one exact request."""

    request_bytes: bytes
    request: DeliveryWorkClaimBatchV1
    capture: _H1DeliveryWorkClaimCapture
    binding: DeliveryWorkClaimBindingV1


class DeliveryWorkClaimAuthority:
    """Binds a reader-issued g0 capture to one g1 selection and physical CAS."""

    def __init__(self) -> None:
        self._runtime: CommonCliExecutionRuntime | None = None
        self._sources: H1DeliveryWorkClaimSources | None = None
        self._registrations: dict[int, _RegisteredClaim] = {}
        self._prepared: dict[str, PreparedOwnerPublication] = {}

    def _bind_installed_runtime(self, runtime: CommonCliExecutionRuntime) -> None:
        from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime

        sources = getattr(runtime, "_h1_delivery_work_claim_sources", None)
        if (
            self._runtime is not None
            or type(runtime) is not CommonCliExecutionRuntime
            or type(sources) is not H1DeliveryWorkClaimSources
        ):
            raise RuntimeError("delivery-work claim runtime binding is unavailable")
        self._runtime = runtime
        self._sources = sources

    def register(
        self, request: DeliveryWorkClaimBatchV1, capture: _H1DeliveryWorkClaimCapture
    ) -> None:
        """Privately consume a reader capture for exactly one canonical request.

        Re-registering the identical bytes is harmless for a retried prepare.  A
        capture cannot be redirected to another command, even before g1 exists.
        Reader-issued identity itself is reproved by ``prepare`` through recheck.
        """
        if (
            type(request) is not DeliveryWorkClaimBatchV1
            or type(capture) is not _H1DeliveryWorkClaimCapture
        ):
            raise TypeError("exact delivery-work claim request and capture required")
        request_bytes = canonical_owner_publication_bytes(request)
        existing = self._registrations.get(id(capture))
        if existing is not None:
            if existing.capture is capture and existing.request_bytes == request_bytes:
                return
            raise RuntimeError("delivery-work claim capture is already bound")
        binding = self._binding_from_capture(request, capture)
        self._registrations[id(capture)] = _RegisteredClaim(
            request_bytes, request, capture, binding
        )

    def _binding_from_capture(
        self, request: DeliveryWorkClaimBatchV1, capture: _H1DeliveryWorkClaimCapture
    ) -> DeliveryWorkClaimBindingV1:
        projection = prepare_delivery_work_claim_projection(capture.source, capture.g0)
        claimant_clock = DeliveryWorkClaimantClockBindingV1(
            holder_id=capture.session.owner_id,
            session_id=capture.session.session_id,
            broker_epoch=str(capture.session.broker_epoch),
            runtime_generation=capture.session.generation_id,
            clock_epoch=capture.clock_epoch,
            trusted_clock_ns=capture.issued_at_ns,
            lease_expires_at_ns=capture.expires_at_ns,
        )
        binding = DeliveryWorkClaimBindingV1(
            genesis_decision_id=capture.genesis_decision_id,
            genesis_decision_fingerprint=capture.genesis_decision_fingerprint,
            selected_source=capture.source,
            claim_projection=projection,
            claimant_clock=claimant_clock,
            request_digest=hashlib.sha256(canonical_owner_publication_bytes(request)).hexdigest(),
        )
        try:
            validate_delivery_work_claim_binding(request, binding)
        except ValueError as error:
            raise RuntimeError("delivery-work claim request differs from reader capture") from error
        expected = prepare_delivery_work_claim(
            capture.source,
            capture.g0,
            holder_id=claimant_clock.holder_id,
            session_id=claimant_clock.session_id,
            lease_expires_at_ns=claimant_clock.lease_expires_at_ns,
            trusted_clock_ns=claimant_clock.trusted_clock_ns,
        )
        record = request.complete_records[0]
        if (
            record.owner != DELIVERY_WORK_OWNER
            or record.schema_id != DELIVERY_WORK_SCHEMA
            or record.record_id != expected.record_id
            or record.canonical_bytes != expected.canonical_bytes()
            or record.fingerprint != hashlib.sha256(record.canonical_bytes).hexdigest()
        ):
            raise RuntimeError("delivery-work claim g1 record differs from reader capture")
        return binding

    async def prepare(
        self, request: RegisteredPublication
    ) -> PreparedOwnerPublication | PublicationRejected:
        if type(request) is not DeliveryWorkClaimBatchV1:
            return self._denied(request, "delivery-work claim request differs")
        registered = next(
            (
                value
                for value in self._registrations.values()
                if value.request_bytes == canonical_owner_publication_bytes(request)
                and value.request is request
            ),
            None,
        )
        if registered is None or self._sources is None:
            return self._denied(request, "delivery-work claim capture is not privately registered")
        try:
            # This is the only live reader use.  Later writer modes must work
            # after g1 insertion and therefore cannot call the source again.
            self._sources.recheck(registered.capture)
            binding = self._binding_from_capture(request, registered.capture)
        except (RuntimeError, TypeError, ValueError) as error:
            return self._denied(request, str(error))
        if binding != registered.binding:
            return self._denied(request, "delivery-work claim capture binding changed")
        prepared = PreparedOwnerPublication(
            request=request,
            issuance_id="delivery-work-claim:" + binding.genesis_decision_id,
            fence_generation="r6",
            fence_frontier=0,
            predecessor_commitment=request.expected.expected_materialization_commitment,
            delivery_work_claim_binding=binding,
        )
        self._prepared[request.identity.command_id] = prepared
        return prepared

    @staticmethod
    def _denied(request: RegisteredPublication, reason: str) -> PublicationRejected:
        return PublicationRejected(
            kind="DENIED",
            tenant_id=request.identity.tenant_id,
            command_id=request.identity.command_id,
            reason=reason,
        )

    def authenticate_replay(self, query: ExactReplayQuery) -> PublicationRejected | None:
        if query.operation != _OPERATION:
            return PublicationRejected(
                kind="DENIED",
                tenant_id=query.identity.tenant_id,
                command_id=query.identity.command_id,
                reason="delivery-work claim replay operation differs",
            )
        return None

    def check_prepared(
        self, prepared: PreparedOwnerPublication
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
        request = prepared.request
        if (
            type(request) is not DeliveryWorkClaimBatchV1
            or self._prepared.get(request.identity.command_id) != prepared
            or prepared.delivery_work_claim_binding is None
        ):
            return "DENIED"
        # This admission guard runs before the writer inserts g1.  It repeats
        # live broker/session/clock checks directly, while preserving the rule
        # that PRESELECT and later modes never invoke the g1-absent source.
        if not self._current_claimant_clock_matches(prepared.delivery_work_claim_binding):
            return "STALE"
        return None

    def _current_claimant_clock_matches(self, binding: DeliveryWorkClaimBindingV1) -> bool:
        runtime = self._runtime
        if runtime is None:
            return False
        try:
            session = runtime._supervisor.runtime().session("agent_loop")
            epoch, now_ns = runtime._require_dispatch_resources().clock()
            clock = binding.claimant_clock
            return (
                session.tenant_id == binding.selected_source.tenant_id
                and session.owner_id == clock.holder_id
                and session.session_id == clock.session_id
                and str(session.broker_epoch) == clock.broker_epoch
                and session.generation_id == clock.runtime_generation
                and epoch == clock.clock_epoch
                and clock.trusted_clock_ns <= now_ns < clock.lease_expires_at_ns
            )
        except AttributeError, OSError, RuntimeError, TypeError, ValueError:
            return False

    def materialization_state(
        self, decision: SelectedOwnerDecision
    ) -> Literal["ABSENT", "EXACT_PREFIX", "COMPLETE", "CONFLICT"]:
        runtime = self._runtime
        if runtime is None:
            return "CONFLICT"
        try:
            with runtime._authority_gate().hold():
                connection = runtime._appender._materializer._connection
                connection.execute("BEGIN")
                try:
                    return self._state_on_connection(decision, connection)
                finally:
                    connection.rollback()
        except sqlite3.Error, OSError, RuntimeError, TypeError, ValueError:
            return "CONFLICT"

    def check_selected_predecessor(self, decision: SelectedOwnerDecision) -> bool:
        runtime = self._runtime
        if runtime is None:
            return False
        try:
            connection = runtime._appender._materializer._connection
            if not connection.in_transaction:
                return False
            return self._state_on_connection(decision, connection) == "ABSENT"
        except sqlite3.Error, OSError, RuntimeError, TypeError, ValueError:
            return False

    def _state_on_connection(
        self, decision: SelectedOwnerDecision, connection: sqlite3.Connection
    ) -> Literal["ABSENT", "COMPLETE", "CONFLICT"]:
        request, binding, genesis = self._selected_parts(decision)
        if not self._exact_g0(genesis, binding, connection):
            return "CONFLICT"
        g1_expectation = self._expectation(request)
        g1 = inspect_publication(connection, g1_expectation, decision.tenant_commit_sequence)
        if g1 == "ABSENT":
            return "ABSENT"
        return "COMPLETE" if g1 == "COMPLETE" else "CONFLICT"

    def _selected_parts(
        self, decision: SelectedOwnerDecision
    ) -> tuple[DeliveryWorkClaimBatchV1, DeliveryWorkClaimBindingV1, SelectedOwnerDecision]:
        request = decision.prepared.request
        binding = decision.prepared.delivery_work_claim_binding
        runtime = self._runtime
        if type(request) is not DeliveryWorkClaimBatchV1 or binding is None or runtime is None:
            raise ValueError("selected delivery-work claim binding is absent")
        validate_delivery_work_claim_binding(request, binding)
        candidates = tuple(
            item
            for item in runtime._owner_decisions().snapshot().decisions
            if item.decision_id == binding.genesis_decision_id
            and item.decision_fingerprint == binding.genesis_decision_fingerprint
        )
        if (
            len(candidates) != 1
            or type(candidates[0].prepared.request) is not DeliveryWorkGenesisBatchV1
        ):
            raise ValueError("selected delivery-work genesis differs")
        genesis = candidates[0]
        genesis_request = genesis.prepared.request
        assert type(genesis_request) is DeliveryWorkGenesisBatchV1
        expected_g0 = prepare_delivery_work_genesis(binding.selected_source)
        if (
            genesis_request.selected_source != binding.selected_source
            or genesis_request.complete_records[0].canonical_bytes != expected_g0.canonical_bytes()
            or binding.claim_projection.predecessor != expected_g0
            or binding.claim_projection.predecessor_head != expected_g0.head
        ):
            raise ValueError("selected delivery-work genesis binding differs")
        return request, binding, genesis

    @staticmethod
    def _expectation(
        request: DeliveryWorkClaimBatchV1 | DeliveryWorkGenesisBatchV1,
    ) -> PublicationExpectation:
        return PublicationExpectation(
            request.identity.tenant_id,
            request.operation,
            request.identity.command_id,
            request.identity.command_fingerprint,
            request.expected.tenant_frontier,
            tuple(
                ExpectedPublicationRecord(
                    record.record_id,
                    record.owner,
                    record.schema_id,
                    record.canonical_bytes,
                    record.fingerprint,
                )
                for record in request.complete_records
            ),
        )

    def _exact_g0(
        self,
        genesis: SelectedOwnerDecision,
        binding: DeliveryWorkClaimBindingV1,
        connection: sqlite3.Connection,
    ) -> bool:
        request = genesis.prepared.request
        assert type(request) is DeliveryWorkGenesisBatchV1
        if (
            inspect_publication(
                connection, self._expectation(request), genesis.tenant_commit_sequence
            )
            != "COMPLETE"
        ):
            return False
        record = request.complete_records[0]
        row = connection.execute(
            "SELECT owner,schema_id,canonical_bytes,commit_sequence FROM records "
            "WHERE tenant_id=? AND record_id=?",
            (request.identity.tenant_id, record.record_id),
        ).fetchone()
        return (
            row is not None
            and tuple(row)
            == (
                record.owner,
                record.schema_id,
                record.canonical_bytes,
                genesis.tenant_commit_sequence,
            )
            and record.canonical_bytes == binding.claim_projection.predecessor.canonical_bytes()
        )

    def verify(
        self,
        command: PhysicalPublicationCommand,
        mode: PublicationVerificationMode,
        connection: sqlite3.Connection,
    ) -> VerifiedOwnerPublication:
        runtime = self._runtime
        if runtime is None or command.operation_kind != _OPERATION:
            raise StoreAdmissionError("delivery-work claim authority is unavailable")
        selected = runtime._owner_decisions().lookup(command.tenant_id, command.idempotency_key)
        prepared = self._prepared.get(command.idempotency_key)
        request = None if selected is None else selected.prepared.request
        if request is None and prepared is not None:
            request = prepared.request
        if type(request) is not DeliveryWorkClaimBatchV1 or (
            selected is not None and selected.prepared.request != request
        ):
            raise StoreAdmissionError("delivery-work claim selected decision differs")
        binding = (None if selected is None else selected.prepared.delivery_work_claim_binding) or (
            None if prepared is None else prepared.delivery_work_claim_binding
        )
        if binding is None:
            raise StoreAdmissionError("delivery-work claim binding is absent")
        try:
            validate_delivery_work_claim_binding(request, binding)
            # A fresh writer reaches ABSENT and PRESELECT before selection, so
            # it must still match the private capture.  Selected recovery and
            # REPLAY intentionally use only durable binding and physical rows.
            if selected is None:
                expected = self._binding_from_capture(request, self._registered_capture(request))
                if binding != expected:
                    raise ValueError("binding differs from private capture")
        except (RuntimeError, TypeError, ValueError) as error:
            raise StoreAdmissionError("delivery-work claim binding differs") from error
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
            raise StoreAdmissionError("delivery-work claim physical projection differs")
        if mode in ("PRECOMMIT", "REPLAY") and selected is None:
            raise StoreAdmissionError("delivery-work claim has no selected decision")
        if (
            mode == "PRESELECT"
            and selected is None
            and not self._current_claimant_clock_matches(binding)
        ):
            raise StoreAdmissionError(
                "delivery-work claim current authority expired before selection"
            )
        if selected is not None:
            state = self._state_on_connection(selected, connection)
        else:
            # Before journal selection g0 is still independently exact.  Build a
            # temporary selected wrapper only after the writer has inserted g1.
            state = self._unselected_state(request, binding, connection)
        expected_state = "ABSENT" if mode == "ABSENT" else "COMPLETE"
        if state != expected_state:
            raise StoreAdmissionError("delivery-work claim physical state differs")
        return VerifiedOwnerPublication.from_command(
            command,
            binding_fingerprint=binding.request_digest,
            selected_identity=None if selected is None else selected.decision_id,
            selected_fingerprint=None if selected is None else selected.decision_fingerprint,
            expected_resulting=None if selected is None else selected.resulting_commitment,
            expected_commit_sequence=None if selected is None else selected.tenant_commit_sequence,
        )

    def _registered_capture(self, request: DeliveryWorkClaimBatchV1) -> _H1DeliveryWorkClaimCapture:
        request_bytes = canonical_owner_publication_bytes(request)
        matches = tuple(
            item.capture
            for item in self._registrations.values()
            if item.request is request and item.request_bytes == request_bytes
        )
        if len(matches) != 1:
            raise RuntimeError("delivery-work claim private capture is absent")
        return matches[0]

    def _unselected_state(
        self,
        request: DeliveryWorkClaimBatchV1,
        binding: DeliveryWorkClaimBindingV1,
        connection: sqlite3.Connection,
    ) -> Literal["ABSENT", "COMPLETE", "CONFLICT"]:
        runtime = self._runtime
        assert runtime is not None
        candidates = tuple(
            item
            for item in runtime._owner_decisions().snapshot().decisions
            if item.decision_id == binding.genesis_decision_id
            and item.decision_fingerprint == binding.genesis_decision_fingerprint
        )
        if (
            len(candidates) != 1
            or type(candidates[0].prepared.request) is not DeliveryWorkGenesisBatchV1
        ):
            return "CONFLICT"
        if not self._exact_g0(candidates[0], binding, connection):
            return "CONFLICT"
        # Before selection this branch is called only at ABSENT.  Seeing exact
        # g1 without a selected claim is an integrity fault, never a replay.
        state = inspect_publication(
            connection,
            self._expectation(request),
            request.expected.tenant_frontier + 1,
        )
        return state if state in ("ABSENT", "COMPLETE") else "CONFLICT"
