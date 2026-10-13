"""Explicit one-call next Turn through selected owner result and sole writer."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Literal

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord, ExecutionTurn
from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
    PreparedNextExecutionTurn,
    PrepareNextExecutionTurn,
    SelectedContinuationPublication,
    SelectedContinuationResultStamp,
)
from chiplog.capabilities.agent_loop.execution_recovery_observations import (
    ContinuationReadyRecord,
    ExecutionRecoveryCut,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    NonSchedulerFence,
    NotApplicable,
    Present,
)
from chiplog.capabilities.agent_loop.recovery_wire_contracts import (
    EXECUTION_RECOVERY_REQUEST_SCHEMA,
)
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess
from chiplog.platform.r7_trust_durability import FrozenTrustObservation

from .r14_loop_history import read_execution_history
from .r14_next_turn_records import (
    NEXT_TURN_PUBLICATION,
    OWNER_OPERATION,
    RetainedNextTurnPublication,
    next_turn_command,
    retained_next_turn,
    validate_selected_next_turn,
)
from .r14_recovery_publication import (
    OPERATION as ACCOUNTING_OPERATION,
)

if TYPE_CHECKING:
    from .common_cli_execution_runtime import CommonCliExecutionRuntime
from .r14_recovery_publication import issue_execution_recovery_actor_v1, source
from .r14_recovery_publication import (
    validate_selected as validate_accounting_selected,
)


def _turn_ref(turn: ExecutionTurn) -> CallSubjectHead:
    raw = turn.canonical_bytes()
    return CallSubjectHead(
        subject_id=turn.turn_id,
        revision=Present(head=turn.head, fingerprint=hashlib.sha256(raw).hexdigest()),
    )


async def _exchange_next_turn(
    runtime: CommonCliExecutionRuntime, request: PrepareNextExecutionTurn
) -> tuple[PreparedNextExecutionTurn, PublicPortCall, PublicPortSuccess]:
    engine = runtime._supervisor.runtime()
    callee = engine.session("agent_loop")
    sent = PublicPortCall(
        operation_id=OWNER_OPERATION,
        request_id="execution-next-turn:" + secrets.token_hex(16),
        caller=BrokerSession(
            tenant_id=runtime._tenant_id,
            broker_epoch=callee.broker_epoch,
            generation_id=callee.generation_id,
            owner_id="broker",
            session_id=f"broker:{callee.generation_id}",
        ),
        callee=callee,
        schema_id=EXECUTION_RECOVERY_REQUEST_SCHEMA,
        canonical_payload=request.canonical_bytes(),
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            policy_version=1,
            absolute_deadline_ns=time.monotonic_ns() + 30_000_000_000,
        ),
    )
    reply = await engine.call(sent)
    if not isinstance(reply, PublicPortSuccess):
        raise LoopRejected("next Turn owner IPC unavailable: " + str(reply))
    try:
        result = PreparedNextExecutionTurn.model_validate_json(reply.canonical_payload)
    except ValueError as error:
        raise LoopRejected("next Turn owner result is invalid") from error
    return result, sent, reply


def _selected_pair_held(
    runtime: CommonCliExecutionRuntime, cut: ExecutionRecoveryCut
) -> tuple[SelectedContinuationPublication, ContinuationReadyRecord]:
    selected = []
    for decision_id, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        if entry.get("kind") == "DECIDED" and entry.get("operation_kind") == ACCOUNTING_OPERATION:
            selected.append((str(decision_id), raw, entry))
    current = []
    for decision_id, raw, entry in selected:
        evidence = validate_accounting_selected(  # type: ignore[no-untyped-call]
            runtime, entry, runtime._publication(entry)
        )
        retained_cut = evidence.accounting_request.cut
        if (
            int(entry.get("expected_head", -1)) + 1 == cut.tenant_commit_sequence
            and entry.get("resulting") == cut.materialization_commitment
            and retained_cut.tenant_id == cut.tenant_id
            and retained_cut.database_id == cut.database_id
        ):
            current.append((decision_id, raw, entry, evidence))
    if len(current) != 1:
        raise LoopRejected("one exact current-cut accounting/readiness publication required")
    decision_id, raw, entry, evidence = current[0]
    old = evidence.accounting_request.cut
    if old.tenant_id != cut.tenant_id or old.database_id != cut.database_id:
        raise LoopRejected("foreign selected accounting/readiness cut")
    batch = runtime._publication(entry)
    accounting_id, readiness_id = (row.record_id for row in batch.records)
    matches = (
        [row for row in cut.complete_sources if row.physical_record.subject_id == accounting_id],
        [row for row in cut.complete_sources if row.physical_record.subject_id == readiness_id],
    )
    if any(len(rows) != 1 for rows in matches):
        raise LoopRejected("selected accounting/readiness physical source absent or rival")
    accounting_source, readiness_source = matches[0][0], matches[1][0]
    decision = CallSubjectHead(
        subject_id=batch.idempotency_key,
        revision=Present(head=decision_id, fingerprint=hashlib.sha256(raw).hexdigest()),
    )
    fingerprint = hashlib.sha256(
        b"chiplog.execution.selected-continuation-publication.v1\0"
        + accounting_source.canonical_bytes()
        + b"\0"
        + readiness_source.canonical_bytes()
    ).hexdigest()
    publication = SelectedContinuationPublication(
        source_cut=None,
        accounting_source=accounting_source,
        readiness_source=readiness_source,
        selected_decision=decision,
        result_stamp=SelectedContinuationResultStamp(
            selected_decision=decision,
            predecessor_tenant_commit_sequence=old.tenant_commit_sequence,
            predecessor_materialization_commitment=old.materialization_commitment,
            resulting_tenant_commit_sequence=cut.tenant_commit_sequence,
            resulting_materialization_commitment=cut.materialization_commitment,
            complete_batch_fingerprint=fingerprint,
        ),
    )
    readiness = evidence.continuation_result.complete_continuations[0]
    return publication, readiness


def _loop_decision_head(runtime: CommonCliExecutionRuntime) -> tuple[str, str | None, str] | None:
    entries = runtime._loop_decisions().entries()
    if not entries:
        return None
    identity, predecessor, raw = entries[-1]
    return identity, predecessor, hashlib.sha256(raw).hexdigest()


def _require_trust_continuity(
    expected: FrozenTrustObservation, actual: FrozenTrustObservation, stage: str
) -> None:
    if expected != actual:
        raise LoopRejected(stage)


async def _finish(
    runtime: CommonCliExecutionRuntime, entry: dict[str, object]
) -> ExecutionRunRecord:
    with runtime._authority_gate().hold():
        evidence = validate_selected_next_turn(runtime, entry, runtime._publication(entry))
        command = next_turn_command(evidence)
    await runtime._recover_exact(
        command,
        str(entry["predecessor"]),
        str(entry["resulting"]),
        is_materialized=lambda: runtime._loop_decision_materialized(command.idempotency_key, entry),
    )
    runtime._finish_decision(command.idempotency_key, expected=entry)
    current = read_execution_history(runtime)
    if evidence.result.run not in current.records:
        raise LoopRejected("selected next Turn Run absent after publication")
    assert isinstance(evidence.result.run, ExecutionRunRecord)
    return evidence.result.run


async def start_next_execution_turn(
    runtime: CommonCliExecutionRuntime, peer: str, run_id: str, expected_head: str
) -> ExecutionRunRecord:
    async with runtime._execution_lane:
        observed = await runtime._execution_actor(peer)
        with runtime._authority_gate().hold():
            runtime._check_execution_actor(observed)
        from chiplog.composition.r14_accepted_open_accounting_publication import (
            hold_if_accepted_open,
        )

        if await hold_if_accepted_open(runtime, peer):
            raise LoopRejected(
                "selected R16 outcome is OUTCOME_UNKNOWN/OPEN; RECOVERY_REQUIRED holds next Turn"
            )
        with runtime._authority_gate().hold():
            prior = [
                json.loads(raw)
                for _, _, raw in runtime._loop_decisions().entries()
                if (entry := json.loads(raw)).get("kind") == "DECIDED"
                and entry.get("operation_kind") == NEXT_TURN_PUBLICATION
                and entry.get("operation_id") == f"execution-next-turn:{run_id}:2"
            ]
            if len(prior) > 1:
                raise LoopRejected("rival selected next Turn identity")
            if not prior:
                runtime._require_no_pending()
                cut = source(runtime).collect_held(runtime)  # type: ignore[no-untyped-call]
                if cut is None or cut.tenant_id != runtime._tenant_id:
                    raise LoopRejected("complete current next Turn cut unavailable; HOLD")
                cut_trust_observation = runtime._trust.capture_verified_observation()
                _require_trust_continuity(
                    observed.observation,
                    cut_trust_observation,
                    "trust changed between next Turn actor authentication and source cut",
                )
                run = cut.complete_ordered_run_lineage[-1]
                if (
                    not isinstance(run, ExecutionRunRecord)
                    or run.run_id != run_id
                    or run.head != expected_head
                ):
                    raise LoopRejected("stale or foreign next Turn Run")
                if len(run.turns) != 1 or run.turns[-1].ordinal != 1:
                    raise LoopRejected("next Turn requires exact first sealed Turn")
                publication, readiness = _selected_pair_held(runtime, cut)
                predecessor = runtime._commitment_journal.load(runtime._tenant_id)
                if predecessor != cut.materialization_commitment:
                    raise LoopRejected("next Turn commitment differs from current cut")
                worker = runtime.current_worker()
                session = runtime._supervisor.runtime().session("agent_loop")
                decision_head = _loop_decision_head(runtime)
                sealed_response = run.turns[-1].response_seal
                if sealed_response is None:
                    raise LoopRejected("next Turn preceding response is unsealed")
                request = PrepareNextExecutionTurn(
                    command_id=f"execution-next-turn:{run_id}:2",
                    run=run,
                    cut=cut,
                    immediately_preceding_response=sealed_response,
                    expected_current_turn=_turn_ref(run.turns[-1]),
                    next_ordinal=2,
                    continuation=readiness,
                    selected_publication=publication,
                    fence=NonSchedulerFence(
                        lineage=NotApplicable(),
                        physical_root=NotApplicable(),
                        lease=NotApplicable(),
                        clock_proof=NotApplicable(),
                        run_id=run_id,
                        run_head=run.head,
                        worker_session_id=worker,
                        runtime_generation=session.generation_id,
                    ),
                )
        if prior:
            evidence = retained_next_turn(prior[0])
            if evidence.request.run.run_id != run_id or evidence.request.run.head != expected_head:
                raise LoopRejected("changed replay request for selected next Turn")
            return await _finish(runtime, prior[0])
        result, sent, reply = await _exchange_next_turn(runtime, request)

        # The complete source cut is already validated before owner exchange.
        # Recheck independent anchors; the appender verifies the exact SQLite
        # preimage and head inside its write transaction.
        with runtime._authority_gate().hold():
            runtime._check_database_identity()
            if _loop_decision_head(runtime) != decision_head:
                raise LoopRejected(
                    "selected decision journal changed during next Turn owner exchange"
                )
            if runtime.current_worker() != worker:
                raise LoopRejected("worker changed during next Turn owner exchange")
            if runtime._supervisor.runtime().session("agent_loop") != session:
                raise LoopRejected("runtime session changed during next Turn owner exchange")
            if runtime._commitment_journal.load(runtime._tenant_id) != predecessor:
                raise LoopRejected("recovery commitment changed during next Turn owner exchange")

        refreshed, actor_budget = await issue_execution_recovery_actor_v1(runtime, peer)  # type: ignore[no-untyped-call]
        with runtime._authority_gate().hold():
            if not runtime._same_execution_actor_identity(observed, refreshed):
                raise LoopRejected("next Turn actor changed before publication")
            _require_trust_continuity(
                cut_trust_observation,
                refreshed.observation,
                "trust source changed during next Turn owner exchange",
            )
            observed = refreshed
        evidence = RetainedNextTurnPublication(
            request=request,
            result=result,
            owner_call=sent,
            owner_reply=reply,
            actor_budget_v1=actor_budget,
        )
        command = next_turn_command(evidence)
        guard_failure = None

        def guard() -> Literal["STALE"] | None:
            nonlocal guard_failure
            with runtime._authority_gate().hold():
                try:
                    if time.monotonic_ns() >= actor_budget.request.budget.absolute_deadline_ns:
                        guard_failure = "recovery actor budget expired"
                        return "STALE"
                    if _loop_decision_head(runtime) != decision_head:
                        guard_failure = "selected decision journal changed"
                        return "STALE"
                    runtime._check_database_identity()
                    if runtime._commitment_journal.load(runtime._tenant_id) != predecessor:
                        guard_failure = "recovery commitment changed"
                        return "STALE"
                    if runtime.current_worker() != worker:
                        guard_failure = "worker changed"
                        return "STALE"
                    if runtime._supervisor.runtime().session("agent_loop") != session:
                        guard_failure = "runtime session changed"
                        return "STALE"
                    runtime._check_execution_actor(observed)
                    if time.monotonic_ns() >= actor_budget.request.budget.absolute_deadline_ns:
                        guard_failure = "recovery actor budget expired"
                        return "STALE"
                except (ValueError, TypeError, RuntimeError, LoopRejected) as error:
                    guard_failure = f"fresh recovery validation failed: {error}"
                    return "STALE"
                return None

        def decide(resulting: str) -> None:
            with runtime._authority_gate().hold():
                failure = None
                try:
                    runtime._check_database_identity()
                    if runtime.current_worker() != worker:
                        failure = "worker changed"
                    elif runtime._supervisor.runtime().session("agent_loop") != session:
                        failure = "runtime session changed"
                    elif _loop_decision_head(runtime) != decision_head:
                        failure = "selected decision journal changed"
                    elif runtime._commitment_journal.load(runtime._tenant_id) != predecessor:
                        failure = "recovery commitment changed"
                    elif runtime._require_no_pending() is not None:
                        failure = "pending publication appeared"
                    elif not resulting:
                        failure = "candidate commitment unavailable"
                    if failure is None:
                        runtime._check_execution_actor(observed)
                        if time.monotonic_ns() >= actor_budget.request.budget.absolute_deadline_ns:
                            failure = "recovery actor budget expired"
                except (ValueError, TypeError, RuntimeError, LoopRejected) as error:
                    failure = f"fresh recovery validation failed: {error}"
                if failure is not None:
                    raise LoopRejected(
                        "next Turn source changed at final decision: " + failure
                    )
                runtime._append_decision(
                    {
                        "version": 1,
                        "kind": "DECIDED",
                        "operation_id": command.idempotency_key,
                        "operation_kind": command.operation_kind,
                        "expected_head": command.expected_head,
                        "fingerprint": command.request_fingerprint,
                        "predecessor": predecessor,
                        "resulting": resulting,
                        "records": [
                            {
                                "record_id": row.record_id,
                                "owner": row.owner,
                                "schema": row.schema_id,
                                "payload": base64.b64encode(row.canonical_bytes).decode(),
                                "digest": row.fingerprint,
                            }
                            for row in command.records
                        ],
                        "execution_next_turn_evidence": evidence.canonical_bytes().decode(),
                    }
                )

        published = await runtime._appender.submit(
            replace(command, admission_guard=guard, decision_guard=decide)
        )
        if published.disposition not in ("COMMITTED", "REPLAY"):
            raise LoopRejected("next Turn publication " + published.disposition)
        with runtime._authority_gate().hold():
            entries = [
                json.loads(raw)
                for _, _, raw in runtime._loop_decisions().entries()
                if (entry := json.loads(raw)).get("operation_id") == command.idempotency_key
            ]
            if len(entries) != 1 or retained_next_turn(entries[0]) != evidence:
                raise LoopRejected("selected next Turn evidence changed")
        return await _finish(runtime, entries[0])
