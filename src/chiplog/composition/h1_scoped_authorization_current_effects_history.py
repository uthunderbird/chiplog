"""Inert reader for the complete current Effects history of one V4/g1 cut."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass

from chiplog.capabilities.effects.dispatch_authority_contracts import CapturedSource
from chiplog.capabilities.effects.h1_normative_conflict_generation import (
    H1EffectsHistoryV1,
    h1_effects_history_capture,
)
from chiplog.capabilities.effects.scoped_intent_record_contracts import make_scoped_intent_member
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV4
from chiplog.composition.h1_producer_history import (
    H1ProducerHistoryError,
    H1ProducerHistoryHold,
    interpret_h1_producer_effects_history,
)
from chiplog.composition.h1_producer_sources import _effects_history
from chiplog.composition.h1_scoped_authorization_clock_reader import (
    H1ScopedAuthorizationClockCaptureV1,
    H1ScopedAuthorizationClockHold,
    H1ScopedAuthorizationClockReaderV1,
)
from chiplog.composition.h1_scoped_authorization_delivery_work_reader import (
    H1ScopedAuthorizationDeliveryWorkCaptureV1,
    H1ScopedAuthorizationDeliveryWorkHold,
    H1ScopedAuthorizationDeliveryWorkReaderV1,
)
from chiplog.composition.r16_effects import (
    EffectsIntegrityError,
    MaterializedEffectsCut,
    _read_materialized_effects_with_history,
)
from chiplog.platform.owner_decision_journal import OwnerJournalSnapshot
from chiplog.platform.owner_publications import SelectedOwnerDecision


class H1ScopedAuthorizationCurrentEffectsHistoryHold(PermissionError):
    """The selected V4/g1 current Effects history is unavailable."""


@dataclass(frozen=True, slots=True)
class _CurrentEffectsHistoryRecord:
    delivery_work_capture: H1ScopedAuthorizationDeliveryWorkCaptureV1
    clock_capture: H1ScopedAuthorizationClockCaptureV1
    cut: MaterializedEffectsCut
    journal: OwnerJournalSnapshot
    selected: SelectedOwnerDecision
    claim: SelectedOwnerDecision
    history: H1EffectsHistoryV1
    source: CapturedSource


class H1ScopedAuthorizationCurrentEffectsHistoryCaptureV1:
    """Opaque, reader-issued capture of one complete current Effects history."""

    __slots__ = ("_record",)
    _record: _CurrentEffectsHistoryRecord

    def __init__(self) -> None:
        raise TypeError("H1 current Effects history captures are reader-issued")

    def __copy__(self) -> H1ScopedAuthorizationCurrentEffectsHistoryCaptureV1:
        raise TypeError("H1 current Effects history captures cannot be copied")

    def __deepcopy__(
        self, memo: dict[int, object]
    ) -> H1ScopedAuthorizationCurrentEffectsHistoryCaptureV1:
        del memo
        raise TypeError("H1 current Effects history captures cannot be copied")

    def __reduce__(self) -> str:
        raise TypeError("H1 current Effects history captures cannot be serialized")


class H1ScopedAuthorizationCurrentEffectsHistoryReaderV1:
    """Authenticate full current Effects history for an opaque selected V4/g1 join.

    This reader creates only an inert source capture.  In particular it does
    not decide intent absence, authorize a transition, write, or call a provider.
    """

    __slots__ = ("_clock", "_closed", "_issued", "_joined", "_runtime")

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 current Effects history reader requires installed runtime")
        joined = getattr(runtime, "_h1_scoped_authorization_delivery_work_reader_v1", None)
        clock = getattr(runtime, "_h1_scoped_authorization_clock_reader_v1", None)
        if (
            type(joined) is not H1ScopedAuthorizationDeliveryWorkReaderV1
            or joined._runtime is not runtime
            or type(clock) is not H1ScopedAuthorizationClockReaderV1
            or clock._runtime is not runtime
        ):
            raise TypeError("H1 current Effects history reader requires mounted V4 readers")
        self._runtime = runtime
        self._joined = joined
        self._clock = clock
        self._issued: dict[
            int,
            tuple[
                H1ScopedAuthorizationCurrentEffectsHistoryCaptureV1,
                _CurrentEffectsHistoryRecord,
            ],
        ] = {}
        self._closed = False

    def read(
        self, capture: H1ScopedAuthorizationDeliveryWorkCaptureV1
    ) -> H1ScopedAuthorizationCurrentEffectsHistoryCaptureV1:
        if type(capture) is not H1ScopedAuthorizationDeliveryWorkCaptureV1:
            raise TypeError("H1 current Effects history reader requires joined reader capture")
        with self._runtime._authority_gate().hold():
            self._require_open_held()
            clock_capture = self._clock.read(capture)
            try:
                record = self._read_record_held(capture, clock_capture)
            except Exception:
                with suppress(H1ScopedAuthorizationClockHold):
                    self._clock.release(clock_capture)
                raise
            issued = object.__new__(H1ScopedAuthorizationCurrentEffectsHistoryCaptureV1)
            issued._record = record
            self._issued[id(issued)] = (issued, record)
            return issued

    def recheck_held(
        self, capture: H1ScopedAuthorizationCurrentEffectsHistoryCaptureV1
    ) -> H1ScopedAuthorizationCurrentEffectsHistoryCaptureV1:
        self._runtime._authority_gate().require_held()
        self._require_open_held()
        record = self._issued_record_held(capture)
        reread = self._read_record_held(record.delivery_work_capture, record.clock_capture)
        if reread != record:
            raise H1ScopedAuthorizationCurrentEffectsHistoryHold(
                "H1 current Effects history sources changed"
            )
        return capture

    def role_held(
        self, capture: H1ScopedAuthorizationCurrentEffectsHistoryCaptureV1
    ) -> CapturedSource:
        self._runtime._authority_gate().require_held()
        self.recheck_held(capture)
        return self._issued_record_held(capture).source

    def release(self, capture: H1ScopedAuthorizationCurrentEffectsHistoryCaptureV1) -> None:
        with self._runtime._authority_gate().hold():
            self._require_open_held()
            record = self._issued_record_held(capture)
            del self._issued[id(capture)]
            with suppress(H1ScopedAuthorizationClockHold):
                self._clock.release(record.clock_capture)

    def close(self) -> None:
        with self._runtime._authority_gate().hold():
            if self._closed:
                return
            self._closed = True
            records = tuple(record for _, record in self._issued.values())
            self._issued.clear()
            for record in records:
                with suppress(H1ScopedAuthorizationClockHold):
                    self._clock.release(record.clock_capture)

    def _require_open_held(self) -> None:
        self._runtime._authority_gate().require_held()
        if self._closed:
            raise H1ScopedAuthorizationCurrentEffectsHistoryHold(
                "H1 current Effects history reader is closed"
            )

    def _issued_record_held(
        self, capture: H1ScopedAuthorizationCurrentEffectsHistoryCaptureV1
    ) -> _CurrentEffectsHistoryRecord:
        issued = self._issued.get(id(capture))
        if (
            type(capture) is not H1ScopedAuthorizationCurrentEffectsHistoryCaptureV1
            or issued is None
            or issued[0] is not capture
            or issued[1] is not capture._record
        ):
            raise H1ScopedAuthorizationCurrentEffectsHistoryHold(
                "H1 current Effects history capture is not reader-issued"
            )
        return issued[1]

    def _read_record_held(
        self,
        delivery_work_capture: H1ScopedAuthorizationDeliveryWorkCaptureV1,
        clock_capture: H1ScopedAuthorizationClockCaptureV1,
    ) -> _CurrentEffectsHistoryRecord:
        self._runtime._authority_gate().require_held()
        try:
            self._joined.recheck_held(delivery_work_capture)
            clock = self._clock.role_held(clock_capture)
            joined = self._joined._issued_record_held(delivery_work_capture)
            effects = self._joined._effects._issued_record_held(joined.effects_capture)
            selected = effects.selected.selected_h1.decision
            issuance = effects.selected.selected_h1.issuance
            evidence = joined.evidence
            if (
                type(selected) is not SelectedOwnerDecision
                or type(issuance) is not H1CompletionIssuanceV4
            ):
                raise TypeError("selected V4 decision is invalid")
            cut, journal = _read_materialized_effects_with_history(
                self._runtime, self._runtime._owner_decisions()
            )
            claim = self._require_joined_current_held(
                self._runtime, cut, journal, selected, issuance, evidence
            )
            history = _effects_history(
                interpret_h1_producer_effects_history(cut, journal, runtime=self._runtime)
            )
            source = h1_effects_history_capture(
                history,
                clock_contract=clock.clock_contract,
                clock_epoch=clock.clock_epoch,
                valid_until_ns=clock.valid_until_ns,
            )
            # The gate covers acquisition; these rechecks cover dependencies that
            # may have been read by their own nested capture paths.
            self._joined.recheck_held(delivery_work_capture)
            self._clock.recheck_held(clock_capture)
        except (
            H1ScopedAuthorizationDeliveryWorkHold,
            H1ScopedAuthorizationClockHold,
            EffectsIntegrityError,
            H1ProducerHistoryError,
            H1ProducerHistoryHold,
            AttributeError,
            TypeError,
            ValueError,
        ) as error:
            raise H1ScopedAuthorizationCurrentEffectsHistoryHold(
                "H1 current Effects history is unavailable"
            ) from error
        return _CurrentEffectsHistoryRecord(
            delivery_work_capture, clock_capture, cut, journal, selected, claim, history, source
        )

    @staticmethod
    def _require_joined_current_held(
        runtime: CommonCliExecutionRuntime,
        cut: MaterializedEffectsCut,
        journal: OwnerJournalSnapshot,
        selected: SelectedOwnerDecision,
        issuance: H1CompletionIssuanceV4,
        evidence: object,
    ) -> SelectedOwnerDecision:
        """Bind current materialization and exact selected V4 intent to the g1 join."""
        source = evidence.source
        current_claim = evidence.current_claim
        runtime._check_database_identity()
        expected = make_scoped_intent_member(issuance.effects_result.intent_result.intent)
        selected_matches = tuple(
            decision
            for decision in journal.decisions
            if decision.decision_id == selected.decision_id
            and decision.decision_fingerprint == selected.decision_fingerprint
        )
        claim_matches = tuple(
            decision
            for decision in journal.decisions
            if decision.decision_id == current_claim.claim_decision_id
            and decision.decision_fingerprint == current_claim.claim_decision_fingerprint
        )
        physical = tuple(
            row
            for row in cut.rows
            if (
                row.record.record_id,
                row.record.record_kind,
                row.record.schema_id,
                row.record.fingerprint,
                row.record.canonical_bytes,
            ) == (
                source.intent.record_id,
                source.intent.record_kind,
                source.intent.schema_id,
                source.intent.fingerprint,
                source.intent.canonical_record_bytes,
            )
        )
        if (
            len(selected_matches) != 1
            or selected_matches[0] != selected
            or len(claim_matches) != 1
            or source.tenant_id != cut.tenant_id
            or source.intent_id != expected.record_id
            or (
                source.intent.record_id,
                source.intent.record_kind,
                source.intent.schema_id,
                source.intent.fingerprint,
                source.intent.canonical_record_bytes,
            )
            != (
                expected.record_id,
                expected.record_kind,
                expected.schema_id,
                expected.fingerprint,
                expected.canonical_record_bytes,
            )
            or len(physical) != 1
            or physical[0].commit_sequence != selected.tenant_commit_sequence
            or physical[0].publication_ordinal
            != next(
                (
                    ordinal
                    for ordinal, record in enumerate(selected.prepared.request.complete_records)
                    if record == physical[0].record
                ),
                -1,
            )
            or cut.owner_journal_head != journal.head
            # Current means the g1 publication is still the selected physical
            # frontier. A later selected decision, even an unrelated one,
            # requires a new joined capture rather than relabelling this g1.
            or cut.tenant_frontier != claim_matches[0].tenant_commit_sequence
            or cut.materialization_commitment != claim_matches[0].resulting_commitment
            or (cut.physical_path, cut.physical_device, cut.physical_inode)
            != runtime._database_identity
        ):
            raise ValueError("selected V4/g1 current materialized frontier differs")
        return claim_matches[0]


__all__ = [
    "H1ScopedAuthorizationCurrentEffectsHistoryCaptureV1",
    "H1ScopedAuthorizationCurrentEffectsHistoryHold",
    "H1ScopedAuthorizationCurrentEffectsHistoryReaderV1",
]
