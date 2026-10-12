"""Selected same-Run resume publication and restart reader."""

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
    PreparedExecutionResume,
    PrepareExecutionResume,
)
from chiplog.capabilities.agent_loop.execution_recovery_preparation import (
    classify_execution_resume,
    prepare_execution_resume,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    NonSchedulerFence,
    NotApplicable,
    Present,
    RecoveryDTO,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    RecoveryRequiredResult,
)
from chiplog.composition.r14_execution_suspension import (
    _command as suspension_command,
)
from chiplog.composition.r14_execution_suspension import (
    _entry_for as suspension_entry_for,
)
from chiplog.composition.r14_execution_suspension import (
    _retained as retained_suspension,
)
from chiplog.composition.r14_execution_suspension import (
    _validate_selected as validate_suspension,
)
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

OPERATION = "agent_loop.execution-resume.v1"
OWNER_OPERATION = "agent_loop.prepare_execution_resume.v1"
OWNER_OBLIGATION_OPERATION = "agent_loop.prepare_execution_resume_with_obligation.v1"
RUN_SCHEMA = "chiplog.agent-loop.execution-record.v2"
EVIDENCE_FIELD = "execution_resume_evidence"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _run_ref(run: ExecutionRunRecord) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=run.run_id,
        revision=Present(head=run.head, fingerprint=run.digest()),
    )


class RetainedExecutionResume(RecoveryDTO):
    request: PrepareExecutionResume
    result: PreparedExecutionResume
    owner_call: PublicPortCall
    owner_reply: PublicPortSuccess
    actor_budget_v1: RecoveryActorBudgetV1


def _command(evidence: RetainedExecutionResume) -> PhysicalPublicationCommand:
    request, result = evidence.request, evidence.result
    call, reply = evidence.owner_call, evidence.owner_reply
    if not isinstance(call, PublicPortCall) or not isinstance(reply, PublicPortSuccess):
        raise ValueError("resume owner exchange has the wrong wire variants")
    validate_recovery_actor_budget(evidence.actor_budget_v1)
    if (
        call.operation_id not in (OWNER_OPERATION, OWNER_OBLIGATION_OPERATION)
        or call.schema_id != "chiplog.execution.recovery-request.v1"
        or call.canonical_payload != request.canonical_bytes()
        or call.caller.owner_id != "broker"
        or call.callee.owner_id != "agent_loop"
        or call.caller.tenant_id != request.cut.tenant_id
        or call.callee.tenant_id != request.cut.tenant_id
        or reply.request_id != call.request_id
        or reply.responder != call.callee
        or reply.schema_id != "chiplog.execution.recovery-result.v1"
        or reply.canonical_payload != result.canonical_bytes()
        or result.source_request_fingerprint != request.digest()
        or prepare_execution_resume(request) != result
        or result.run.state != "ACTIVE"
        or not isinstance(request.fence, NonSchedulerFence)
        or request.run.state != "SUSPENDED"
        or result.original_pair != request.original_suspension.selected_pair
    ):
        raise ValueError("resume retained owner result or selected suspension differs")
    if (
        call.operation_id == OWNER_OPERATION
        and request.original_suspension.baseline.original_obligations
    ):
        raise ValueError("legacy resume owner route cannot retain original obligations")
    run_raw = result.run.canonical_bytes()
    if result.complete_batch_fingerprint != _sha(run_raw):
        raise ValueError("resume complete batch fingerprint differs")
    command_id = f"execution-resume:{request.run.run_id}:{request.run.head}"
    if request.command_id != command_id:
        raise ValueError("resume operation identity differs from selected suspended Run")
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
        records=(
            PhysicalRecord(
                result.run.head,
                "agent_loop",
                RUN_SCHEMA,
                run_raw,
                _sha(run_raw),
            ),
        ),
    )


def _selected_decision(entry: dict[str, object]) -> CallSubjectHead:
    decision_id = entry.get("decision_id")
    decision_bytes = entry.get("decision_bytes")
    if not isinstance(decision_id, str) or not isinstance(decision_bytes, (str, bytes)):
        raise ValueError("resume selected decision reference absent")
    raw = decision_bytes.encode() if isinstance(decision_bytes, str) else decision_bytes
    return CallSubjectHead(
        subject_id=str(entry["operation_id"]),
        revision=Present(head=decision_id, fingerprint=_sha(raw)),
    )


