"""Private, gate-held source for a claimable materialized delivery-work genesis."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Literal

from chiplog.capabilities.agent_loop.completion_owner_record_contracts import (
    CompletionCanonicalMember,
    make_terminal_manifest_member,
    make_terminal_run_member,
)
from chiplog.capabilities.agent_loop.delivery_work_contracts import (
    DELIVERY_WORK_OWNER,
    DELIVERY_WORK_SCHEMA,
    DeliveryWorkSourceMemberV1,
    DeliveryWorkSourceV1,
    DeliveryWorkStateV1,
    delivery_work_record_id,
    prepare_delivery_work_genesis,
)
from chiplog.capabilities.effects.scoped_intent_record_contracts import (
    ScopedIntentCanonicalMemberV3,
    make_scoped_intent_member,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV4
from chiplog.composition.scoped_delivery_selected_sources import ScopedDeliverySelectedSources
from chiplog.platform._owner_publication_contracts import (
    CompleteDeliveryBatchV2,
    DeliveryWorkGenesisBatchV1,
    OwnerRecordBytes,
)
from chiplog.platform.broker import BrokerSession
from chiplog.platform.owner_decision_journal import canonical_owner_publication_bytes
from chiplog.platform.owner_publications import SelectedOwnerDecision
from chiplog.platform.publication_inspection import (
    ExpectedPublicationRecord,
    PublicationExpectation,
    inspect_publication,
)
from chiplog.platform.workspace_snapshot import read_connection

# The mounted H1 source recapture can take minutes.  The broker retains one
# bounded first-claim window through that admission, with exact epoch/session
# checks at PRESELECT; this is not a renewal after selection.
_LEASE_DURATION_NS = 15 * 60_000_000_000


class H1DeliveryWorkClaimSourceHold(RuntimeError):
    pass


def _exact_owner_member_count(
    member: DeliveryWorkSourceMemberV1, records: tuple[OwnerRecordBytes, ...]
) -> int:
    """Compare the two wire types by their signed fields, not Python class equality."""
    return sum(
        (
            row.owner,
            row.record_kind,
            row.record_id,
            row.schema_id,
            row.canonical_bytes,
            row.fingerprint,
        )
        == (
            member.owner,
            member.record_kind,
            member.record_id,
            member.schema_id,
            member.canonical_record_bytes,
            member.fingerprint,
        )
        for row in records
    )


@dataclass(frozen=True, slots=True)
class _H1DeliveryWorkClaimCapture:
    """Reader-issued, nonserializable evidence for one later fresh claim admission."""

    source: DeliveryWorkSourceV1
    genesis_decision_id: str
    genesis_decision_fingerprint: str
    g0: DeliveryWorkStateV1
    g0_head: str
    session: BrokerSession
    clock_epoch: str
    issued_at_ns: int
    expires_at_ns: int


class H1DeliveryWorkClaimSources:
    """Read exactly one selected-and-materialized g0 and current broker facts."""

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("installed common CLI runtime required")
        self._runtime = runtime
        self._selected = ScopedDeliverySelectedSources(runtime)
        self._issued: dict[int, _H1DeliveryWorkClaimCapture] = {}
        self._closed = False

    def close(self) -> None:
        self._closed = True
        self._issued.clear()

    def capture(self, intent_id: str) -> _H1DeliveryWorkClaimCapture:
        if type(intent_id) is not str or not intent_id:
            raise TypeError("delivery-work claim intent locator is invalid")
        with self._runtime._authority_gate().hold():
            capture = self._capture_held(intent_id)
            self._issued[id(capture)] = capture
            return capture

    def recheck(self, capture: _H1DeliveryWorkClaimCapture) -> None:
        if (
            type(capture) is not _H1DeliveryWorkClaimCapture
            or self._issued.get(id(capture)) is not capture
        ):
            raise H1DeliveryWorkClaimSourceHold("delivery-work claim capture is not reader-issued")
        with self._runtime._authority_gate().hold():
            fresh = self._capture_held(capture.source.intent_id)
            if (
                fresh.source != capture.source
                or fresh.genesis_decision_id != capture.genesis_decision_id
                or fresh.genesis_decision_fingerprint != capture.genesis_decision_fingerprint
                or fresh.g0 != capture.g0
                or fresh.g0_head != capture.g0_head
                or fresh.session != capture.session
                or fresh.clock_epoch != capture.clock_epoch
                or fresh.issued_at_ns < capture.issued_at_ns
                or fresh.issued_at_ns >= capture.expires_at_ns
            ):
                raise H1DeliveryWorkClaimSourceHold(
                    "delivery-work claim capture is no longer current"
                )

    def _capture_held(self, intent_id: str) -> _H1DeliveryWorkClaimCapture:
        if self._closed:
            raise H1DeliveryWorkClaimSourceHold("delivery-work claim source is closed")
        self._runtime._authority_gate().require_held()
        source, candidate = self._selected_source_held(intent_id)
        g0 = self._read_exact_g0(candidate, source)
        g1 = delivery_work_record_id(source.subject_id, 1)
        with read_connection(self._runtime._database) as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM records WHERE tenant_id=? AND record_id=?",
                    (source.tenant_id, g1),
                ).fetchone()
                is not None
            ):
                raise H1DeliveryWorkClaimSourceHold("delivery-work g1 is already present")
        session = self._runtime._supervisor.runtime().session("agent_loop")
        epoch, now_ns = self._runtime._require_dispatch_resources().clock()
        if (
            session.tenant_id != source.tenant_id
            or type(epoch) is not str
            or not epoch
            or type(now_ns) is not int
            or now_ns < 0
            or now_ns > (2**63 - 1) - _LEASE_DURATION_NS
        ):
            raise H1DeliveryWorkClaimSourceHold("current broker session or clock differs")
        return _H1DeliveryWorkClaimCapture(
            source,
            candidate.decision_id,
            candidate.decision_fingerprint,
            g0,
            g0.head,
            session,
            epoch,
            now_ns,
            now_ns + _LEASE_DURATION_NS,
        )

    def _selected_source_held(
        self, intent_id: str
    ) -> tuple[DeliveryWorkSourceV1, SelectedOwnerDecision]:
        """Read the durable V4-to-g0 source chain without requiring g1 absence."""
        if self._closed:
            raise H1DeliveryWorkClaimSourceHold("delivery-work claim source is closed")
        self._runtime._authority_gate().require_held()
        selected = self._selected._read_held(intent_id=intent_id)
        completion, issuance = selected.selected_h1.decision, selected.selected_h1.issuance
        if (
            type(completion.prepared.request) is not CompleteDeliveryBatchV2
            or type(issuance) is not H1CompletionIssuanceV4
        ):
            raise H1DeliveryWorkClaimSourceHold("selected V4 completion is unavailable")
        candidate = self._find_materialized_genesis(selected.selected_h1.decision, intent_id)
        request = candidate.prepared.request
        assert type(request) is DeliveryWorkGenesisBatchV1
        binding = candidate.prepared.delivery_work_genesis_binding
        if (
            binding is None
            or request.selected_source != binding.selected_source
            or request.selected_source.selected_decision_id != completion.decision_id
            or request.selected_source.selected_decision_fingerprint
            != completion.decision_fingerprint
            or binding.request_digest
            != hashlib.sha256(canonical_owner_publication_bytes(request)).hexdigest()
        ):
            raise H1DeliveryWorkClaimSourceHold("selected V3 genesis binding differs")
        source = request.selected_source
        if source.intent_id != intent_id:
            raise H1DeliveryWorkClaimSourceHold("selected genesis intent differs")
        completion_request = completion.prepared.request
        assert type(completion_request) is CompleteDeliveryBatchV2
        prepared = issuance.assembly.prepared_completion
        wanted = (
            self._record("agent_loop", make_terminal_run_member(prepared.run)),
            self._record("agent_loop", make_terminal_manifest_member(prepared.terminal_manifest)),
            self._record(
                "effects", make_scoped_intent_member(issuance.effects_result.intent_result.intent)
            ),
        )
        expected_source = DeliveryWorkSourceV1(
            tenant_id=completion_request.identity.tenant_id,
            intent_id=wanted[2].record_id,
            selected_decision_id=completion.decision_id,
            selected_decision_fingerprint=completion.decision_fingerprint,
            terminal_run=wanted[0],
            terminal_manifest=wanted[1],
            intent=wanted[2],
        )
        if source != expected_source or any(
            _exact_owner_member_count(member, completion_request.complete_records) != 1
            for member in wanted
        ):
            raise H1DeliveryWorkClaimSourceHold("selected V4 source differs")
        return source, candidate

    @staticmethod
    def _record(
        owner: Literal["agent_loop", "effects"],
        member: CompletionCanonicalMember | ScopedIntentCanonicalMemberV3,
    ) -> DeliveryWorkSourceMemberV1:
        return DeliveryWorkSourceMemberV1(
            owner=owner,
            record_kind=member.record_kind,
            record_id=member.record_id,
            schema_id=member.schema_id,
            canonical_record_bytes=member.canonical_record_bytes,
            fingerprint=member.fingerprint,
        )

    def _find_materialized_genesis(
        self, completion: SelectedOwnerDecision, intent_id: str
    ) -> SelectedOwnerDecision:
        snapshot = self._runtime._owner_decisions().snapshot()
        candidates = tuple(
            decision
            for decision in snapshot.decisions
            if type(decision.prepared.request) is DeliveryWorkGenesisBatchV1
            and decision.prepared.request.selected_source.intent_id == intent_id
        )
        if len(candidates) != 1:
            raise H1DeliveryWorkClaimSourceHold(
                "delivery-work genesis decision is missing or ambiguous"
            )
        decision = candidates[0]
        request = decision.prepared.request
        assert type(request) is DeliveryWorkGenesisBatchV1
        if (
            request.selected_decision_id != completion.decision_id
            or request.selected_decision_fingerprint != completion.decision_fingerprint
            or request.identity.command_id not in snapshot.materialized_command_ids
        ):
            raise H1DeliveryWorkClaimSourceHold(
                "delivery-work genesis is not selected and materialized"
            )
        return decision

    def _read_exact_g0(
        self,
        decision: SelectedOwnerDecision,
        source: DeliveryWorkSourceV1,
        connection: sqlite3.Connection | None = None,
    ) -> DeliveryWorkStateV1:
        request = decision.prepared.request
        assert type(request) is DeliveryWorkGenesisBatchV1
        expected = prepare_delivery_work_genesis(source)
        member = request.complete_records[0]
        if member.canonical_bytes != expected.canonical_bytes():
            raise H1DeliveryWorkClaimSourceHold("selected genesis bytes differ")
        expectation = PublicationExpectation(
            request.identity.tenant_id,
            request.operation,
            request.identity.command_id,
            request.identity.command_fingerprint,
            request.expected.tenant_frontier,
            (
                ExpectedPublicationRecord(
                    member.record_id,
                    member.owner,
                    member.schema_id,
                    member.canonical_bytes,
                    member.fingerprint,
                ),
            ),
        )
        def read_on(connection: sqlite3.Connection) -> tuple[object, ...] | None:
            if (
                inspect_publication(connection, expectation, decision.tenant_commit_sequence)
                != "COMPLETE"
            ):
                raise H1DeliveryWorkClaimSourceHold(
                    "selected genesis is not physically materialized"
                )
            row = connection.execute(
                "SELECT owner,schema_id,canonical_bytes,commit_sequence FROM records "
                "WHERE tenant_id=? AND record_id=?",
                (source.tenant_id, expected.record_id),
            ).fetchone()
            return None if row is None else tuple(row)

        if connection is None:
            with read_connection(self._runtime._database) as owned_connection:
                row = read_on(owned_connection)
        else:
            if not connection.in_transaction:
                raise H1DeliveryWorkClaimSourceHold("delivery-work g0 read lacks transaction")
            row = read_on(connection)
        if row is None or tuple(row) != (
            DELIVERY_WORK_OWNER,
            DELIVERY_WORK_SCHEMA,
            expected.canonical_bytes(),
            decision.tenant_commit_sequence,
        ):
            raise H1DeliveryWorkClaimSourceHold("physical delivery-work g0 differs")
        try:
            state = DeliveryWorkStateV1.model_validate_json(expected.canonical_bytes())
        except ValueError as error:
            raise H1DeliveryWorkClaimSourceHold("physical delivery-work g0 is invalid") from error
        if state.canonical_bytes() != expected.canonical_bytes() or state != expected:
            raise H1DeliveryWorkClaimSourceHold("physical delivery-work g0 is noncanonical")
        return state
