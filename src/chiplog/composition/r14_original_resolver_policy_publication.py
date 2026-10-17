"""Durable, actor-authenticated selection for the narrow R14 resolver policy."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import replace

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallPreparationRejected
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    AdoptOriginalResolverAuthorityPolicyV1,
    PreparedOriginalResolverAuthorityPolicyV1,
    original_effect_evidence_reducer_policy_v1,
    original_resolver_authority_policy_v1,
)
from chiplog.capabilities.agent_loop.original_resolver_policy_preparation import (
    prepare_original_resolver_authority_policy_v1,
)
from chiplog.capabilities.agent_loop.recovery_contracts import RecoveryDTO
from chiplog.capabilities.agent_loop.recovery_wire_contracts import (
    ORIGINAL_RECOVERY_REQUEST_SCHEMA,
    ORIGINAL_RECOVERY_RESULT_SCHEMA,
    decode_original_recovery_result,
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

OPERATION = "agent_loop.original-resolver-authority-policy.v1"
OWNER_OPERATION = "agent_loop.adopt_original_resolver_authority_policy.v1"
SCHEMA = "chiplog.loop.original-resolver-authority-policy.v1"
REDUCER_SCHEMA = "chiplog.loop.original-effect-evidence-reducer-policy.v1"
EVIDENCE_FIELD = "original_resolver_authority_policy_evidence"


class RetainedOriginalResolverPolicyPublication(RecoveryDTO):
    tenant_id: str
    database_id: str
    request: AdoptOriginalResolverAuthorityPolicyV1
    prepared: PreparedOriginalResolverAuthorityPolicyV1
    owner_call: PublicPortCall
    owner_reply: PublicPortSuccess
    actor_budget_v1: RecoveryActorBudgetV1
    expected_head: int
    predecessor: str


def command(evidence: RetainedOriginalResolverPolicyPublication) -> PhysicalPublicationCommand:
    request = AdoptOriginalResolverAuthorityPolicyV1.model_validate_json(
        evidence.request.canonical_bytes()
    )
    expected_policy = original_resolver_authority_policy_v1(evidence.tenant_id)
    expected = prepare_original_resolver_authority_policy_v1(request)
    reply = decode_original_recovery_result(
        ORIGINAL_RECOVERY_RESULT_SCHEMA, evidence.owner_reply.canonical_payload
    ).value
    validate_recovery_actor_budget(evidence.actor_budget_v1)
    if (
        request.policy != expected_policy
        or evidence.prepared != expected
        or isinstance(expected, CallPreparationRejected)
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
    ):
        raise ValueError("original resolver policy owner exchange or actor differs")
    policy_bytes = expected_policy.canonical_bytes()
    digest = expected_policy.digest()
    reducer_policy = original_effect_evidence_reducer_policy_v1(evidence.tenant_id)
    reducer_bytes = reducer_policy.canonical_bytes()
    return PhysicalPublicationCommand(
        tenant_id=evidence.tenant_id,
        operation_kind=OPERATION,
        idempotency_key="original-resolver-authority-policy:" + digest,
        request_fingerprint=request.digest(),
        expected_head=evidence.expected_head,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=(
            PhysicalRecord(
                expected_policy.policy_id,
                "agent_loop",
                SCHEMA,
                policy_bytes,
                hashlib.sha256(policy_bytes).hexdigest(),
            ),
            PhysicalRecord(
                reducer_policy.policy_id,
                "agent_loop",
                REDUCER_SCHEMA,
                reducer_bytes,
                hashlib.sha256(reducer_bytes).hexdigest(),
            ),
        ),
    )


def _validate_actor_history(runtime, receipt: RecoveryActorBudgetV1) -> None:
    from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
        ExactHead,
        HermeticTrustObservationV1,
    )

    validate_recovery_actor_budget(receipt)
    entries = runtime._trust._journal.entries()
    matches = [
        index
        for index, (identity, _, _) in enumerate(entries)
        if identity == receipt.trust_journal_head
    ]
    if len(matches) != 1:
        raise ValueError("selected resolver actor trust head absent or ambiguous")
    index = matches[0]
    logical = runtime._trust.owner_snapshot_entries()
    prefix = runtime._trust.historical_prefix(
        HermeticTrustObservationV1(
            physical_journal_head=ExactHead(
                identity="deployment-trust/journal",
                head=receipt.trust_journal_head,
                fingerprint=hashlib.sha256(entries[index][2]).hexdigest(),
            ),
            logical_snapshot_head=logical[index][0],
        )
    )
    result = runtime._decode_trust_result(receipt.response.canonical_payload)
    raw = result.reference_bytes
    if raw is None:
        raise ValueError("selected resolver actor lacks authenticated identity")
    identity = json.loads(raw)
    call = json.loads(receipt.request.canonical_payload)
    auth_request = json.loads(base64.b64decode(call["request_bytes"], validate=True))
    expected_uid = auth_request.get("peer_credential")
    if (
        prefix.snapshot_bytes != receipt.trust_snapshot_bytes
        or receipt.request.budget.absolute_deadline_ns <= receipt.issued_monotonic_ns
        or result.disposition != "VALID"
        or call.get("mode") != "AUTHENTICATE"
        or identity.get("tenant_id") != runtime._tenant_id
        or identity.get("principal_id") != "hermetic-principal"
        or identity.get("contour") != "CLI"
        or identity.get("source_head") != "local"
        or identity.get("peer_credential") != expected_uid
        or expected_uid != f"uid:{os.getuid()}"
    ):
        raise ValueError("selected resolver actor identity or trust prefix differs")


def _actor_matches_policy(runtime, evidence: RetainedOriginalResolverPolicyPublication) -> None:
    _validate_actor_history(runtime, evidence.actor_budget_v1)
    result = runtime._decode_trust_result(evidence.actor_budget_v1.response.canonical_payload)
    identity = json.loads(result.reference_bytes or b"{}")
    if (
        identity.get("tenant_id") != evidence.request.policy.tenant_id
        or identity.get("principal_id") != evidence.request.policy.principal_id
    ):
        raise ValueError("authenticated adoption principal differs from resolver policy")


def validate_selected(runtime, entry, batch):
    raw = entry.get(EVIDENCE_FIELD)
    if not isinstance(raw, str):
        raise ValueError("selected original resolver policy evidence missing")
    evidence = RetainedOriginalResolverPolicyPublication.model_validate_json(raw)
    if (
        evidence.canonical_bytes().decode() != raw
        or evidence.tenant_id != runtime._tenant_id
        or evidence.database_id != runtime._database_identity[0]
        or evidence.predecessor != entry.get("predecessor")
        or command(evidence) != batch
        or entry.get("operation_kind") != OPERATION
        or entry.get("operation_id") != batch.idempotency_key
    ):
        raise ValueError("selected original resolver policy differs from exact configuration")
    _actor_matches_policy(runtime, evidence)
    return evidence


def _selected_entries(runtime):
    matches = []
    for decision_id, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        if entry.get("kind") != "DECIDED":
            continue
        rows = entry.get("records")
        schema_match = isinstance(rows, list) and any(
            isinstance(row, dict) and row.get("schema") in (SCHEMA, REDUCER_SCHEMA) for row in rows
        )
        if entry.get("operation_kind") == OPERATION or EVIDENCE_FIELD in entry or schema_match:
            matches.append((decision_id, raw, entry, runtime._publication(entry)))
    if len(matches) > 1:
        raise ValueError("ambiguous original resolver policy selection")
    return matches


def _validate_physical_catalog(runtime, batch: PhysicalPublicationCommand | None) -> None:
    expected_records = () if batch is None else batch.records
    with read_connection(runtime._database) as connection:
        records = tuple(
            connection.execute(
                "SELECT record_id, owner, schema_id, canonical_bytes, commit_sequence "
                "FROM records WHERE tenant_id=? AND schema_id IN (?, ?) ORDER BY record_id",
                (runtime._tenant_id, SCHEMA, REDUCER_SCHEMA),
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
                raise ValueError("unselected original resolver policy physical records exist")
            return
        expected_records_rows = tuple(
            sorted(
                (
                    (
                        record.record_id,
                        record.owner,
                        record.schema_id,
                        record.canonical_bytes,
                        batch.expected_head + 1,
                    )
                    for record in expected_records
                ),
                key=lambda row: row[0],
            )
        )
        expected_publication = (
            batch.idempotency_key,
            batch.request_fingerprint,
            batch.expected_head + 1,
            "\n".join(record.record_id for record in expected_records),
        )
        actual_publications = tuple(
            (key, fingerprint, sequence, record_ids.split("\n"))
            for key, fingerprint, sequence, record_ids in publications
        )
        if records != expected_records_rows or actual_publications != (
            (*expected_publication[:3], expected_publication[3].split("\n")),
        ):
            raise ValueError("original resolver policy physical catalog differs from selection")


def read_selected_original_resolver_authority_policy_source_held(runtime, *, historical=False):
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
    historical_suffix = False
    if historical and actual != anchored:
        from chiplog.composition.r14_historical_suffix import (
            authenticate_selected_r14_source_cut_held,
        )

        historical_suffix = authenticate_selected_r14_source_cut_held(
            runtime, actual, anchored, target_commitment=str(entry.get("resulting"))
        )
    if (
        state != "COMPLETE"
        or (actual != anchored and not historical_suffix)
        or (not historical and actual != entry.get("resulting"))
    ):
        raise ValueError("selected original resolver policy selected/physical mismatch")
    _validate_physical_catalog(runtime, batch)
    if len(batch.records) != 2:
        raise ValueError("selected resolver policy catalog must contain exactly two records")
    record, reducer_record = batch.records
    reducer_policy = original_effect_evidence_reducer_policy_v1(runtime._tenant_id)
    if (
        record.canonical_bytes != evidence.request.policy.canonical_bytes()
        or reducer_record.canonical_bytes != reducer_policy.canonical_bytes()
        or reducer_record.schema_id != REDUCER_SCHEMA
    ):
        raise ValueError("selected resolver or reducer policy physical bytes differ")
    from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
    from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
    from chiplog.capabilities.agent_loop.recovery_contracts import Present

    digest = hashlib.sha256(record.canonical_bytes).hexdigest()
    selected_digest = hashlib.sha256(
        decision_bytes.encode() if isinstance(decision_bytes, str) else decision_bytes
    ).hexdigest()
    source = RecoverySourceRecord(
        owner=record.owner,
        subject=CallSubjectHead(
            subject_id=evidence.request.policy.policy_id,
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
    return evidence.request.policy, source


def read_selected_original_effect_evidence_reducer_policy_source_held(runtime, *, historical=False):
    selected = read_selected_original_resolver_authority_policy_source_held(
        runtime, historical=historical
    )
    if selected is None:
        return None
    policy = original_effect_evidence_reducer_policy_v1(runtime._tenant_id)
    decision_id, decision_bytes, _, batch = _selected_entries(runtime)[0]
    record = batch.records[1]
    from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
    from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
    from chiplog.capabilities.agent_loop.recovery_contracts import Present

    digest = hashlib.sha256(record.canonical_bytes).hexdigest()
    selected_digest = hashlib.sha256(
        decision_bytes.encode() if isinstance(decision_bytes, str) else decision_bytes
    ).hexdigest()
    source = RecoverySourceRecord(
        owner=record.owner,
        subject=CallSubjectHead(
            subject_id=policy.policy_id,
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
    return policy, source


def read_selected_original_resolver_authority_policy_held(runtime):
    selected = read_selected_original_resolver_authority_policy_source_held(
        runtime, historical=True
    )
    return None if selected is None else selected[0]


def _validate_startup_state(runtime, entry, batch) -> None:
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
    if state == "COMPLETE":
        from chiplog.composition.r14_historical_suffix import (
            authenticate_selected_r14_startup_suffix_held,
        )

        if authenticate_selected_r14_startup_suffix_held(
            runtime, actual, anchored, target_commitment=resulting
        ):
            _validate_physical_catalog(runtime, batch)
            return
    raise ValueError(
        "selected original resolver policy has partial or mismatched physical state: "
        f"state={state}, actual={actual}, anchored={anchored}, "
        f"predecessor={predecessor}, resulting={resulting}"
    )


def validate_policy_startup(runtime) -> None:
    runtime._pending()
    selected = _selected_entries(runtime)
    if not selected:
        _validate_physical_catalog(runtime, None)
        return
    _, _, entry, batch = selected[0]
    validate_selected(runtime, entry, batch)
    _validate_startup_state(runtime, entry, batch)


async def _owner_exchange(runtime, request):
    engine = runtime._supervisor.runtime()
    callee = engine.session("agent_loop")
    sent = PublicPortCall(
        operation_id=OWNER_OPERATION,
        request_id="original-resolver-policy:" + secrets.token_hex(16),
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
        raise LoopRejected("original resolver policy owner IPC unavailable")
    result = decode_original_recovery_result(reply.schema_id, reply.canonical_payload).value
    if not isinstance(result, PreparedOriginalResolverAuthorityPolicyV1):
        raise LoopRejected("original resolver policy owner rejected adoption")
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
        return read_selected_original_resolver_authority_policy_held(runtime)


async def publish_original_resolver_authority_policy(runtime, peer="hermetic-ingress"):
    observed, actor_budget = await issue_execution_recovery_actor_v1(runtime, peer)
    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        selected = _selected_entries(runtime)
        if selected:
            return read_selected_original_resolver_authority_policy_held(runtime)
        _validate_physical_catalog(runtime, None)
        runtime._pending()
        runtime._require_no_pending()
        predecessor = runtime._commitment_journal.load(runtime._tenant_id)
        if predecessor is None:
            raise LoopRejected("original resolver policy predecessor is not anchored")
        with read_connection(runtime._database) as connection:
            actual = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
            row = connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
            ).fetchone()
            sequence = 0 if row is None else int(row[0])
        if actual != predecessor:
            raise LoopRejected("original resolver policy predecessor differs from physical state")
        worker = runtime.current_worker()
        session = runtime._supervisor.runtime().session("agent_loop")
        request = AdoptOriginalResolverAuthorityPolicyV1(
            command_id="adopt-original-resolver-authority:" + runtime._tenant_id,
            policy=original_resolver_authority_policy_v1(runtime._tenant_id),
        )
    prepared, sent, reply = await _owner_exchange(runtime, request)
    evidence = RetainedOriginalResolverPolicyPublication(
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
        raise LoopRejected("original resolver policy evidence failed validation") from error

    def guard():
        with runtime._authority_gate().hold():
            try:
                runtime._check_execution_actor(observed)
                if (
                    runtime.current_worker() != worker
                    or runtime._supervisor.runtime().session("agent_loop") != session
                    or runtime._commitment_journal.load(runtime._tenant_id) != predecessor
                    or _selected_entries(runtime)
                ):
                    return "STALE"
            except ValueError, TypeError, RuntimeError:
                return "STALE"
        return None

    def decide(resulting):
        if guard() is not None:
            raise LoopRejected("original resolver policy source changed at selection")
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
        raise LoopRejected("original resolver policy publication " + result.disposition)
    with runtime._authority_gate().hold():
        selected = _selected_entries(runtime)
        if len(selected) != 1:
            raise LoopRejected("original resolver policy selected identity missing")
    return await _finish(runtime, selected[0][2])
