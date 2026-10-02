"""Authenticated, read-only target accounting facts for one native Run lineage.

This reader only collects retained history.  It neither constructs an execution
recovery cut nor calls an owner, publishes, continues a Run, or authorizes an
effect.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallLifecycleObservation,
    CallSubjectHead,
    InitializedCallRecord,
)
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
    ExecutionRunRecord,
)
from chiplog.capabilities.agent_loop.execution_preparation_bindings_contracts import (
    NativeExecutionPreparationBindingsV2,
    decode_native_execution_preparation_bindings_v2,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import InitializedCall
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.r14_acceptance_v2_contracts import RetainedAcceptancePreparationV2
from chiplog.composition.r14_execution_cancellation_contracts import (
    RetainedExecutionCancellationPreparation,
)
from chiplog.composition.r14_execution_fanout_records import reference
from chiplog.composition.r14_fanout_contracts import RetainedFanOutPreparation
from chiplog.composition.r14_loop_history import read_execution_recovery_history
from chiplog.composition.r14_native_preparation_bindings import (
    bind_native_execution_preparation_v2,
)
from chiplog.composition.r14_original_prepare_source import (
    R14OriginalNativePrepareProjection,
    R14OriginalNativePrepareSource,
)


@dataclass(frozen=True, slots=True)
class R14NativeSelectedResponseSealFacts:
    """One selected native seal and its exact physical Run/call observations."""

    response_seal: CallSubjectHead
    captured_run: CallSubjectHead
    sealed_run: CallSubjectHead
    physical_sealed_run: CallSubjectHead
    initialized_records: tuple[InitializedCallRecord, ...]
    inventory_rows: tuple[CallLifecycleObservation, ...]


@dataclass(frozen=True, slots=True)
class R14NativeSelectedAccountingSourceFacts:
    """Complete inert selected facts for one authenticated native Run lineage."""

    tenant_id: str
    database_identity: tuple[str, int, int]
    original_prepare: R14OriginalNativePrepareProjection
    preparation_bindings: NativeExecutionPreparationBindingsV2
    target_run_lineage: tuple[CallSubjectHead, ...]
    selected_native_seals: tuple[R14NativeSelectedResponseSealFacts, ...]
    target_inventory_rows: tuple[CallLifecycleObservation, ...]
    target_legacy_seals: tuple[RetainedFanOutPreparation, ...]
    target_cancellations: tuple[RetainedExecutionCancellationPreparation, ...]
    target_acceptances: tuple[RetainedAcceptancePreparationV2, ...]


@dataclass(frozen=True, slots=True)
class R14NativeOneCallCancelledFacts:
    """The narrow, observed one-call pre-accept cancellation; never a recovery cut."""

    source: R14NativeSelectedAccountingSourceFacts
    selected_seal: R14NativeSelectedResponseSealFacts
    original_call_id: str
    initialized: CallSubjectHead


class R14NativeSelectedAccountingSource:
    """Read complete selected native facts for the original Prepare Run only."""

    def __init__(
        self,
        runtime: CommonCliExecutionRuntime,
        original_prepare: R14OriginalNativePrepareProjection,
        preparation_bindings: NativeExecutionPreparationBindingsV2,
    ) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("selected accounting source requires the installed CLI runtime")
        if type(original_prepare) is not R14OriginalNativePrepareProjection:
            raise TypeError("selected accounting source requires an original Prepare projection")
        if type(preparation_bindings) is not NativeExecutionPreparationBindingsV2:
            raise TypeError("selected accounting source requires native V2 preparation bindings")
        self._runtime = runtime
        self._original_prepare = original_prepare
        self._preparation_bindings = preparation_bindings

    def collect(
        self, target_run: CallSubjectHead | None = None
    ) -> R14NativeSelectedAccountingSourceFacts | None:
        """Collect one complete Run lineage; ``target_run`` can only anchor it exactly."""
        if target_run is not None and type(target_run) is not CallSubjectHead:
            raise TypeError("target Run locator must be an exact CallSubjectHead")
        gate = self._runtime._authority_gate()
        try:
            with gate.hold():
                self._runtime._check_database_identity()
                database_identity = self._runtime._database_identity
                facts = self._collect_held(target_run, database_identity)
                self._runtime._check_database_identity()
                if self._runtime._database_identity != database_identity:
                    return None
                return facts
        except (OSError, RuntimeError, TypeError, ValueError, binascii.Error):
            return None

    def collect_one_cancelled(
        self, target_run: CallSubjectHead | None = None
    ) -> R14NativeOneCallCancelledFacts | None:
        """Hold unless the complete target facts prove one unaccepted self-effect call."""
        facts = self.collect(target_run)
        if facts is None:
            return None
        if (
            facts.target_legacy_seals
            or facts.target_acceptances
            or len(facts.selected_native_seals) != 1
        ):
            return None
        seal = facts.selected_native_seals[0]
        if len(seal.initialized_records) != 1 or len(seal.inventory_rows) != 1:
            return None
        record, row = seal.initialized_records[0], seal.inventory_rows[0]
        initialized = reference(record.original_call_id, record)
        try:
            raw = base64.b64decode(record.call.canonical_call_base64, validate=True)
            tool = ConsequentialToolCall.model_validate_json(raw)
        except (ValueError, binascii.Error):
            return None
        if (
            tool.canonical_bytes() != raw
            or tool.tool != "request_self_effect"
            or row.original_call_id != record.original_call_id
            or row.initialized != initialized
            or row.initialized_record != record
            or not isinstance(row.acceptance, InitializedCall)
            or row.acceptance.initialized != initialized.revision
            or not isinstance(row.terminal, Present)
            or len(facts.target_cancellations) != 1
        ):
            return None
        cancellation = facts.target_cancellations[0]
        terminal, result = cancellation.proposal.terminal, cancellation.proposal.result
        terminal_ref = reference(terminal.terminal_id, terminal)
        if (
            cancellation.request.original_call_id != record.original_call_id
            or cancellation.request.initialized != initialized
            or cancellation.request.initialized_record != record
            or cancellation.proposal.complete_ordered_record_manifest
            != (terminal_ref, reference(result.result_id, result))
            or terminal.original_call_id != record.original_call_id
            or terminal.initialized != initialized
            or result.original_call_id != record.original_call_id
            or result.initialized != initialized
            or result.terminal != terminal_ref
            or result.outcome != "NOT_EXECUTED"
            or row.terminal != terminal_ref.revision
        ):
            return None
        return R14NativeOneCallCancelledFacts(
            source=facts,
            selected_seal=seal,
            original_call_id=record.original_call_id,
            initialized=initialized,
        )

    def _collect_held(
        self,
        target_run: CallSubjectHead | None,
        database_identity: tuple[str, int, int],
    ) -> R14NativeSelectedAccountingSourceFacts | None:
        # Both values are public DTOs.  Reopen their only authenticated source
        # before using a single field from either supplied instance.
        reopened = R14OriginalNativePrepareSource(self._runtime).collect(
            self._original_prepare.selected_response_seal
        )
        if reopened is None or reopened != self._original_prepare:
            return None
        rebuilt = bind_native_execution_preparation_v2(reopened)
        decoded = decode_native_execution_preparation_bindings_v2(rebuilt.canonical_bytes())
        if decoded != rebuilt or decoded != self._preparation_bindings:
            return None
        if (
            reopened.database_identity != database_identity
            or decoded.database_identity != database_identity
            or reopened.tenant_id != self._runtime._tenant_id
            or decoded.tenant_id != reopened.tenant_id
            or decoded.prepared_run != reopened.prepared_run
            or decoded.prepared_run_bytes != reopened.prepared_run_bytes
            or decoded.selected_response_seal != reopened.selected_response_seal
            or decoded.selected_prepare_decision_id != reopened.selected_prepare_decision_id
            or decoded.selected_prepare_decision_bytes != reopened.selected_prepare_decision_bytes
            or decoded.selected_prepare_decision_fingerprint
            != reopened.selected_prepare_decision_fingerprint
            or decoded.artifact_bytes != reopened.artifact_bytes
            or decoded.rendered_prompt_bytes != reopened.rendered_prompt_bytes
            or decoded.generated_schema_bytes != reopened.generated_schema_bytes
            or decoded.ordered_tool_spec_bytes != reopened.ordered_tool_spec_bytes
            or decoded.policy_bytes != reopened.policy_bytes
        ):
            return None
        snapshot, inventory, legacy, seals, cancellations, acceptances = (
            read_execution_recovery_history(self._runtime)
        )
        lineage = self._lineage(
            snapshot.records, reopened.prepared_run, reopened.prepared_run_bytes
        )
        if lineage is None or (target_run is not None and target_run not in lineage):
            return None
        target_id = reopened.prepared_run.subject_id
        chain = {item.revision.head: item for item in lineage}
        physical = {
            run.head: run
            for run in snapshot.records
            if isinstance(run, ExecutionRunRecord) and run.run_id == target_id
        }
        if tuple(_run_reference(run) for run in physical.values()) != lineage:
            return None
        target_seals: list[R14NativeSelectedResponseSealFacts] = []
        initialized: dict[CallSubjectHead, InitializedCallRecord] = {}
        for evidence in seals:
            captured, sealed = evidence.request.captured_run, evidence.proposal.sealed_run
            response = evidence.proposal.fan_out.response_seal
            if (
                captured.run_id != target_id
                and sealed.run_id != target_id
                and response.original_run_id != target_id
            ):
                continue
            captured_ref = _run_reference(captured)
            sealed_ref = _run_reference(sealed)
            if (
                captured_ref not in chain.values()
                or sealed_ref not in chain.values()
                or physical.get(sealed.head) != sealed
            ):
                return None
            response_ref = reference(response.response_seal_id, response)
            turn = sealed.turns[-1] if sealed.turns else None
            records = evidence.proposal.fan_out.initialized_records
            refs = tuple(reference(record.original_call_id, record) for record in records)
            if (
                turn is None
                or turn.response_seal != response_ref
                or turn.initialized_calls != refs
                or response.original_run_id != target_id
                or response.original_turn_id != turn.turn_id
                or response.captured_response != captured_ref
                or response.complete_ordered_initialized != refs
            ):
                return None
            for record, ref in zip(records, refs, strict=True):
                original = record.call.original
                if (
                    original.original_run_id != target_id
                    or original.original_turn_id != turn.turn_id
                    or original.captured_response != captured_ref
                    or ref in initialized
                ):
                    return None
                initialized[ref] = record
            target_seals.append(
                R14NativeSelectedResponseSealFacts(
                    response_seal=response_ref,
                    captured_run=captured_ref,
                    sealed_run=sealed_ref,
                    physical_sealed_run=_run_reference(physical[sealed.head]),
                    initialized_records=records,
                    inventory_rows=(),
                )
            )
        target_rows = tuple(
            row
            for row in inventory.ordered_calls
            if row.initialized_record.call.original.original_run_id == target_id
        )
        rows_by_ref = {row.initialized: row for row in target_rows}
        if (
            len(rows_by_ref) != len(target_rows)
            or set(rows_by_ref) != set(initialized)
            or any(
                row.original_call_id != initialized[row.initialized].original_call_id
                or row.initialized_record != initialized[row.initialized]
                for row in target_rows
            )
        ):
            return None
        finalized_seals = tuple(
            seal.__class__(
                response_seal=seal.response_seal,
                captured_run=seal.captured_run,
                sealed_run=seal.sealed_run,
                physical_sealed_run=seal.physical_sealed_run,
                initialized_records=seal.initialized_records,
                inventory_rows=tuple(
                    rows_by_ref[reference(record.original_call_id, record)]
                    for record in seal.initialized_records
                ),
            )
            for seal in target_seals
        )
        target_legacy = tuple(
            item
            for item in legacy
            if item.request.request.original_run_id == target_id
            or item.request.captured_run.run_id == target_id
            or item.accepted_run.run_id == target_id
        )
        target_call_ids = {record.original_call_id for record in initialized.values()}
        target_cancellations = tuple(
            item
            for item in cancellations
            if item.request.original_call_id in target_call_ids
            or item.request.original.original_run_id == target_id
        )
        target_acceptances = tuple(
            item
            for item in acceptances
            if item.loop_request.binding.original_call_id in target_call_ids
            or item.loop_request.binding.original.original_run_id == target_id
        )
        for item in target_cancellations:
            matched_record = initialized.get(item.request.initialized)
            if (
                matched_record is None
                or item.request.original_call_id != matched_record.original_call_id
                or item.request.initialized_record != matched_record
                or item.request.original != matched_record.call.original
            ):
                return None
        for acceptance in target_acceptances:
            binding = acceptance.loop_request.binding
            matched_record = initialized.get(binding.initialized)
            if (
                matched_record is None
                or binding.original_call_id != matched_record.original_call_id
                or acceptance.loop_request.initialized_record != matched_record
                or binding.original != matched_record.call.original
            ):
                return None
        if snapshot.tenant_head != inventory.tenant_commit_sequence:
            return None
        return R14NativeSelectedAccountingSourceFacts(
            tenant_id=reopened.tenant_id,
            database_identity=database_identity,
            original_prepare=reopened,
            preparation_bindings=decoded,
            target_run_lineage=lineage,
            selected_native_seals=finalized_seals,
            target_inventory_rows=target_rows,
            target_legacy_seals=target_legacy,
            target_cancellations=target_cancellations,
            target_acceptances=target_acceptances,
        )

    @staticmethod
    def _lineage(
        records: tuple[object, ...], prepared: CallSubjectHead, prepared_bytes: bytes
    ) -> tuple[CallSubjectHead, ...] | None:
        if not isinstance(prepared.revision, Present):
            return None
        target = [
            record
            for record in records
            if isinstance(record, ExecutionRunRecord) and record.run_id == prepared.subject_id
        ]
        if not target:
            return None
        refs = tuple(_run_reference(record) for record in target)
        by_head = {record.head: record for record in target}
        if len(by_head) != len(target) or prepared.revision.head not in by_head:
            return None
        prepared_record = by_head[prepared.revision.head]
        if (
            prepared_record.canonical_bytes() != prepared_bytes
            or _run_reference(prepared_record) != prepared
        ):
            return None
        previous: ExecutionRunRecord | None = None
        for record in target:
            if previous is None:
                if record.predecessor is not None:
                    return None
            elif record.predecessor != previous.head:
                return None
            previous = record
        return refs


__all__ = [
    "R14NativeOneCallCancelledFacts",
    "R14NativeSelectedAccountingSource",
    "R14NativeSelectedAccountingSourceFacts",
    "R14NativeSelectedResponseSealFacts",
]


def _run_reference(run: ExecutionRunRecord) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=run.run_id,
        revision=Present(
            head=run.head,
            fingerprint=hashlib.sha256(run.canonical_bytes()).hexdigest(),
        ),
    )
