"""Selected native CLI successor publication and restart recovery.

The successor is deliberately a broker composition operation: the owner prepares
two exact batches, while this module retains the authenticated ingress and the
selected suspended cut that justify publishing them.
"""

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
from chiplog.capabilities.agent_loop.execution_initialization_contracts import (
    SelectedAdmittedRunInput,
)
from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
    PreparedExecutionSuccessor,
    PreparedExecutionSuccessorStart,
    PrepareExecutionSuccessor,
    PrepareExecutionSuccessorStart,
)
from chiplog.capabilities.agent_loop.execution_recovery_preparation import (
    classify_execution_successor,
    prepare_execution_successor,
    prepare_execution_successor_start,
)
from chiplog.capabilities.agent_loop.execution_transition_contracts import CreateExecutionRun
from chiplog.capabilities.agent_loop.recovery_contracts import (
    NonSchedulerFence,
    NotApplicable,
    Present,
    RecoveryDTO,
)
from chiplog.capabilities.agent_loop.recovery_wire_contracts import (
    EXECUTION_RECOVERY_REQUEST_SCHEMA,
    EXECUTION_RECOVERY_RESULT_SCHEMA,
)
from chiplog.capabilities.agent_loop.successor_record_contracts import (
    SUCCESSOR_INITIALIZATION_SCHEMA,
)
from chiplog.composition.common_execution_driver_contracts import DriveInputRequestV1
from chiplog.composition.r14_execution_fanout_contracts import EXECUTION_RUN_SCHEMA
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

OPERATION = "agent_loop.execution-successor.v1"
START_OPERATION = "agent_loop.execution-successor-start.v1"
OWNER_OPERATION = "agent_loop.prepare_execution_successor.v1"
OWNER_START_OPERATION = "agent_loop.prepare_execution_successor.v1"
EVIDENCE_FIELD = "execution_successor_evidence"
START_EVIDENCE_FIELD = "execution_successor_start_evidence"
RUN_SCHEMA = EXECUTION_RUN_SCHEMA
EDGE_SCHEMA = "chiplog.execution.successor-edge.v1"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _run_ref(run: ExecutionRunRecord) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=run.run_id,
        revision=Present(head=run.head, fingerprint=run.digest()),
    )


def _run_id(admitted) -> str:
    raw = json.dumps(
        [admitted.tenant_id, admitted.database_id, admitted.inbox.subject_id],
        separators=(",", ":"),
    ).encode()
    return "run:inbox:" + hashlib.sha256(b"chiplog.h0.run.v1\x00" + raw).hexdigest()


