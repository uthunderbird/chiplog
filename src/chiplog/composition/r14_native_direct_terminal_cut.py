"""Exact selected-source cut for a first accepted-call R14 terminal."""

from __future__ import annotations

import hashlib
import json

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.direct_terminal_contracts import AcceptedDirectTerminalCutV1
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    ConsequentialAcceptedCall,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.r14_loop_history import read_execution_history
from chiplog.composition.r14_native_selected_accounting_source import (
    R14NativeAcceptedOpenOutcome,
)
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.workspace_snapshot import read_connection


def _fingerprint(cut: AcceptedDirectTerminalCutV1) -> str:
    value = cut.model_dump(mode="json")
    del value["cut_fingerprint"]
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def build_accepted_direct_terminal_cut(
    runtime: CommonCliExecutionRuntime,
    closed: R14NativeAcceptedOpenOutcome,
    *,
    source_run: CallSubjectHead | None = None,
    source_materialization_commitment: str | None = None,
    source_tenant_commit_sequence: int | None = None,
) -> AcceptedDirectTerminalCutV1 | None:
    """Bind accepted-call lineage to the exact selected physical R16 closure."""
    if type(runtime) is not CommonCliExecutionRuntime:
        raise TypeError("accepted/direct terminal cut requires the mounted CLI runtime")
    try:
        with runtime._authority_gate().hold():
            runtime._check_database_identity()
            if (
                runtime._database_identity != closed.database_identity
                or runtime._tenant_id != closed.tenant_id
                or runtime._commitment_journal.load(runtime._tenant_id)
                != closed.materialization_commitment
            ):
                return None
            snapshot = read_execution_history(runtime)
            lineage = closed.accepted.source.target_run_lineage
            selected_run = lineage[-1] if source_run is None else source_run
            if selected_run not in lineage:
                return None
            matches = [
                item
                for item in snapshot.records
                if isinstance(item, ExecutionRunRecord)
                and item.run_id == selected_run.subject_id
                and item.head == selected_run.revision.head
            ]
            if len(matches) != 1:
                return None
            run = matches[0]
            acceptance = closed.accepted.acceptance
            matching_rows = tuple(
                row
                for row in closed.accepted.source.target_inventory_rows
                if row.original_call_id == closed.accepted.original_call_id
            )
            if len(matching_rows) != 1:
                return None
            branch = matching_rows[0].acceptance
            member = closed.latest_selected_member
            outcome = closed.latest_outcome
            if (
                not isinstance(branch, ConsequentialAcceptedCall)
                or outcome.snapshot.obligation.state != "CLOSED"
                or outcome.snapshot.state not in ("CONFIRMED", "FAILED_NO_EFFECT")
                or member.record_bytes != outcome.canonical_bytes()
                or member.publication_ordinal != closed.latest_publication_ordinal
                or closed.latest_outcome_record_id not in closed.latest_batch_record_ids
            ):
                return None
            cut = AcceptedDirectTerminalCutV1(
                tenant_id=runtime._tenant_id,
                database_id=runtime._database_identity[0],
                tenant_commit_sequence=(
                    closed.tenant_commit_sequence
                    if source_tenant_commit_sequence is None
                    else source_tenant_commit_sequence
                ),
                materialization_commitment=(
                    closed.materialization_commitment
                    if source_materialization_commitment is None
                    else source_materialization_commitment
                ),
                current_run=CallSubjectHead(
                    subject_id=run.run_id,
                    revision=Present(head=run.head, fingerprint=run.digest()),
                ),
                current_run_bytes=run.canonical_bytes(),
                response_id=closed.accepted.selected_seal.response_seal.subject_id,
                original_call_id=closed.accepted.original_call_id,
                initialized=closed.accepted.initialized,
                accepted=CallSubjectHead(
                    subject_id=acceptance.loop_proposal.accepted.accepted_id,
                    revision=branch.accepted,
                ),
                accepted_record_bytes=acceptance.loop_proposal.accepted.canonical_bytes(),
                execution_intent=CallSubjectHead(
                    subject_id=acceptance.loop_proposal.execution_intent.execution_intent_id,
                    revision=branch.execution_intent,
                ),
                execution_intent_record_bytes=(
                    acceptance.loop_proposal.execution_intent.canonical_bytes()
                ),
                external_effect_intent=acceptance.loop_proposal.execution_intent.external_intent,
                selected_outcome_record_id=closed.latest_outcome_record_id,
                selected_outcome_bytes=outcome.canonical_bytes(),
                selected_outcome_fingerprint=hashlib.sha256(
                    outcome.canonical_bytes()
                ).hexdigest(),
                selected_effect_member_bytes=member.canonical_bytes(),
                selected_decision=CallSubjectHead(
                    subject_id=member.selected_decision.subject_id,
                    revision=Present(
                        head=member.selected_decision.head,
                        fingerprint=member.selected_decision.fingerprint,
                    ),
                ),
                semantic_binding=member.semantics,
                outcome_commit_sequence=closed.latest_commit_sequence,
                publication_ordinal=closed.latest_publication_ordinal,
                batch_record_ids=closed.latest_batch_record_ids,
                cut_fingerprint="0" * 64,
            )
            cut = cut.model_copy(update={"cut_fingerprint": _fingerprint(cut)})
            with read_connection(runtime._database) as connection:
                commitment = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
            if (
                commitment != closed.materialization_commitment
                or runtime._commitment_journal.load(runtime._tenant_id) != commitment
                or runtime._database_identity != closed.database_identity
            ):
                return None
            return cut
    except (OSError, RuntimeError, TypeError, ValueError, KeyError, AttributeError):
        return None


__all__ = ["build_accepted_direct_terminal_cut"]
