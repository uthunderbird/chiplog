"""Select and recover the first direct terminal for one closed R16 result."""

from __future__ import annotations

import base64
import json
from dataclasses import replace
from typing import TYPE_CHECKING, Literal, cast

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.direct_terminal_contracts import (
    AcceptedDirectTerminalCutV1,
    PrepareAcceptedDirectTerminalV1,
    PreparedAcceptedDirectTerminalV1,
)
from chiplog.capabilities.agent_loop.direct_terminal_wire_contracts import REQUEST_SCHEMA
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.r14_accepted_direct_terminal_records import (
    ACCEPTED_DIRECT_TERMINAL_OPERATION,
    RetainedAcceptedDirectTerminalPublication,
    accepted_direct_terminal_command,
    retained_accepted_direct_terminal,
)
from chiplog.composition.r14_call_preview import execution_run_head
from chiplog.composition.r14_loop_history import (
    ExecutionRecoveryHistory,
    read_execution_recovery_history,
)
from chiplog.composition.r14_native_direct_terminal_cut import (
    build_accepted_direct_terminal_cut,
)
from chiplog.composition.r14_native_preparation_bindings import (
    bind_native_execution_preparation_v2,
)
from chiplog.composition.r14_native_selected_accounting_source import (
    R14NativeAcceptedOpenOutcome,
    R14NativeSelectedAccountingSource,
)
from chiplog.composition.r14_original_prepare_source import R14OriginalNativePrepareSource
from chiplog.composition.r14_recovery_publication import (
    _exchange,
    issue_execution_recovery_actor_v1,
)
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.workspace_snapshot import read_connection

if TYPE_CHECKING:
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime


def _selected(runtime: CommonCliExecutionRuntime, identity: str) -> dict[str, object] | None:
    runtime._pending()
    entries = [
        cast(dict[str, object], json.loads(raw))
        for _, _, raw in runtime._loop_decisions().entries()
        if (entry := json.loads(raw)).get("kind") == "DECIDED"
        and entry.get("operation_id") == identity
    ]
    if len(entries) > 1:
        raise LoopRejected("ambiguous accepted/direct terminal selection")
    if not entries:
        return None
    entry = entries[0]
    if entry.get("operation_kind") != ACCEPTED_DIRECT_TERMINAL_OPERATION:
        raise LoopRejected("accepted/direct terminal identity belongs to another operation")
    return entry


def _current_cut(
    runtime: CommonCliExecutionRuntime,
    *,
    selected_only: bool = False,
    expected_source_cut: AcceptedDirectTerminalCutV1 | None = None,
    source_materialization_commitment: str | None = None,
    source_tenant_commit_sequence: int | None = None,
    authenticated_history: ExecutionRecoveryHistory | None = None,
) -> tuple[R14NativeAcceptedOpenOutcome, AcceptedDirectTerminalCutV1] | None:
    history = authenticated_history or read_execution_recovery_history(
        runtime, selected_only=selected_only
    )
    snapshot, _, _, executions, _, _ = history
    if len(executions) != 1 or not snapshot.records:
        raise LoopRejected("accepted/direct terminal requires one selected native execution")
    seal = executions[0].proposal.fan_out.response_seal
    seal_digest = seal.digest()
    seal_ref = CallSubjectHead(
        subject_id=seal.response_seal_id,
        revision=Present(head="record:" + seal_digest, fingerprint=seal_digest),
    )
    projection = R14OriginalNativePrepareSource(runtime).collect(seal_ref)
    if projection is None:
        raise LoopRejected("accepted/direct terminal original Prepare source unavailable")
    source = R14NativeSelectedAccountingSource(
        runtime,
        projection,
        bind_native_execution_preparation_v2(projection),
    )
    source_run = (
        execution_run_head(snapshot.records[-1])
        if expected_source_cut is None
        else expected_source_cut.current_run
    )
    closed = source.collect_one_accepted_closed_outcome(
        source_run,
        selected_only=selected_only,
    )
    if closed is None:
        return None
    cut = build_accepted_direct_terminal_cut(
        runtime,
        closed,
        source_run=None if expected_source_cut is None else expected_source_cut.current_run,
        source_materialization_commitment=source_materialization_commitment,
        source_tenant_commit_sequence=source_tenant_commit_sequence,
    )
    if cut is None:
        return None
    return closed, cut


async def _finish(
    runtime: CommonCliExecutionRuntime, entry: dict[str, object]
) -> PreparedAcceptedDirectTerminalV1:
    from chiplog.composition.r14_loop_history import read_selected_accepted_direct_terminal

    evidence = retained_accepted_direct_terminal(entry)
    command = accepted_direct_terminal_command(evidence)
    if runtime._publication(entry) != command:
        raise LoopRejected("selected accepted/direct terminal differs from retained command")
    await runtime._recover_exact(
        command,
        str(entry["predecessor"]),
        str(entry["resulting"]),
        is_materialized=lambda: runtime._loop_decision_materialized(command.idempotency_key, entry),
    )
    runtime._finish_decision(command.idempotency_key, expected=entry)
    selected = read_selected_accepted_direct_terminal(runtime)
    if selected != evidence.result:
        raise LoopRejected("selected accepted/direct terminal result was not observed")
    return cast(PreparedAcceptedDirectTerminalV1, selected)