def _retained(entry: dict[str, object]) -> RetainedExecutionResume:
    raw = entry.get(EVIDENCE_FIELD)
    if not isinstance(raw, str):
        raise ValueError("selected resume evidence absent")
    evidence = RetainedExecutionResume.model_validate_json(raw)
    if evidence.canonical_bytes().decode() != raw:
        raise ValueError("selected resume evidence is noncanonical")
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
        raise LoopRejected("rival selected execution resume")
    return matches[0] if matches else None


def _stored_entry(entry: dict[str, object]) -> dict[str, object]:
    internal = {"decision_id", "decision_bytes"}
    return {key: value for key, value in entry.items() if key not in internal}


def _validate_obligation_transition_publications(
    runtime, evidence: RetainedExecutionResume
) -> None:
    """Bind the exact closure transition sources to publications after suspension."""
    from chiplog.composition.r14_native_recovery_cut import _physical_inventory_id

    request = evidence.request
    cut = request.cut
    baseline = request.original_suspension.baseline
    if len(baseline.original_obligations) != 1:
        raise ValueError("obligation resume requires one frozen original obligation")
    if (
        len(cut.frontier.ordered_calls) != 1
        or not isinstance(cut.frontier.ordered_calls[0].disposition, RecoveryRequiredResult)
        or len(cut.original_closures) != 1
        or len(cut.current_reductions) != 1
    ):
        raise ValueError("obligation resume transition sources are incomplete")

    disposition = cut.frontier.ordered_calls[0].disposition
    closure = cut.original_closures[0]
    reduction = cut.current_reductions[0]
    required = (
        (
            disposition.recovered_outcome.outcome,
            "chiplog.loop.recovered-call-outcome.v1",
        ),
        (closure.closure, "chiplog.loop.original-obligation-closure.v1"),
        (closure.resolver_batch, "chiplog.loop.original-resolver-batch.v1"),
        (reduction.current_reduction, "chiplog.loop.semantic-reduction.v1"),
    )
    carriers = {}
    for revision, schema_id in required:
        if not isinstance(revision, Present):
            raise ValueError("obligation resume transition reference is not present")
        matches = tuple(
            source_record
            for source_record in cut.complete_sources
            if source_record.owner == "agent_loop"
            and source_record.schema_id == schema_id
            and source_record.subject.revision == revision
        )
        unique = {
            (
                source_record.owner,
                source_record.schema_id,
                source_record.selected_decision,
                source_record.physical_record,
                source_record.canonical_record_bytes,
            ): source_record
            for source_record in matches
        }
        if len(unique) != 1:
            raise ValueError("obligation resume transition source is absent or ambiguous")
        source_record = next(iter(unique.values()))
        physical_id = _physical_inventory_id(source_record)
        existing = carriers.get(physical_id)
        value = (
            source_record.owner,
            source_record.schema_id,
            source_record.canonical_record_bytes,
            source_record.selected_decision,
        )
        if existing is not None and existing != value:
            raise ValueError("obligation resume physical carrier has conflicting sources")
        carriers[physical_id] = value

    # The physical suspension sequence is the publication immediately following
    # the selected suspension's retained expected head.
    suspension_entry = suspension_entry_for(runtime, baseline.suspension_command_id)
    if suspension_entry is None:
        raise ValueError("resume source suspension decision is absent")
    suspension_batch = suspension_command(retained_suspension(suspension_entry[2]))
    suspension_sequence = suspension_batch.expected_head + 1
    with read_connection(runtime._database) as connection:
        for physical_id, (owner, schema_id, raw, _) in carriers.items():
            records = connection.execute(
                "SELECT owner,schema_id,canonical_bytes,commit_sequence FROM records "
                "WHERE tenant_id=? AND record_id=?",
                (cut.tenant_id, physical_id),
            ).fetchall()
            if len(records) != 1:
                raise ValueError("obligation resume physical source record is absent or ambiguous")
            record_owner, record_schema, record_raw, sequence = records[0]
            sequence = int(sequence)
            if (str(record_owner), str(record_schema), bytes(record_raw)) != (
                owner,
                schema_id,
                raw,
            ) or not suspension_sequence < sequence <= cut.tenant_commit_sequence:
                raise ValueError("obligation resume source was not published after suspension")
            publications = connection.execute(
                "SELECT record_ids FROM publications WHERE tenant_id=? AND commit_sequence=?",
                (cut.tenant_id, sequence),
            ).fetchall()
            if len(publications) != 1 or physical_id not in str(publications[0][0]).splitlines():
                raise ValueError("obligation resume source is outside its selected publication")


