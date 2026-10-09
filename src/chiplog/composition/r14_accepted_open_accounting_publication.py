"""Durable effects-specific accounting for one selected R16 OPEN obligation."""

from __future__ import annotations

import base64
import json
from dataclasses import replace
from typing import Literal

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
    PrepareAcceptedOpenAccounting,
    PreparedAcceptedOpenAccounting,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.r14_accepted_open_accounting_records import (
    ACCEPTED_OPEN_ACCOUNTING_OPERATION,
    RetainedAcceptedOpenAccountingPublication,
    accepted_open_accounting_command,
    retained_accepted_open_accounting,
)
from chiplog.composition.r14_call_preview import execution_run_head
from chiplog.composition.r14_loop_history import read_execution_recovery_history
from chiplog.composition.r14_native_accepted_open_cut import build_accepted_open_recovery_cut
from chiplog.composition.r14_native_preparation_bindings import (
    bind_native_execution_preparation_v2,
)
from chiplog.composition.r14_native_selected_accounting_source import (
    R14NativeSelectedAccountingSource,
)
from chiplog.composition.r14_original_prepare_source import R14OriginalNativePrepareSource
from chiplog.composition.r14_recovery_publication import (
    _exchange,
    issue_execution_recovery_actor_v1,
)


def _selected(runtime, identity: str) -> dict[str, object] | None:
    runtime._pending()
    entries = [
        json.loads(raw)
        for _, _, raw in runtime._loop_decisions().entries()
        if (entry := json.loads(raw)).get("kind") == "DECIDED"
        and entry.get("operation_id") == identity
    ]
    if len(entries) > 1:
        raise LoopRejected("ambiguous accepted/open accounting selection")
    if not entries:
        return None
    entry = entries[0]
    if entry.get("operation_kind") != ACCEPTED_OPEN_ACCOUNTING_OPERATION:
        raise LoopRejected("accepted/open accounting identity belongs to another operation")
    return entry


def _current_cut(
    runtime,
    *,
    selected_only: bool = False,
    source_materialization_commitment: str | None = None,
    source_tenant_commit_sequence: int | None = None,
):
    history = read_execution_recovery_history(runtime, selected_only=selected_only)
    snapshot, _, _, executions, _, _ = history
    if len(executions) != 1 or not snapshot.records:
        raise LoopRejected("accepted/open source requires one selected native execution")
    seal = executions[0].proposal.fan_out.response_seal
    seal_digest = seal.digest()
    seal_ref = CallSubjectHead(
        subject_id=seal.response_seal_id,
        revision=Present(head="record:" + seal_digest, fingerprint=seal_digest),
    )
    projection = R14OriginalNativePrepareSource(runtime).collect(seal_ref)
    if projection is None:
        raise LoopRejected("accepted/open original Prepare source unavailable")
    source = R14NativeSelectedAccountingSource(
        runtime,
        projection,
        bind_native_execution_preparation_v2(projection),
    )
    facts = source.collect_one_accepted_open_outcome(
        execution_run_head(snapshot.records[-1]), selected_only=selected_only
    )
    if facts is None:
        return None
    cut = build_accepted_open_recovery_cut(
        runtime,
        facts,
        source_materialization_commitment=source_materialization_commitment,
        source_tenant_commit_sequence=source_tenant_commit_sequence,
    )
    if cut is None:
        return None
    return facts, cut


async def _finish(runtime, entry: dict[str, object]):
    from chiplog.composition.r14_loop_history import read_selected_accepted_open_accounting

    evidence = retained_accepted_open_accounting(entry)
    command = accepted_open_accounting_command(evidence)
    if runtime._publication(entry) != command:
        raise LoopRejected("selected accepted/open accounting differs from retained command")
    await runtime._recover_exact(
        command,
        str(entry["predecessor"]),
        str(entry["resulting"]),
        is_materialized=lambda: runtime._loop_decision_materialized(
            command.idempotency_key, entry
        ),
    )
    runtime._finish_decision(command.idempotency_key, expected=entry)
    selected = read_selected_accepted_open_accounting(runtime)
    if selected != evidence.result.accounting:
        raise LoopRejected("selected accepted/open accounting was not observed")
    return selected


