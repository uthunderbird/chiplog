"""Selected R14 suspension publication and restart reader."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import replace
from typing import TYPE_CHECKING

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
    PreparedExecutionSuspension,
    PrepareExecutionSuspension,
)
from chiplog.capabilities.agent_loop.execution_recovery_observations import (
    SelectedExecutionSuspension,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    NonSchedulerFence,
    NotApplicable,
    Present,
    RecoveryDTO,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import RecoveryRequiredResult
from chiplog.capabilities.agent_loop.recovery_record_contracts import (
    RecoveryRecordMember,
    decode_recovery_record_member,
    validate_selected_execution_suspension,
)
from chiplog.composition.r14_loop_history import read_execution_history
from chiplog.composition.r14_recovery_publication import (
    RecoveryActorBudgetV1,
    _exchange,
    issue_execution_recovery_actor_v1,
    source,
    validate_recovery_actor_budget,
)
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.broker import PublicPortCall, PublicPortSuccess
from chiplog.platform.publication_readback import inspect_publication
from chiplog.platform.workspace_snapshot import read_connection

if TYPE_CHECKING:
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime

OPERATION = "agent_loop.execution-suspension.v1"
OWNER_OPERATION = "agent_loop.prepare_execution_suspension.v1"
OWNER_OBLIGATION_OPERATION = "agent_loop.prepare_execution_suspension_with_obligation.v1"
BASELINE_SCHEMA = "chiplog.execution.suspension-baseline.v2"
PAIR_SCHEMA = "chiplog.execution.suspension-pair.v2"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _run_ref(run: ExecutionRunRecord) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=run.run_id,
        revision=Present(head=run.head, fingerprint=run.digest()),
    )


def _pair_record_id(raw: bytes) -> str:
    return PAIR_SCHEMA + ":" + _sha(raw)


class RetainedExecutionSuspension(RecoveryDTO):
    request: PrepareExecutionSuspension
    result: PreparedExecutionSuspension
    owner_call: PublicPortCall
    owner_reply: PublicPortSuccess
    actor_budget_v1: RecoveryActorBudgetV1


def _command(evidence: RetainedExecutionSuspension) -> PhysicalPublicationCommand:
    from chiplog.capabilities.agent_loop.execution_recovery_preparation import (
        prepare_execution_suspension,
    )
    from chiplog.capabilities.agent_loop.recovery_wire_contracts import (
        EXECUTION_RECOVERY_REQUEST_SCHEMA,
        EXECUTION_RECOVERY_RESULT_SCHEMA,
    )

    request, result = evidence.request, evidence.result
    call, reply = evidence.owner_call, evidence.owner_reply
    if not isinstance(call, PublicPortCall) or not isinstance(reply, PublicPortSuccess):
        raise ValueError("suspension owner exchange has the wrong wire variants")
    if not isinstance(evidence.actor_budget_v1, RecoveryActorBudgetV1):
        raise ValueError("suspension actor budget has the wrong wire variant")
    validate_recovery_actor_budget(evidence.actor_budget_v1)
    if (
        call.operation_id not in (OWNER_OPERATION, OWNER_OBLIGATION_OPERATION)
        or call.schema_id != EXECUTION_RECOVERY_REQUEST_SCHEMA
        or call.canonical_payload != request.canonical_bytes()
        or call.caller.owner_id != "broker"
        or call.callee.owner_id != "agent_loop"
        or call.caller.tenant_id != request.cut.tenant_id
        or call.callee.tenant_id != request.cut.tenant_id
        or reply.request_id != call.request_id
        or reply.responder != call.callee
        or reply.schema_id != EXECUTION_RECOVERY_RESULT_SCHEMA
        or reply.canonical_payload != result.canonical_bytes()
        or result.source_request_fingerprint != request.digest()
        or prepare_execution_suspension(request) != result
        or result.run.state != "SUSPENDED"
        or request.fence.kind != "NON_SCHEDULER_NOT_APPLICABLE"
        or any(
            run.root_binding != "NOT_APPLICABLE" for run in request.cut.complete_ordered_run_lineage
        )
    ):
        raise ValueError("suspension retained owner result or non-scheduler source differs")
    if call.operation_id == OWNER_OPERATION and result.baseline.original_obligations:
        raise ValueError("legacy suspension owner route cannot retain original obligations")
    baseline_raw = result.baseline.canonical_bytes()
    run_raw = result.run.canonical_bytes()
    pair_raw = result.pair.canonical_bytes()
    expected_batch_fingerprint = _sha(b"\0".join((baseline_raw, run_raw, pair_raw)))
    if result.complete_batch_fingerprint != expected_batch_fingerprint:
        raise ValueError("suspension complete batch fingerprint differs")
    command_id = (
        "execution-suspension:"
        + request.cut.current_run.subject_id
        + ":"
        + request.cut.current_run.revision.head
    )
    if request.command_id != command_id:
        raise ValueError("suspension operation identity differs from selected Run")
    records = (
        PhysicalRecord(
            result.baseline.baseline_id,
            "agent_loop",
            BASELINE_SCHEMA,
            baseline_raw,
            _sha(baseline_raw),
        ),
        PhysicalRecord(
            result.run.head,
            "agent_loop",
            result.run.schema_id,
            run_raw,
            _sha(run_raw),
        ),
        PhysicalRecord(
            _pair_record_id(pair_raw),
            "agent_loop",
            PAIR_SCHEMA,
            pair_raw,
            _sha(pair_raw),
        ),
    )
    return PhysicalPublicationCommand(
        tenant_id=request.cut.tenant_id,
        operation_kind=OPERATION,
        idempotency_key=command_id,
        request_fingerprint=evidence.digest(),
        expected_head=request.cut.tenant_commit_sequence,
        expected_preimage_commitment=request.cut.materialization_commitment,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=records,
    )


def _selected_decision(entry: dict[str, object]) -> CallSubjectHead:
    decision_id = entry.get("decision_id")
    decision_bytes = entry.get("decision_bytes")
    if not isinstance(decision_id, str) or not isinstance(decision_bytes, (str, bytes)):
        raise ValueError("suspension selected decision reference absent")
    raw = decision_bytes.encode() if isinstance(decision_bytes, str) else decision_bytes
    return CallSubjectHead(
        subject_id=str(entry["operation_id"]),
        revision=Present(head=decision_id, fingerprint=_sha(raw)),
    )


def _retained(entry: dict[str, object]) -> RetainedExecutionSuspension:
    raw = entry.get("execution_suspension_evidence")
    if not isinstance(raw, str):
        raise ValueError("selected suspension evidence absent")
    evidence = RetainedExecutionSuspension.model_validate_json(raw)
    if evidence.canonical_bytes().decode() != raw:
        raise ValueError("selected suspension evidence is noncanonical")
    return evidence


def _entry_for(runtime, operation_id: str) -> tuple[str, str, dict[str, object]] | None:
    matches = []
    for decision_id, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        if (
            entry.get("kind") == "DECIDED"
            and entry.get("operation_kind") == OPERATION
            and entry.get("operation_id") == operation_id
        ):
            entry["decision_id"] = str(decision_id)
            entry["decision_bytes"] = raw
            matches.append((str(decision_id), raw, entry))
    if len(matches) > 1:
        raise LoopRejected("rival selected execution suspension")
    return matches[0] if matches else None


def _stored_entry(entry: dict[str, object]) -> dict[str, object]:
    internal = {"decision_id", "decision_bytes"}
    return {key: value for key, value in entry.items() if key not in internal}


def _validate_selected(
    runtime,
    entry: dict[str, object],
    physical_command,
    *,
    check_history: bool = True,
    allow_pending_absent: bool = False,
):
    if allow_pending_absent:
        runtime._authority_gate().require_held()
        if check_history:
            raise ValueError("pending suspension requires outer authenticated history")
    evidence = _retained(entry)
    command = _command(evidence)
    if (
        replace(command, expected_preimage_commitment=None)
        != replace(physical_command, expected_preimage_commitment=None)
        or entry.get("fingerprint") != command.request_fingerprint
        or int(entry.get("expected_head", -1)) != command.expected_head
        or entry.get("predecessor") != evidence.request.cut.materialization_commitment
        or entry.get("execution_suspension_evidence") != evidence.canonical_bytes().decode()
    ):
        raise ValueError("selected suspension decision differs from exact owner batch")
    with read_connection(runtime._database) as connection:
        state = inspect_publication(connection, command, int(entry.get("expected_head", -1)) + 1)
        if state != "COMPLETE" and not (allow_pending_absent and state == "ABSENT"):
            raise ValueError("selected suspension physical publication is incomplete")
    budget = evidence.actor_budget_v1
    if not isinstance(budget, RecoveryActorBudgetV1):
        raise ValueError("selected suspension actor budget is invalid")
    validate_recovery_actor_budget(budget)
    with read_connection(runtime._database) as connection:
        rows = connection.execute(
            "SELECT record_id, owner, schema_id, canonical_bytes FROM records "
            "WHERE tenant_id=? AND commit_sequence=? ORDER BY record_id",
            (command.tenant_id, int(entry["expected_head"]) + 1),
        ).fetchall()
    found = {(str(row[0]), str(row[1]), str(row[2])): bytes(row[3]) for row in rows}
    expected = {
        (record.record_id, record.owner, record.schema_id): record.canonical_bytes
        for record in command.records
    }
    if found != expected and not (allow_pending_absent and state == "ABSENT" and not found):
        raise ValueError("selected suspension physical records differ from retained batch")
    if check_history:
        history = read_execution_history(runtime)
        if evidence.result.run not in history.records:
            raise ValueError("selected suspended Run is absent from authenticated Run history")
    baseline_record, pair_record = command.records[0], command.records[2]
    baseline_member = RecoveryRecordMember(
        owner=baseline_record.owner,
        record_kind="SUSPENSION_BASELINE",
        schema_id=baseline_record.schema_id,
        record_id=baseline_record.record_id,
        canonical_record_bytes=baseline_record.canonical_bytes,
        fingerprint=baseline_record.fingerprint,
    )
    pair_member = RecoveryRecordMember(
        owner=pair_record.owner,
        record_kind="SUSPENSION_PAIR",
        schema_id=pair_record.schema_id,
        record_id=pair_record.record_id,
        canonical_record_bytes=pair_record.canonical_bytes,
        fingerprint=pair_record.fingerprint,
    )
    decoded_baseline = decode_recovery_record_member(baseline_member)
    decoded_pair = decode_recovery_record_member(pair_member)
    selected = SelectedExecutionSuspension(
        baseline=evidence.result.baseline,
        pair=evidence.result.pair,
        selected_pair=CallSubjectHead(
            subject_id=pair_record.record_id,
            revision=Present(
                head=pair_record.record_id,
                fingerprint=pair_record.fingerprint,
            ),
        ),
        selected_decision=_selected_decision(entry),
        canonical_pair_bytes=pair_record.canonical_bytes,
    )
    validate_selected_execution_suspension(
        selected,
        decoded_baseline,
        _run_ref(evidence.result.run),
        decoded_pair,
        selected.selected_decision,
    )
    return evidence, selected


async def _finish(runtime, entry: dict[str, object]) -> SelectedExecutionSuspension:
    stored = _stored_entry(entry)
    with runtime._authority_gate().hold():
        evidence = _retained(stored)
        command = _command(evidence)
    await runtime._recover_exact(
        command,
        str(stored["predecessor"]),
        str(stored["resulting"]),
        is_materialized=lambda: runtime._loop_decision_materialized(
            command.idempotency_key, stored
        ),
    )
    runtime._finish_decision(command.idempotency_key, expected=stored)
    with runtime._authority_gate().hold():
        current = _entry_for(runtime, command.idempotency_key)
        if current is None:
            raise LoopRejected("selected execution suspension disappeared after recovery")
        _, _, selected_entry = current
        return _validate_selected(runtime, selected_entry, command)[1]


async def suspend_execution(
    runtime: CommonCliExecutionRuntime,
    peer: str,
    run_id: str,
    expected_head: str,
    activation_blocking_predicates: tuple[CallSubjectHead, ...],
) -> SelectedExecutionSuspension:
    async with runtime._execution_lane:
        actor = await runtime._execution_actor(peer)
        with runtime._authority_gate().hold():
            runtime._check_execution_actor(actor)
            command_id = "execution-suspension:" + run_id + ":" + expected_head
            prior = _entry_for(runtime, command_id)
        if prior is not None:
            return await _finish(runtime, prior[2])
        with runtime._authority_gate().hold():
            runtime._require_no_pending()
            cut = source(runtime).collect_held(runtime)
            if cut is None or cut.tenant_id != runtime._tenant_id:
                raise LoopRejected("complete authenticated suspension cut unavailable; HOLD")
            lineage = cut.complete_ordered_run_lineage
            run = lineage[-1]
            if (
                not isinstance(run, ExecutionRunRecord)
                or run.run_id != run_id
                or run.head != expected_head
                or run.state != "ACTIVE"
                or any(item.root_binding != "NOT_APPLICABLE" for item in lineage)
            ):
                raise LoopRejected("suspension requires the exact active non-scheduler Run")
            trust_cut = runtime._trust.capture_verified_observation()
            if trust_cut != actor.observation:
                raise LoopRejected("trust source changed before suspension cut")
            predecessor = runtime._commitment_journal.load(runtime._tenant_id)
            if predecessor != cut.materialization_commitment:
                raise LoopRejected("suspension source cut differs from current commitment")
            worker = runtime.current_worker()
            session = runtime._supervisor.runtime().session("agent_loop")
            decisions = runtime._loop_decisions().entries()
            decision_head = decisions[-1][:1] if decisions else ()
            request = PrepareExecutionSuspension(
                command_id=command_id,
                run=run,
                cut=cut,
                activation_blocking_predicates=activation_blocking_predicates,
                fence=NonSchedulerFence(
                    lineage=NotApplicable(),
                    physical_root=NotApplicable(),
                    lease=NotApplicable(),
                    clock_proof=NotApplicable(),
                    run_id=run_id,
                    run_head=expected_head,
                    worker_session_id=worker,
                    runtime_generation=session.generation_id,
                ),
            )
        owner_capabilities = next(
            row.capability_ids
            for row in runtime._supervisor.runtime().graph_generation().owners
            if row.owner_id == "agent_loop"
        )
        owner_operation = (
            OWNER_OBLIGATION_OPERATION
            if OWNER_OBLIGATION_OPERATION in owner_capabilities
            else OWNER_OPERATION
        )
        result, owner_call, owner_reply = await _exchange(
            runtime,
            request,
            owner_operation,
            PreparedExecutionSuspension,
        )
        refreshed, actor_budget = await issue_execution_recovery_actor_v1(runtime, peer)
        with runtime._authority_gate().hold():
            if not runtime._same_execution_actor_identity(actor, refreshed):
                raise LoopRejected("suspension actor changed during owner preparation")
            if runtime._trust.capture_verified_observation() != trust_cut:
                raise LoopRejected("trust source changed during suspension owner preparation")
            actor = refreshed
        evidence = RetainedExecutionSuspension(
            request=request,
            result=result,
            owner_call=owner_call,
            owner_reply=owner_reply,
            actor_budget_v1=actor_budget,
        )
        batch = _command(evidence)
        if (
            batch.expected_head != cut.tenant_commit_sequence
            or batch.expected_preimage_commitment != cut.materialization_commitment
        ):
            raise LoopRejected("suspension command does not bind the authenticated source cut")
        transaction_binds_open_cut = any(
            isinstance(item.disposition, RecoveryRequiredResult)
            for item in cut.frontier.ordered_calls
        )
        source_cut_validated = False

        def guard():
            nonlocal source_cut_validated
            with runtime._authority_gate().hold():
                try:
                    runtime._check_execution_actor(actor)
                    if not source_cut_validated:
                        runtime._check_database_identity()
                        if not transaction_binds_open_cut:
                            current = source(runtime).collect_held(runtime)
                            if current != cut:
                                return "STALE"
                        # A retained recovery-required frontier has already
                        # authenticated the accepted/open source. The writer
                        # transaction binds that exact cut by tenant head and
                        # materialization preimage before this guard; the
                        # remaining live actor/runtime fences still run here.
                        # Other frontiers keep the full source replay because
                        # their current dispatch-resource check is independent
                        # of the SQLite materialization commitment.
                        source_cut_validated = True
                    if runtime.current_worker() != worker:
                        return "STALE"
                    if runtime._supervisor.runtime().session("agent_loop") != session:
                        return "STALE"
                    decisions = runtime._loop_decisions().entries()
                    current_decision_head = decisions[-1][:1] if decisions else ()
                    if current_decision_head != decision_head:
                        return "STALE"
                    if runtime._commitment_journal.load(runtime._tenant_id) != predecessor:
                        return "STALE"
                    if runtime._require_no_pending() is not None:
                        return "STALE"
                    if time.monotonic_ns() >= actor_budget.request.budget.absolute_deadline_ns:
                        return "STALE"
                except ValueError, TypeError, RuntimeError, LoopRejected:
                    return "STALE"
            return None

        def decide(resulting: str) -> None:
            with runtime._authority_gate().hold():
                if guard() is not None or not resulting:
                    raise LoopRejected("suspension source cut changed at final transaction guard")
                runtime._append_decision(
                    {
                        "version": 1,
                        "kind": "DECIDED",
                        "operation_id": batch.idempotency_key,
                        "operation_kind": OPERATION,
                        "expected_head": batch.expected_head,
                        "fingerprint": batch.request_fingerprint,
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
                            for record in batch.records
                        ],
                        "execution_suspension_evidence": evidence.canonical_bytes().decode(),
                    }
                )

        published = await runtime._appender.submit(
            replace(batch, admission_guard=guard, decision_guard=decide)
        )
        if published.disposition not in ("COMMITTED", "REPLAY"):
            raise LoopRejected("execution suspension publication " + published.disposition)
        with runtime._authority_gate().hold():
            selected = _entry_for(runtime, command_id)
            if selected is None or _retained(selected[2]) != evidence:
                raise LoopRejected("selected suspension evidence differs after publication")
        return await _finish(runtime, selected[2])


def read_selected_execution_suspension_held(runtime) -> SelectedExecutionSuspension | None:
    from chiplog.composition.r14_loop_history import _read_call_history

    _read_call_history(runtime)
    selected = [
        _entry_for(runtime, str(entry["operation_id"]))
        for _, _, raw in runtime._loop_decisions().entries()
        if (entry := json.loads(raw)).get("kind") == "DECIDED"
        and entry.get("operation_kind") == OPERATION
    ]
    selected = [item for item in selected if item is not None]
    if not selected:
        return None
    if len(selected) != 1:
        raise LoopRejected("multiple selected execution suspensions")
    return _finish_sync(runtime, selected[0][2])


def _finish_sync(runtime, entry: dict[str, object]) -> SelectedExecutionSuspension:
    evidence = _retained(entry)
    command = _command(evidence)
    return _validate_selected(runtime, entry, command)[1]