def _validate_selected(
    runtime,
    entry: dict[str, object],
    physical_command: PhysicalPublicationCommand,
    *,
    check_history: bool = True,
) -> RetainedExecutionResume:
    evidence = _retained(entry)
    command = _command(evidence)
    if (
        replace(command, expected_preimage_commitment=None)
        != replace(physical_command, expected_preimage_commitment=None)
        or entry.get("fingerprint") != command.request_fingerprint
        or int(entry.get("expected_head", -1)) != command.expected_head
        or entry.get("predecessor") != evidence.request.cut.materialization_commitment
        or entry.get(EVIDENCE_FIELD) != evidence.canonical_bytes().decode()
    ):
        raise ValueError("selected resume decision differs from exact owner batch")
    suspension_entry = suspension_entry_for(
        runtime, evidence.request.original_suspension.baseline.suspension_command_id
    )
    if suspension_entry is None:
        raise ValueError("resume source suspension decision is absent")
    retained_suspension_evidence = retained_suspension(suspension_entry[2])
    suspension_batch = suspension_command(retained_suspension_evidence)
    if (
        evidence.request.cut.tenant_id != runtime._tenant_id
        or evidence.request.cut.database_id != runtime._database_identity[0]
        or (
            not evidence.request.original_suspension.baseline.original_obligations
            and (
                int(suspension_entry[2].get("expected_head", -1)) + 1
                != evidence.request.cut.tenant_commit_sequence
                or suspension_entry[2].get("resulting")
                != evidence.request.cut.materialization_commitment
                or suspension_batch.expected_head + 1 != evidence.request.cut.tenant_commit_sequence
            )
        )
    ):
        raise ValueError("resume cut tenant, database, or suspension snapshot differs")
    selected_suspension = validate_suspension(
        runtime,
        suspension_entry[2],
        suspension_batch,
        check_history=False,
    )[1]
    if selected_suspension != evidence.request.original_suspension:
        raise ValueError("resume source differs from selected suspension")
    if evidence.request.original_suspension.baseline.original_obligations:
        _validate_obligation_transition_publications(runtime, evidence)
    with read_connection(runtime._database) as connection:
        if inspect_publication(connection, command, command.expected_head + 1) != "COMPLETE":
            raise ValueError("selected resume physical publication is incomplete")
        rows = connection.execute(
            "SELECT record_id, owner, schema_id, canonical_bytes FROM records "
            "WHERE tenant_id=? AND commit_sequence=? ORDER BY record_id",
            (command.tenant_id, command.expected_head + 1),
        ).fetchall()
    found = {(str(row[0]), str(row[1]), str(row[2])): bytes(row[3]) for row in rows}
    expected = {
        (record.record_id, record.owner, record.schema_id): record.canonical_bytes
        for record in command.records
    }
    if found != expected:
        raise ValueError("selected resume physical record differs from retained batch")
    validate_recovery_actor_budget(evidence.actor_budget_v1)
    if check_history:
        from chiplog.composition.r14_loop_history import read_execution_history

        if evidence.result.run not in read_execution_history(runtime).records:
            raise ValueError("resumed Run is absent from authenticated Run history")
    return evidence


async def _finish(runtime, entry: dict[str, object]) -> ExecutionRunRecord:
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
            raise LoopRejected("selected execution resume disappeared after recovery")
        return _validate_selected(runtime, current[2], command).result.run


