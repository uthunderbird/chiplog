"""Actual hermetic cancellation issuance, writer CAS and exact selected replay."""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Literal, cast

from chiplog.capabilities.agent_loop import call_acceptance_contracts as call
from chiplog.capabilities.agent_loop import domain
from chiplog.capabilities.agent_loop.contracts import LoopRejected, RunRecord
from chiplog.capabilities.agent_loop.recovery_contracts import (
    NonSchedulerFence,
    NotApplicable,
    Present,
)
from chiplog.composition.r14_cancellation_contracts import (
    CANCELLATION_OPERATION,
    CancelCallSubmission,
    HermeticCancellationPolicy,
    RetainedCancellationAct,
    RetainedCancellationPreparation,
    RetainedCancellationTrust,
)
from chiplog.composition.r14_cancellation_records import (
    act_reference,
    build_cancellation_envelope,
    cancellation_command,
    cancellation_identity,
)
from chiplog.composition.r14_fanout_records import inventory_from_history, reference
from chiplog.composition.r14_loop_history import read_call_history
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess

if TYPE_CHECKING:
    from chiplog.composition.r7_planning import ObservedTrustCall
    from chiplog.composition.r14_runtime import R14PlanningRuntime


def _authenticated(runtime: R14PlanningRuntime, observed: ObservedTrustCall) -> None:
    runtime._check_database_identity()
    if runtime._trust_observation_guard(observed) is not None:
        raise LoopRejected("cancellation authentication denied, stale or unavailable")
    raw = observed.result.reference_bytes
    if raw is None:
        raise LoopRejected("cancellation authentication reference absent")
    value = json.loads(raw)
    policy = HermeticCancellationPolicy()
    if (
        runtime._tenant_id != policy.tenant_id
        or value.get("tenant_id") != policy.tenant_id
        or value.get("principal_id") != policy.principal_id
        or value.get("contour") != "CLI"
        or value.get("source_head") != "local"
        or value.get("peer_credential") != f"uid:{os.getuid()}"
    ):
        raise LoopRejected("authenticated caller is outside hermetic cancellation policy")


def _selected(runtime: R14PlanningRuntime, identity: str) -> dict[str, object] | None:
    runtime._pending()
    entries = [json.loads(raw) for _, _, raw in runtime._loop_decisions().entries()]
    found = [
        entry
        for entry in entries
        if entry.get("kind") == "DECIDED" and entry.get("operation_id") == identity
    ]
    if len(found) > 1:
        raise LoopRejected("ambiguous cancellation selection")
    if not found:
        return None
    entry: dict[str, object] = found[0]
    if entry.get("operation_kind") != CANCELLATION_OPERATION:
        raise LoopRejected("cancellation act identity belongs to another operation")
    return entry


def _retained(entry: dict[str, object]) -> RetainedCancellationPreparation:
    raw = entry.get("cancellation_preparation")
    if not isinstance(raw, str):
        raise LoopRejected("selected cancellation lacks retained evidence")
    evidence = RetainedCancellationPreparation.model_validate_json(raw)
    if evidence.canonical_bytes().decode() != raw:
        raise LoopRejected("selected cancellation is not canonical")
    return evidence


async def _finish(runtime: R14PlanningRuntime, entry: dict[str, object]) -> RunRecord:
    evidence = _retained(entry)
    envelope = build_cancellation_envelope(evidence)
    command = cancellation_command(evidence)
    if (
        entry.get("cancellation_envelope") != envelope.canonical_bytes().decode()
        or runtime._publication(entry) != command
    ):
        raise LoopRejected("selected cancellation differs from complete original batch")
    await runtime._recover_exact(
        command,
        str(entry["predecessor"]),
        str(entry["resulting"]),
        is_materialized=lambda: runtime._loop_decision_materialized(command.idempotency_key, entry),
    )
    runtime._finish_decision(command.idempotency_key, expected=entry)
    # A replay is not authority to return a stale or partial unverified projection.
    read_call_history(runtime)
    return evidence.run_companion


