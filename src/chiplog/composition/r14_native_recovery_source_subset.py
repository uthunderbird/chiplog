"""Authenticated selected/physical source subset for native cancellation recovery.

The returned value is deliberately narrower than an ExecutionRecoveryCut.  The
selected V24 accounting history proves these physical execution members, while
the other recovery families still need their own complete enumeration.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from pydantic import TypeAdapter

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.r14_acceptance_v2_contracts import RetainedAcceptancePreparationV2
from chiplog.composition.r14_execution_cancellation_contracts import (
    RetainedExecutionCancellationPreparation,
)
from chiplog.composition.r14_execution_fanout_contracts import RetainedExecutionFanOutPreparation
from chiplog.composition.r14_execution_transition_records import ExecutionHistorySnapshot
from chiplog.composition.r14_loop_history import (
    ExecutionRecoveryHistory,
    read_execution_recovery_history,
)
from chiplog.composition.r14_native_accounting_history import retained_selected_native_accounting
from chiplog.composition.r14_native_accounting_records import (
    NATIVE_ACCOUNTING_OPERATION,
    NativeOneCallCancelledAccountingRecord,
)
from chiplog.composition.r14_native_execution_record_source import (
    R14NativeExecutionRecordSourceManifest,
    R14NativeExecutionRecordSourceMember,
    _fingerprint,
)
from chiplog.composition.r14_native_preparation_bindings import (
    bind_native_execution_preparation_v2,
)
from chiplog.composition.r14_native_selected_accounting_source import (
    R14NativeAcceptedOpenOutcome,
    R14NativeOneCallAcceptedFacts,
    R14NativeOneCallCancelledFacts,
    R14NativeSelectedAccountingSource,
)
from chiplog.composition.r14_original_prepare_source import R14OriginalNativePrepareSource
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.workspace_snapshot import join_workspace_snapshot, read_connection


@dataclass(frozen=True, slots=True)
class R14NativeRecoverySourceSubset:
    """Exact V24 execution sources; no absent-family or cut authority claim."""

    tenant_id: str
    database_identity: tuple[str, int, int]
    tenant_commit_sequence: int
    materialization_commitment: str
    selected_accounting: NativeOneCallCancelledAccountingRecord | None
    selected_facts: R14NativeOneCallCancelledFacts | R14NativeOneCallAcceptedFacts
    execution_sources: tuple[RecoverySourceRecord, ...]
    authenticated_execution_snapshot: ExecutionHistorySnapshot | None = None
    authenticated_seals: tuple[RetainedExecutionFanOutPreparation, ...] = ()
    authenticated_cancellations: tuple[RetainedExecutionCancellationPreparation, ...] = ()
    authenticated_acceptances: tuple[RetainedAcceptancePreparationV2, ...] = ()
    accepted_open_outcome: R14NativeAcceptedOpenOutcome | None = None
    accepted_closed_outcome: R14NativeAcceptedOpenOutcome | None = None
    authenticated_execution_history: ExecutionRecoveryHistory | None = None

    @property
    def accepted_effect_sources(self) -> tuple[RecoverySourceRecord, ...]:
        """Exact selected R16 dispatch/evidence sources for the accepted call.

        These are source references returned by the authenticated R16 history
        reader. They do not assert that unrelated inventory families are absent;
        the full native cut still has to reconcile that inventory independently.
        """
        accepted = self.accepted_closed_outcome or self.accepted_open_outcome
        if accepted is None:
            return ()
        sources = (
            *accepted.complete_ordered_dispatch_sources,
            *accepted.complete_ordered_evidence_sources,
        )
        return sources


def _source(
    member: R14NativeExecutionRecordSourceMember, subject: CallSubjectHead
) -> RecoverySourceRecord:
    raw = member.canonical_record_bytes
    digest = hashlib.sha256(raw).hexdigest()
    return RecoverySourceRecord(
        owner=member.owner,
        subject=subject,
        schema_id=member.schema_id,
        canonical_record_bytes=raw,
        selected_decision=CallSubjectHead(
            subject_id=member.selected_decision_id,
            revision=Present(
                head=member.selected_decision_id,
                fingerprint=member.selected_decision_fingerprint,
            ),
        ),
        physical_record=CallSubjectHead(
            subject_id=member.record_id,
            revision=Present(head="record:" + digest, fingerprint=digest),
        ),
    )


class R14NativeRecoverySourceSubsetReader:
    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("native recovery sources require the mounted CLI runtime")
        self._runtime = runtime

    def collect(self) -> R14NativeRecoverySourceSubset | None:
        runtime = self._runtime
        try:
            with runtime._authority_gate().hold(), join_workspace_snapshot(runtime._database):
                runtime._check_database_identity()
                identity = runtime._database_identity
                before = runtime._commitment_journal.load(runtime._tenant_id)
                # This selected-history reader validates the retained V24 source
                # against every selected decision and physical execution row.
                authenticated_history = read_execution_recovery_history(
                    runtime, selected_only=True, exact_historical_sources=True
                )
                execution_snapshot, _, legacy, seals, cancellations, acceptances = (
                    authenticated_history
                )
                if legacy or len(seals) != 1:
                    return None
                if acceptances:
                    return self._collect_accepted(
                        runtime,
                        identity,
                        before,
                        execution_snapshot,
                        seals,
                        cancellations,
                        acceptances,
                        authenticated_history,
                    )
                if len(cancellations) != 1:
                    return None
                selected = []
                for _, _, raw in runtime._loop_decisions().entries():
                    entry = json.loads(raw)
                    if (
                        entry.get("kind") == "DECIDED"
                        and entry.get("operation_kind") == NATIVE_ACCOUNTING_OPERATION
                    ):
                        selected.append(retained_selected_native_accounting(entry))
                if len(selected) != 1:
                    return None
                evidence = selected[0]
                accounting = evidence.record
                facts = TypeAdapter(R14NativeOneCallCancelledFacts).validate_json(
                    evidence.selected_source_bytes
                )
                physical = TypeAdapter(R14NativeExecutionRecordSourceManifest).validate_json(
                    evidence.physical_source_bytes
                )
                if (
                    physical.source_stamp.database_identity != identity
                    or physical.tenant_id != runtime._tenant_id
                    or physical.fingerprint
                    != _fingerprint(
                        physical.tenant_id,
                        physical.source_stamp,
                        physical.original_prepared_run,
                        physical.selected_response_seal,
                        physical.initialized,
                        physical.terminal,
                        physical.result,
                        physical.complete_ordered_run_lineage,
                        physical.ordered_members,
                    )
                    or facts.source.target_run_lineage != physical.complete_ordered_run_lineage
                    or facts.selected_seal.response_seal != physical.selected_response_seal
                    or facts.initialized != physical.initialized
                ):
                    return None
                with read_connection(runtime._database) as connection:
                    commitment = capture_authority_snapshot_commitment(
                        connection, runtime._tenant_id
                    )
                    row = connection.execute(
                        "SELECT head FROM tenant_heads WHERE tenant_id=?",
                        (runtime._tenant_id,),
                    ).fetchone()
                if (
                    row is None
                    or commitment != before
                    or runtime._commitment_journal.load(runtime._tenant_id) != before
                    or runtime._database_identity != identity
                ):
                    return None
                subjects = (
                    *physical.complete_ordered_run_lineage,
                    physical.selected_response_seal,
                    physical.initialized,
                    physical.terminal,
                    physical.result,
                )
                if len(subjects) != len(physical.ordered_members):
                    return None
                sources = tuple(
                    _source(member, subject)
                    for member, subject in zip(physical.ordered_members, subjects, strict=True)
                )
                if len({source.physical_record.subject_id for source in sources}) != len(sources):
                    return None
                return R14NativeRecoverySourceSubset(
                    runtime._tenant_id,
                    identity,
                    int(row[0]),
                    commitment,
                    accounting,
                    facts,
                    sources,
                    execution_snapshot,
                    seals,
                    cancellations,
                    authenticated_execution_history=authenticated_history,
                )
        except (OSError, RuntimeError, TypeError, ValueError):
            return None

    @staticmethod
    def _collect_accepted(
        runtime,
        identity,
        before,
        execution_snapshot,
        seals,
        cancellations,
        acceptances,
        authenticated_history: ExecutionRecoveryHistory,
    ) -> R14NativeRecoverySourceSubset | None:
        if (
            cancellations
            or len(acceptances) != 1
            or not execution_snapshot.records
            or not isinstance(execution_snapshot.records[-1], ExecutionRunRecord)
        ):
            return None
        seal = seals[0].proposal.fan_out.response_seal
        seal_ref = CallSubjectHead(
            subject_id=seal.response_seal_id,
            revision=Present(head="record:" + seal.digest(), fingerprint=seal.digest()),
        )
        projection = R14OriginalNativePrepareSource(runtime).collect(seal_ref)
        if projection is None:
            return None
        source = R14NativeSelectedAccountingSource(
            runtime, projection, bind_native_execution_preparation_v2(projection)
        )
        run = execution_snapshot.records[-1]
        run_ref = CallSubjectHead(
            subject_id=run.run_id,
            revision=Present(head=run.head, fingerprint=run.digest()),
        )
        selected = source._collect_held(
            run_ref,
            identity,
            selected_only=True,
            authenticated_history=authenticated_history,
            authenticated_projection=True,
        )
        accepted = source._accepted_facts(selected)
        if accepted is None:
            return None
        from chiplog.composition import r14_original_call_resolution_publication

        allow_resolution_suffix = bool(
            r14_original_call_resolution_publication._selected_entries(runtime)
        )
        closed = source.collect_one_accepted_closed_outcome(
            run_ref,
            selected_only=True,
            allow_authenticated_later_suffix=allow_resolution_suffix,
            _accepted_facts=accepted,
        )
        opened = (
            None
            if closed is not None
            else source.collect_one_accepted_open_outcome(
                run_ref,
                selected_only=True,
                _accepted_facts=accepted,
            )
        )
        selected_outcome = closed or opened
        if (
            selected_outcome is None
            or selected_outcome.accepted.source.target_run_lineage[-1] != run_ref
            or selected_outcome.accepted.acceptance != acceptances[0]
            or selected_outcome.database_identity != identity
            or selected_outcome.tenant_id != runtime._tenant_id
            or selected_outcome.materialization_commitment != before
        ):
            return None
        with read_connection(runtime._database) as connection:
            commitment = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
            row = connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
            ).fetchone()
        if (
            row is None
            or commitment != before
            or runtime._commitment_journal.load(runtime._tenant_id) != before
            or runtime._database_identity != identity
        ):
            return None
        return R14NativeRecoverySourceSubset(
            tenant_id=runtime._tenant_id,
            database_identity=identity,
            tenant_commit_sequence=int(row[0]),
            materialization_commitment=commitment,
            selected_accounting=None,
            selected_facts=selected_outcome.accepted,
            execution_sources=(),
            authenticated_execution_snapshot=execution_snapshot,
            authenticated_seals=seals,
            authenticated_cancellations=(),
            authenticated_acceptances=acceptances,
            accepted_open_outcome=opened,
            accepted_closed_outcome=closed,
            authenticated_execution_history=authenticated_history,
        )


__all__ = ["R14NativeRecoverySourceSubset", "R14NativeRecoverySourceSubsetReader"]