async def resume_execution(
    runtime: CommonCliExecutionRuntime,
    peer: str,
    run_id: str,
    expected_head: str,
    activation_payload: bytes,
) -> ExecutionRunRecord:
    async with runtime._execution_lane:
        actor = await runtime._execution_actor(peer)
        command_id = f"execution-resume:{run_id}:{expected_head}"
        with runtime._authority_gate().hold():
            runtime._check_execution_actor(actor)
            prior = _entry_for(runtime, command_id)
        if prior is not None:
            retained = _retained(prior[2])
            if retained.request.activation_payload != activation_payload:
                raise LoopRejected("resume changed replay request")
            return await _finish(runtime, prior[2])
        with runtime._authority_gate().hold():
            runtime._require_no_pending()
            port = source(runtime)
            collect = getattr(port, "collect_for_resume_held", None)
            observation = collect(runtime) if callable(collect) else None
            if observation is None:
                raise LoopRejected(
                    "complete authenticated suspended recovery cut unavailable; HOLD"
                )
            cut, suspension = observation
            if (
                cut.tenant_id != runtime._tenant_id
                or cut.database_id != runtime._database_identity[0]
            ):
                raise LoopRejected("suspended resume cut tenant or database differs")
            current = cut.complete_ordered_run_lineage[-1]
            if (
                not isinstance(current, ExecutionRunRecord)
                or current.run_id != run_id
                or current.head != expected_head
                or current.state != "SUSPENDED"
            ):
                raise LoopRejected("resume requires the exact selected suspended Run")
            predecessor = runtime._commitment_journal.load(runtime._tenant_id)
            if predecessor != cut.materialization_commitment:
                raise LoopRejected("resume source cut differs from current commitment")
            trust_cut = runtime._trust.capture_verified_observation()
            worker = runtime.current_worker()
            session = runtime._supervisor.runtime().session("agent_loop")
            decisions = runtime._loop_decisions().entries()
            decision_head = decisions[-1][:1] if decisions else ()
            request = PrepareExecutionResume(
                command_id=command_id,
                run=current,
                original_suspension=suspension,
                cut=cut,
                disposition_version="r14-same-run-equality.v1",
                activation_payload=activation_payload,
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
            runtime, request, owner_operation, PreparedExecutionResume
        )
        refreshed, actor_budget = await issue_execution_recovery_actor_v1(runtime, peer)
        with runtime._authority_gate().hold():
            if not runtime._same_execution_actor_identity(actor, refreshed):
                raise LoopRejected("resume actor changed during owner preparation")
            if runtime._trust.capture_verified_observation() != trust_cut:
                raise LoopRejected("resume trust source changed during owner preparation")
            actor = refreshed
        evidence = RetainedExecutionResume(
            request=request,
            result=result,
            owner_call=owner_call,
            owner_reply=owner_reply,
            actor_budget_v1=actor_budget,
        )
        batch = _command(evidence)

        def guard():
            with runtime._authority_gate().hold():
                try:
                    runtime._check_execution_actor(actor)
                    current_observation = source(runtime).collect_for_resume_held(runtime)
                    if current_observation != (cut, suspension):
                        return "STALE"
                    current_request = request.model_copy(
                        update={
                            "cut": current_observation[0],
                            "original_suspension": current_observation[1],
                        }
                    )
                    current_preparation = prepare_execution_resume(current_request)
                    classification = classify_execution_resume(
                        current_request, current_preparation
                    )
                    if (
                        classification.disposition != "SAME_RUN"
                        or current_preparation != result
                    ):
                        return "STALE"
                    if runtime.current_worker() != worker:
                        return "STALE"
                    if runtime._supervisor.runtime().session("agent_loop") != session:
                        return "STALE"
                    decisions = runtime._loop_decisions().entries()
                    if (decisions[-1][:1] if decisions else ()) != decision_head:
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
                    raise LoopRejected("resume cut changed at final transaction guard")
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
                        EVIDENCE_FIELD: evidence.canonical_bytes().decode(),
                    }
                )

        published = await runtime._appender.submit(
            replace(batch, admission_guard=guard, decision_guard=decide)
        )
        if published.disposition not in ("COMMITTED", "REPLAY"):
            raise LoopRejected("execution resume publication " + published.disposition)
        with runtime._authority_gate().hold():
            selected_entry = _entry_for(runtime, command_id)
            if selected_entry is None or _retained(selected_entry[2]) != evidence:
                raise LoopRejected("selected resume evidence differs after publication")
        return await _finish(runtime, selected_entry[2])


def read_selected_execution_resume_held(runtime) -> ExecutionRunRecord | None:
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
        raise LoopRejected("multiple selected execution resumes")
    evidence = _retained(selected[0][2])
    return _validate_selected(runtime, selected[0][2], _command(evidence)).result.run
