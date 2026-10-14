"""Sole-writer publication of one authenticated native response-seal V3 result."""

from __future__ import annotations

import base64
import json
import secrets
import time
from dataclasses import replace
from typing import Literal

from pydantic import TypeAdapter

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.native_sealed_accounting_v3_contracts import (
    NATIVE_SEALED_ACCOUNTING_V3_REQUEST_SCHEMA,
    NATIVE_SEALED_ACCOUNTING_V3_RESULT_SCHEMA,
    PreparedNativeSealedAccountingV3,
)
from chiplog.composition.r14_loop_history import read_execution_recovery_history
from chiplog.composition.r14_native_accounting_records import (
    NATIVE_SEALED_ACCOUNTING_V3_OPERATION,
    NATIVE_SEALED_ACCOUNTING_V3_PUBLICATION_OPERATION,
    NativeSealedAccountingSourceStampV3,
    RetainedNativeSealedAccountingV3Publication,
    sealed_accounting_v3_command,
    sealed_accounting_v3_envelope,
    sealed_accounting_v3_record,
)
from chiplog.composition.r14_native_preparation_bindings import bind_native_execution_preparation_v2
from chiplog.composition.r14_native_selected_accounting_source import (
    R14NativeSelectedAccountingSource,
    R14NativeSelectedAccountingSourceFacts,
)
from chiplog.composition.r14_original_prepare_source import R14OriginalNativePrepareSource
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess
from chiplog.platform.workspace_snapshot import read_connection


def _selected(runtime, identity: str) -> dict[str, object] | None:
    runtime._pending()
    entries = [
        json.loads(raw)
        for _, _, raw in runtime._loop_decisions().entries()
        if (entry := json.loads(raw)).get("kind") == "DECIDED"
        and entry.get("operation_id") == identity
    ]
    if len(entries) > 1:
        raise LoopRejected("ambiguous native sealed accounting V3 selection")
    if not entries:
        return None
    entry = entries[0]
    if entry.get("operation_kind") != NATIVE_SEALED_ACCOUNTING_V3_PUBLICATION_OPERATION:
        raise LoopRejected("native sealed accounting V3 identity belongs to another operation")
    return entry


def _authenticated_accepted_open_recoveries(runtime) -> tuple:
    """Read recovery envelopes after the caller authenticated their outer history."""
    from chiplog.composition.r14_accepted_open_accounting_records import (
        ACCEPTED_OPEN_RECOVERY_OPERATION,
        retained_accepted_open_recovery,
    )

    found = tuple(
        retained_accepted_open_recovery(entry)
        for _, _, raw in runtime._loop_decisions().entries()
        if (entry := json.loads(raw)).get("kind") == "DECIDED"
        and entry.get("operation_kind") == ACCEPTED_OPEN_RECOVERY_OPERATION
    )
    if len(found) > 1:
        raise LoopRejected("ambiguous accepted/open recovery selection")
    return found


async def _finish(runtime, entry: dict[str, object]):
    from chiplog.composition.r14_loop_history import read_selected_native_sealed_accounting_v3
    from chiplog.composition.r14_native_sealed_accounting_v3_history import (
        retained_selected_native_sealed_accounting_v3,
    )

    raw = entry.get("native_sealed_accounting_v3_evidence")
    if not isinstance(raw, str):
        raise LoopRejected("selected native sealed accounting V3 lacks retained evidence")
    evidence = retained_selected_native_sealed_accounting_v3(entry)
    command = sealed_accounting_v3_command(evidence)
    envelope = sealed_accounting_v3_envelope(evidence)
    if (
        evidence.canonical_bytes().decode() != raw
        or entry.get("native_sealed_accounting_v3_envelope") != envelope.canonical_bytes().decode()
        or runtime._publication(entry) != command
    ):
        raise LoopRejected("selected native sealed accounting V3 differs from original command")
    await runtime._recover_exact(
        command,
        str(entry["predecessor"]),
        str(entry["resulting"]),
        is_materialized=lambda: runtime._loop_decision_materialized(command.idempotency_key, entry),
    )
    runtime._finish_decision(command.idempotency_key, expected=entry)
    selected = read_selected_native_sealed_accounting_v3(
        runtime, evidence.request.selected_response.revision.fingerprint
    )
    if selected != evidence.record:
        raise LoopRejected("selected native sealed accounting V3 was not read after publication")
    return selected


