"""Build the R14 sealed-call frontier from selected direct-terminal records."""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.direct_terminal_contracts import (
    AcceptedDirectTerminalCutV1,
    PreparedAcceptedDirectTerminalV1,
)
from chiplog.capabilities.agent_loop.recovery_contracts import NotApplicable, Present
from chiplog.capabilities.agent_loop.recovery_domain import SealedAccounting, sealed_call_accounting
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    ConsequentialAcceptedCall,
    DefiniteFailureResult,
    SuccessResult,
    TerminalCallFrontier,
)

if TYPE_CHECKING:
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime

_MAPPING_ID = "R14_DIRECT_EFFECT_TERMINAL_V1"
_MAPPING_VERSION = "r14-direct-effect-terminal.v1"


def _record_ref(record_id: str, canonical_bytes: bytes) -> CallSubjectHead:
    fingerprint = hashlib.sha256(canonical_bytes).hexdigest()
    return CallSubjectHead(
        subject_id=record_id,
        revision=Present(head="record:" + fingerprint, fingerprint=fingerprint),
    )


def accepted_direct_terminal_sealed_accounting(
    cut: AcceptedDirectTerminalCutV1,
    prepared: PreparedAcceptedDirectTerminalV1,
    acceptance: ConsequentialAcceptedCall,
    ordered_initialized_call_ids: tuple[str, ...],
    sealed_manifest_fingerprint: str,
) -> SealedAccounting:
    """Join one exact selected terminal to its complete one-call seal manifest."""
    if ordered_initialized_call_ids != (cut.original_call_id,):
        raise LoopRejected("selected direct terminal does not join complete one-call seal")
    call = accepted_direct_terminal_call_frontier(
        cut,
        prepared,
        acceptance,
        ordered_initialized_call_ids,
        sealed_manifest_fingerprint,
    )
    return sealed_call_accounting(
        cut.response_id,
        sealed_manifest_fingerprint,
        ordered_initialized_call_ids,
        (call,),
    )


def accepted_direct_terminal_call_frontier(
    cut: AcceptedDirectTerminalCutV1,
    prepared: PreparedAcceptedDirectTerminalV1,
    acceptance: ConsequentialAcceptedCall,
    ordered_initialized_call_ids: tuple[str, ...],
    sealed_manifest_fingerprint: str,
) -> TerminalCallFrontier:
    """Return one member frontier; it does not assert that the whole seal is accounted."""
    accounting = prepared.accounting
    terminal = prepared.terminal
    result = prepared.result
    terminal_ref = _record_ref(terminal.terminal_id, terminal.canonical_bytes())
    result_ref = _record_ref(result.result_id, result.canonical_bytes())
    manifest = (
        cut.accepted.revision,
        cut.execution_intent.revision,
        cut.external_effect_intent.revision,
    )
    if (
        cut.original_call_id not in ordered_initialized_call_ids
        or len(set(ordered_initialized_call_ids)) != len(ordered_initialized_call_ids)
        or not re.fullmatch(r"[0-9a-f]{64}", sealed_manifest_fingerprint)
        or cut.initialized.subject_id != cut.original_call_id
        or acceptance.initialized != cut.initialized.revision
        or acceptance.accepted != cut.accepted.revision
        or acceptance.execution_intent != cut.execution_intent.revision
        or acceptance.external_effect_intent != cut.external_effect_intent.revision
        or acceptance.complete_acceptance_manifest != manifest
        or accounting.schema_id != "chiplog.execution.accepted-direct-terminal-accounting.v1"
        or terminal.schema_id != "chiplog.execution.accepted-direct-terminal-disposition.v1"
        or result.schema_id != "chiplog.execution.accepted-direct-terminal-result.v1"
        or accounting.accounting_id != "accepted-direct-terminal-accounting:" + cut.cut_fingerprint
        or terminal.terminal_id != "accepted-direct-terminal-record:" + cut.cut_fingerprint
        or result.result_id != "accepted-direct-terminal-result:" + cut.cut_fingerprint
        or accounting.source_cut_fingerprint != cut.cut_fingerprint
        or terminal.source_cut_fingerprint != cut.cut_fingerprint
        or result.source_cut_fingerprint != cut.cut_fingerprint
        or accounting.original_call_id != cut.original_call_id
        or terminal.original_call_id != cut.original_call_id
        or result.original_call_id != cut.original_call_id
        or accounting.response_id != cut.response_id
        or terminal.response_id != cut.response_id
        or accounting.accepted != cut.accepted
        or terminal.accepted != cut.accepted
        or accounting.execution_intent != cut.execution_intent
        or accounting.external_effect_intent != cut.external_effect_intent
        or terminal.external_effect_intent != cut.external_effect_intent
        or result.terminal != terminal_ref
        or accounting.terminal != terminal_ref
        or accounting.result != result_ref
        or terminal.mapping_id != _MAPPING_ID
        or terminal.mapping_version != _MAPPING_VERSION
        or result.classification.mapping_id != _MAPPING_ID
        or result.classification.mapping_version != _MAPPING_VERSION
        or terminal.kind != result.classification.disposition
        or result.classification.semantic_class
        != (
            "R14_EFFECT_CONFIRMED"
            if terminal.kind == "SUCCEEDED"
            else "R14_EFFECT_FAILED_NO_EFFECT"
        )
        or terminal.selected_effect_terminal != result.classification.terminal
        or result.classification.semantic_binding != cut.semantic_binding
        or accounting.selected_outcome_record_id != cut.selected_outcome_record_id
        or accounting.selected_outcome_fingerprint != cut.selected_outcome_fingerprint
        or accounting.selected_decision != cut.selected_decision
        or accounting.outcome_commit_sequence != cut.outcome_commit_sequence
        or accounting.publication_ordinal != cut.publication_ordinal
        or accounting.batch_record_ids != cut.batch_record_ids
    ):
        raise LoopRejected("selected direct terminal does not join exact one-call source")

    disposition = (
        SuccessResult(
            terminal=terminal_ref.revision,
            result=result_ref.revision,
            recovered_outcome=NotApplicable(),
        )
        if terminal.kind == "SUCCEEDED"
        else DefiniteFailureResult(
            terminal=terminal_ref.revision,
            result=result_ref.revision,
            recovered_outcome=NotApplicable(),
        )
    )
    if terminal.kind not in ("SUCCEEDED", "FAILED_DEFINITE"):
        raise LoopRejected("selected direct terminal mapping is not an R14 terminal result")
    call = TerminalCallFrontier(
        original_call_id=cut.original_call_id,
        response_id=cut.response_id,
        acceptance=acceptance,
        disposition=disposition,
    )
    return call


