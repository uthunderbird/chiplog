"""Authenticated per-seal reader for selected V3 native accounting."""

from __future__ import annotations

import hashlib
import sqlite3

from pydantic import TypeAdapter

from chiplog.capabilities.agent_loop.contracts import RunRecord
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.composition.r14_execution_transition_records import ExecutionHistorySnapshot
from chiplog.composition.r14_loop_history import ExecutionRecoveryHistory
from chiplog.composition.r14_native_accounting_history import _verify_original_prepare_projection
from chiplog.composition.r14_native_accounting_records import (
    NATIVE_SEALED_ACCOUNTING_V3_PUBLICATION_OPERATION,
    NativeSealedAccountingV3Record,
    RetainedNativeSealedAccountingV3Publication,
    sealed_accounting_v3_command,
    sealed_accounting_v3_envelope,
    validate_retained_sealed_accounting_v3_publication,
)
from chiplog.composition.r14_native_preparation_bindings import bind_native_execution_preparation_v2
from chiplog.composition.r14_native_selected_accounting_source import (
    R14NativeSelectedAccountingSource,
    R14NativeSelectedAccountingSourceFacts,
)
from chiplog.composition.r14_original_prepare_source import R14OriginalNativePrepareProjection
from chiplog.platform._sqlite import PhysicalPublicationCommand
from chiplog.platform.publication_readback import inspect_publication


def retained_selected_native_sealed_accounting_v3(
    entry: dict[str, object],
) -> RetainedNativeSealedAccountingV3Publication:
    raw = entry.get("native_sealed_accounting_v3_evidence")
    if not isinstance(raw, str):
        raise ValueError("selected native sealed accounting V3 evidence is absent")
    evidence = RetainedNativeSealedAccountingV3Publication.model_validate_json(raw)
    if evidence.canonical_bytes().decode() != raw:
        raise ValueError("selected native sealed accounting V3 evidence is noncanonical")
    return evidence


def decode_selected_native_sealed_accounting_v3(
    runtime,
    connection: sqlite3.Connection,
    command: PhysicalPublicationCommand,
    entry: dict[str, object],
    loop_decision_bytes_by_id: dict[str, bytes],
    history: ExecutionRecoveryHistory,
    current_history: ExecutionHistorySnapshot,
    fanouts: list,
    cancellations: list,
    run_records: list[RunRecord | ExecutionRunRecord],
    verified_h1_prepares: dict,
    accepted_open_recoveries: tuple = (),
) -> NativeSealedAccountingV3Record:
    """Rebuild source and exact V3 request from the authenticated outer history cut."""
    if command.operation_kind != NATIVE_SEALED_ACCOUNTING_V3_PUBLICATION_OPERATION:
        raise ValueError("native sealed accounting V3 has another operation")
    evidence = retained_selected_native_sealed_accounting_v3(entry)
    source_adapter = TypeAdapter(R14NativeSelectedAccountingSourceFacts)
    selected_source = source_adapter.validate_json(evidence.selected_source_bytes)
    if source_adapter.dump_json(selected_source) != evidence.selected_source_bytes:
        raise ValueError("native sealed accounting V3 source is noncanonical")
    projection = selected_source.original_prepare
    if type(projection) is not R14OriginalNativePrepareProjection:
        raise ValueError("native sealed accounting V3 Prepare projection has wrong type")
    matching_fanouts = tuple(
        item
        for item in fanouts
        if item.proposal.fan_out.response_seal.response_seal_id
        == evidence.request.selected_response.subject_id
    )
    if len(matching_fanouts) != 1:
        raise ValueError("native sealed accounting V3 seal has no unique authenticated fanout")
    fanout = matching_fanouts[0]
    seal = fanout.proposal.fan_out.response_seal
    prepare = projection
    prepare_bytes = loop_decision_bytes_by_id.get(prepare.selected_prepare_decision_id)
    if (
        prepare_bytes != prepare.selected_prepare_decision_bytes
        or prepare_bytes is None
        or hashlib.sha256(prepare_bytes).hexdigest()
        != prepare.selected_prepare_decision_fingerprint
    ):
        raise ValueError("native sealed accounting V3 Prepare selection differs")
    _verify_original_prepare_projection(
        runtime,
        prepare,
        prepare.retained_prepare_bytes,
        prepare.selected_prepare_decision_id,
        prepare_bytes,
        seal,
        fanout,
        run_records,
        selected_source.preparation_bindings,
        verified_h1_prepares.get(
            (
                prepare.retained_prepare_bytes,
                prepare.workspace_issuance.model_dump_json(),
            )
        ),
    )
    source = R14NativeSelectedAccountingSource(
        runtime, projection, bind_native_execution_preparation_v2(projection)
    )
    current_facts = source.collect(
        projection.prepared_run,
        selected_only=True,
        authenticated_history=history,
        authenticated_projection=True,
    )
    if current_facts != selected_source:
        raise ValueError("native sealed accounting V3 selected source differs from current cut")
    rebuilt = source.prepare_sealed_accounting_v3(
        evidence.request.selected_response,
        authenticated_history=history,
        authenticated_effects_history=current_history,
        authenticated_accepted_open_recoveries=accepted_open_recoveries,
        authenticated_projection=True,
    )
    if rebuilt != evidence.request:
        raise ValueError(
            "native sealed accounting V3 request differs from current source: "
            + ("unavailable" if rebuilt is None else "fingerprint-mismatch")
        )
    record = validate_retained_sealed_accounting_v3_publication(evidence)
    expected = sealed_accounting_v3_command(evidence)
    envelope = sealed_accounting_v3_envelope(evidence)
    if (
        command != expected
        or entry.get("operation_kind") != NATIVE_SEALED_ACCOUNTING_V3_PUBLICATION_OPERATION
        or entry.get("operation_id") != expected.idempotency_key
        or entry.get("native_sealed_accounting_v3_envelope")
        != envelope.canonical_bytes().decode()
        or entry.get("predecessor")
        != evidence.source_stamp.authority_materialization_commitment
        or entry.get("expected_head") != expected.expected_head
        or entry.get("fingerprint") != expected.request_fingerprint
        or evidence.source_stamp.tenant_id != runtime._tenant_id
        or evidence.source_stamp.database_identity != runtime._database_identity
        or evidence.source_stamp.tenant_commit_sequence != expected.expected_head
    ):
        raise ValueError("selected native sealed accounting V3 publication differs")
    state = inspect_publication(connection, expected, expected.expected_head + 1)
    if state != "COMPLETE":
        raise ValueError("selected native sealed accounting V3 physical row is incomplete")
    return record
