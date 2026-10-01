"""Gate-held current authority read for one selected materialized delivery-work g1."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

from chiplog.capabilities.agent_loop.delivery_work_contracts import (
    DeliveryWorkSourceV1,
    DeliveryWorkStateV1,
    prepare_delivery_work_genesis,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_delivery_work_claim_sources import (
    H1DeliveryWorkClaimSourceHold,
    H1DeliveryWorkClaimSources,
)
from chiplog.platform._owner_publication_contracts import (
    DeliveryWorkClaimBatchV1,
    decode_delivery_work_claim_command,
)
from chiplog.platform.delivery_work_claim_binding_contracts import (
    DeliveryWorkClaimBindingV1,
    validate_delivery_work_claim_binding,
)
from chiplog.platform.owner_decision_journal import canonical_owner_publication_bytes
from chiplog.platform.owner_publications import SelectedOwnerDecision
from chiplog.platform.publication_inspection import (
    ExpectedPublicationRecord,
    PublicationExpectation,
    inspect_publication,
)


class H1DeliveryWorkCurrentClaimHold(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class H1DeliveryWorkCurrentClaimEvidence:
    """Inert exact evidence for a currently held g1; it has no send capability."""

    source: DeliveryWorkSourceV1
    genesis_decision_id: str
    genesis_decision_fingerprint: str
    claim_decision_id: str
    claim_decision_fingerprint: str
    g0: DeliveryWorkStateV1
    g1: DeliveryWorkStateV1
    binding: DeliveryWorkClaimBindingV1


class H1DeliveryWorkCurrentClaimReader:
    """Reconstruct a current g1 only at one authority-gated SQLite cut."""

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("installed common CLI runtime required")
        sources = getattr(runtime, "_h1_delivery_work_claim_sources", None)
        if type(sources) is not H1DeliveryWorkClaimSources:
            raise TypeError("installed delivery-work claim sources required")
        self._runtime: CommonCliExecutionRuntime | None = runtime
        self._sources: H1DeliveryWorkClaimSources | None = sources
        self._closed = False

    def close(self) -> None:
        self._closed = True
        self._runtime = None
        self._sources = None

    def read(self, intent_id: str) -> H1DeliveryWorkCurrentClaimEvidence:
        if type(intent_id) is not str or not intent_id:
            raise TypeError("delivery-work claim intent locator is invalid")
        runtime, sources = self._require_open()
        try:
            with runtime._authority_gate().hold():
                connection = runtime._appender._materializer._connection  # type: ignore[attr-defined]
                connection.execute("BEGIN")
                try:
                    source, genesis = sources._selected_source_held(intent_id)
                    g0 = sources._read_exact_g0(genesis, source, connection)
                    claim, binding, decision = self._selected_claim(
                        intent_id, source, genesis, g0
                    )
                    self._require_current(runtime, source.tenant_id, binding)
                    g1 = self._read_exact_g1(connection, claim, decision, binding)
                    return H1DeliveryWorkCurrentClaimEvidence(
                        source=source,
                        genesis_decision_id=genesis.decision_id,
                        genesis_decision_fingerprint=genesis.decision_fingerprint,
                        claim_decision_id=decision.decision_id,
                        claim_decision_fingerprint=decision.decision_fingerprint,
                        g0=g0,
                        g1=g1,
                        binding=binding,
                    )
                finally:
                    connection.rollback()
        except H1DeliveryWorkCurrentClaimHold:
            raise
        except (
            H1DeliveryWorkClaimSourceHold,
            sqlite3.Error,
            AttributeError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as error:
            raise H1DeliveryWorkCurrentClaimHold(
                "delivery-work current claim evidence is unavailable"
            ) from error

    def _require_open(
        self,
    ) -> tuple[CommonCliExecutionRuntime, H1DeliveryWorkClaimSources]:
        runtime, sources = self._runtime, self._sources
        if self._closed or runtime is None or sources is None:
            raise H1DeliveryWorkCurrentClaimHold("delivery-work current claim reader is closed")
        return runtime, sources

    def _selected_claim(
        self,
        intent_id: str,
        source: DeliveryWorkSourceV1,
        genesis: SelectedOwnerDecision,
        g0: DeliveryWorkStateV1,
    ) -> tuple[DeliveryWorkClaimBatchV1, DeliveryWorkClaimBindingV1, SelectedOwnerDecision]:
        runtime, _ = self._require_open()
        snapshot = runtime._owner_decisions().snapshot()
        candidates = tuple(
            item
            for item in snapshot.decisions
            if type(item.prepared.request) is DeliveryWorkClaimBatchV1
            and item.prepared.delivery_work_claim_binding is not None
            and item.prepared.delivery_work_claim_binding.selected_source.intent_id == intent_id
        )
        if len(candidates) != 1:
            raise H1DeliveryWorkCurrentClaimHold(
                "selected delivery-work g1 decision is missing or ambiguous"
            )
        decision = candidates[0]
        claim = decision.prepared.request
        binding = decision.prepared.delivery_work_claim_binding
        assert type(claim) is DeliveryWorkClaimBatchV1
        assert binding is not None
        try:
            validate_delivery_work_claim_binding(claim, binding)
        except ValueError as error:
            raise H1DeliveryWorkCurrentClaimHold(
                "selected delivery-work g1 binding differs"
            ) from error
        expected_g0 = prepare_delivery_work_genesis(source)
        if (
            claim.identity.command_id not in snapshot.materialized_command_ids
            or binding.request_digest
            != hashlib.sha256(canonical_owner_publication_bytes(claim)).hexdigest()
            or binding.selected_source != source
            or binding.genesis_decision_id != genesis.decision_id
            or binding.genesis_decision_fingerprint != genesis.decision_fingerprint
            or binding.claim_projection.predecessor != g0
            or binding.claim_projection.predecessor != expected_g0
            or binding.claim_projection.predecessor_head != g0.head
            or binding.claim_projection.predecessor_head != expected_g0.head
        ):
            raise H1DeliveryWorkCurrentClaimHold("selected delivery-work g1 chain differs")
        return claim, binding, decision

    @staticmethod
    def _expectation(claim: DeliveryWorkClaimBatchV1) -> PublicationExpectation:
        return PublicationExpectation(
            claim.identity.tenant_id,
            claim.operation,
            claim.identity.command_id,
            claim.identity.command_fingerprint,
            claim.expected.tenant_frontier,
            tuple(
                ExpectedPublicationRecord(
                    record.record_id,
                    record.owner,
                    record.schema_id,
                    record.canonical_bytes,
                    record.fingerprint,
                )
                for record in claim.complete_records
            ),
        )

    def _read_exact_g1(
        self,
        connection: sqlite3.Connection,
        claim: DeliveryWorkClaimBatchV1,
        decision: SelectedOwnerDecision,
        binding: DeliveryWorkClaimBindingV1,
    ) -> DeliveryWorkStateV1:
        expectation = self._expectation(claim)
        if (
            inspect_publication(connection, expectation, decision.tenant_commit_sequence)
            != "COMPLETE"
        ):
            raise H1DeliveryWorkCurrentClaimHold("selected delivery-work g1 is not physical")
        record = claim.complete_records[0]
        row = connection.execute(
            "SELECT owner,schema_id,canonical_bytes,commit_sequence FROM records "
            "WHERE tenant_id=? AND record_id=?",
            (claim.identity.tenant_id, record.record_id),
        ).fetchone()
        if row is None or tuple(row) != (
            record.owner,
            record.schema_id,
            record.canonical_bytes,
            decision.tenant_commit_sequence,
        ):
            raise H1DeliveryWorkCurrentClaimHold("physical delivery-work g1 differs")
        try:
            command = decode_delivery_work_claim_command(claim.operation, claim.claim_command)
            g1 = DeliveryWorkStateV1.model_validate_json(record.canonical_bytes)
        except ValueError as error:
            raise H1DeliveryWorkCurrentClaimHold("physical delivery-work g1 is invalid") from error
        if (
            g1.canonical_bytes() != record.canonical_bytes
            or g1.selected_source != binding.selected_source
            or g1.predecessor_head != binding.claim_projection.predecessor_head
            or g1.holder_id != binding.claimant_clock.holder_id
            or g1.session_id != binding.claimant_clock.session_id
            or g1.trusted_clock_ns != binding.claimant_clock.trusted_clock_ns
            or g1.lease_expires_at_ns != binding.claimant_clock.lease_expires_at_ns
            or command.projection != binding.claim_projection
        ):
            raise H1DeliveryWorkCurrentClaimHold("physical delivery-work g1 is noncanonical")
        return g1

    @staticmethod
    def _require_current(
        runtime: CommonCliExecutionRuntime, tenant_id: str, binding: DeliveryWorkClaimBindingV1
    ) -> None:
        session = runtime._supervisor.runtime().session("agent_loop")
        epoch, now_ns = runtime._require_dispatch_resources().clock()
        clock = binding.claimant_clock
        if (
            type(epoch) is not str
            or not epoch
            or type(now_ns) is not int
            or type(clock.trusted_clock_ns) is not int
            or type(clock.lease_expires_at_ns) is not int
            or now_ns < 0
            or session.tenant_id != tenant_id
            or session.owner_id != clock.holder_id
            or session.session_id != clock.session_id
            or str(session.broker_epoch) != clock.broker_epoch
            or session.generation_id != clock.runtime_generation
            or epoch != clock.clock_epoch
            or not clock.trusted_clock_ns <= now_ns < clock.lease_expires_at_ns
        ):
            raise H1DeliveryWorkCurrentClaimHold("current broker session or clock differs")