def _ingress_claim(request: DriveInputRequestV1) -> str:
    canonical = json.dumps(
        {
            "tenant_id": request.identity.tenant_id,
            "database_id": request.identity.database_id,
            "original_ingress_request_fingerprint": (
                request.identity.original_ingress_request_fingerprint
            ),
            "selected_source": request.selected_source.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return _sha(b"chiplog.selected-ingress-claim.v1\0" + canonical)


class RetainedExecutionSuccessor(RecoveryDTO):
    """Exact CLI request, selected recovery cut and owner publication batch."""

    driver_request_bytes: bytes
    driver_request_fingerprint: str
    admitted: SelectedAdmittedRunInput
    request: PrepareExecutionSuccessor
    result: PreparedExecutionSuccessor
    owner_call: PublicPortCall
    owner_reply: PublicPortSuccess
    actor_budget_v1: RecoveryActorBudgetV1


class RetainedExecutionSuccessorStart(RecoveryDTO):
    source: RetainedExecutionSuccessor
    source_resulting_commitment: str
    request: PrepareExecutionSuccessorStart
    result: PreparedExecutionSuccessorStart
    owner_call: PublicPortCall
    owner_reply: PublicPortSuccess
    actor_budget_v1: RecoveryActorBudgetV1


def _owner_exchange_valid(request, result, call, reply, operation: str) -> None:
    tenant_id = request.cut.tenant_id if hasattr(request, "cut") else request.created_run.tenant
    if (
        not isinstance(call, PublicPortCall)
        or not isinstance(reply, PublicPortSuccess)
        or call.operation_id != operation
        or call.schema_id != EXECUTION_RECOVERY_REQUEST_SCHEMA
        or call.canonical_payload != request.canonical_bytes()
        or call.caller.owner_id != "broker"
        or call.callee.owner_id != "agent_loop"
        or call.caller.tenant_id != tenant_id
        or call.callee.tenant_id != tenant_id
        or reply.request_id != call.request_id
        or reply.responder != call.callee
        or reply.schema_id != EXECUTION_RECOVERY_RESULT_SCHEMA
        or reply.canonical_payload != result.canonical_bytes()
        or result.source_request_fingerprint != request.digest()
    ):
        raise ValueError("successor retained owner exchange differs from request/result")


def _command(evidence: RetainedExecutionSuccessor) -> PhysicalPublicationCommand:
    request, result = evidence.request, evidence.result
    _owner_exchange_valid(
        request, result, evidence.owner_call, evidence.owner_reply, OWNER_OPERATION
    )
    validate_recovery_actor_budget(evidence.actor_budget_v1)
    if (
        prepare_execution_successor(request) != result
        or request.command_id != f"execution-successor:{request.run.run_id}:{request.run.head}"
        or request.admitted_input != evidence.admitted
        or evidence.driver_request_fingerprint
        != DriveInputRequestV1.model_validate_json(
            evidence.driver_request_bytes
        ).original_driver_command_fingerprint()
        or request.successor.run_id != _run_id(evidence.admitted)
    ):
        raise ValueError("successor owner result or retained CLI identity differs")
    records = []
    for run in (result.superseded_run, result.successor_run):
        raw = run.canonical_bytes()
        records.append(PhysicalRecord(run.head, "agent_loop", RUN_SCHEMA, raw, _sha(raw)))
    edge_raw = result.edge.canonical_bytes()
    records.append(
        PhysicalRecord(
            EDGE_SCHEMA + ":" + _sha(edge_raw), "agent_loop", EDGE_SCHEMA, edge_raw, _sha(edge_raw)
        )
    )
    if len(result.complete_initialization_bytes) != 1:
        raise ValueError("native successor batch must contain exactly one initialization")
    init_raw = result.complete_initialization_bytes[0]
    init_id = SUCCESSOR_INITIALIZATION_SCHEMA + ":" + _sha(init_raw)
    records.append(
        PhysicalRecord(
            init_id, "agent_loop", SUCCESSOR_INITIALIZATION_SCHEMA, init_raw, _sha(init_raw)
        )
    )
    if result.complete_batch_fingerprint != _sha(
        b"\0".join(record.canonical_bytes for record in records)
    ):
        raise ValueError("successor complete batch fingerprint differs")
    if len({r.record_id for r in records}) != len(records):
        raise ValueError("successor physical record IDs collide")
    return PhysicalPublicationCommand(
        tenant_id=request.cut.tenant_id,
        operation_kind=OPERATION,
        idempotency_key=request.command_id,
        request_fingerprint=evidence.digest(),
        expected_head=request.cut.tenant_commit_sequence,
        expected_preimage_commitment=request.cut.materialization_commitment,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=tuple(records),
    )


def _start_command(evidence: RetainedExecutionSuccessorStart) -> PhysicalPublicationCommand:
    request, result = evidence.request, evidence.result
    source_evidence = evidence.source
    _owner_exchange_valid(
        request, result, evidence.owner_call, evidence.owner_reply, OWNER_START_OPERATION
    )
    validate_recovery_actor_budget(evidence.actor_budget_v1)
    if (
        prepare_execution_successor_start(request) != result
        or request.source_request != source_evidence.request
        or request.created_run != source_evidence.result.successor_run
        or request.command_id
        != f"execution-successor-start:{request.created_run.run_id}:{request.created_run.head}"
    ):
        raise ValueError("successor start owner result differs from selected CREATED Run")
    raw = result.run.canonical_bytes()
    if result.complete_batch_fingerprint != _sha(raw):
        raise ValueError("successor start batch fingerprint differs")
    command_id = request.command_id
    return PhysicalPublicationCommand(
        tenant_id=request.created_run.tenant,
        operation_kind=START_OPERATION,
        idempotency_key=command_id,
        request_fingerprint=evidence.digest(),
        expected_head=source_evidence.request.cut.tenant_commit_sequence + 1,
        expected_preimage_commitment=evidence.source_resulting_commitment,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=(PhysicalRecord(result.run.head, "agent_loop", RUN_SCHEMA, raw, _sha(raw)),),
    )


def _selected_decision(entry: dict[str, object]) -> CallSubjectHead:
    raw = entry.get("decision_bytes")
    decision_id = entry.get("decision_id")
    if not isinstance(raw, (bytes, str)) or not isinstance(decision_id, str):
        raise ValueError("successor selected decision reference absent")
    raw = raw.encode() if isinstance(raw, str) else raw
    return CallSubjectHead(
        subject_id=str(entry["operation_id"]),
        revision=Present(head=decision_id, fingerprint=_sha(raw)),
    )


def _entry_for(runtime, operation_id: str, operation: str = OPERATION):
    matches = []
    for decision_id, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        if (
            entry.get("kind") == "DECIDED"
            and entry.get("operation_kind") == operation
            and entry.get("operation_id") == operation_id
        ):
            entry["decision_id"], entry["decision_bytes"] = str(decision_id), raw
            matches.append((str(decision_id), raw, entry))
    if len(matches) > 1:
        raise LoopRejected("rival selected execution successor publication")
    return matches[0] if matches else None


def _retained(entry, field=EVIDENCE_FIELD, cls=RetainedExecutionSuccessor):
    raw = entry.get(field)
    if not isinstance(raw, str):
        raise ValueError("selected successor evidence absent")
    value = cls.model_validate_json(raw)
    if value.canonical_bytes().decode() != raw:
        raise ValueError("selected successor evidence is noncanonical")
    return value


def _selected_matches(
    runtime,
    entry,
    command,
    evidence,
    *,
    start=False,
    source_validated=False,
    allow_pending_absent=False,
):
    if allow_pending_absent:
        runtime._authority_gate().require_held()
        if start:
            raise ValueError("pending successor start is not supported by OPEN history")
    expected = _start_command(evidence) if start else _command(evidence)
    selected_predecessor = (
        evidence.source_resulting_commitment
        if start
        else evidence.request.cut.materialization_commitment
    )
    if (
        replace(expected, expected_preimage_commitment=None)
        != replace(command, expected_preimage_commitment=None)
        or entry.get("fingerprint") != command.request_fingerprint
        or int(entry.get("expected_head", -1)) != command.expected_head
        or entry.get("predecessor") != selected_predecessor
    ):
        raise ValueError("selected successor decision differs from retained batch")
    if start and not source_validated:
        source_entry = _entry_for(runtime, evidence.source.request.command_id, OPERATION)
        if source_entry is None:
            raise ValueError("successor start lacks selected source batch")
        source_evidence = _retained(source_entry[2])
        source_command = _command(source_evidence)
        if (
            source_evidence != evidence.source
            or source_entry[2].get("resulting") != evidence.source_resulting_commitment
        ):
            raise ValueError("successor start source commitment differs from selected batch")
        _selected_matches(runtime, source_entry[2], source_command, source_evidence)
    with read_connection(runtime._database) as connection:
        state = inspect_publication(connection, command, command.expected_head + 1)
        if state != "COMPLETE" and not (allow_pending_absent and state == "ABSENT"):
            raise ValueError("selected successor physical publication is incomplete")
        rows = connection.execute(
            "SELECT record_id,owner,schema_id,canonical_bytes FROM records "
            "WHERE tenant_id=? AND commit_sequence=? ORDER BY record_id",
            (command.tenant_id, command.expected_head + 1),
        ).fetchall()
    found = {(str(a), str(b), str(c)): bytes(d) for a, b, c, d in rows}
    expected_records = {
        (r.record_id, r.owner, r.schema_id): r.canonical_bytes for r in command.records
    }
    if found != expected_records and not (allow_pending_absent and state == "ABSENT" and not found):
        raise ValueError("selected successor physical rows differ from retained batch")
    return True


async def _finish_batch(runtime, entry):
    stored = {k: v for k, v in entry.items() if k not in {"decision_id", "decision_bytes"}}
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
        selected = _entry_for(runtime, command.idempotency_key)
        if selected is None:
            raise LoopRejected("selected successor batch disappeared after recovery")
        _selected_matches(runtime, selected[2], command, evidence)
    return evidence


async def _finish_start(runtime, entry):
    stored = {k: v for k, v in entry.items() if k not in {"decision_id", "decision_bytes"}}
    with runtime._authority_gate().hold():
        evidence = _retained(stored, START_EVIDENCE_FIELD, RetainedExecutionSuccessorStart)
        command = _start_command(evidence)
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
        selected = _entry_for(runtime, command.idempotency_key, START_OPERATION)
        if selected is None:
            raise LoopRejected("selected successor start disappeared after recovery")
        _selected_matches(runtime, selected[2], command, evidence, start=True)
    return evidence


def _find_replay(runtime, request: DriveInputRequestV1):
    fingerprint = request.original_driver_command_fingerprint()
    found = []
    for decision_id, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        if entry.get("kind") != "DECIDED" or entry.get("operation_kind") != OPERATION:
            continue
        evidence = _retained(entry)
        if (
            evidence.driver_request_fingerprint == fingerprint
            and evidence.driver_request_bytes == request.canonical_bytes()
            and DriveInputRequestV1.model_validate_json(evidence.driver_request_bytes).identity
            == request.identity
        ):
            entry["decision_id"], entry["decision_bytes"] = str(decision_id), raw
            found.append(entry)
    if len(found) > 1:
        raise LoopRejected("multiple successor publications claim this exact CLI request")
    return found[0] if found else None


def find_by_driver_request(runtime, driver_request: DriveInputRequestV1):
    """Return validated selected owner evidence, decision ID and physical batch."""
    with runtime._authority_gate().hold():
        entry = _find_replay(runtime, driver_request)
        if entry is None:
            return None
        evidence = _retained(entry)
        command = _command(evidence)
        _selected_matches(runtime, entry, command, evidence)
        return evidence, str(entry["decision_id"]), command


def find_started_by_driver_request(runtime, driver_request: DriveInputRequestV1):
    """Return the selected ACTIVE publication that created the first Turn."""
    with runtime._authority_gate().hold():
        source_entry = _find_replay(runtime, driver_request)
        if source_entry is None:
            return None
        source_evidence = _retained(source_entry)
        created = source_evidence.result.successor_run
        start_id = f"execution-successor-start:{created.run_id}:{created.head}"
        selected = _entry_for(runtime, start_id, START_OPERATION)
        if selected is None:
            return None
        evidence = _retained(selected[2], START_EVIDENCE_FIELD, RetainedExecutionSuccessorStart)
        command = _start_command(evidence)
        _selected_matches(runtime, selected[2], command, evidence, start=True)
        return evidence, selected[0], command


def _source_for(
    runtime,
    principal: str,
    driver_request: DriveInputRequestV1,
    admitted: SelectedAdmittedRunInput,
):
    port = source(runtime)
    collect = getattr(port, "collect_for_successor_held", None)
    observation = collect(runtime, driver_request, admitted) if callable(collect) else None
    if observation is not None:
        cut, suspension = observation
        run = cut.complete_ordered_run_lineage[-1]
        if isinstance(run, ExecutionRunRecord) and run.principal == principal:
            return cut, suspension, run
    # An unavailable complete cut is only ordinary genesis when authenticated
    # history proves there is no current suspended Run for this principal.
    from chiplog.composition.r14_loop_history import read_execution_history

    history = read_execution_history(runtime)
    principal_lineage = [r for r in history.records if r.principal == principal]
    if principal_lineage and principal_lineage[-1].state == "SUSPENDED":
        raise LoopRejected(
            "same-principal suspended Run exists but its selected recovery cut is incomplete; HOLD"
        )
    return None


async def drive_successor(
    runtime: CommonCliExecutionRuntime,
    peer: str,
    driver_request: DriveInputRequestV1,
    admitted: SelectedAdmittedRunInput | None,
) -> ExecutionRunRecord | None:
    """Publish/recover this authenticated CLI request as a successor if eligible."""
    async with runtime._execution_lane:
        actor = await runtime._execution_actor(peer)
        fingerprint = driver_request.original_driver_command_fingerprint()
        with runtime._authority_gate().hold():
            runtime._check_execution_actor(actor)
            replay = _find_replay(runtime, driver_request)
        if replay is not None:
            await _finish_batch(runtime, replay)
            source_evidence = _retained(replay)
            with runtime._authority_gate().hold():
                start_entry = _entry_for(
                    runtime,
                    f"execution-successor-start:{source_evidence.result.successor_run.run_id}:{source_evidence.result.successor_run.head}",
                    START_OPERATION,
                )
            if start_entry is not None:
                started = await _finish_start(runtime, start_entry[2])
                return started.result.run
            return await _publish_start(runtime, peer, source_evidence)

        if admitted is None:
            raise LoopRejected("selected admitted CLI input is required for a new successor")
        with runtime._authority_gate().hold():
            runtime._require_no_pending()
            if admitted.tenant_id != runtime._tenant_id or not admitted.database_id:
                raise LoopRejected("selected admitted input tenant/database is absent or differs")
            if not admitted.principal_id:
                raise LoopRejected("selected admitted principal is absent")
            _assert_ingress_unclaimed(runtime, driver_request)
            observation = _source_for(runtime, admitted.principal_id, driver_request, admitted)
            if observation is None:
                return None
            cut, suspension, current = observation
            if (
                cut.tenant_id != runtime._tenant_id
                or cut.database_id != runtime._database_identity[0]
            ):
                raise LoopRejected("successor selected recovery cut tenant/database differs")
            if current.principal != admitted.principal_id:
                return None
            predecessor = runtime._commitment_journal.load(runtime._tenant_id)
            if predecessor != cut.materialization_commitment:
                raise LoopRejected("successor recovery cut differs from current commitment")
            worker = runtime.current_worker()
            session = runtime._supervisor.runtime().session("agent_loop")
            command_id = f"execution-successor:{current.run_id}:{current.head}"
            create = CreateExecutionRun(
                command_id="create:successor:" + _run_id(admitted).rsplit(":", 1)[-1],
                tenant=runtime._tenant_id,
                principal=admitted.principal_id,
                run_id=_run_id(admitted),
                prompt=admitted.normalized_prompt,
                policy=current.policy,
                origin=admitted.origin,
                contour_head=admitted.contour_head,
                policy_head=current.policy_head,
                worker_session=worker,
            )
            request = PrepareExecutionSuccessor(
                command_id=command_id,
                run=current,
                original_suspension=suspension,
                cut=cut,
                disposition_version="1",
                successor=create,
                fence=NonSchedulerFence(
                    lineage=NotApplicable(),
                    physical_root=NotApplicable(),
                    lease=NotApplicable(),
                    clock_proof=NotApplicable(),
                    run_id=current.run_id,
                    run_head=current.head,
                    worker_session_id=current.worker_session,
                    runtime_generation=session.generation_id,
                ),
                admitted_input=admitted,
            )
            trust = runtime._trust.capture_verified_observation()
            decisions = runtime._loop_decisions().entries()
            decision_head = decisions[-1][:1] if decisions else ()
        result, owner_call, owner_reply = await _exchange(
            runtime, request, OWNER_OPERATION, PreparedExecutionSuccessor
        )
        refreshed, actor_budget = await issue_execution_recovery_actor_v1(runtime, peer)
        with runtime._authority_gate().hold():
            if (
                not runtime._same_execution_actor_identity(actor, refreshed)
                or runtime._trust.capture_verified_observation() != trust
            ):
                raise LoopRejected(
                    "successor actor or trust source changed during owner preparation"
                )
            actor = refreshed
        evidence = RetainedExecutionSuccessor(
            driver_request_bytes=driver_request.canonical_bytes(),
            driver_request_fingerprint=fingerprint,
            admitted=admitted,
            request=request,
            result=result,
            owner_call=owner_call,
            owner_reply=owner_reply,
            actor_budget_v1=actor_budget,
        )
        batch = _command(evidence)
        await _publish_batch(
            runtime,
            actor,
            worker,
            session,
            cut,
            suspension,
            predecessor,
            decision_head,
            evidence,
            batch,
        )
        entry = _entry_for(runtime, command_id)
        if entry is None:
            raise LoopRejected("selected successor batch disappeared after publication")
        return (await _publish_start(runtime, peer, evidence)).result.run


def _assert_ingress_unclaimed(runtime, request):
    claim = _ingress_claim(request)
    for _, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        if entry.get("kind") != "DECIDED":
            continue
        value = entry.get("inbox_initialization")
        if isinstance(value, str):
            from chiplog.composition.r14_execution_inbox_records import (
                RetainedInboxExecutionInitialization,
            )

            evidence = RetainedInboxExecutionInitialization.model_validate_json(value)
            prior = DriveInputRequestV1.model_validate_json(evidence.driver_request_bytes)
            if _ingress_claim(prior) == claim:
                raise LoopRejected("selected ingress already claimed by inbox initialization")
        retained = entry.get(EVIDENCE_FIELD)
        if isinstance(retained, str):
            prior = _retained(entry)
            prior_request = DriveInputRequestV1.model_validate_json(prior.driver_request_bytes)
            if _ingress_claim(prior_request) == claim:
                raise LoopRejected("selected ingress already claimed by successor")


def assert_ingress_unclaimed_by_successor(runtime, request, selected_claim: str) -> None:
    """Reject a selected ingress or deterministic Run ID owned by another command."""
    expected_fingerprint = request.original_driver_command_fingerprint()
    with runtime._authority_gate().hold():
        for _, _, raw in runtime._loop_decisions().entries():
            entry = json.loads(raw)
            if entry.get("kind") != "DECIDED" or entry.get("operation_kind") != OPERATION:
                continue
            evidence = _retained(entry)
            prior_request = DriveInputRequestV1.model_validate_json(evidence.driver_request_bytes)
            prior_claim = _ingress_claim(prior_request)
            if prior_claim == selected_claim and (
                evidence.driver_request_fingerprint != expected_fingerprint
                or evidence.driver_request_bytes != request.canonical_bytes()
                or prior_request.identity != request.identity
            ):
                raise LoopRejected(
                    "selected ingress or Run ID is already owned by another successor command"
                )


async def replay_successor(runtime, peer: str, driver_request: DriveInputRequestV1):
    """Finish an exact retained successor before live dispatch observation."""
    with runtime._authority_gate().hold():
        if _find_replay(runtime, driver_request) is None:
            return None
    return await drive_successor(runtime, peer, driver_request, None)


async def _publish_batch(
    runtime, actor, worker, session, cut, suspension, predecessor, decision_head, evidence, batch
):
    driver_request = DriveInputRequestV1.model_validate_json(evidence.driver_request_bytes)

    def guard():
        with runtime._authority_gate().hold():
            try:
                runtime._check_execution_actor(actor)
                current = _source_for(
                    runtime,
                    evidence.admitted.principal_id,
                    driver_request,
                    evidence.admitted,
                )
                if current != (cut, suspension, evidence.request.run):
                    return "STALE"
                classification = classify_execution_successor(evidence.request, evidence.result)
                if (
                    classification.disposition != "SUCCESSOR_REQUIRED"
                    or classification.source_request_fingerprint != evidence.request.digest()
                ):
                    return "STALE"
                if (
                    runtime.current_worker() != worker
                    or runtime._supervisor.runtime().session("agent_loop") != session
                ):
                    return "STALE"
                decisions = runtime._loop_decisions().entries()
                if (decisions[-1][:1] if decisions else ()) != decision_head:
                    return "STALE"
                if (
                    runtime._commitment_journal.load(runtime._tenant_id) != predecessor
                    or runtime._require_no_pending() is not None
                ):
                    return "STALE"
                if (
                    time.monotonic_ns()
                    >= evidence.actor_budget_v1.request.budget.absolute_deadline_ns
                ):
                    return "STALE"
            except ValueError, TypeError, RuntimeError, LoopRejected:
                return "STALE"
        return None

    def decide(resulting):
        with runtime._authority_gate().hold():
            guard_result = guard()
            if guard_result is not None or not resulting:
                raise LoopRejected("successor cut changed at final transaction guard")
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
                            "record_id": r.record_id,
                            "owner": r.owner,
                            "schema": r.schema_id,
                            "payload": base64.b64encode(r.canonical_bytes).decode(),
                            "digest": r.fingerprint,
                        }
                        for r in batch.records
                    ],
                    EVIDENCE_FIELD: evidence.canonical_bytes().decode(),
                }
            )

    result = await runtime._appender.submit(
        replace(batch, admission_guard=guard, decision_guard=decide)
    )
    if result.disposition not in ("COMMITTED", "REPLAY"):
        raise LoopRejected("execution successor publication " + result.disposition)
    selected = _entry_for(runtime, batch.idempotency_key)
    if selected is None:
        raise LoopRejected("selected successor decision absent")
    await _finish_batch(runtime, selected[2])