async def publish_accepted_open_accounting(runtime, peer: str = "hermetic-ingress"):
    """Select one account-only record for the exact current accepted/open obligation."""
    observed = await runtime._execution_actor(peer)
    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        runtime._pending()
        selected = [
            json.loads(raw)
            for _, _, raw in runtime._loop_decisions().entries()
            if (entry := json.loads(raw)).get("kind") == "DECIDED"
            and entry.get("operation_kind") == ACCEPTED_OPEN_ACCOUNTING_OPERATION
        ]
        if len(selected) > 1:
            raise LoopRejected("ambiguous accepted/open accounting history")
        prior = selected[0] if selected else None
    if prior is not None:
        return await _finish(runtime, prior)

    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        current = _current_cut(runtime)
        if current is None:
            raise LoopRejected("exact selected accepted/open outcome unavailable; HOLD")
        facts, cut = current
        identity = "accepted-open-accounting:" + cut.cut_fingerprint
        if _selected(runtime, identity) is not None:
            raise LoopRejected("accepted/open selection changed during source read")
        predecessor = runtime._commitment_journal.load(runtime._tenant_id)
        worker = runtime.current_worker()
        session = runtime._supervisor.runtime().session("agent_loop")
        if predecessor != cut.materialization_commitment:
            raise LoopRejected("accepted/open cut predecessor differs")

    refreshed, actor_budget = await issue_execution_recovery_actor_v1(runtime, peer)
    with runtime._authority_gate().hold():
        if not runtime._same_execution_actor_identity(observed, refreshed):
            raise LoopRejected("accepted/open authenticated actor changed before owner IPC")
        observed = refreshed

    request = PrepareAcceptedOpenAccounting(command_id=identity, cut=cut)
    result, owner_call, owner_reply = await _exchange(
        runtime,
        request,
        "agent_loop.prepare_accepted_open_accounting.v1",
        PreparedAcceptedOpenAccounting,
    )
    evidence = RetainedAcceptedOpenAccountingPublication(
        request=request,
        result=result,
        owner_call=owner_call,
        owner_reply=owner_reply,
        actor_budget_v1=actor_budget,
    )
    command = accepted_open_accounting_command(evidence)

    def guard() -> Literal["STALE"] | None:
        with runtime._authority_gate().hold():
            try:
                runtime._check_execution_actor(observed)
                current_facts, current_cut = _current_cut(runtime)
                if (
                    current_facts != facts
                    or current_cut != cut
                    or runtime.current_worker() != worker
                    or runtime._supervisor.runtime().session("agent_loop") != session
                    or runtime._commitment_journal.load(runtime._tenant_id) != predecessor
                ):
                    return "STALE"
            except (LoopRejected, ValueError, TypeError, RuntimeError):
                return "STALE"
        return None

    def decide(resulting: str) -> None:
        with runtime._authority_gate().hold():
            runtime._require_no_pending()
            runtime._append_decision(
                {
                    "version": 1,
                    "kind": "DECIDED",
                    "operation_id": identity,
                    "operation_kind": ACCEPTED_OPEN_ACCOUNTING_OPERATION,
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
                    "accepted_open_accounting_evidence": evidence.canonical_bytes().decode(),
                }
            )

    result = await runtime._appender.submit(
        replace(command, admission_guard=guard, decision_guard=decide)
    )
    if result.disposition not in ("COMMITTED", "REPLAY"):
        raise LoopRejected("accepted/open accounting publication " + result.disposition)
    with runtime._authority_gate().hold():
        entry = _selected(runtime, identity)
        if entry is None or retained_accepted_open_accounting(entry) != evidence:
            raise LoopRejected("selected accepted/open accounting evidence differs")
    return await _finish(runtime, entry)


def validate_selected_accepted_open_accounting(runtime) -> None:
    """Authenticate selected/open source bytes before startup can replay the writer."""
    from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
    from chiplog.platform.workspace_snapshot import read_connection

    runtime._pending()
    selected: list[dict[str, object]] = []
    claimed = False
    for _, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        if entry.get("kind") != "DECIDED":
            continue
        command = runtime._publication(entry)
        marker = (
            command.operation_kind == ACCEPTED_OPEN_ACCOUNTING_OPERATION
            or "accepted_open_accounting_evidence" in entry
            or any(
                record.schema_id
                == "chiplog.execution.accepted-open-accounting.v1"
                for record in command.records
            )
        )
        if not marker:
            continue
        claimed = True
        evidence = retained_accepted_open_accounting(entry)
        expected = accepted_open_accounting_command(evidence)
        if (
            command != expected
            or entry.get("operation_kind") != ACCEPTED_OPEN_ACCOUNTING_OPERATION
            or entry.get("operation_id") != command.idempotency_key
            or entry.get("predecessor") != evidence.request.cut.materialization_commitment
            or entry.get("expected_head") != command.expected_head
            or entry.get("fingerprint") != command.request_fingerprint
            or len(command.records) != 1
        ):
            raise LoopRejected("selected accepted/open accounting envelope differs")
        with read_connection(runtime._database) as connection:
            actual = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
        anchored = runtime._commitment_journal.load(runtime._tenant_id)
        if actual not in (entry.get("predecessor"), entry.get("resulting")) or anchored not in (
            entry.get("predecessor"),
            entry.get("resulting"),
        ):
            raise LoopRejected("accepted/open publication is not an exact predecessor/result")
        current = _current_cut(
            runtime,
            selected_only=True,
            source_materialization_commitment=str(entry["predecessor"]),
            source_tenant_commit_sequence=command.expected_head,
        )
        if current is None:
            raise LoopRejected("accepted/open current cut unavailable")
        facts, cut = current
        if facts.outcome.canonical_bytes() != evidence.request.cut.selected_outcome_bytes or (
            cut != evidence.request.cut
        ):
            raise LoopRejected("accepted/open retained cut differs from exact selected history")
        selected.append(entry)
    if claimed and len(selected) != 1:
        raise LoopRejected("ambiguous accepted/open accounting selection")


async def hold_if_accepted_open(runtime, peer: str) -> bool:
    """Durably account and HOLD before any successor-turn owner preparation."""
    current = _current_cut(runtime)
    if current is None:
        return False
    await publish_accepted_open_accounting(runtime, peer)
    return True


__all__ = ["publish_accepted_open_accounting"]