def selected_accepted_direct_terminal_call_frontier(
    runtime: CommonCliExecutionRuntime,
    *,
    response_id: str,
    original_call_id: str,
    ordered_initialized_call_ids: tuple[str, ...],
    sealed_manifest_fingerprint: str,
    acceptance: ConsequentialAcceptedCall,
) -> TerminalCallFrontier | None:
    """Read one exact, physically materialized direct-terminal member frontier."""
    from chiplog.composition.r14_accepted_direct_terminal_publication import (
        validate_selected_accepted_direct_terminal,
    )
    from chiplog.composition.r14_accepted_direct_terminal_records import (
        ACCEPTED_DIRECT_TERMINAL_OPERATION,
        accepted_direct_terminal_command,
        retained_accepted_direct_terminal,
    )
    from chiplog.composition.r14_loop_history import read_selected_accepted_direct_terminal
    from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
    from chiplog.platform.workspace_snapshot import read_connection

    try:
        current = validate_selected_accepted_direct_terminal(runtime)
        if current is None:
            return None
        closed, cut = current
        if (
            cut.response_id != response_id
            or cut.original_call_id != original_call_id
            or closed.accepted.original_call_id != original_call_id
            or closed.accepted.selected_seal.response_seal.subject_id != response_id
            or closed.latest_outcome.snapshot.state not in ("CONFIRMED", "FAILED_NO_EFFECT")
            or closed.latest_outcome.snapshot.obligation.state != "CLOSED"
        ):
            return None
        entries = [
            json.loads(raw)
            for _, _, raw in runtime._loop_decisions().entries()
            if (entry := json.loads(raw)).get("kind") == "DECIDED"
            and entry.get("operation_kind") == ACCEPTED_DIRECT_TERMINAL_OPERATION
        ]
        if len(entries) != 1:
            return None
        evidence = retained_accepted_direct_terminal(entries[0])
        command = accepted_direct_terminal_command(evidence)
        if (
            runtime._publication(entries[0]) != command
            or evidence.request.cut != cut
            or evidence.result.accounting.original_call_id != original_call_id
            or evidence.result.accounting.response_id != response_id
            or read_selected_accepted_direct_terminal(runtime) != evidence.result
        ):
            return None
        runtime._check_database_identity()
        with read_connection(runtime._database) as connection:
            current_snapshot = capture_authority_snapshot_commitment(
                connection, runtime._tenant_id
            )
        journal_anchor = runtime._commitment_journal.load(runtime._tenant_id)
        state, selected_snapshot, selected_anchor = runtime._selected_physical_state(command)
        require_complete_direct_terminal_publication(
            state,
            current_snapshot,
            journal_anchor,
            selected_snapshot,
            selected_anchor,
        )
        return accepted_direct_terminal_call_frontier(
            cut,
            evidence.result,
            acceptance,
            ordered_initialized_call_ids,
            sealed_manifest_fingerprint,
        )
    except (OSError, RuntimeError, TypeError, ValueError, KeyError, AttributeError):
        return None


def require_complete_direct_terminal_publication(
    state: str,
    current_snapshot: str,
    journal_anchor: str | None,
    selected_snapshot: str,
    selected_anchor: str | None,
) -> None:
    """Require completed physical membership and a stable three-way snapshot cut."""
    if (
        state != "COMPLETE"
        or selected_snapshot != current_snapshot
        or selected_anchor != journal_anchor
        or current_snapshot != journal_anchor
    ):
        raise LoopRejected("selected direct terminal batch is not completely materialized")


__all__ = [
    "accepted_direct_terminal_call_frontier",
    "accepted_direct_terminal_sealed_accounting",
    "require_complete_direct_terminal_publication",
    "selected_accepted_direct_terminal_call_frontier",
]