async def _publish_start(runtime, peer, source_evidence):
    """Prepare and publish the distinct evidence-bound CREATED -> ACTIVE step."""
    created = source_evidence.result.successor_run
    operation_id = f"execution-successor-start:{created.run_id}:{created.head}"
    actor = await runtime._execution_actor(peer)
    with runtime._authority_gate().hold():
        prior = _entry_for(runtime, operation_id, START_OPERATION)
    if prior is not None:
        return await _finish_start(runtime, prior[2])
    source_entry = _entry_for(runtime, source_evidence.request.command_id)
    if source_entry is None:
        raise LoopRejected("selected successor source batch disappeared before start")
    await _finish_batch(runtime, source_entry[2])
    with runtime._authority_gate().hold():
        runtime._require_no_pending()
        if runtime._commitment_journal.load(runtime._tenant_id) is None:
            raise LoopRejected("successor source commitment unavailable before first Turn")
        # The first batch is exactly one selected atomic publication after its cut.
        snapshot = runtime._commitment_journal.load(runtime._tenant_id)
        if not snapshot:
            raise LoopRejected("successor source commitment absent")
        current_seq = runtime._loop_decisions().entries()
        expected_decision_head = current_seq[-1][:1] if current_seq else ()
        request = PrepareExecutionSuccessorStart(
            command_id=operation_id,
            source_request=source_evidence.request,
            created_run=created,
            edge_bytes=source_evidence.result.edge.canonical_bytes(),
            initialization_bytes=source_evidence.result.complete_initialization_bytes,
        )
    result, call, reply = await _exchange(
        runtime, request, OWNER_START_OPERATION, PreparedExecutionSuccessorStart
    )
    refreshed, budget = await issue_execution_recovery_actor_v1(runtime, peer)
    with runtime._authority_gate().hold():
        if not runtime._same_execution_actor_identity(actor, refreshed):
            raise LoopRejected("successor start actor changed during owner preparation")
    evidence = RetainedExecutionSuccessorStart(
        source=source_evidence,
        source_resulting_commitment=str(source_entry[2]["resulting"]),
        request=request,
        result=result,
        owner_call=call,
        owner_reply=reply,
        actor_budget_v1=budget,
    )
    batch = _start_command(evidence)
    predecessor = snapshot

    def guard():
        with runtime._authority_gate().hold():
            try:
                runtime._check_execution_actor(actor)
                source_entry = _entry_for(runtime, source_evidence.request.command_id)
                if source_entry is None:
                    return "STALE"
                _selected_matches(
                    runtime, source_entry[2], _command(source_evidence), source_evidence
                )
                if runtime._commitment_journal.load(runtime._tenant_id) != predecessor:
                    return "STALE"
                if runtime._require_no_pending() is not None:
                    return "STALE"
                decisions = runtime._loop_decisions().entries()
                if (decisions[-1][:1] if decisions else ()) != expected_decision_head:
                    return "STALE"
                if time.monotonic_ns() >= budget.request.budget.absolute_deadline_ns:
                    return "STALE"
            except ValueError, TypeError, RuntimeError, LoopRejected:
                return "STALE"
        return None

    def decide(resulting):
        with runtime._authority_gate().hold():
            if guard() is not None or not resulting:
                raise LoopRejected("successor start source changed at transaction guard")
            runtime._append_decision(
                {
                    "version": 1,
                    "kind": "DECIDED",
                    "operation_id": operation_id,
                    "operation_kind": START_OPERATION,
                    "expected_head": batch.expected_head,
                    "fingerprint": batch.request_fingerprint,
                    "predecessor": predecessor,
                    "resulting": resulting,
                    "records": [
                        {
                            "record_id": r.record_id,
                            "owner": r.owner,
                            "schema": r.schema_id,
                            "payload": base64.b64encode(r.canonical_bytes).decode(),
                            "digest": r.fingerprint,
                        }
                        for r in batch.records
                    ],
                    START_EVIDENCE_FIELD: evidence.canonical_bytes().decode(),
                }
            )

    publication = await runtime._appender.submit(
        replace(batch, admission_guard=guard, decision_guard=decide)
    )
    if publication.disposition not in ("COMMITTED", "REPLAY"):
        raise LoopRejected("execution successor start publication " + publication.disposition)
    entry = _entry_for(runtime, operation_id, START_OPERATION)
    if entry is None:
        raise LoopRejected("selected successor start decision absent")
    return await _finish_start(runtime, entry[2])