async def publish_accepted_direct_terminal(
    runtime: CommonCliExecutionRuntime, peer: str = "hermetic-ingress"
) -> PreparedAcceptedDirectTerminalV1:
    """Select one atomic accounting + terminal + result batch for the exact closed source."""
    observed = await runtime._execution_actor(peer)
    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        runtime._pending()
        prior = [
            json.loads(raw)
            for _, _, raw in runtime._loop_decisions().entries()
            if (entry := json.loads(raw)).get("kind") == "DECIDED"
            and (
                entry.get("operation_kind") == ACCEPTED_DIRECT_TERMINAL_OPERATION
                or "accepted_direct_terminal_evidence" in entry
            )
        ]
        if len(prior) > 1:
            raise LoopRejected("ambiguous accepted/direct terminal history")
    if prior:
        return await _finish(runtime, prior[0])

    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        current = _current_cut(runtime)
        if current is None:
            raise LoopRejected("exact selected closed R16 outcome unavailable; HOLD")
        facts, cut = current
        identity = "accepted-direct-terminal:" + cut.cut_fingerprint
        if _selected(runtime, identity) is not None:
            raise LoopRejected("accepted/direct terminal selection changed during source read")
        predecessor = runtime._commitment_journal.load(runtime._tenant_id)
        worker = runtime.current_worker()
        session = runtime._supervisor.runtime().session("agent_loop")
        if predecessor != cut.materialization_commitment:
            raise LoopRejected("accepted/direct terminal predecessor differs")

    refreshed, actor_budget = await issue_execution_recovery_actor_v1(  # type: ignore[no-untyped-call]
        runtime, peer
    )
    with runtime._authority_gate().hold():
        if not runtime._same_execution_actor_identity(observed, refreshed):
            raise LoopRejected("accepted/direct terminal actor changed before owner IPC")
        observed = refreshed

    request = PrepareAcceptedDirectTerminalV1(command_id=identity, cut=cut)
    result, owner_call, owner_reply = await _exchange(  # type: ignore[no-untyped-call]
        runtime,
        request,
        "agent_loop.prepare_accepted_direct_terminal.v1",
        PreparedAcceptedDirectTerminalV1,
        request_schema=REQUEST_SCHEMA,
    )
    evidence = RetainedAcceptedDirectTerminalPublication(
        request=request,
        result=result,
        owner_call=owner_call,
        owner_reply=owner_reply,
        actor_budget_v1=actor_budget,
    )
    command = accepted_direct_terminal_command(evidence)

    def guard() -> Literal["STALE"] | None:
        with runtime._authority_gate().hold():
            try:
                runtime._check_execution_actor(observed)
                current = _current_cut(runtime)
                if current is None:
                    return "STALE"
                current_facts, current_cut = current
                if (
                    current_facts != facts
                    or current_cut != cut
                    or runtime.current_worker() != worker
                    or runtime._supervisor.runtime().session("agent_loop") != session
                    or runtime._commitment_journal.load(runtime._tenant_id) != predecessor
                    or _selected_competing_open(runtime, cut.original_call_id)
                ):
                    return "STALE"
            except LoopRejected, ValueError, TypeError, RuntimeError:
                return "STALE"
        return None

    def decide(resulting: str) -> None:
        with runtime._authority_gate().hold():
            runtime._require_no_pending()
            if _selected_competing_open(runtime, cut.original_call_id):
                raise LoopRejected(
                    "accepted/direct terminal conflicts with selected RECOVERY_REQUIRED"
                )
            runtime._append_decision(
                {
                    "version": 1,
                    "kind": "DECIDED",
                    "operation_id": identity,
                    "operation_kind": ACCEPTED_DIRECT_TERMINAL_OPERATION,
                    "expected_head": command.expected_head,
                    "fingerprint": command.request_fingerprint,
                    "predecessor": predecessor,
                    "resulting": resulting,
                    "records": [
                        {
                            "record_id": record.record_id,
                            "owner": record.owner,
                            "schema": record.schema_id,
                            "payload": base64.b64encode(record.canonical_bytes).decode(),
                            "digest": record.fingerprint,
                        }
                        for record in command.records
                    ],
                    "accepted_direct_terminal_evidence": evidence.canonical_bytes().decode(),
                }
            )

    submitted = await runtime._appender.submit(
        replace(command, admission_guard=guard, decision_guard=decide)
    )
    if submitted.disposition not in ("COMMITTED", "REPLAY"):
        raise LoopRejected("accepted/direct terminal publication " + submitted.disposition)
    with runtime._authority_gate().hold():
        entry = _selected(runtime, identity)
        if entry is None or retained_accepted_direct_terminal(entry) != evidence:
            raise LoopRejected("selected accepted/direct terminal evidence differs")
    return await _finish(runtime, entry)


