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
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
    ExecutionRunRecord,
)
from chiplog.capabilities.agent_loop.execution_preparation_bindings_contracts import (
    NativeExecutionPreparationBindingsV2,
    decode_native_execution_preparation_bindings_v2,
)
from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    ConsequentialAcceptedCall,
    InitializedCall,
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
from chiplog.composition.r14_loop_history import read_execution_recovery_history
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
                    target_run, database_identity, selected_only=selected_only
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

    def collect_one_accepted(
        self,
        target_run: CallSubjectHead | None = None,
        *,
        selected_only: bool = False,
        original_call_id: str | None = None,
    ) -> R14NativeOneCallAcceptedFacts | None:
        """Project one exact accepted member from the complete selected Run facts."""
        facts = self.collect(target_run, selected_only=selected_only)
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
        if len(facts.selected_native_seals) != 1:
            return None
        seal = facts.selected_native_seals[0]
        if not seal.initialized_records or not seal.inventory_rows:
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
                )
                if accepted is None:
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
                if (
                    expected_tenant_commit_sequence is not None
                    or allow_authenticated_later_suffix
                ):
                    from chiplog.composition.r14_original_call_resolution_source import (
                        authenticate_selected_resolution_prefix_held,
                    )

                    suffix_verifier = authenticate_selected_resolution_prefix_held
                cut, retained_journal = _read_materialized_effects_with_history(
                    runtime,
                    runtime._owner_decisions(),
                    authenticate_later_suffix=suffix_verifier,
                )
                verified = verify_dispatch_history(cut, retained_journal)
                authenticated_suffix = False
                if (
                    expected_tenant_commit_sequence is not None
                    or allow_authenticated_later_suffix
                ) and (
                    before != anchored_before
                    or anchored_before != cut.materialization_commitment
                ):
                    from chiplog.composition.r14_original_call_resolution_source import (
                        authenticate_selected_resolution_prefix_held,
                    )

                    authenticated_suffix = authenticate_selected_resolution_prefix_held(
                        runtime, before, anchored_before
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
            if expected_outcome_record_id is None:
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
            current = (
                latest_at_cut.get(outcome.snapshot.intent.intent_id)
                if expected_tenant_commit_sequence is not None
                else latest.get(outcome.snapshot.intent.intent_id)
            )
            if current is None:
                return None
            latest_record, latest_member, latest_row = current
            if current_obligation_state == "OPEN":
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
                from chiplog.composition.r14_original_call_resolution_source import (
                    authenticate_selected_resolution_prefix_held,
                )

                authenticated_suffix = authenticate_selected_resolution_prefix_held(
                    runtime, after, anchored_after
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
