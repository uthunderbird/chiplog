"""Sole-writer publication of the supported native cancellation accounting record."""

from __future__ import annotations

import base64
import json
from dataclasses import replace
from typing import TYPE_CHECKING, Literal

from pydantic import TypeAdapter

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.composition.r14_execution_recovery_source import R14NativeCancelledCallSource
from chiplog.composition.r14_native_accounting_records import (
    NATIVE_ACCOUNTING_OPERATION,
    NativeOneCallCancelledAccountingRecord,
    RetainedNativeOneCallAccountingPublication,
    accounting_command,
    accounting_envelope,
    accounting_record,
)
from chiplog.composition.r14_native_execution_record_source import (
    R14NativeExecutionRecordSourceManifest,
)
from chiplog.composition.r14_native_one_call_accounting_producer import (
    R14NativeOneCallCancelledAccountingV24Producer,
)
from chiplog.composition.r14_native_selected_accounting_source import R14NativeOneCallCancelledFacts

if TYPE_CHECKING:
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime


def _selected(runtime: CommonCliExecutionRuntime, identity: str) -> dict[str, object] | None:
    runtime._pending()
    entries = [
        json.loads(raw)
        for _, _, raw in runtime._loop_decisions().entries()
        if (entry := json.loads(raw)).get("kind") == "DECIDED"
        and entry.get("operation_id") == identity
    ]
    if len(entries) > 1:
        raise LoopRejected("ambiguous native accounting selection")
    if not entries:
        return None
    entry = entries[0]
    if entry.get("operation_kind") != NATIVE_ACCOUNTING_OPERATION:
        raise LoopRejected("native accounting identity belongs to another operation")
    return entry


async def _finish(
    runtime: CommonCliExecutionRuntime, entry: dict[str, object]
) -> NativeOneCallCancelledAccountingRecord:
    from chiplog.composition.r14_loop_history import read_selected_native_accounting

    raw = entry.get("native_one_call_accounting_evidence")
    if not isinstance(raw, str):
        raise LoopRejected("selected native accounting lacks retained evidence")
    evidence = RetainedNativeOneCallAccountingPublication.model_validate_json(raw)
    source = TypeAdapter(R14NativeExecutionRecordSourceManifest).validate_json(
        evidence.physical_source_bytes
    )
    command = accounting_command(evidence, source)
    envelope = accounting_envelope(evidence, source)
    if (
        evidence.canonical_bytes().decode() != raw
        or entry.get("native_one_call_accounting_envelope") != envelope.canonical_bytes().decode()
        or runtime._publication(entry) != command
    ):
        raise LoopRejected("selected native accounting differs from original batch")
    await runtime._recover_exact(
        command,
        str(entry["predecessor"]),
        str(entry["resulting"]),
        is_materialized=lambda: runtime._loop_decision_materialized(command.idempotency_key, entry),
    )
    runtime._finish_decision(command.idempotency_key, expected=entry)
    selected = read_selected_native_accounting(runtime)
    if selected != evidence.record:
        raise LoopRejected("selected native accounting was not observed by canonical reader")
    return selected


async def publish_native_one_call_cancelled_accounting(
    runtime: CommonCliExecutionRuntime, peer: str = "hermetic-ingress"
) -> NativeOneCallCancelledAccountingRecord:
    """Select one exact V24 result after live source and actor CAS."""
    observed = await runtime._execution_actor(peer)
    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        runtime._pending()
        selected_entries = [
            json.loads(raw)
            for _, _, raw in runtime._loop_decisions().entries()
            if (entry := json.loads(raw)).get("kind") == "DECIDED"
            and entry.get("operation_kind") == NATIVE_ACCOUNTING_OPERATION
        ]
        if len(selected_entries) > 1:
            raise LoopRejected("ambiguous selected native accounting history")
        prior = selected_entries[0] if selected_entries else None
    if prior is not None:
        return await _finish(runtime, prior)
    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        facts = R14NativeCancelledCallSource(runtime).collect()
        if facts is None:
            raise LoopRejected("native cancellation accounting source is unavailable")
        identity = "native-accounting:" + facts.selected_response_seal.revision.fingerprint
        if _selected(runtime, identity) is not None:
            raise LoopRejected("native accounting selection changed during source read")
    producer = R14NativeOneCallCancelledAccountingV24Producer(runtime)
    exchange = await producer.prepare_exchange(facts)
    if exchange is None:
        raise LoopRejected("native cancellation accounting owner preparation is unavailable")
    adapter = TypeAdapter(R14NativeExecutionRecordSourceManifest)
    selected_adapter = TypeAdapter(R14NativeOneCallCancelledFacts)
    evidence = RetainedNativeOneCallAccountingPublication(
        request=exchange.request,
        response=exchange.result,
        owner_request=exchange.owner_request,
        owner_response=exchange.owner_response,
        selected_source_bytes=selected_adapter.dump_json(exchange.selected_source),
        physical_source_bytes=adapter.dump_json(exchange.physical_source),
        record=accounting_record(exchange.request, exchange.result, exchange.physical_source),
    )
    command = accounting_command(evidence, exchange.physical_source)
    envelope = accounting_envelope(evidence, exchange.physical_source)
    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        acquired = producer._acquire_held(facts)
        if acquired is None or acquired[1:] != (
            exchange.selected_source,
            exchange.request.source_stamp,
            exchange.physical_source,
        ):
            raise LoopRejected("native accounting source changed after owner IPC")
        predecessor = runtime._commitment_journal.load(runtime._tenant_id)
        worker = runtime.current_worker()
        session = runtime._supervisor.runtime().session("agent_loop")
        if predecessor != exchange.request.source_stamp.authority_materialization_commitment:
            raise LoopRejected("native accounting predecessor changed after owner IPC")

    def guard() -> Literal["STALE"] | None:
        with runtime._authority_gate().hold():
            try:
                runtime._check_execution_actor(observed)
                acquired = producer._acquire_held(facts)
                if (
                    acquired is None
                    or acquired[1:]
                    != (
                        exchange.selected_source,
                        exchange.request.source_stamp,
                        exchange.physical_source,
                    )
                    or runtime.current_worker() != worker
                    or runtime._supervisor.runtime().session("agent_loop") != session
                    or runtime._commitment_journal.load(runtime._tenant_id) != predecessor
                    or accounting_command(evidence, acquired[3]) != command
                ):
                    return "STALE"
            except LoopRejected, ValueError, TypeError, RuntimeError:
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
                    "operation_kind": NATIVE_ACCOUNTING_OPERATION,
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
                    "native_one_call_accounting_evidence": evidence.canonical_bytes().decode(),
                    "native_one_call_accounting_envelope": envelope.canonical_bytes().decode(),
                }
            )

    result = await runtime._appender.submit(
        replace(command, admission_guard=guard, decision_guard=decide)
    )
    if result.disposition not in ("COMMITTED", "REPLAY"):
        raise LoopRejected("native accounting publication " + result.disposition)
    with runtime._authority_gate().hold():
        selected = _selected(runtime, identity)
        if (
            selected is None
            or selected.get("native_one_call_accounting_evidence")
            != evidence.canonical_bytes().decode()
        ):
            raise LoopRejected("selected native accounting differs from prepared candidate")
    return await _finish(runtime, selected)
