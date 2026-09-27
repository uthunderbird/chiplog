"""Mounted native pre-accept cancellation: authority, selection and recovery."""

from __future__ import annotations

import base64
import json
import secrets
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Literal, cast

from chiplog.capabilities.agent_loop import call_acceptance_contracts as call
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    NonSchedulerFence,
    NotApplicable,
    Present,
)
from chiplog.composition.r14_cancellation_contracts import (
    CancelCallSubmission,
    HermeticCancellationPolicy,
    RetainedCancellationAct,
    RetainedCancellationTrust,
)
from chiplog.composition.r14_cancellation_records import act_reference, cancellation_identity
from chiplog.composition.r14_execution_cancellation import (
    build_execution_cancellation_envelope,
    execution_cancellation_command,
)
from chiplog.composition.r14_execution_cancellation_contracts import (
    EXECUTION_CANCELLATION_OPERATION,
    ExecutionCancelledCallReceipt,
    RetainedExecutionCancellationPreparation,
)
from chiplog.composition.r14_execution_fanout_records import reference
from chiplog.composition.r14_loop_history import read_execution_call_history
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess

if TYPE_CHECKING:
    from chiplog.composition.r14_execution_runtime import R14ExecutionRuntime


def _selected(runtime: R14ExecutionRuntime, identity: str) -> dict[str, object] | None:
    runtime._pending()
    found = [
        entry
        for _, _, raw in runtime._loop_decisions().entries()
        if (entry := json.loads(raw)).get("kind") == "DECIDED"
        and entry.get("operation_id") == identity
    ]
    if len(found) > 1:
        raise LoopRejected("ambiguous execution cancellation selection")
    if not found:
        return None
    entry = cast(dict[str, object], found[0])
    if entry.get("operation_kind") != EXECUTION_CANCELLATION_OPERATION:
        raise LoopRejected("cancellation act identity belongs to another operation")
    return entry


def _retained(entry: dict[str, object]) -> RetainedExecutionCancellationPreparation:
    raw = entry.get("execution_cancellation_preparation")
    if not isinstance(raw, str):
        raise LoopRejected("selected execution cancellation lacks retained evidence")
    try:
        evidence = RetainedExecutionCancellationPreparation.model_validate_json(raw)
    except ValueError as error:
        raise LoopRejected("selected execution cancellation evidence is malformed") from error
    if evidence.canonical_bytes().decode() != raw:
        raise LoopRejected("selected execution cancellation is not canonical")
    return evidence


def _receipt(evidence: RetainedExecutionCancellationPreparation) -> ExecutionCancelledCallReceipt:
    return ExecutionCancelledCallReceipt(
        original_call_id=evidence.request.original_call_id,
        initialized=evidence.request.initialized,
        terminal=reference(evidence.proposal.terminal.terminal_id, evidence.proposal.terminal),
        result=reference(evidence.proposal.result.result_id, evidence.proposal.result),
        publication_id=evidence.request.command_id,
        publication_fingerprint=execution_cancellation_command(evidence).request_fingerprint,
    )


async def _finish(
    runtime: R14ExecutionRuntime, entry: dict[str, object]
) -> ExecutionCancelledCallReceipt:
    evidence = _retained(entry)
    envelope = build_execution_cancellation_envelope(evidence)
    command = execution_cancellation_command(evidence)
    if (
        entry.get("execution_cancellation_envelope") != envelope.canonical_bytes().decode()
        or runtime._publication(entry) != command
    ):
        raise LoopRejected("selected execution cancellation differs from complete original batch")
    await runtime._recover_exact(
        command,
        str(entry["predecessor"]),
        str(entry["resulting"]),
        is_materialized=lambda: runtime._loop_decision_materialized(command.idempotency_key, entry),
    )
    runtime._finish_decision(command.idempotency_key, expected=entry)
    # History owns selected semantics, including corruption of the two members.
    read_execution_call_history(runtime)
    return _receipt(evidence)


