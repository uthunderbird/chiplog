"""Selected physical record for a fresh R14 original-resolver invocation."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import replace

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    OriginalResolverInvocationV1,
    PreparedOriginalResolverInvocationV1,
    PrepareOriginalResolverInvocationV1,
)
from chiplog.capabilities.agent_loop.original_resolver_invocation_preparation import (
    prepare_original_resolver_invocation_v1,
)
from chiplog.capabilities.agent_loop.recovery_contracts import RecoveryDTO
from chiplog.capabilities.agent_loop.recovery_wire_contracts import (
    ORIGINAL_RECOVERY_REQUEST_SCHEMA,
    ORIGINAL_RECOVERY_RESULT_SCHEMA,
    decode_original_recovery_result,
)
from chiplog.composition.r14_loop_history import read_selected_original_obligation_sources
from chiplog.composition.r14_original_resolver_policy_publication import (
    _validate_actor_history,
)
from chiplog.composition.r14_recovery_publication import (
    RecoveryActorBudgetV1,
    issue_execution_recovery_actor_v1,
    validate_recovery_actor_budget,
)
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess
from chiplog.platform.publication_readback import inspect_publication
from chiplog.platform.workspace_snapshot import read_connection

OPERATION = "agent_loop.original-resolver-invocation.v1"
OWNER_OPERATION = "agent_loop.prepare_original_resolver_invocation.v1"
SCHEMA = "chiplog.loop.original-resolver-invocation.v1"
EVIDENCE_FIELD = "original_resolver_invocation_evidence"


class RetainedOriginalResolverInvocationPublication(RecoveryDTO):
    tenant_id: str
    database_id: str
    request: PrepareOriginalResolverInvocationV1
    prepared: PreparedOriginalResolverInvocationV1
    owner_call: PublicPortCall
    owner_reply: PublicPortSuccess
    actor_budget_v1: RecoveryActorBudgetV1
    expected_head: int
    predecessor: str


def _validate_invocation_actor(runtime, evidence) -> None:
    receipt = evidence.actor_budget_v1
    _validate_actor_history(runtime, receipt)
    result = runtime._decode_trust_result(receipt.response.canonical_payload)
    identity = json.loads(result.reference_bytes or b"{}")
    if (
        receipt.trust_journal_head != evidence.prepared.invocation.trust_journal_head
        or hashlib.sha256(receipt.response.canonical_payload).hexdigest()
        != evidence.prepared.invocation.trust_response_fingerprint
        or identity.get("tenant_id") != evidence.tenant_id
        or identity.get("principal_id") != "hermetic-principal"
    ):
        raise ValueError("selected resolver invocation differs from its authenticated actor")


def command(evidence: RetainedOriginalResolverInvocationPublication):
    request = PrepareOriginalResolverInvocationV1.model_validate_json(
        evidence.request.canonical_bytes()
    )
    prepared = prepare_original_resolver_invocation_v1(request)
    validate_recovery_actor_budget(evidence.actor_budget_v1)
    _validate_invocation_actor_fields(evidence.actor_budget_v1, request.invocation)
    reply = decode_original_recovery_result(
        ORIGINAL_RECOVERY_RESULT_SCHEMA, evidence.owner_reply.canonical_payload
    ).value
    if (
        request != evidence.request
        or prepared != evidence.prepared
        or not isinstance(prepared, PreparedOriginalResolverInvocationV1)
        or reply != evidence.prepared
        or evidence.owner_call.operation_id != OWNER_OPERATION
        or evidence.owner_call.schema_id != ORIGINAL_RECOVERY_REQUEST_SCHEMA
        or evidence.owner_call.canonical_payload != request.canonical_bytes()
        or evidence.owner_reply.request_id != evidence.owner_call.request_id
        or evidence.owner_reply.responder != evidence.owner_call.callee
        or evidence.owner_reply.schema_id != ORIGINAL_RECOVERY_RESULT_SCHEMA
        or evidence.owner_reply.canonical_payload != evidence.prepared.canonical_bytes()
        or evidence.owner_call.caller.owner_id != "broker"
        or evidence.owner_call.callee.owner_id != "agent_loop"
        or evidence.owner_call.caller.tenant_id != evidence.tenant_id
        or evidence.owner_call.callee.tenant_id != evidence.tenant_id
        or evidence.actor_budget_v1.request.caller.tenant_id != evidence.tenant_id
        or evidence.prepared.source_request_fingerprint != request.digest()
        or evidence.prepared.complete_batch_fingerprint
        != hashlib.sha256(evidence.prepared.invocation.canonical_bytes()).hexdigest()
    ):
        raise ValueError("original resolver invocation owner exchange or actor differs")
    invocation = evidence.prepared.invocation
    raw = invocation.canonical_bytes()
    return PhysicalPublicationCommand(
        tenant_id=evidence.tenant_id,
        operation_kind=OPERATION,
        idempotency_key=invocation.invocation_id,
        request_fingerprint=evidence.digest(),
        expected_head=evidence.expected_head,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=(
            PhysicalRecord(
                invocation.invocation_id,
                "agent_loop",
                SCHEMA,
                raw,
                hashlib.sha256(raw).hexdigest(),
            ),
        ),
    )


def _validate_invocation_actor_fields(receipt, invocation) -> None:
    validate_recovery_actor_budget(receipt)
    if (
        receipt.trust_journal_head != invocation.trust_journal_head
        or hashlib.sha256(receipt.response.canonical_payload).hexdigest()
        != invocation.trust_response_fingerprint
    ):
        raise ValueError("resolver invocation trust reference differs from retained actor receipt")


def _current_sources(runtime):
    from chiplog.composition.r14_original_resolver_policy_publication import (
        read_selected_original_effect_evidence_reducer_policy_source_held,
        read_selected_original_resolver_authority_policy_source_held,
    )

    opened = read_selected_original_obligation_sources(runtime)
    resolver = read_selected_original_resolver_authority_policy_source_held(runtime)
    reducer = read_selected_original_effect_evidence_reducer_policy_source_held(runtime)
    if opened is None or resolver is None or reducer is None:
        return None
    opening, terminal = opened
    return opening, terminal, resolver[1], reducer[1]


def _selected_entries(runtime):
    matches = []
    for decision_id, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        if entry.get("kind") != "DECIDED":
            continue
        rows = entry.get("records")
        schema_match = isinstance(rows, list) and any(
            isinstance(row, dict) and row.get("schema") == SCHEMA for row in rows
        )
        if entry.get("operation_kind") == OPERATION or EVIDENCE_FIELD in entry or schema_match:
            matches.append((decision_id, raw, entry, runtime._publication(entry)))
    if len(matches) > 1:
        raise ValueError("ambiguous original resolver invocation selection")
    return matches


def _validate_physical_catalog(runtime, batch: PhysicalPublicationCommand | None) -> None:
    with read_connection(runtime._database) as connection:
        records = tuple(
            connection.execute(
                "SELECT record_id, owner, schema_id, canonical_bytes, commit_sequence "
                "FROM records WHERE tenant_id=? AND schema_id=? ORDER BY record_id",
                (runtime._tenant_id, SCHEMA),
            ).fetchall()
        )
        publications = tuple(
            connection.execute(
                "SELECT idempotency_key, request_fingerprint, commit_sequence, record_ids "
                "FROM publications WHERE tenant_id=? AND operation_kind=? "
                "ORDER BY idempotency_key",
                (runtime._tenant_id, OPERATION),
            ).fetchall()
        )
    if batch is None:
        if records or publications:
            raise ValueError("unselected original resolver invocation physical records exist")
        return
    record = batch.records[0]
    expected_record = (
        record.record_id,
        record.owner,
        record.schema_id,
        record.canonical_bytes,
        batch.expected_head + 1,
    )
    actual_publications = tuple(
        (key, fingerprint, sequence, record_ids.split("\n"))
        for key, fingerprint, sequence, record_ids in publications
    )
    expected_publication = (
        batch.idempotency_key,
        batch.request_fingerprint,
        batch.expected_head + 1,
        [record.record_id],
    )
    if records != (expected_record,) or actual_publications != (expected_publication,):
        raise ValueError("original resolver invocation physical catalog differs from selection")


def validate_selected(runtime, entry, batch):
    raw = entry.get(EVIDENCE_FIELD)
    if not isinstance(raw, str):
        raise ValueError("selected original resolver invocation evidence missing")
    evidence = RetainedOriginalResolverInvocationPublication.model_validate_json(raw)
    invocation = evidence.prepared.invocation
    sources = _current_sources(runtime)
    if sources is None:
        raise ValueError("selected original resolver invocation sources are absent")
    if (
        evidence.canonical_bytes().decode() != raw
        or evidence.tenant_id != runtime._tenant_id
        or evidence.database_id != runtime._database_identity[0]
        or evidence.predecessor != entry.get("predecessor")
        or command(evidence) != batch
        or entry.get("operation_kind") != OPERATION
        or entry.get("operation_id") != batch.idempotency_key
        or invocation.tenant_id != runtime._tenant_id
        or (
            invocation.accepted_opening_source,
            invocation.accepted_terminal_source,
            invocation.resolver_policy_source,
            invocation.reducer_policy_source,
        )
        != sources
    ):
        raise ValueError("selected original resolver invocation differs from exact current sources")
    _validate_invocation_actor(runtime, evidence)
    return evidence


def read_selected_original_resolver_invocation_source_held(runtime):
    runtime._pending()
    selected = _selected_entries(runtime)
    if not selected:
        _validate_physical_catalog(runtime, None)
        return None
    decision_id, decision_bytes, entry, batch = selected[0]
    evidence = validate_selected(runtime, entry, batch)
    with read_connection(runtime._database) as connection:
        state = inspect_publication(connection, batch, batch.expected_head + 1)
        actual = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
    anchored = runtime._commitment_journal.load(runtime._tenant_id)
    if state != "COMPLETE" or actual != anchored or actual != entry.get("resulting"):
        raise ValueError("selected original resolver invocation selected/physical mismatch")
    _validate_physical_catalog(runtime, batch)
    record = batch.records[0]
    digest = hashlib.sha256(record.canonical_bytes).hexdigest()
    selected_digest = hashlib.sha256(
        decision_bytes.encode() if isinstance(decision_bytes, str) else decision_bytes
    ).hexdigest()
    from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
    from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
    from chiplog.capabilities.agent_loop.recovery_contracts import Present

    source = RecoverySourceRecord(
        owner=record.owner,
        subject=CallSubjectHead(
            subject_id=evidence.prepared.invocation.invocation_id,
            revision=Present(head="record:" + digest, fingerprint=digest),
        ),
        schema_id=record.schema_id,
        canonical_record_bytes=record.canonical_bytes,
        selected_decision=CallSubjectHead(
            subject_id=decision_id,
            revision=Present(head=decision_id, fingerprint=selected_digest),
        ),
        physical_record=CallSubjectHead(
            subject_id=record.record_id,
            revision=Present(head="record:" + digest, fingerprint=digest),
        ),
    )
    return evidence.prepared.invocation, source


def _startup_state(runtime, entry, batch):
    with read_connection(runtime._database) as connection:
        state = inspect_publication(connection, batch, batch.expected_head + 1)
        actual = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
    anchored = runtime._commitment_journal.load(runtime._tenant_id)
    predecessor, resulting = str(entry["predecessor"]), str(entry["resulting"])
    materialized = any(
        json.loads(raw).get("kind") == "MATERIALIZED"
        and json.loads(raw).get("operation_id") == batch.idempotency_key
        for _, _, raw in runtime._loop_decisions().entries()
    )
    if state == "ABSENT" and not materialized and actual == predecessor and anchored == predecessor:
        _validate_physical_catalog(runtime, None)
        return
    if (
        state == "COMPLETE"
        and actual == resulting
        and (anchored == resulting or (not materialized and anchored == predecessor))
    ):
        _validate_physical_catalog(runtime, batch)
        return
    raise ValueError("selected original resolver invocation has partial physical state")


def validate_invocation_startup(runtime) -> None:
    runtime._pending()
    selected = _selected_entries(runtime)
    if not selected:
        _validate_physical_catalog(runtime, None)
        return
    _, _, entry, batch = selected[0]
    validate_selected(runtime, entry, batch)
    _startup_state(runtime, entry, batch)


async def _owner_exchange(runtime, request):
    engine = runtime._supervisor.runtime()
    callee = engine.session("agent_loop")
    sent = PublicPortCall(
        operation_id=OWNER_OPERATION,
        request_id="original-resolver-invocation:" + secrets.token_hex(16),
        caller=BrokerSession(
            tenant_id=runtime._tenant_id,
            broker_epoch=callee.broker_epoch,
            generation_id=callee.generation_id,
            owner_id="broker",
            session_id=f"broker:{callee.generation_id}",
        ),
        callee=callee,
        schema_id=ORIGINAL_RECOVERY_REQUEST_SCHEMA,
        canonical_payload=request.canonical_bytes(),
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            policy_version=1,
            absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
        ),
    )
    reply = await engine.call(sent)
    if (
        not isinstance(reply, PublicPortSuccess)
        or reply.request_id != sent.request_id
        or reply.responder != sent.callee
        or reply.schema_id != ORIGINAL_RECOVERY_RESULT_SCHEMA
        or time.monotonic_ns() >= sent.budget.absolute_deadline_ns
    ):
        raise LoopRejected("original resolver invocation owner IPC unavailable")
    result = decode_original_recovery_result(reply.schema_id, reply.canonical_payload).value
    if not isinstance(result, PreparedOriginalResolverInvocationV1):
        raise LoopRejected("original resolver invocation owner rejected preparation")
    return result, sent, reply


async def _finish(runtime, entry):
    batch = runtime._publication(entry)
    with runtime._authority_gate().hold():
        validate_selected(runtime, entry, batch)
    await runtime._recover_exact(
        batch,
        str(entry["predecessor"]),
        str(entry["resulting"]),
        is_materialized=lambda: runtime._loop_decision_materialized(batch.idempotency_key, entry),
    )
    runtime._finish_decision(batch.idempotency_key, expected=entry)
    with runtime._authority_gate().hold():
        selected = read_selected_original_resolver_invocation_source_held(runtime)
        return None if selected is None else selected[0]


async def publish_original_resolver_invocation(runtime, peer="hermetic-ingress"):
    with runtime._authority_gate().hold():
        selected = _selected_entries(runtime)
        if selected:
            validate_selected(runtime, selected[0][2], selected[0][3])
            selected_entry = selected[0][2]
        else:
            selected_entry = None
    if selected_entry is not None:
        return await _finish(runtime, selected_entry)
    observed, actor_budget = await issue_execution_recovery_actor_v1(runtime, peer)
    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        selected = _selected_entries(runtime)
        if selected:
            validate_selected(runtime, selected[0][2], selected[0][3])
            selected_entry = selected[0][2]
        else:
            selected_entry = None
        sources = None if selected_entry is not None else _current_sources(runtime)
        if selected_entry is not None:
            pass
        elif sources is None:
            raise LoopRejected("original resolver invocation sources are incomplete")
        if selected_entry is None:
            opening, terminal, resolver_source, reducer_source = sources
            from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
                AcceptedOpenRecoveryOpeningV1,
            )

            original = AcceptedOpenRecoveryOpeningV1.model_validate_json(
                opening.canonical_record_bytes
            ).original
            invocation_id = (
                "r14-resolver-invocation:" + runtime._tenant_id + ":" + original.obligation_id
            )
            identity = json.loads(
                runtime._decode_trust_result(
                    actor_budget.response.canonical_payload
                ).reference_bytes
                or b"{}"
            )
            invocation = OriginalResolverInvocationV1(
                invocation_id=invocation_id,
                tenant_id=runtime._tenant_id,
                original=original,
                accepted_opening_source=opening,
                accepted_terminal_source=terminal,
                resolver_policy_source=resolver_source,
                reducer_policy_source=reducer_source,
                principal_id=identity.get("principal_id"),
                trust_journal_head=actor_budget.trust_journal_head,
                trust_response_fingerprint=hashlib.sha256(
                    actor_budget.response.canonical_payload
                ).hexdigest(),
            )
            request = PrepareOriginalResolverInvocationV1(
                command_id=invocation_id,
                invocation=invocation,
            )
            _validate_invocation_actor_fields(actor_budget, invocation)
            predecessor = runtime._commitment_journal.load(runtime._tenant_id)
            if predecessor is None:
                raise LoopRejected("original resolver invocation predecessor is not anchored")
            with read_connection(runtime._database) as connection:
                actual = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
                row = connection.execute(
                    "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
                ).fetchone()
                sequence = 0 if row is None else int(row[0])
            if actual != predecessor:
                raise LoopRejected(
                    "original resolver invocation predecessor differs from physical state"
                )
            worker = runtime.current_worker()
            session = runtime._supervisor.runtime().session("agent_loop")
    if selected_entry is not None:
        return await _finish(runtime, selected_entry)
    prepared, sent, reply = await _owner_exchange(runtime, request)
    evidence = RetainedOriginalResolverInvocationPublication(
        tenant_id=runtime._tenant_id,
        database_id=runtime._database_identity[0],
        request=request,
        prepared=prepared,
        owner_call=sent,
        owner_reply=reply,
        actor_budget_v1=actor_budget,
        expected_head=sequence,
        predecessor=predecessor,
    )
    try:
        command_value = command(evidence)
    except (TypeError, ValueError) as error:
        raise LoopRejected("original resolver invocation evidence failed validation") from error

    def guard():
        with runtime._authority_gate().hold():
            try:
                runtime._check_execution_actor(observed)
                if (
                    runtime.current_worker() != worker
                    or runtime._supervisor.runtime().session("agent_loop") != session
                    or runtime._commitment_journal.load(runtime._tenant_id) != predecessor
                    or _selected_entries(runtime)
                    or _current_sources(runtime)
                    != (
                        invocation.accepted_opening_source,
                        invocation.accepted_terminal_source,
                        invocation.resolver_policy_source,
                        invocation.reducer_policy_source,
                    )
                ):
                    return "STALE"
            except ValueError, TypeError, RuntimeError:
                return "STALE"
        return None

    def decide(resulting):
        if guard() is not None:
            raise LoopRejected("original resolver invocation sources changed at selection")
        runtime._require_no_pending()
        runtime._append_decision(
            {
                "version": 1,
                "kind": "DECIDED",
                "operation_id": command_value.idempotency_key,
                "operation_kind": OPERATION,
                "expected_head": command_value.expected_head,
                "fingerprint": command_value.request_fingerprint,
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
                    for record in command_value.records
                ],
                EVIDENCE_FIELD: evidence.canonical_bytes().decode(),
            }
        )

    result = await runtime._appender.submit(
        replace(command_value, admission_guard=guard, decision_guard=decide)
    )
    if result.disposition not in ("COMMITTED", "REPLAY"):
        raise LoopRejected("original resolver invocation publication " + result.disposition)
    with runtime._authority_gate().hold():
        selected = _selected_entries(runtime)
        if len(selected) != 1:
            raise LoopRejected("original resolver invocation selected identity missing")
    return await _finish(runtime, selected[0][2])