async def publish_native_sealed_accounting_v3(
    runtime,
    selected_response,
    peer: str = "hermetic-ingress",
):
    """Prepare one exact per-seal result, CAS its live source and publish atomically."""
    identity = "native-sealed-accounting:" + selected_response.revision.fingerprint
    with runtime._authority_gate().hold():
        prior = _selected(runtime, identity)
    if prior is not None:
        return await _finish(runtime, prior)
    observed_actor = await runtime._execution_actor(peer)
    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed_actor)
        runtime._pending()
        history = read_execution_recovery_history(runtime, selected_only=True)
        accepted_open_recoveries = _authenticated_accepted_open_recoveries(runtime)
        projection = R14OriginalNativePrepareSource(runtime).collect(selected_response)
        if projection is None:
            raise LoopRejected("native sealed accounting V3 Prepare source is unavailable")
        source = R14NativeSelectedAccountingSource(
            runtime, projection, bind_native_execution_preparation_v2(projection)
        )
        facts = source.collect(
            projection.prepared_run,
            selected_only=True,
            authenticated_history=history,
            authenticated_projection=True,
        )
        request = source.prepare_sealed_accounting_v3(
            selected_response,
            authenticated_history=history,
            authenticated_accepted_open_recoveries=accepted_open_recoveries,
            authenticated_projection=True,
        )
        if facts is None or request is None:
            raise LoopRejected("native sealed accounting V3 source is incomplete")
        with read_connection(runtime._database) as connection:
            if not connection.in_transaction:
                raise LoopRejected("native sealed accounting V3 source snapshot is not stable")
            commitment = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
            row = connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
            ).fetchone()
            journal = runtime._commitment_journal.load(runtime._tenant_id)
        if row is None or journal != commitment:
            raise LoopRejected("native sealed accounting V3 authority stamp is unavailable")
        stamp = NativeSealedAccountingSourceStampV3(
            tenant_id=runtime._tenant_id,
            database_identity=runtime._database_identity,
            tenant_commit_sequence=int(row[0]),
            authority_materialization_commitment=commitment,
        )
        identity = "native-sealed-accounting:" + selected_response.revision.fingerprint
        if _selected(runtime, identity) is not None:
            raise LoopRejected("native sealed accounting V3 selection changed during source read")
        worker = runtime.current_worker()
        session = runtime._supervisor.runtime().session("agent_loop")

    engine = runtime._supervisor.runtime()
    callee = engine.session("agent_loop")
    caller = BrokerSession(
        tenant_id=runtime._tenant_id,
        broker_epoch=callee.broker_epoch,
        generation_id=callee.generation_id,
        owner_id="broker",
        session_id=f"broker:{callee.generation_id}",
    )
    owner_call = PublicPortCall(
        operation_id=NATIVE_SEALED_ACCOUNTING_V3_OPERATION,
        request_id="native-sealed-accounting-v3:" + secrets.token_hex(16),
        caller=caller,
        callee=callee,
        schema_id=NATIVE_SEALED_ACCOUNTING_V3_REQUEST_SCHEMA,
        canonical_payload=request.canonical_bytes(),
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            policy_version=1,
            absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
        ),
    )
    reply = await engine.call(owner_call)
    if (
        not isinstance(reply, PublicPortSuccess)
        or reply.request_id != owner_call.request_id
        or reply.responder != callee
        or reply.schema_id != NATIVE_SEALED_ACCOUNTING_V3_RESULT_SCHEMA
    ):
        raise LoopRejected("native sealed accounting V3 owner exchange was rejected")
    response = PreparedNativeSealedAccountingV3.model_validate_json(reply.canonical_payload)
    if response.canonical_bytes() != reply.canonical_payload:
        raise LoopRejected("native sealed accounting V3 owner result is noncanonical")

    source_adapter = TypeAdapter(R14NativeSelectedAccountingSourceFacts)
    evidence = RetainedNativeSealedAccountingV3Publication(
        request=request,
        response=response,
        owner_request=owner_call,
        owner_response=reply,
        selected_source_bytes=source_adapter.dump_json(facts),
        physical_source_bytes=source_adapter.dump_json(facts),
        source_stamp=stamp,
        record=sealed_accounting_v3_record(request, response, stamp),
    )
    command = sealed_accounting_v3_command(evidence)
    envelope = sealed_accounting_v3_envelope(evidence)
    refreshed_actor = await runtime._execution_actor(peer)
    with runtime._authority_gate().hold():
        if not runtime._same_execution_actor_identity(observed_actor, refreshed_actor):
            raise LoopRejected("native sealed accounting V3 authenticated actor changed")
        runtime._check_execution_actor(refreshed_actor)
        if (
            runtime._database_identity != stamp.database_identity
            or runtime.current_worker() != worker
            or runtime._supervisor.runtime().session("agent_loop") != session
            or runtime._commitment_journal.load(runtime._tenant_id) != commitment
        ):
            raise LoopRejected("native sealed accounting V3 source stamp changed after owner IPC")
        current_history = read_execution_recovery_history(runtime, selected_only=True)
        current_open_recoveries = _authenticated_accepted_open_recoveries(runtime)
        current_projection = R14OriginalNativePrepareSource(runtime).collect(selected_response)
        if current_projection != projection:
            raise LoopRejected("native sealed accounting V3 Prepare changed after owner IPC")
        current_source = R14NativeSelectedAccountingSource(
            runtime, current_projection, bind_native_execution_preparation_v2(current_projection)
        )
        current_facts = current_source.collect(
            current_projection.prepared_run,
            selected_only=True,
            authenticated_history=current_history,
            authenticated_projection=True,
        )
        current_request = current_source.prepare_sealed_accounting_v3(
            selected_response,
            authenticated_history=current_history,
            authenticated_accepted_open_recoveries=current_open_recoveries,
            authenticated_projection=True,
        )
        with read_connection(runtime._database) as connection:
            current_commitment = capture_authority_snapshot_commitment(
                connection, runtime._tenant_id
            )
            current_head = connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
            ).fetchone()
            if (
                current_facts != facts
                or current_request != request
                or current_commitment != commitment
                or current_head is None
                or int(current_head[0]) != stamp.tenant_commit_sequence
            ):
                raise LoopRejected("native sealed accounting V3 source changed after owner IPC")


    final_actor = await runtime._execution_actor(peer)
    with runtime._authority_gate().hold():
        if not runtime._same_execution_actor_identity(refreshed_actor, final_actor):
            raise LoopRejected("native sealed accounting V3 authenticated actor changed")
        runtime._check_execution_actor(final_actor)
    refreshed_actor = final_actor

    def guard() -> Literal["STALE"] | None:
        with runtime._authority_gate().hold():
            try:
                runtime._check_execution_actor(refreshed_actor)
                if (
                    runtime._database_identity != stamp.database_identity
                    or runtime.current_worker() != worker
                    or runtime._supervisor.runtime().session("agent_loop") != session
                    or runtime._commitment_journal.load(runtime._tenant_id) != commitment
                    or _selected(runtime, identity) is not None
                ):
                    return "STALE"
            except (LoopRejected, OSError, RuntimeError, TypeError, ValueError):
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
                    "operation_kind": NATIVE_SEALED_ACCOUNTING_V3_PUBLICATION_OPERATION,
                    "expected_head": command.expected_head,
                    "fingerprint": command.request_fingerprint,
                    "predecessor": commitment,
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
                    "native_sealed_accounting_v3_evidence": evidence.canonical_bytes().decode(),
                    "native_sealed_accounting_v3_envelope": envelope.canonical_bytes().decode(),
                }
            )

    submitted = await runtime._appender.submit(
        replace(command, admission_guard=guard, decision_guard=decide)
    )
    if submitted.disposition not in ("COMMITTED", "REPLAY"):
        raise LoopRejected("native sealed accounting V3 publication " + submitted.disposition)
    with runtime._authority_gate().hold():
        entry = _selected(runtime, identity)
        if (
            entry is None
            or entry.get("native_sealed_accounting_v3_evidence")
            != evidence.canonical_bytes().decode()
        ):
            raise LoopRejected("selected native sealed accounting V3 differs from candidate")
    return await _finish(runtime, entry)