async def cancel_execution_call(
    runtime: R14ExecutionRuntime,
    peer: str,
    submission: CancelCallSubmission,
) -> ExecutionCancelledCallReceipt:
    """Authenticate one native call cancellation and publish exactly two records."""
    submission = CancelCallSubmission.model_validate_json(submission.canonical_bytes())
    policy = HermeticCancellationPolicy()
    if peer != policy.ingress:
        raise LoopRejected("execution cancellation requires registered hermetic ingress")
    observed = await runtime._execution_actor(peer)
    identity = cancellation_identity(runtime._tenant_id, submission.act_id)
    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        prior = _selected(runtime, identity)
        if prior is not None and _retained(prior).act.submission != submission:
            raise LoopRejected("execution cancellation act replay differs from original submission")
    if prior is not None:
        return await _finish(runtime, prior)

    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        snapshot, inventory, preparations = read_execution_call_history(runtime)
        runs = [
            row
            for row in snapshot.records
            if isinstance(row, ExecutionRunRecord)
            and row.run_id == submission.current_run.subject_id
        ]
        if not runs:
            raise LoopRejected("execution cancellation Run is absent")
        run = runs[-1]
        if (
            run.principal != policy.principal_id
            or run.state != "ACTIVE"
            or run.root_binding != "NOT_APPLICABLE"
            or run.worker_session != runtime.current_worker()
            or submission.current_run.revision != Present(head=run.head, fingerprint=run.digest())
        ):
            raise LoopRejected("execution cancellation Run or worker is stale or unsupported")
        rows = [
            row
            for row in inventory.ordered_calls
            if row.original_call_id == submission.original_call_id
        ]
        if (
            len(rows) != 1
            or rows[0].initialized != submission.initialized
            or rows[0].acceptance.kind != "INITIALIZED"
            or not isinstance(rows[0].terminal, Absent)
        ):
            raise LoopRejected(
                "execution cancellation call is absent, accepted or already terminal"
            )
        initialized = rows[0].initialized_record
        if (
            initialized.call.original.original_run_id != run.run_id
            or not run.turns
            or initialized.call.original.original_turn_id != run.turns[-1].turn_id
        ):
            raise LoopRejected("execution cancellation call differs from original Run/Turn")
        if (
            not isinstance(observed.response, PublicPortSuccess)
            or observed.result.reference_bytes is None
            or observed.observation.journal_head is None
        ):
            raise LoopRejected("execution cancellation authentication exchange unavailable")
        trust = RetainedCancellationTrust(
            snapshot_bytes=observed.observation.snapshot_bytes,
            journal_head=observed.observation.journal_head,
            bundle_path=observed.observation.bundle_path,
            sources=observed.observation.sources,
            request=observed.request,
            response=observed.response,
            authenticated_reference_bytes=observed.result.reference_bytes,
        )
        act = RetainedCancellationAct(
            submission=submission,
            policy=policy,
            authenticated_reference_bytes=trust.authenticated_reference_bytes,
            trust_evidence_fingerprint=trust.digest(),
        )
        engine = runtime._supervisor.runtime()
        callee = engine.session("agent_loop")
        caller = BrokerSession(
            tenant_id=runtime._tenant_id,
            broker_epoch=callee.broker_epoch,
            generation_id=callee.generation_id,
            owner_id="broker",
            session_id=f"broker:{callee.generation_id}",
        )
        now = time.monotonic_ns()
        deadline = min(now + 5_000_000_000, observed.request.budget.absolute_deadline_ns)
        if deadline <= now:
            raise LoopRejected("execution cancellation authentication expired before preparation")
        predecessor = runtime._commitment_journal.load(runtime._tenant_id)
        if predecessor is None:
            raise LoopRejected("execution cancellation independent predecessor absent")
        policy_ref = reference("cancellation-policy:v1", policy)
        sources = (
            (policy_ref, "POLICY", policy),
            (reference("cancellation-trust:" + submission.act_id, trust), "ACTOR", trust),
            (act_reference(act), "MANDATE", act),
        )
        cut = call.CallPreparationCut(
            tenant_id=runtime._tenant_id,
            current_run=submission.current_run,
            run_state="ACTIVE",
            tenant_commit_sequence=snapshot.tenant_head,
            materialization_commitment=predecessor,
            complete_call_inventory=reference("call-inventory:" + runtime._tenant_id, inventory),
            predecessor_inventory=inventory,
            authority_registry=policy_ref,
            sources=tuple(
                call.CallAuthorityObservation(
                    source_id=ref.subject_id,
                    family=cast(Literal["POLICY", "ACTOR", "MANDATE"], family),
                    source=ref,
                    generation=callee.generation_id,
                    frontier=str(snapshot.tenant_head),
                    canonical_value_base64=base64.b64encode(value.canonical_bytes()).decode(),
                    observed_at_ns=now,
                    valid_until_ns=deadline,
                )
                for ref, family, value in sources
            ),
            fence=NonSchedulerFence(
                lineage=NotApplicable(),
                physical_root=NotApplicable(),
                lease=NotApplicable(),
                clock_proof=NotApplicable(),
                run_id=run.run_id,
                run_head=run.head,
                worker_session_id=run.worker_session,
                runtime_generation=callee.generation_id,
            ),
        )
        request = call.CancelBeforeAcceptRequest(
            command_id=identity,
            original_call_id=submission.original_call_id,
            original=initialized.call.original,
            initialized=submission.initialized,
            initialized_record=initialized,
            cancellation_act=act_reference(act),
            cut=cut,
        )
    sent = PublicPortCall(
        operation_id="agent_loop.prepare_pre_accept_cancellation",
        request_id="execution-cancel:" + secrets.token_hex(16),
        caller=caller,
        callee=callee,
        schema_id="chiplog.call.cancellation-preparation.v1",
        canonical_payload=request.canonical_bytes(),
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            absolute_deadline_ns=deadline,
            policy_version=1,
        ),
    )
    returned = await engine.call(sent)
    if not isinstance(returned, PublicPortSuccess):
        raise LoopRejected("execution cancellation owner exchange rejected")
    proposal = call.PreparedPreAcceptCancellation.model_validate_json(returned.canonical_payload)
    evidence = RetainedExecutionCancellationPreparation(
        act=act,
        trust=trust,
        request=request,
        proposal=proposal,
        run_predecessor=run,
        expected_snapshot_fingerprint=snapshot.digest(),
        owner_request=sent,
        owner_response=returned,
    )
    envelope = build_execution_cancellation_envelope(evidence)
    command = execution_cancellation_command(evidence)

    def guard() -> Literal["STALE"] | None:
        with runtime._authority_gate().hold():
            try:
                runtime._check_execution_actor(observed)
                if (
                    read_execution_call_history(runtime) != (snapshot, inventory, preparations)
                    or runtime.current_worker() != run.worker_session
                    or engine.session("agent_loop") != callee
                    or runtime._commitment_journal.load(runtime._tenant_id) != predecessor
                    or time.monotonic_ns() >= deadline
                    or execution_cancellation_command(evidence) != command
                ):
                    return "STALE"
            except LoopRejected, ValueError:
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
                    "operation_kind": EXECUTION_CANCELLATION_OPERATION,
                    "expected_head": command.expected_head,
                    "fingerprint": command.request_fingerprint,
                    "predecessor": predecessor,
                    "resulting": resulting,
                    "records": [
                        {
                            "record_id": item.record_id,
                            "owner": item.owner,
                            "schema": item.schema_id,
                            "payload": base64.b64encode(item.canonical_bytes).decode(),
                            "digest": item.fingerprint,
                        }
                        for item in command.records
                    ],
                    "execution_cancellation_preparation": evidence.canonical_bytes().decode(),
                    "execution_cancellation_envelope": envelope.canonical_bytes().decode(),
                }
            )

    result = await runtime._appender.submit(
        replace(command, admission_guard=guard, decision_guard=decide)
    )
    if result.disposition not in ("COMMITTED", "REPLAY"):
        raise LoopRejected("execution cancellation publication " + result.disposition)
    with runtime._authority_gate().hold():
        selected = _selected(runtime, identity)
        if selected is None or _retained(selected) != evidence:
            raise LoopRejected(
                "selected execution cancellation differs from privately prepared candidate"
            )
    return await _finish(runtime, selected)