async def cancel_call(
    runtime: R14PlanningRuntime,
    peer: str,
    submission: CancelCallSubmission,
) -> RunRecord:
    submission = CancelCallSubmission.model_validate_json(submission.canonical_bytes())
    if peer != HermeticCancellationPolicy().ingress:
        raise LoopRejected("cancellation requires registered hermetic ingress")
    observed = await runtime._observed_trust_call(
        "AUTHENTICATE",
        {
            "contour": "CLI",
            "credential_id": "hermetic-credential",
            "peer_credential": f"uid:{os.getuid()}",
            "session_id": "hermetic-session",
        },
    )
    identity = cancellation_identity(runtime._tenant_id, submission.act_id)
    with runtime._authority_gate().hold():
        _authenticated(runtime, observed)
        prior = _selected(runtime, identity)
        if prior is not None and _retained(prior).act.submission != submission:
            raise LoopRejected("cancellation act replay differs from original submission")
    if prior is not None:
        return await _finish(runtime, prior)

    with runtime._authority_gate().hold():
        _authenticated(runtime, observed)
        snapshot, fanout, cancellations = read_call_history(runtime)
        runs = [row for row in snapshot.records if row.run_id == submission.current_run.subject_id]
        if not runs:
            raise LoopRejected("cancellation Run absent")
        run = runs[-1]
        policy = HermeticCancellationPolicy()
        if (
            run.principal != policy.principal_id
            or run.state != "ACTIVE"
            or run.root_binding != "NOT_APPLICABLE"
            or run.worker_session != runtime.current_worker()
            or submission.current_run.revision != Present(head=run.head, fingerprint=run.digest())
        ):
            raise LoopRejected("cancellation Run or worker is stale or unsupported")
        inventory = inventory_from_history(runtime._tenant_id, snapshot, fanout, cancellations)
        rows = [
            row
            for row in inventory.ordered_calls
            if row.original_call_id == submission.original_call_id
        ]
        if (
            len(rows) != 1
            or rows[0].initialized != submission.initialized
            or rows[0].acceptance.kind != "INITIALIZED"
            or rows[0].terminal.kind != "ABSENT"
        ):
            raise LoopRejected("cancellation call is absent, accepted or already terminal")
        initialized = rows[0].initialized_record
        if (
            initialized.call.original.original_run_id != run.run_id
            or initialized.call.original.original_turn_id != domain.current_turn(run).turn_id
        ):
            raise LoopRejected("cancellation call differs from original Run/Turn")
        if (
            not isinstance(observed.response, PublicPortSuccess)
            or observed.result.reference_bytes is None
            or observed.observation.journal_head is None
        ):
            raise LoopRejected("cancellation authentication exchange unavailable")
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
            raise LoopRejected("cancellation authentication expired before preparation")
        predecessor = runtime._commitment_journal.load(runtime._tenant_id)
        if predecessor is None:
            raise LoopRejected("cancellation independent predecessor absent")
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
        request_id="cancel:" + secrets.token_hex(16),
        caller=caller,
        callee=callee,
        schema_id="chiplog.call.cancellation-preparation.v1",
        canonical_payload=request.canonical_bytes(),
        budget=CallBudget(
            remaining_calls=1, remaining_depth=1, absolute_deadline_ns=deadline, policy_version=1
        ),
    )
    returned = await engine.call(sent)
    if not isinstance(returned, PublicPortSuccess):
        raise LoopRejected("cancellation owner exchange rejected")
    proposal = call.PreparedPreAcceptCancellation.model_validate_json(returned.canonical_payload)
    companion = domain.terminal_tool(
        run, request.original.model_call_label, proposal.result.canonical_bytes().decode()
    )
    evidence = RetainedCancellationPreparation(
        act=act,
        trust=trust,
        request=request,
        proposal=proposal,
        run_predecessor=run,
        run_companion=companion,
        expected_snapshot_fingerprint=snapshot.digest(),
        owner_request=sent,
        owner_response=returned,
    )
    envelope = build_cancellation_envelope(evidence)
    command = cancellation_command(evidence)

    def guard() -> Literal["STALE"] | None:
        with runtime._authority_gate().hold():
            try:
                _authenticated(runtime, observed)
                if (
                    read_call_history(runtime) != (snapshot, fanout, cancellations)
                    or runtime.current_worker() != run.worker_session
                    or engine.session("agent_loop") != callee
                    or runtime._commitment_journal.load(runtime._tenant_id) != predecessor
                    or time.monotonic_ns() >= deadline
                    or cancellation_command(evidence) != command
                ):
                    return "STALE"
            except LoopRejected:
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
                    "operation_kind": CANCELLATION_OPERATION,
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
                    "cancellation_preparation": evidence.canonical_bytes().decode(),
                    "cancellation_envelope": envelope.canonical_bytes().decode(),
                }
            )

    result = await runtime._appender.submit(
        replace(command, admission_guard=guard, decision_guard=decide)
    )
    if result.disposition not in ("COMMITTED", "REPLAY"):
        raise LoopRejected("cancellation publication " + result.disposition)
    with runtime._authority_gate().hold():
        selected = _selected(runtime, identity)
        if selected is None or _retained(selected) != evidence:
            raise LoopRejected("selected cancellation differs from privately prepared candidate")
    return await _finish(runtime, selected)
