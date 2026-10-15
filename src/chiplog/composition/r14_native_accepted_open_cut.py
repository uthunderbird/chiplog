"""Versioned effects-specific R14 cut for one accepted OPEN R16 obligation."""

from __future__ import annotations

import hashlib
import json

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
    AcceptedOpenRecoveryCutV1,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.r14_loop_history import (
    read_execution_history,
    read_execution_recovery_history,
)
from chiplog.composition.r14_native_selected_accounting_source import (
    R14NativeAcceptedOpenOutcome,
)
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.workspace_snapshot import read_connection


def _fingerprint(cut: AcceptedOpenRecoveryCutV1) -> str:
    value = cut.model_dump(mode="json")
    del value["cut_fingerprint"]
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def build_accepted_open_recovery_cut(
    runtime: CommonCliExecutionRuntime,
    accepted_open: R14NativeAcceptedOpenOutcome,
    *,
    source_materialization_commitment: str | None = None,
    source_tenant_commit_sequence: int | None = None,
    authenticated_history=None,
) -> AcceptedOpenRecoveryCutV1 | None:
    """Bind retained R14 acceptance and selected R16 OPEN evidence to one source cut."""
    if type(runtime) is not CommonCliExecutionRuntime:
        raise TypeError("accepted/open recovery cut requires the mounted CLI runtime")
    if authenticated_history is not None:
        runtime._authority_gate().require_held()
    try:
        with runtime._authority_gate().hold():
            runtime._check_database_identity()
            with read_connection(runtime._database) as connection:
                actual = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
            anchored = runtime._commitment_journal.load(runtime._tenant_id)
            if (
                actual != accepted_open.materialization_commitment
                or runtime._database_identity != accepted_open.database_identity
                or runtime._tenant_id != accepted_open.tenant_id
            ):
                return None
            historical = (
                source_materialization_commitment is not None
                and source_tenant_commit_sequence is not None
            )
            if historical:
                # The canonical outer fold proves complete membership and any
                # exact pending suffix before a historical cut can be exported.
                history = (
                    authenticated_history if authenticated_history is not None
                    else read_execution_recovery_history(
                        runtime,
                        selected_only=True,
                        exact_historical_sources=True,
                    )
                )
                snapshot = history[0]
            else:
                if anchored != actual:
                    return None
                snapshot = read_execution_history(runtime)
            matches = [
                item
                for item in snapshot.records
                if isinstance(item, ExecutionRunRecord)
                and item.run_id == accepted_open.accepted.source.target_run_lineage[-1].subject_id
                and item.head == accepted_open.accepted.source.target_run_lineage[-1].revision.head
            ]
            if len(matches) != 1:
                return None
            run = matches[0]
            matching_rows = tuple(
                row
                for row in accepted_open.accepted.source.target_inventory_rows
                if row.original_call_id == accepted_open.accepted.original_call_id
            )
            if len(matching_rows) != 1:
                return None
            branch = matching_rows[0].acceptance
            acceptance = accepted_open.accepted.acceptance
            if (
                branch.kind != "CONSEQUENTIAL_ACCEPTED"
                or acceptance.loop_proposal.execution_intent.external_intent.subject_id
                != accepted_open.outcome.snapshot.intent.intent_id
            ):
                return None
            initialized = accepted_open.accepted.initialized
            selected_member = accepted_open.selected_member
            selected_decision = selected_member.selected_decision
            cut = AcceptedOpenRecoveryCutV1(
                tenant_id=runtime._tenant_id,
                database_id=runtime._database_identity[0],
                tenant_commit_sequence=(
                    accepted_open.tenant_commit_sequence
                    if source_tenant_commit_sequence is None
                    else source_tenant_commit_sequence
                ),
                materialization_commitment=(
                    accepted_open.materialization_commitment
                    if source_materialization_commitment is None
                    else source_materialization_commitment
                ),
                current_run=CallSubjectHead(
                    subject_id=run.run_id,
                    revision=Present(head=run.head, fingerprint=run.digest()),
                ),
                current_run_bytes=run.canonical_bytes(),
                response_id=accepted_open.accepted.selected_seal.response_seal.subject_id,
                original_call_id=accepted_open.accepted.original_call_id,
                initialized=initialized,
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
                selected_outcome_record_id=accepted_open.outcome_record_id,
                selected_outcome_bytes=accepted_open.outcome.canonical_bytes(),
                selected_outcome_fingerprint=hashlib.sha256(
                    accepted_open.outcome.canonical_bytes()
                ).hexdigest(),
                selected_decision=CallSubjectHead(
                    subject_id=selected_decision.subject_id,
                    revision=Present(
                        head=selected_decision.head,
                        fingerprint=selected_decision.fingerprint,
                    ),
                ),
                outcome_commit_sequence=accepted_open.commit_sequence,
                publication_ordinal=accepted_open.publication_ordinal,
                batch_record_ids=accepted_open.batch_record_ids,
                cut_fingerprint="0" * 64,
            )
            cut = cut.model_copy(update={"cut_fingerprint": _fingerprint(cut)})
            with read_connection(runtime._database) as connection:
                commitment = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
            if (
                commitment != accepted_open.materialization_commitment
                or runtime._commitment_journal.load(runtime._tenant_id) != anchored
                or runtime._database_identity != accepted_open.database_identity
            ):
                return None
            return cut
    except (OSError, RuntimeError, TypeError, ValueError, KeyError):
        return None


__all__ = ["build_accepted_open_recovery_cut"]
