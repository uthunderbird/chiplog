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
from typing import Literal

from chiplog.adapters.driven.effects_queries import StoredEffectRow
from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallLifecycleObservation,
    CallSubjectHead,
    InitializedCallRecord,
)
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
    ExecutionRunRecord,
)
from chiplog.capabilities.agent_loop.execution_preparation_bindings_contracts import (
    NativeExecutionPreparationBindingsV2,
    decode_native_execution_preparation_bindings_v2,
)
from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
from chiplog.capabilities.agent_loop.native_sealed_accounting_v3_contracts import (
    PrepareNativeSealedAccountingV3,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    NotApplicable,
    OriginalObligationBinding,
    Present,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    CancelledBeforeAcceptResult,
    ConsequentialAcceptedCall,
    InitializedCall,
    TerminalCallFrontier,
)
from chiplog.capabilities.effects.dispatch_authority_contracts import SelectedEffectHistoryMember
from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
from chiplog.capabilities.effects.dispatch_v2 import (
    DispatchRecordV2,
)
from chiplog.capabilities.effects.dispatch_v2 import (
    reference as effects_reference,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.r14_acceptance_v2_contracts import RetainedAcceptancePreparationV2
from chiplog.composition.r14_execution_cancellation_contracts import (
    RetainedExecutionCancellationPreparation,
    RetainedExecutionCancellationPreparationV2,
)
from chiplog.composition.r14_execution_fanout_records import reference
from chiplog.composition.r14_fanout_contracts import RetainedFanOutPreparation
from chiplog.composition.r14_loop_history import (
    ExecutionHistorySnapshot,
    read_execution_recovery_history,
)
from chiplog.composition.r14_native_evidence_sources import (
    complete_ordered_dispatch_sources,
    complete_ordered_evidence_sources,
)
from chiplog.composition.r14_native_preparation_bindings import (
    bind_native_execution_preparation_v2,
)
from chiplog.composition.r14_original_prepare_source import (
    R14OriginalNativePrepareProjection,
    R14OriginalNativePrepareSource,
)
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.workspace_snapshot import read_connection


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
    target_cancellations: tuple[
        RetainedExecutionCancellationPreparation | RetainedExecutionCancellationPreparationV2, ...
    ]
    target_acceptances: tuple[RetainedAcceptancePreparationV2, ...]


@dataclass(frozen=True, slots=True)
class R14NativeOneCallCancelledFacts:
    """The narrow, observed one-call pre-accept cancellation; never a recovery cut."""

    source: R14NativeSelectedAccountingSourceFacts
    selected_seal: R14NativeSelectedResponseSealFacts
    original_call_id: str
    initialized: CallSubjectHead


@dataclass(frozen=True, slots=True)
class R14NativeOneCallAcceptedFacts:
    """One accepted native consequential call with its exact selected acceptance."""

    source: R14NativeSelectedAccountingSourceFacts
    selected_seal: R14NativeSelectedResponseSealFacts
    original_call_id: str
    initialized: CallSubjectHead
    acceptance: RetainedAcceptancePreparationV2


@dataclass(frozen=True, slots=True)
class R14NativeSelectedOutcomePublication:
    """Physical provenance for one exact selected current R16 outcome."""

    record_id: str
    commit_sequence: int
    publication_ordinal: int
    batch_record_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class R14NativeHistoricalClosedOutcomeLocator:
    """Exact retained CLOSED member selected by an R14 terminal cut."""

    selected_outcome_record_id: str
    selected_outcome_bytes: bytes
    selected_outcome_fingerprint: str
    selected_effect_member_bytes: bytes
    selected_decision: CallSubjectHead
    outcome_commit_sequence: int
    publication_ordinal: int
    batch_record_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class R14NativeAcceptedOpenOutcome:
    """Retained selected R16 OPEN cause and current outcome for one accepted call."""

    accepted: R14NativeOneCallAcceptedFacts
    outcome: DispatchOutcomeRecordV2
    selected_member: SelectedEffectHistoryMember
    latest_outcome: DispatchOutcomeRecordV2
    latest_selected_member: SelectedEffectHistoryMember
    outcome_record_id: str
    commit_sequence: int
    publication_ordinal: int
    batch_record_ids: tuple[str, ...]
    tenant_id: str
    tenant_commit_sequence: int
    materialization_commitment: str
    owner_journal_head: str | None
    database_identity: tuple[str, int, int]
    latest_outcome_record_id: str
    latest_commit_sequence: int
    latest_publication_ordinal: int
    latest_batch_record_ids: tuple[str, ...]
    complete_ordered_dispatch_sources: tuple[RecoverySourceRecord, ...]
    complete_ordered_dispatch_members: tuple[SelectedEffectHistoryMember, ...]
    complete_ordered_evidence_sources: tuple[RecoverySourceRecord, ...]


def _accepted_open_cut_mismatches(
    runtime: CommonCliExecutionRuntime,
    cut,
    outcome,
    authenticated_history,
    original_obligation: OriginalObligationBinding,
) -> tuple[str, ...]:
    """Name retained OPEN cut fields that disagree with accepted/Effects source cuts."""
    accepted = outcome.accepted
    source = accepted.source
    seal = accepted.selected_seal
    acceptance = accepted.acceptance
    rows = tuple(
        row
        for row in source.target_inventory_rows
        if row.original_call_id == accepted.original_call_id
    )
    lineage = tuple(source.target_run_lineage)
    if len(rows) != 1 or len(lineage) == 0:
        return ("accepted_member_or_run_lineage",)
    row = rows[0]
    accepted_record = acceptance.loop_proposal.accepted
    execution_intent = acceptance.loop_proposal.execution_intent
    selected = outcome.selected_member.selected_decision
    snapshot = authenticated_history[0]
    run_matches = tuple(
        run
        for run in snapshot.records
        if isinstance(run, ExecutionRunRecord)
        and run.run_id == lineage[-1].subject_id
        and run.head == lineage[-1].revision.head
    )
    if len(run_matches) != 1:
        return ("current_run",)
    run = run_matches[0]
    current_run_ref = CallSubjectHead(
        subject_id=run.run_id,
        revision=Present(head=run.head, fingerprint=run.digest()),
    )
    if lineage[-1] != current_run_ref:
        return ("current_run",)
    selected_outcome_bytes = outcome.outcome.canonical_bytes()
    checks = {
        "tenant_id": cut.tenant_id == source.tenant_id,
        "database_id": cut.database_id == source.database_identity[0],
        "current_run_id": cut.current_run.subject_id == run.run_id,
        "current_run_head": cut.current_run.revision.head == run.head,
        "current_run_fingerprint": cut.current_run.revision.fingerprint == run.digest(),
        "current_run_bytes": cut.current_run_bytes == run.canonical_bytes(),
        "response_id": cut.response_id == seal.response_seal.subject_id,
        "original_call_id": cut.original_call_id == accepted.original_call_id,
        "initialized": cut.initialized == accepted.initialized,
        "acceptance_accepted": row.acceptance.accepted == cut.accepted.revision,
        "acceptance_execution_intent": (
            row.acceptance.execution_intent == cut.execution_intent.revision
        ),
        "acceptance_effect_intent": (
            row.acceptance.external_effect_intent == cut.external_effect_intent.revision
        ),
        "accepted_id": cut.accepted.subject_id == accepted_record.accepted_id,
        "accepted_bytes": cut.accepted_record_bytes == accepted_record.canonical_bytes(),
        "execution_intent_id": (
            cut.execution_intent.subject_id == execution_intent.execution_intent_id
        ),
        "execution_intent_bytes": (
            cut.execution_intent_record_bytes == execution_intent.canonical_bytes()
        ),
        "effect_intent": cut.external_effect_intent == execution_intent.external_intent,
        "outcome_record_id": cut.selected_outcome_record_id == outcome.outcome_record_id,
        "outcome_bytes": cut.selected_outcome_bytes == selected_outcome_bytes,
        "outcome_fingerprint": cut.selected_outcome_fingerprint
        == hashlib.sha256(selected_outcome_bytes).hexdigest(),
        "decision_id": cut.selected_decision.subject_id == selected.subject_id,
        "decision_head": cut.selected_decision.revision.head == selected.head,
        "decision_fingerprint": cut.selected_decision.revision.fingerprint == selected.fingerprint,
        "outcome_commit_sequence": cut.outcome_commit_sequence == outcome.commit_sequence,
        "publication_ordinal": cut.publication_ordinal == outcome.publication_ordinal,
        "batch_record_ids": cut.batch_record_ids == outcome.batch_record_ids,
    }
    mismatches = tuple(name for name, valid in checks.items() if not valid)
    if mismatches != (
        "current_run_head",
        "current_run_fingerprint",
        "current_run_bytes",
    ):
        return mismatches
    if not _selected_suspension_preserves_open_cut(
        runtime,
        cut,
        run,
        current_run_ref,
        snapshot,
        original_obligation,
    ):
        return mismatches
    return ()


def _selected_suspension_preserves_open_cut(
    runtime: CommonCliExecutionRuntime,
    cut,
    current_run: ExecutionRunRecord,
    current_run_ref: CallSubjectHead,
    authenticated_history: ExecutionHistorySnapshot,
    original_obligation: OriginalObligationBinding,
) -> bool:
    """Allow the retained OPEN cut's Run to advance only through its selected suspension."""
    if (
        original_obligation.original_run_id != cut.current_run.subject_id
        or original_obligation.original_call_id != cut.original_call_id
        or original_obligation.evidence_stream_id != cut.external_effect_intent.subject_id
    ):
        return False
    retained_runs = tuple(
        record
        for record in authenticated_history.records
        if isinstance(record, ExecutionRunRecord)
        and record.run_id == cut.current_run.subject_id
        and record.head == cut.current_run.revision.head
        and record.digest() == cut.current_run.revision.fingerprint
        and record.canonical_bytes() == cut.current_run_bytes
    )
    current_runs = tuple(
        record
        for record in authenticated_history.records
        if isinstance(record, ExecutionRunRecord)
        and record.run_id == current_run.run_id
        and record.head == current_run.head
        and record.digest() == current_run.digest()
        and record.canonical_bytes() == current_run.canonical_bytes()
    )
    if (
        len(retained_runs) != 1
        or len(current_runs) != 1
        or current_run.tenant != retained_runs[0].tenant
        or current_run.run_id != retained_runs[0].run_id
        or current_run.predecessor != retained_runs[0].head
    ):
        return False
    from chiplog.composition import r14_execution_suspension as suspension

    operation_id = (
        "execution-suspension:"
        + cut.current_run.subject_id
        + ":"
        + cut.current_run.revision.head
    )
    try:
        selected_entry = suspension._entry_for(runtime, operation_id)
        if selected_entry is None:
            return False
        _, _, entry = selected_entry
        evidence = suspension._retained(entry)
        command = suspension._command(evidence)
        validated_evidence, selected = suspension._validate_selected(
            runtime,
            entry,
            command,
            check_history=False,
        )
        if validated_evidence != evidence:
            return False
        result_run = evidence.result.run
        matching_obligations = tuple(
            binding
            for binding in selected.baseline.original_obligations
            if binding == original_obligation
        )
        return (
            evidence.request.cut.current_run == cut.current_run
            and selected.baseline.predecessor_run == cut.current_run
            and selected.pair.predecessor_run == cut.current_run
            and selected.pair.suspended_run == current_run_ref
            and result_run == current_run
            and result_run.canonical_bytes() == current_run.canonical_bytes()
            and len(matching_obligations) == 1
        )
    except (LoopRejected, OSError, RuntimeError, TypeError, ValueError, KeyError):
        return False


def _selected_outcome_publication(
    outcome: DispatchOutcomeRecordV2,
    member: SelectedEffectHistoryMember,
    row: StoredEffectRow,
) -> R14NativeSelectedOutcomePublication | None:
    raw = outcome.canonical_bytes()
    if not (
        row.record.owner == "effects"
        and row.record.record_id == outcome.record.head
        and row.record.schema_id == outcome.schema_id
        and row.record.record_kind == "effects." + outcome.kind
        and row.record.canonical_bytes == raw
        and row.record.fingerprint == hashlib.sha256(raw).hexdigest()
        and member.record == outcome.record
        and member.record_bytes == raw
        and member.publication_ordinal == row.publication_ordinal
        and row.commit_sequence > 0
        and bool(row.batch_record_ids)
        and outcome.record.head in row.batch_record_ids
    ):
        return None
    return R14NativeSelectedOutcomePublication(
        record_id=row.record.record_id,
        commit_sequence=row.commit_sequence,
        publication_ordinal=row.publication_ordinal,
        batch_record_ids=row.batch_record_ids,
    )


def _open_outcome_join_is_valid(
    expected_intent_id: str,
    retained: tuple[DispatchOutcomeRecordV2, SelectedEffectHistoryMember, StoredEffectRow],
    latest: tuple[
        DispatchRecordV2 | DispatchOutcomeRecordV2,
        SelectedEffectHistoryMember,
        StoredEffectRow,
    ],
) -> DispatchOutcomeRecordV2 | None:
    """Check the inert R14 cause/current-outcome join after verified traversal.

    The caller supplies the last per-intent member from the authenticated,
    ordered R16 history. This helper does not select or authenticate history.
    """
    outcome, member, row = retained
    latest_record, latest_member, latest_row = latest
    if not isinstance(latest_record, DispatchOutcomeRecordV2):
        return None
    intent = outcome.snapshot.intent
    latest_snapshot = latest_record.snapshot
    intent_reference = effects_reference(intent.intent_id, intent.canonical_bytes())
    if not (
        outcome.snapshot.state == "OUTCOME_UNKNOWN"
        and outcome.snapshot.obligation.state == "OPEN"
        and intent.intent_id == expected_intent_id
        and outcome.snapshot.obligation.original_intent == intent_reference
        and member.record_bytes == row.record.canonical_bytes
        and member.publication_ordinal == row.publication_ordinal
        and row.commit_sequence > 0
        and bool(row.batch_record_ids)
        and latest_snapshot.intent.canonical_bytes() == intent.canonical_bytes()
        and latest_snapshot.obligation.state == "OPEN"
        and latest_snapshot.obligation.original_intent
        == outcome.snapshot.obligation.original_intent
        and latest_member.record_bytes == latest_row.record.canonical_bytes
        and latest_member.publication_ordinal == latest_row.publication_ordinal
        and latest_row.commit_sequence > 0
        and bool(latest_row.batch_record_ids)
    ):
        return None
    return latest_record


def _closed_outcome_join_is_valid(
    expected_intent_id: str,
    retained: tuple[DispatchOutcomeRecordV2, SelectedEffectHistoryMember, StoredEffectRow],
    latest: tuple[
        DispatchRecordV2 | DispatchOutcomeRecordV2,
        SelectedEffectHistoryMember,
        StoredEffectRow,
    ],
    verified_records: tuple[DispatchRecordV2 | DispatchOutcomeRecordV2, ...],
    verified_members: tuple[SelectedEffectHistoryMember, ...],
) -> DispatchOutcomeRecordV2 | None:
    """Join one retained OPEN cause to its exact terminal in this verified chain."""
    outcome, member, row = retained
    latest_record, latest_member, latest_row = latest
    if not isinstance(latest_record, DispatchOutcomeRecordV2):
        return None
    intent = outcome.snapshot.intent
    latest_snapshot = latest_record.snapshot
    intent_reference = effects_reference(intent.intent_id, intent.canonical_bytes())
    if not (
        outcome.snapshot.state == "OUTCOME_UNKNOWN"
        and outcome.snapshot.obligation.state == "OPEN"
        and latest_snapshot.state in {"CONFIRMED", "FAILED_NO_EFFECT"}
        and latest_snapshot.obligation.state == "CLOSED"
        and intent.intent_id == expected_intent_id
        and outcome.snapshot.obligation.original_intent == intent_reference
        and latest_snapshot.intent.canonical_bytes() == intent.canonical_bytes()
        and latest_snapshot.obligation.original_intent
        == outcome.snapshot.obligation.original_intent
        and latest_snapshot.obligation.obligation == outcome.snapshot.obligation.obligation
        and latest_snapshot.obligation.original_send == outcome.snapshot.obligation.original_send
        and latest_snapshot.obligation.original_children
        == outcome.snapshot.obligation.original_children
        and member in verified_members
        and latest_member in verified_members
        and member.record == outcome.record
        and latest_member.record == latest_record.record
        and member.record_bytes == row.record.canonical_bytes == outcome.canonical_bytes()
        and latest_member.record_bytes
        == latest_row.record.canonical_bytes
        == latest_record.canonical_bytes()
        and member.intent == effects_reference(intent.intent_id, intent.canonical_bytes())
        and latest_member.intent == effects_reference(intent.intent_id, intent.canonical_bytes())
        and row.record.owner == "effects"
        and latest_row.record.owner == "effects"
        and row.record.record_id == outcome.record.head
        and latest_row.record.record_id == latest_record.record.head
        and row.record.schema_id == outcome.schema_id
        and latest_row.record.schema_id == latest_record.schema_id
        and row.record.record_kind == "effects." + outcome.kind
        and latest_row.record.record_kind == "effects." + latest_record.kind
        and row.record.fingerprint == hashlib.sha256(outcome.canonical_bytes()).hexdigest()
        and latest_row.record.fingerprint
        == hashlib.sha256(latest_record.canonical_bytes()).hexdigest()
        and member.publication_ordinal == row.publication_ordinal
        and latest_member.publication_ordinal == latest_row.publication_ordinal
        and row.commit_sequence > 0
        and latest_row.commit_sequence > 0
        and bool(row.batch_record_ids)
        and bool(latest_row.batch_record_ids)
        and outcome.record.head in row.batch_record_ids
        and latest_record.record.head in latest_row.batch_record_ids
    ):
        return None
    try:
        retained_index = verified_records.index(outcome)
        latest_index = verified_records.index(latest_record)
    except ValueError:
        return None
    if retained_index >= latest_index:
        return None
    intent_history = tuple(
        record
        for record in verified_records
        if record.snapshot.intent.intent_id == expected_intent_id
    )
    if not intent_history or intent_history[-1] != latest_record:
        return None
    try:
        first = intent_history.index(outcome)
        last = intent_history.index(latest_record)
    except ValueError:
        return None
    if first >= last or any(
        current.predecessor != previous.record
        for previous, current in zip(
            intent_history[first:last], intent_history[first + 1 : last + 1], strict=True
        )
    ):
        return None
    return latest_record


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
        self,
        target_run: CallSubjectHead | None = None,
        *,
        selected_only: bool = False,
        authenticated_history=None,
        authenticated_projection: bool = False,
    ) -> R14NativeSelectedAccountingSourceFacts | None:
        """Collect one complete Run lineage; ``target_run`` can only anchor it exactly."""
        if target_run is not None and type(target_run) is not CallSubjectHead:
            raise TypeError("target Run locator must be an exact CallSubjectHead")
        gate = self._runtime._authority_gate()
        try:
            with gate.hold():
                self._runtime._check_database_identity()
                database_identity = self._runtime._database_identity
                facts = self._collect_held(
                    target_run,
                    database_identity,
                    selected_only=selected_only,
                    authenticated_history=authenticated_history,
                    authenticated_projection=authenticated_projection,
                )
                self._runtime._check_database_identity()
                if self._runtime._database_identity != database_identity:
                    return None
                return facts
        except OSError, RuntimeError, TypeError, ValueError, binascii.Error:
            return None

    def collect_one_cancelled(
        self,
        target_run: CallSubjectHead | None = None,
        *,
        original_call_id: str | None = None,
    ) -> R14NativeOneCallCancelledFacts | None:
        """Project one exact cancelled member from the complete selected Run facts."""
        facts = self.collect(target_run)
        if facts is None:
            return None
        if (
            facts.target_legacy_seals
            or len(facts.selected_native_seals) != 1
        ):
            return None
        seal = facts.selected_native_seals[0]
        if not seal.initialized_records or not seal.inventory_rows:
            return None
        cancellations = facts.target_cancellations
        if original_call_id is None:
            if len(cancellations) != 1:
                return None
            original_call_id = cancellations[0].request.original_call_id
        matching_cancellations = tuple(
            item for item in cancellations if item.request.original_call_id == original_call_id
        )
        if len(matching_cancellations) != 1:
            return None
        records = tuple(
            item for item in seal.initialized_records if item.original_call_id == original_call_id
        )
        rows = tuple(
            item for item in seal.inventory_rows if item.original_call_id == original_call_id
        )
        if len(records) != 1 or len(rows) != 1:
            return None
        record, row = records[0], rows[0]
        initialized = reference(record.original_call_id, record)
        try:
            raw = base64.b64decode(record.call.canonical_call_base64, validate=True)
            tool = ConsequentialToolCall.model_validate_json(raw)
        except ValueError, binascii.Error:
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
        ):
            return None
        cancellation = matching_cancellations[0]
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

    def prepare_sealed_accounting_v3(
        self,
        selected_response: CallSubjectHead,
        *,
        authenticated_history=None,
        authenticated_effects_history: ExecutionHistorySnapshot | None = None,
        authenticated_accepted_open_recoveries: tuple = (),
        authenticated_projection: bool = False,
    ) -> PrepareNativeSealedAccountingV3 | None:
        """Build exact ordered terminal frontiers for one authenticated native seal."""
        if type(selected_response) is not CallSubjectHead:
            raise TypeError("selected response must be an exact CallSubjectHead")
        facts = self.collect(
            self._original_prepare.prepared_run,
            selected_only=authenticated_history is not None,
            authenticated_history=authenticated_history,
            authenticated_projection=authenticated_projection,
        )
        if facts is None or facts.target_legacy_seals:
            return None
        selected_seals = tuple(
            seal
            for seal in facts.selected_native_seals
            if seal.response_seal == selected_response
        )
        if len(selected_seals) != 1:
            return None
        seal = selected_seals[0]
        ordered_call_ids = tuple(
            record.original_call_id for record in seal.initialized_records
        )
        if (
            not ordered_call_ids
            or len(set(ordered_call_ids)) != len(ordered_call_ids)
            or len(seal.inventory_rows) != len(ordered_call_ids)
            or tuple(row.original_call_id for row in seal.inventory_rows) != ordered_call_ids
            or tuple(row.initialized_record for row in seal.inventory_rows)
            != seal.initialized_records
        ):
            return None
        frontiers: list[TerminalCallFrontier] = []
        for row in seal.inventory_rows:
            if isinstance(row.acceptance, InitializedCall):
                if row.terminal.kind != "PRESENT":
                    return None
                cancellation = self._cancelled_frontier_for_member(
                    facts, seal, row.original_call_id
                )
                if cancellation is None:
                    return None
                frontiers.append(cancellation)
                continue
            if (
                not isinstance(row.acceptance, ConsequentialAcceptedCall)
                or row.terminal.kind != "ABSENT"
            ):
                return None
            accepted = self._accepted_member_facts(
                facts, seal, row.original_call_id
            )
            if accepted is None:
                return None
            open_recoveries = tuple(
                evidence
                for evidence in authenticated_accepted_open_recoveries
                if evidence.request.cut.response_id == selected_response.subject_id
                and evidence.request.cut.original_call_id == row.original_call_id
            )
            if len(open_recoveries) > 1:
                return None
            if open_recoveries:
                if authenticated_history is None:
                    return None
                from chiplog.composition.r14_accepted_open_accounting_publication import (
                    _accepted_open_call_frontier,
                )

                cut = open_recoveries[0].request.cut
                accepted_outcome = self.collect_one_accepted_open_outcome(
                    accepted.source.target_run_lineage[-1],
                    selected_only=True,
                    expected_outcome_record_id=cut.selected_outcome_record_id,
                    expected_selected_decision=cut.selected_decision,
                    expected_tenant_commit_sequence=cut.tenant_commit_sequence,
                    original_call_id=row.original_call_id,
                    _accepted_facts=accepted,
                    authenticated_effects_history=authenticated_effects_history,
                )
                if accepted_outcome is None:
                    return None
                mismatches = _accepted_open_cut_mismatches(
                    self._runtime,
                    cut,
                    accepted_outcome,
                    authenticated_history,
                    open_recoveries[0].result.opening.original,
                )
                if mismatches:
                    return None
                try:
                    frontier = _accepted_open_call_frontier(
                        accepted,
                        cut,
                        open_recoveries[0].result,
                    )
                except (LoopRejected, TypeError, ValueError):
                    return None
                frontiers.append(frontier)
                continue
            from chiplog.composition.r14_accepted_direct_terminal_frontier import (
                selected_accepted_direct_terminal_call_frontier,
            )

            frontier = selected_accepted_direct_terminal_call_frontier(
                self._runtime,
                response_id=selected_response.subject_id,
                original_call_id=row.original_call_id,
                ordered_initialized_call_ids=ordered_call_ids,
                sealed_manifest_fingerprint=selected_response.revision.fingerprint,
                acceptance=row.acceptance,
                authenticated_history=authenticated_history,
                authenticated_effects_history=authenticated_effects_history,
                authenticated_projection=(
                    self._original_prepare if authenticated_projection else None
                ),
            )
            if frontier is None:
                return None
            frontiers.append(frontier)
        if len(frontiers) != len(ordered_call_ids) or tuple(
            item.original_call_id for item in frontiers
        ) != ordered_call_ids:
            return None
        try:
            return PrepareNativeSealedAccountingV3(
                command_id="native-sealed-accounting:" + selected_response.revision.fingerprint,
                selected_response=selected_response,
                sealed_manifest_fingerprint=selected_response.revision.fingerprint,
                ordered_sealed_call_ids=ordered_call_ids,
                complete_ordered_frontiers=tuple(frontiers),
            )
        except ValueError:
            return None

    @staticmethod
    def _cancelled_frontier_for_member(
        facts: R14NativeSelectedAccountingSourceFacts,
        seal: R14NativeSelectedResponseSealFacts,
        original_call_id: str,
    ) -> TerminalCallFrontier | None:
        records = tuple(
            record
            for record in seal.initialized_records
            if record.original_call_id == original_call_id
        )
        rows = tuple(
            row
            for row in seal.inventory_rows
            if row.original_call_id == original_call_id
        )
        cancellations = tuple(
            item
            for item in facts.target_cancellations
            if item.request.original_call_id == original_call_id
        )
        if len(records) != 1 or len(rows) != 1 or len(cancellations) != 1:
            return None
        record, row, cancellation = records[0], rows[0], cancellations[0]
        initialized = reference(record.original_call_id, record)
        terminal = cancellation.proposal.terminal
        result = cancellation.proposal.result
        terminal_ref = reference(terminal.terminal_id, terminal)
        result_ref = reference(result.result_id, result)
        if (
            row.initialized != initialized
            or row.initialized_record != record
            or row.acceptance != InitializedCall(initialized=initialized.revision)
            or row.terminal != terminal_ref.revision
            or cancellation.request.initialized != initialized
            or cancellation.request.initialized_record != record
            or cancellation.proposal.complete_ordered_record_manifest
            != (terminal_ref, result_ref)
            or terminal.original_call_id != original_call_id
            or terminal.initialized != initialized
            or result.original_call_id != original_call_id
            or result.initialized != initialized
            or result.terminal != terminal_ref
            or result.outcome != "NOT_EXECUTED"
        ):
            return None
        return TerminalCallFrontier(
            original_call_id=original_call_id,
            response_id=seal.response_seal.subject_id,
            acceptance=InitializedCall(initialized=initialized.revision),
            disposition=CancelledBeforeAcceptResult(
                terminal=terminal_ref.revision,
                not_executed_result=result_ref.revision,
                initialized_predecessor=initialized.revision,
                recovered_outcome=NotApplicable(),
            ),
        )

    @staticmethod
    def _accepted_member_facts(
        facts: R14NativeSelectedAccountingSourceFacts,
        seal: R14NativeSelectedResponseSealFacts,
        original_call_id: str,
    ) -> R14NativeOneCallAcceptedFacts | None:
        if facts.target_legacy_seals:
            return None
        acceptances = tuple(
            item
            for item in facts.target_acceptances
            if item.loop_request.binding.original_call_id == original_call_id
        )
        records = tuple(
            item for item in seal.initialized_records if item.original_call_id == original_call_id
        )
        rows = tuple(
            item for item in seal.inventory_rows if item.original_call_id == original_call_id
        )
        if len(acceptances) != 1 or len(records) != 1 or len(rows) != 1:
            return None
        acceptance, record, row = acceptances[0], records[0], rows[0]
        initialized = reference(record.original_call_id, record)
        try:
            raw = base64.b64decode(record.call.canonical_call_base64, validate=True)
            tool = ConsequentialToolCall.model_validate_json(raw)
        except ValueError, binascii.Error:
            return None
        binding = acceptance.loop_request.binding
        branch = row.acceptance
        manifest = tuple(
            member.revision for member in acceptance.loop_proposal.complete_acceptance_manifest
        )
        if (
            len(manifest) != 3
            or tool.canonical_bytes() != raw
            or tool.tool != "request_self_effect"
            or row.initialized != initialized
            or row.initialized_record != record
            or not isinstance(branch, ConsequentialAcceptedCall)
            or branch.initialized != initialized.revision
            or branch.accepted != manifest[0]
            or branch.execution_intent != manifest[1]
            or branch.external_effect_intent != manifest[2]
            or branch.complete_acceptance_manifest != manifest
            or row.terminal.kind != "ABSENT"
            or binding.original_call_id != original_call_id
            or binding.initialized != initialized
            or acceptance.loop_request.initialized_record != record
            or binding.original != record.call.original
            or acceptance.loop_proposal.accepted.binding != binding
            or acceptance.loop_proposal.execution_intent.original_call_id != original_call_id
            or acceptance.loop_proposal.execution_intent.initialized != initialized
        ):
            return None
        return R14NativeOneCallAcceptedFacts(
            source=facts,
            selected_seal=seal,
            original_call_id=original_call_id,
            initialized=initialized,
            acceptance=acceptance,
        )

    def collect_one_accepted(
        self,
        target_run: CallSubjectHead | None = None,
        *,
        selected_only: bool = False,
        original_call_id: str | None = None,
        _source_facts: R14NativeSelectedAccountingSourceFacts | None = None,
        authenticated_history=None,
    ) -> R14NativeOneCallAcceptedFacts | None:
        """Project one exact accepted member from the complete selected Run facts."""
        facts = (
            _source_facts
            if _source_facts is not None
            else self.collect(
                target_run,
                selected_only=selected_only,
                authenticated_history=authenticated_history,
            )
        )
        return self._accepted_facts(facts, original_call_id=original_call_id)

    @staticmethod
    def _accepted_facts(
        facts: R14NativeSelectedAccountingSourceFacts | None,
        *,
        original_call_id: str | None = None,
    ) -> R14NativeOneCallAcceptedFacts | None:
        """Project already-authenticated selected history to one exact acceptance."""
        if facts is None or facts.target_legacy_seals:
            return None
        acceptances = facts.target_acceptances
        if original_call_id is None:
            if len(acceptances) != 1:
                return None
            original_call_id = acceptances[0].loop_request.binding.original_call_id
        matching_acceptances = tuple(
            item
            for item in acceptances
            if item.loop_request.binding.original_call_id == original_call_id
        )
        matching_seals = tuple(
            seal
            for seal in facts.selected_native_seals
            if any(
                record.original_call_id == original_call_id
                for record in seal.initialized_records
            )
        )
        if len(matching_seals) != 1:
            return None
        seal = matching_seals[0]
        if not seal.initialized_records or not seal.inventory_rows:
            return None
        records = tuple(
            item for item in seal.initialized_records if item.original_call_id == original_call_id
        )
        rows = tuple(
            item for item in seal.inventory_rows if item.original_call_id == original_call_id
        )
        if len(matching_acceptances) != 1 or len(records) != 1 or len(rows) != 1:
            return None
        acceptance = matching_acceptances[0]
        record, row = records[0], rows[0]
        initialized = reference(record.original_call_id, record)
        try:
            raw = base64.b64decode(record.call.canonical_call_base64, validate=True)
            tool = ConsequentialToolCall.model_validate_json(raw)
        except ValueError, binascii.Error:
            return None
        binding = acceptance.loop_request.binding
        branch = row.acceptance
        manifest = tuple(
            member.revision for member in acceptance.loop_proposal.complete_acceptance_manifest
        )
        if (
            len(manifest) != 3
            or tool.canonical_bytes() != raw
            or tool.tool != "request_self_effect"
            or row.original_call_id != record.original_call_id
            or row.initialized != initialized
            or row.initialized_record != record
            or not isinstance(branch, ConsequentialAcceptedCall)
            or branch.initialized != initialized.revision
            or branch.accepted != manifest[0]
            or branch.execution_intent != manifest[1]
            or branch.external_effect_intent != manifest[2]
            or branch.complete_acceptance_manifest != manifest
            or row.terminal.kind != "ABSENT"
            or binding.original_call_id != record.original_call_id
            or binding.initialized != initialized
            or acceptance.loop_request.initialized_record != record
            or binding.original != record.call.original
            or acceptance.loop_proposal.accepted.binding != binding
            or acceptance.loop_proposal.execution_intent.original_call_id != record.original_call_id
            or acceptance.loop_proposal.execution_intent.initialized != initialized
        ):
            return None
        return R14NativeOneCallAcceptedFacts(
            source=facts,
            selected_seal=seal,
            original_call_id=record.original_call_id,
            initialized=initialized,
            acceptance=acceptance,
        )

    def collect_one_accepted_open_outcome(
        self,
        target_run: CallSubjectHead | None = None,
        *,
        selected_only: bool = False,
        expected_outcome_record_id: str | None = None,
        expected_selected_decision: CallSubjectHead | None = None,
        expected_tenant_commit_sequence: int | None = None,
        original_call_id: str | None = None,
        _accepted_facts: R14NativeOneCallAcceptedFacts | None = None,
        authenticated_effects_history: ExecutionHistorySnapshot | None = None,
        authenticated_recovery_history=None,
    ) -> R14NativeAcceptedOpenOutcome | None:
        """Join the retained OPEN cause to a still-open current R16 outcome."""
        return self._collect_one_accepted_outcome(
            target_run,
            selected_only=selected_only,
            expected_outcome_record_id=expected_outcome_record_id,
            expected_selected_decision=expected_selected_decision,
            expected_tenant_commit_sequence=expected_tenant_commit_sequence,
            current_obligation_state="OPEN",
            accepted_facts=_accepted_facts,
            original_call_id=original_call_id,
            authenticated_effects_history=authenticated_effects_history,
            authenticated_recovery_history=authenticated_recovery_history,
        )

    def collect_one_accepted_closed_outcome(
        self,
        target_run: CallSubjectHead | None = None,
        *,
        selected_only: bool = False,
        expected_outcome_record_id: str | None = None,
        expected_selected_decision: CallSubjectHead | None = None,
        expected_tenant_commit_sequence: int | None = None,
        allow_authenticated_later_suffix: bool = False,
        original_call_id: str | None = None,
        _accepted_facts: R14NativeOneCallAcceptedFacts | None = None,
        authenticated_effects_history: ExecutionHistorySnapshot | None = None,
        expected_closed_outcome_locator: R14NativeHistoricalClosedOutcomeLocator | None = None,
    ) -> R14NativeAcceptedOpenOutcome | None:
        """Read a closed current outcome while retaining its original OPEN cause.

        This is an inert observation only; it does not select or publish an R14
        terminal disposition.
        """
        return self._collect_one_accepted_outcome(
            target_run,
            selected_only=selected_only,
            expected_outcome_record_id=expected_outcome_record_id,
            expected_selected_decision=expected_selected_decision,
            expected_tenant_commit_sequence=expected_tenant_commit_sequence,
            current_obligation_state="CLOSED",
            allow_authenticated_later_suffix=allow_authenticated_later_suffix,
            accepted_facts=_accepted_facts,
            original_call_id=original_call_id,
            authenticated_effects_history=authenticated_effects_history,
            expected_closed_outcome_locator=expected_closed_outcome_locator,
        )

    def _collect_one_accepted_outcome(
        self,
        target_run: CallSubjectHead | None = None,
        *,
        selected_only: bool = False,
        expected_outcome_record_id: str | None = None,
        expected_selected_decision: CallSubjectHead | None = None,
        expected_tenant_commit_sequence: int | None = None,
        current_obligation_state: Literal["OPEN", "CLOSED"],
        allow_authenticated_later_suffix: bool = False,
        accepted_facts: R14NativeOneCallAcceptedFacts | None = None,
        original_call_id: str | None = None,
        authenticated_effects_history: ExecutionHistorySnapshot | None = None,
        authenticated_recovery_history=None,
        expected_closed_outcome_locator: R14NativeHistoricalClosedOutcomeLocator | None = None,
    ) -> R14NativeAcceptedOpenOutcome | None:
        """Join one accepted call's retained OPEN cause to its current outcome.

        For an OPEN current head, select only that current record. For a CLOSED
        current head, select the unique original OUTCOME_UNKNOWN/OPEN member as
        cause and verify that its exact chain ends at the current closed member.
        Startup validation may provide the exact original member explicitly.
        """
        if (
            (expected_outcome_record_id is None) != (expected_selected_decision is None)
            or (expected_outcome_record_id is None)
            != (expected_tenant_commit_sequence is None)
        ):
            raise TypeError("historical OPEN locator requires its selected decision")
        if (
            expected_selected_decision is not None
            and type(expected_selected_decision) is not CallSubjectHead
        ):
            raise TypeError("historical OPEN selected decision must be exact")
        if expected_closed_outcome_locator is not None:
            if current_obligation_state != "CLOSED":
                raise TypeError("historical CLOSED locator requires a closed outcome reader")
            if type(expected_closed_outcome_locator) is not R14NativeHistoricalClosedOutcomeLocator:
                raise TypeError("historical CLOSED locator must be exact")
            if (
                expected_tenant_commit_sequence is not None
                and expected_tenant_commit_sequence
                != expected_closed_outcome_locator.outcome_commit_sequence
            ):
                raise TypeError("historical CLOSED locator disagrees with selected tenant sequence")
            expected_tenant_commit_sequence = (
                expected_closed_outcome_locator.outcome_commit_sequence
            )
        from chiplog.composition.r16_dispatch_history import verify_dispatch_history
        from chiplog.composition.r16_effects import _read_materialized_effects_with_history

        runtime = self._runtime
        try:
            if not hasattr(runtime, "_owner_decisions"):
                return None
            with runtime._authority_gate().hold():
                runtime._check_database_identity()
                identity = runtime._database_identity
                with read_connection(runtime._database) as connection:
                    before = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
                anchored_before = runtime._commitment_journal.load(runtime._tenant_id)
                journal = runtime._owner_decisions().snapshot()
                # Collect under the authority gate so this exact selected source
                # remains fixed through the R16 history read below. Recollecting
                # it under the same gate added a second complete R14 replay.
                accepted = accepted_facts or self.collect_one_accepted(
                    target_run,
                    selected_only=selected_only,
                    original_call_id=original_call_id,
                    authenticated_history=authenticated_recovery_history,
                )
                if accepted is None:
                    return None
                seal_call_ids = frozenset(
                    item.original_call_id for item in accepted.selected_seal.initialized_records
                )
                # The physical suffix may interleave accepted members from any
                # authenticated seal of this Run. The target terminal still
                # selects its own seal and acceptance below.
                suffix_call_ids = frozenset(
                    item.original_call_id
                    for seal in accepted.source.selected_native_seals
                    for item in seal.initialized_records
                )
                allowed_intent_ids = frozenset(
                    row.acceptance.external_effect_intent.head
                    for row in accepted.source.target_inventory_rows
                    if row.original_call_id in suffix_call_ids
                    and isinstance(row.acceptance, ConsequentialAcceptedCall)
                    and isinstance(row.acceptance.external_effect_intent, Present)
                )
                if accepted.original_call_id not in seal_call_ids or not allowed_intent_ids:
                    return None
                matching_rows = tuple(
                    row
                    for row in accepted.source.target_inventory_rows
                    if row.original_call_id == accepted.original_call_id
                )
                if len(matching_rows) != 1:
                    return None
                branch = matching_rows[0].acceptance
                if not isinstance(branch, ConsequentialAcceptedCall) or not isinstance(
                    branch.external_effect_intent, Present
                ):
                    return None
                suffix_verifier = None
                native_suffix_authenticated = False
                if (
                    expected_tenant_commit_sequence is not None
                    or allow_authenticated_later_suffix
                ):
                    from chiplog.composition.r14_historical_suffix import (
                        authenticate_selected_open_prefix_held,
                        authenticate_selected_pending_r14_suffix_held,
                        authenticate_selected_r14_native_terminal_suffix_held,
                    )
                    from chiplog.composition.r14_original_call_resolution_source import (
                        authenticate_selected_resolution_prefix_held,
                    )

                    def authenticate_selected_suffix(runtime, actual, anchored):
                        nonlocal native_suffix_authenticated
                        pending = runtime._pending()
                        if (
                            len(pending) == 1
                            and pending[0].get("operation_kind")
                            == "agent_loop.execution-accounting-readiness.v1"
                        ):
                            from chiplog.composition.r14_recovery_publication import (
                                authenticate_selected_recovery_prefix_held,
                            )

                            return authenticate_selected_recovery_prefix_held(
                                runtime, actual, anchored
                            )
                        if authenticate_selected_resolution_prefix_held(
                            runtime, actual, anchored
                        ):
                            return True
                        if authenticate_selected_open_prefix_held(runtime, actual, anchored):
                            return True
                        if authenticate_selected_pending_r14_suffix_held(
                            runtime, actual, anchored
                        ):
                            return True
                        native_suffix_authenticated = (
                            authenticate_selected_r14_native_terminal_suffix_held(
                                runtime,
                                actual,
                                anchored,
                                original_call_id=accepted.original_call_id,
                            )
                        )
                        return native_suffix_authenticated

                    suffix_verifier = authenticate_selected_suffix
                cut, retained_journal = _read_materialized_effects_with_history(
                    runtime,
                    runtime._owner_decisions(),
                    authenticate_later_suffix=suffix_verifier,
                    authenticated_execution_history=authenticated_effects_history,
                )
                verified = verify_dispatch_history(cut, retained_journal)
                if native_suffix_authenticated:
                    from chiplog.composition.r14_historical_suffix import (
                        validate_native_terminal_suffix_semantics,
                    )

                    validate_native_terminal_suffix_semantics(
                        runtime,
                        retained_journal,
                        accepted.original_call_id,
                        allowed_intent_ids=allowed_intent_ids,
                    )
                authenticated_suffix = False
                if (
                    expected_tenant_commit_sequence is not None
                    or allow_authenticated_later_suffix
                ) and (
                    before != anchored_before
                    or anchored_before != cut.materialization_commitment
                ):
                    authenticated_suffix = bool(
                        suffix_verifier is not None
                        and suffix_verifier(runtime, before, anchored_before)
                    )
                if (
                    retained_journal != journal
                    or cut.materialization_commitment != before
                    or (
                        runtime._commitment_journal.load(runtime._tenant_id) != before
                        and not authenticated_suffix
                    )
                    or runtime._database_identity != identity
                ):
                    return None
            expected_intent = accepted.acceptance.loop_proposal.execution_intent.external_intent
            candidates: list[
                tuple[DispatchOutcomeRecordV2, SelectedEffectHistoryMember, StoredEffectRow]
            ] = []
            latest: dict[
                str,
                tuple[
                    DispatchRecordV2 | DispatchOutcomeRecordV2,
                    SelectedEffectHistoryMember,
                    StoredEffectRow,
                ],
            ] = {}
            latest_at_cut: dict[
                str,
                tuple[
                    DispatchRecordV2 | DispatchOutcomeRecordV2,
                    SelectedEffectHistoryMember,
                    StoredEffectRow,
                ],
            ] = {}
            member_by_bytes = {item.record_bytes: item for item in verified.members}
            if len(member_by_bytes) != len(verified.members):
                return None
            for row in cut.rows:
                member = member_by_bytes.get(row.record.canonical_bytes)
                if member is None:
                    return None
                record = next(
                    (
                        item
                        for item in verified.v2_records
                        if item.record.head == member.record.head
                    ),
                    None,
                )
                if record is None:
                    return None
                latest[record.snapshot.intent.intent_id] = (record, member, row)
                if (
                    expected_tenant_commit_sequence is not None
                    and row.commit_sequence <= expected_tenant_commit_sequence
                ):
                    latest_at_cut[record.snapshot.intent.intent_id] = (record, member, row)
                if not isinstance(record, DispatchOutcomeRecordV2):
                    continue
                intent_ref = effects_reference(
                    record.snapshot.intent.intent_id,
                    record.snapshot.intent.canonical_bytes(),
                )
                if (
                    expected_intent.subject_id == intent_ref.subject_id
                    and expected_intent.revision.fingerprint == intent_ref.fingerprint
                ):
                    candidates.append((record, member, row))
            if expected_closed_outcome_locator is not None:
                # The terminal identifies the CLOSED member. The OPEN cause is
                # still selected independently from the same verified intent
                # history; the locator must never be reinterpreted as that cause.
                selected = [
                    item
                    for item in candidates
                    if item[0].snapshot.state == "OUTCOME_UNKNOWN"
                    and item[0].snapshot.obligation.state == "OPEN"
                ]
            elif expected_outcome_record_id is None:
                if current_obligation_state == "OPEN":
                    selected = [
                        item
                        for item in candidates
                        if item[0].snapshot.state == "OUTCOME_UNKNOWN"
                        and item[0].snapshot.obligation.state == "OPEN"
                        and isinstance(
                            latest.get(
                                item[0].snapshot.intent.intent_id,
                                (None, None, None),
                            )[0],
                            DispatchOutcomeRecordV2,
                        )
                        and latest[item[0].snapshot.intent.intent_id][0]
                        .snapshot.obligation.state
                        == "OPEN"
                    ]
                else:
                    selected = [
                        item
                        for item in candidates
                        if item[0].snapshot.state == "OUTCOME_UNKNOWN"
                        and item[0].snapshot.obligation.state == "OPEN"
                    ]
            else:
                expected_decision = expected_selected_decision
                if expected_decision is None:
                    return None
                selected = [
                    item
                    for item in candidates
                    if item[2].record.record_id == expected_outcome_record_id
                    and item[1].selected_decision.subject_id == expected_decision.subject_id
                    and item[1].selected_decision.head == expected_decision.revision.head
                    and item[1].selected_decision.fingerprint
                    == expected_decision.revision.fingerprint
                    and latest_at_cut.get(item[0].snapshot.intent.intent_id)
                    == (item if item[0].snapshot.obligation.state == "OPEN" else None)
                ]
            if len(selected) != 1:
                return None
            outcome, member, row = selected[0]
            if expected_closed_outcome_locator is not None:
                locator = expected_closed_outcome_locator
                historical_closure = latest_at_cut.get(outcome.snapshot.intent.intent_id)
                # `latest_at_cut` was built from the authenticated physical
                # snapshot, so it is the last member for this intent at the
                # terminal's exact tenant sequence. Later members remain in
                # `latest` and stay independently covered by full-snapshot and
                # suffix authentication above.
                if (
                    historical_closure is None
                    or not isinstance(historical_closure[0], DispatchOutcomeRecordV2)
                    or historical_closure[0].snapshot.obligation.state != "CLOSED"
                    or historical_closure[2].record.record_id != locator.selected_outcome_record_id
                    or historical_closure[0].canonical_bytes() != locator.selected_outcome_bytes
                    or historical_closure[2].record.fingerprint
                    != locator.selected_outcome_fingerprint
                    or historical_closure[1].canonical_bytes()
                    != locator.selected_effect_member_bytes
                    or historical_closure[1].selected_decision.subject_id
                    != locator.selected_decision.subject_id
                    or historical_closure[1].selected_decision.head
                    != locator.selected_decision.revision.head
                    or historical_closure[1].selected_decision.fingerprint
                    != locator.selected_decision.revision.fingerprint
                    or historical_closure[2].commit_sequence != locator.outcome_commit_sequence
                    or historical_closure[2].publication_ordinal != locator.publication_ordinal
                    or historical_closure[2].batch_record_ids != locator.batch_record_ids
                ):
                    return None
                prefix_record_ids = {
                    item.record.record_id
                    for item in cut.rows
                    if item.commit_sequence <= locator.outcome_commit_sequence
                }
                prefix_records = tuple(
                    item for item in verified.v2_records if item.record.head in prefix_record_ids
                )
                prefix_members = tuple(
                    item
                    for item in verified.members
                    if item.record.head in prefix_record_ids
                )
                latest_outcome = _closed_outcome_join_is_valid(
                    expected_intent.subject_id,
                    (outcome, member, row),
                    historical_closure,
                    prefix_records,
                    prefix_members,
                )
                if latest_outcome is None:
                    return None
                current = historical_closure
            else:
                current = (
                    latest_at_cut.get(outcome.snapshot.intent.intent_id)
                    if expected_tenant_commit_sequence is not None
                    else latest.get(outcome.snapshot.intent.intent_id)
                )
            if current is None:
                return None
            latest_record, latest_member, latest_row = current
            if expected_closed_outcome_locator is not None:
                # Already checked as the exact last member of its historical
                # prefix above; do not apply full-history terminality to it.
                pass
            elif current_obligation_state == "OPEN":
                latest_outcome = _open_outcome_join_is_valid(
                    expected_intent.subject_id,
                    (outcome, member, row),
                    (latest_record, latest_member, latest_row),
                )
            else:
                latest_outcome = _closed_outcome_join_is_valid(
                    expected_intent.subject_id,
                    (outcome, member, row),
                    (latest_record, latest_member, latest_row),
                    verified.v2_records,
                    verified.members,
                )
            if latest_outcome is None:
                return None
            latest_publication = _selected_outcome_publication(
                latest_outcome, latest_member, latest_row
            )
            if latest_publication is None:
                return None
            evidence_sources = complete_ordered_evidence_sources(latest_outcome, verified)
            dispatch_sources = complete_ordered_dispatch_sources(expected_intent, verified)
            if evidence_sources is None or dispatch_sources is None:
                return None
            dispatch_members = tuple(
                member
                for member in verified.members
                if member.intent.subject_id == expected_intent.subject_id
            )
            if len(dispatch_members) != len(dispatch_sources):
                return None
            with read_connection(runtime._database) as connection:
                after = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
            anchored_after = runtime._commitment_journal.load(runtime._tenant_id)
            if (
                expected_tenant_commit_sequence is not None or allow_authenticated_later_suffix
            ) and (
                after != anchored_after or anchored_after != before
            ):
                authenticated_suffix = bool(
                    suffix_verifier is not None
                    and suffix_verifier(runtime, after, anchored_after)
                )
            if (
                after != before
                or (anchored_after != before and not authenticated_suffix)
                or runtime._database_identity != identity
            ):
                return None
            return R14NativeAcceptedOpenOutcome(
                accepted=accepted,
                outcome=outcome,
                selected_member=member,
                latest_outcome=latest_outcome,
                latest_selected_member=latest_member,
                outcome_record_id=row.record.record_id,
                commit_sequence=row.commit_sequence,
                publication_ordinal=row.publication_ordinal,
                batch_record_ids=row.batch_record_ids,
                tenant_id=cut.tenant_id,
                tenant_commit_sequence=cut.tenant_frontier,
                materialization_commitment=cut.materialization_commitment,
                owner_journal_head=cut.owner_journal_head,
                database_identity=identity,
                latest_outcome_record_id=latest_publication.record_id,
                latest_commit_sequence=latest_publication.commit_sequence,
                latest_publication_ordinal=latest_publication.publication_ordinal,
                latest_batch_record_ids=latest_publication.batch_record_ids,
                complete_ordered_dispatch_sources=dispatch_sources,
                complete_ordered_dispatch_members=dispatch_members,
                complete_ordered_evidence_sources=evidence_sources,
            )
        except OSError, RuntimeError, TypeError, ValueError, KeyError, AttributeError:
            return None

    def _collect_held(
        self,
        target_run: CallSubjectHead | None,
        database_identity: tuple[str, int, int],
        *,
        selected_only: bool = False,
        authenticated_history=None,
        authenticated_projection: bool = False,
    ) -> R14NativeSelectedAccountingSourceFacts | None:
        # Both values are public DTOs.  Reopen their only authenticated source
        # before using a single field from either supplied instance.
        reopened = (
            self._original_prepare
            if authenticated_projection
            else R14OriginalNativePrepareSource(self._runtime).collect(
                self._original_prepare.selected_response_seal
            )
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
        if authenticated_history is None:
            history = read_execution_recovery_history(
                self._runtime, selected_only=selected_only
            )
        else:
            if not selected_only:
                return None
            history = authenticated_history
        snapshot, inventory, legacy, seals, cancellations, acceptances = history
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
    "R14NativeAcceptedOpenOutcome",
    "R14NativeOneCallAcceptedFacts",
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
