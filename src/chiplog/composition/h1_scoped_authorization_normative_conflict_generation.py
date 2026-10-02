"""Inert V22 source for a selected V4 intent's prepublication conflict generation."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, replace

from chiplog.capabilities.effects.dispatch_authority_contracts import CapturedSource
from chiplog.capabilities.effects.h1_normative_conflict_generation import (
    H1EffectsHistoryV1,
    H1NormativeConflictGenerationV1,
    h1_effects_history_capture,
    h1_normative_conflict_generation,
    h1_normative_conflict_generation_capture,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV4
from chiplog.composition.h1_historical_selected_sources import _require_v2_read_plan_prechecks
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
from chiplog.composition.r16_effects import _read_materialized_effects_with_history
from chiplog.platform.owner_decision_journal import OwnerJournalIntegrityError, OwnerJournalSnapshot
from chiplog.platform.owner_publications import SelectedOwnerDecision


class H1ScopedAuthorizationNormativeConflictGenerationHold(PermissionError):
    """The exact V4 prepublication prefix is unavailable or no longer current."""


@dataclass(frozen=True, slots=True)
class _NormativeConflictRecord:
    delivery_work_capture: H1ScopedAuthorizationDeliveryWorkCaptureV1
    clock_capture: H1ScopedAuthorizationClockCaptureV1
    selected_decision: SelectedOwnerDecision
    predecessor: OwnerJournalSnapshot
    history: H1EffectsHistoryV1
    generation: H1NormativeConflictGenerationV1
    history_source: CapturedSource
    generation_source: CapturedSource


class H1ScopedAuthorizationNormativeConflictGenerationCaptureV1:
    """Opaque, reader-issued V22 prepublication-generation capture."""

    __slots__ = ("_record",)
    _record: _NormativeConflictRecord

    def __init__(self) -> None:
        raise TypeError("H1 V22 normative conflict captures are reader-issued")

    def __copy__(self) -> H1ScopedAuthorizationNormativeConflictGenerationCaptureV1:
        raise TypeError("H1 V22 normative conflict captures cannot be copied")

    def __deepcopy__(
        self, memo: dict[int, object]
    ) -> H1ScopedAuthorizationNormativeConflictGenerationCaptureV1:
        del memo
        raise TypeError("H1 V22 normative conflict captures cannot be copied")

    def __reduce__(self) -> str:
        raise TypeError("H1 V22 normative conflict captures cannot be serialized")


class H1ScopedAuthorizationNormativeConflictGenerationReaderV1:
    """Authenticate the exact predecessor history of one selected V4 intent."""

    __slots__ = ("_clock", "_closed", "_issued", "_joined", "_runtime")

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 V22 normative conflict reader requires installed runtime")
        joined = getattr(runtime, "_h1_scoped_authorization_delivery_work_reader_v1", None)
        clock = getattr(runtime, "_h1_scoped_authorization_clock_reader_v1", None)
        if (
            type(joined) is not H1ScopedAuthorizationDeliveryWorkReaderV1
            or joined._runtime is not runtime
            or type(clock) is not H1ScopedAuthorizationClockReaderV1
            or clock._runtime is not runtime
        ):
            raise TypeError("H1 V22 normative conflict reader requires mounted V4 readers")
        self._runtime = runtime
        self._joined = joined
        self._clock = clock
        self._issued: dict[
            int,
            tuple[
                H1ScopedAuthorizationNormativeConflictGenerationCaptureV1,
                _NormativeConflictRecord,
            ],
        ] = {}
        self._closed = False

    def read(
        self, capture: H1ScopedAuthorizationDeliveryWorkCaptureV1
    ) -> H1ScopedAuthorizationNormativeConflictGenerationCaptureV1:
        if type(capture) is not H1ScopedAuthorizationDeliveryWorkCaptureV1:
            raise TypeError("H1 V22 normative conflict reader requires joined reader capture")
        with self._runtime._authority_gate().hold():
            self._require_open_held()
            clock_capture = self._clock.read(capture)
            try:
                record = self._read_record_held(capture, clock_capture)
            except Exception:
                with suppress(H1ScopedAuthorizationClockHold):
                    self._clock.release(clock_capture)
                raise
            issued = object.__new__(H1ScopedAuthorizationNormativeConflictGenerationCaptureV1)
            issued._record = record
            self._issued[id(issued)] = (issued, record)
            return issued

    def recheck_held(
        self, capture: H1ScopedAuthorizationNormativeConflictGenerationCaptureV1
    ) -> H1ScopedAuthorizationNormativeConflictGenerationCaptureV1:
        self._runtime._authority_gate().require_held()
        self._require_open_held()
        record = self._issued_record_held(capture)
        reread = self._read_record_held(record.delivery_work_capture, record.clock_capture)
        if reread != record:
            raise H1ScopedAuthorizationNormativeConflictGenerationHold(
                "H1 V22 normative conflict prefix or current sources changed"
            )
        return capture

    def source_held(
        self, capture: H1ScopedAuthorizationNormativeConflictGenerationCaptureV1
    ) -> CapturedSource:
        self._runtime._authority_gate().require_held()
        self.recheck_held(capture)
        return self._issued_record_held(capture).generation_source

    def release(self, capture: H1ScopedAuthorizationNormativeConflictGenerationCaptureV1) -> None:
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
            raise H1ScopedAuthorizationNormativeConflictGenerationHold(
                "H1 V22 normative conflict reader is closed"
            )

    def _issued_record_held(
        self, capture: H1ScopedAuthorizationNormativeConflictGenerationCaptureV1
    ) -> _NormativeConflictRecord:
        issued = self._issued.get(id(capture))
        if (
            type(capture) is not H1ScopedAuthorizationNormativeConflictGenerationCaptureV1
            or issued is None
            or issued[0] is not capture
            or issued[1] is not capture._record
        ):
            raise H1ScopedAuthorizationNormativeConflictGenerationHold(
                "H1 V22 normative conflict capture is not reader-issued"
            )
        return issued[1]

    def _read_record_held(
        self,
        delivery_work_capture: H1ScopedAuthorizationDeliveryWorkCaptureV1,
        clock_capture: H1ScopedAuthorizationClockCaptureV1,
    ) -> _NormativeConflictRecord:
        self._runtime._authority_gate().require_held()
        try:
            self._joined.recheck_held(delivery_work_capture)
            clock = self._clock.role_held(clock_capture)
            joined = self._joined._issued_record_held(delivery_work_capture)
            effects = self._joined._effects._issued_record_held(joined.effects_capture)
            selected = effects.selected.selected_h1.decision
            issuance = effects.selected.selected_h1.issuance
            if (
                not isinstance(selected, SelectedOwnerDecision)
                or type(issuance) is not H1CompletionIssuanceV4
            ):
                raise TypeError("selected V4 decision is invalid")
            owner_journal = self._runtime._owner_decisions()
            predecessor = owner_journal.predecessor_snapshot_for_selected(selected)
            cut, current = _read_materialized_effects_with_history(self._runtime, owner_journal)
            if selected not in current.decisions or selected.tenant_commit_sequence <= 0:
                raise ValueError("selected V4 decision is not current")
            verified_rows, verified_predecessor = _require_v2_read_plan_prechecks(
                selected.prepared.request,
                issuance,
                runtime=self._runtime,
                owner_journal=owner_journal,
                decision=selected,
            )
            if verified_predecessor != predecessor:
                raise ValueError("selected V4 predecessor differs from verified checkpoint")
            expected = selected.prepared.request.expected
            if (
                verified_rows.commitment != expected.expected_materialization_commitment
                or verified_rows.tenant_head(expected.tenant_id) != expected.tenant_frontier
                or predecessor.tenant_id != expected.tenant_id
                or set(predecessor.materialized_command_ids)
                != {item.prepared.request.identity.command_id for item in predecessor.decisions}
            ):
                raise ValueError("selected V4 predecessor physical boundary differs")
            history = self._prefix_history(
                cut, predecessor, selected, verified_rows, runtime=self._runtime
            )
        except (
            H1ScopedAuthorizationDeliveryWorkHold,
            H1ScopedAuthorizationClockHold,
            OwnerJournalIntegrityError,
            H1ProducerHistoryError,
            H1ProducerHistoryHold,
            AttributeError,
            TypeError,
            ValueError,
        ) as error:
            raise H1ScopedAuthorizationNormativeConflictGenerationHold(
                "H1 V22 normative conflict prefix is unavailable"
            ) from error
        generation = h1_normative_conflict_generation(history)
        history_source = h1_effects_history_capture(
            history,
            clock_contract=clock.clock_contract,
            clock_epoch=clock.clock_epoch,
            valid_until_ns=clock.valid_until_ns,
        )
        generation_source = h1_normative_conflict_generation_capture(
            generation,
            clock_contract=clock.clock_contract,
            clock_epoch=clock.clock_epoch,
            valid_until_ns=clock.valid_until_ns,
        )
        return _NormativeConflictRecord(
            delivery_work_capture,
            clock_capture,
            selected,
            predecessor,
            history,
            generation,
            history_source,
            generation_source,
        )

    @staticmethod
    def _prefix_history(
        cut: object,
        predecessor: OwnerJournalSnapshot,
        selected: SelectedOwnerDecision,
        verified_rows: object,
        *,
        runtime: CommonCliExecutionRuntime,
    ) -> H1EffectsHistoryV1:
        decisions = {item.tenant_commit_sequence: item for item in predecessor.decisions}
        if len(decisions) != len(predecessor.decisions):
            raise ValueError("prepublication decision sequence is ambiguous")
        expected = tuple(
            record
            for decision in predecessor.decisions
            for record in decision.prepared.request.complete_records
            if record.owner == "effects"
        )
        selected_members = tuple(
            record
            for record in selected.prepared.request.complete_records
            if record.owner == "effects"
        )
        if not selected_members:
            raise ValueError("selected V4 decision has no effects member")
        rows = tuple(
            row
            for row in cut.rows
            if row.commit_sequence in decisions
        )
        if (
            tuple(row.record for row in cut.rows) != (*expected, *selected_members)
            or
            any(row.commit_sequence >= selected.tenant_commit_sequence for row in rows)
            or tuple(row.record for row in rows) != expected
            or any(
                item.tenant_commit_sequence >= selected.tenant_commit_sequence
                for item in predecessor.decisions
            )
        ):
            raise ValueError("prepublication effects prefix differs")
        for record in expected:
            physical = verified_rows.physical_record(
                tenant_id=predecessor.tenant_id, record_id=record.record_id
            )
            if physical != (
                record.owner,
                record.schema_id,
                record.canonical_bytes,
                next(
                    decision.tenant_commit_sequence
                    for decision in predecessor.decisions
                    if record in decision.prepared.request.complete_records
                ),
            ):
                raise ValueError("prepublication verified effects member differs")
        prefix = replace(cut, owner_journal_head=predecessor.head, rows=rows)
        interpreted = interpret_h1_producer_effects_history(prefix, predecessor, runtime=runtime)
        return _effects_history(interpreted)


__all__ = [
    "H1ScopedAuthorizationNormativeConflictGenerationCaptureV1",
    "H1ScopedAuthorizationNormativeConflictGenerationHold",
    "H1ScopedAuthorizationNormativeConflictGenerationReaderV1",
]