def _selected_competing_open(runtime: CommonCliExecutionRuntime, original_call_id: str) -> bool:
    from chiplog.composition.r14_accepted_open_accounting_records import (
        ACCEPTED_OPEN_ACCOUNTING_OPERATION,
        ACCEPTED_OPEN_RECOVERY_OPERATION,
        retained_accepted_open_accounting,
        retained_accepted_open_recovery,
    )

    for _, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        if entry.get("kind") != "DECIDED":
            continue
        operation = entry.get("operation_kind")
        if operation == ACCEPTED_OPEN_ACCOUNTING_OPERATION:
            if (
                retained_accepted_open_accounting(entry).result.accounting.original_call_id
                == original_call_id
            ):
                return True
        elif (
            operation == ACCEPTED_OPEN_RECOVERY_OPERATION
            and retained_accepted_open_recovery(entry).result.accounting.original_call_id
            == original_call_id
        ):
            return True
    return False


def selected_direct_terminal_for_call(
    runtime: CommonCliExecutionRuntime, original_call_id: str
) -> bool:
    """Detect an already selected direct terminal during competing OPEN guards."""
    for _, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        if entry.get("kind") != "DECIDED":
            continue
        if (
            entry.get("operation_kind") == ACCEPTED_DIRECT_TERMINAL_OPERATION
            or "accepted_direct_terminal_evidence" in entry
        ) and retained_accepted_direct_terminal(
            entry
        ).result.accounting.original_call_id == original_call_id:
            return True
    return False


def validate_selected_accepted_direct_terminal(
    runtime: CommonCliExecutionRuntime,
    *,
    authenticated_history: ExecutionRecoveryHistory | None = None,
) -> tuple[R14NativeAcceptedOpenOutcome, AcceptedDirectTerminalCutV1] | None:
    """Verify the selected batch and return its authenticated current source cut."""
    runtime._pending()
    claimed = False
    selected: list[dict[str, object]] = []
    current_cut = None
    for _, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        if entry.get("kind") != "DECIDED":
            continue
        command = runtime._publication(entry)
        marker = (
            command.operation_kind == ACCEPTED_DIRECT_TERMINAL_OPERATION
            or "accepted_direct_terminal_evidence" in entry
            or any(
                record.schema_id.startswith("chiplog.execution.accepted-direct-terminal-")
                for record in command.records
            )
        )
        if not marker:
            continue
        claimed = True
        evidence = retained_accepted_direct_terminal(entry)
        expected = accepted_direct_terminal_command(evidence)
        if (
            command != expected
            or entry.get("operation_kind") != ACCEPTED_DIRECT_TERMINAL_OPERATION
            or entry.get("operation_id") != command.idempotency_key
            or entry.get("predecessor") != evidence.request.cut.materialization_commitment
            or entry.get("expected_head") != command.expected_head
            or entry.get("fingerprint") != command.request_fingerprint
            or len(command.records) != 3
        ):
            raise LoopRejected("selected accepted/direct terminal envelope differs")
        with runtime._authority_gate().hold():
            runtime._check_database_identity()
            with read_connection(runtime._database) as connection:
                actual = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
            anchored = runtime._commitment_journal.load(runtime._tenant_id)
            state, physical, physical_anchor = runtime._selected_physical_state(command)
            if (
                physical != actual
                or physical_anchor != anchored
                or actual != anchored
                or state not in ("ABSENT", "COMPLETE")
                or (state == "ABSENT" and actual != entry.get("predecessor"))
            ):
                raise LoopRejected("accepted/direct terminal exact batch is absent or inconsistent")
        current = _current_cut(
            runtime,
            selected_only=True,
            expected_source_cut=evidence.request.cut,
            source_materialization_commitment=evidence.request.cut.materialization_commitment,
            source_tenant_commit_sequence=evidence.request.cut.tenant_commit_sequence,
            authenticated_history=authenticated_history,
        )
        if current is None or current[1] != evidence.request.cut:
            raise LoopRejected("accepted/direct terminal retained source cut differs")
        current_cut = current
        if _selected_competing_open(runtime, evidence.result.accounting.original_call_id):
            raise LoopRejected("accepted/direct terminal conflicts with selected RECOVERY_REQUIRED")
        selected.append(entry)
    if claimed and len(selected) != 1:
        raise LoopRejected("ambiguous accepted/direct terminal selection")
    return current_cut


__all__ = [
    "publish_accepted_direct_terminal",
    "selected_direct_terminal_for_call",
    "validate_selected_accepted_direct_terminal",
]
