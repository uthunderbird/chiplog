"""Authenticated per-seal reader for selected V3 native accounting."""

from __future__ import annotations

import sqlite3

from chiplog.composition.r14_execution_fanout_records import reference
from chiplog.composition.r14_native_accounting_records import (
    NATIVE_SEALED_ACCOUNTING_V3_PUBLICATION_OPERATION,
    NativeSealedAccountingV3Record,
    RetainedNativeSealedAccountingV3Publication,
    _sealed_accounting_v3_artifacts,
)
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
    fanouts: list,
) -> NativeSealedAccountingV3Record:
    """Decode the stored result bound to its selected seal and physical publication."""
    if command.operation_kind != NATIVE_SEALED_ACCOUNTING_V3_PUBLICATION_OPERATION:
        raise ValueError("native sealed accounting V3 has another operation")
    evidence = retained_selected_native_sealed_accounting_v3(entry)
    matching_fanouts = tuple(
        item
        for item in fanouts
        if item.proposal.fan_out.response_seal.response_seal_id
        == evidence.request.selected_response.subject_id
    )
    if len(matching_fanouts) != 1:
        raise ValueError("native sealed accounting V3 seal has no unique authenticated fanout")
    seal = matching_fanouts[0].proposal.fan_out.response_seal
    if reference(seal.response_seal_id, seal) != evidence.request.selected_response:
        raise ValueError("native sealed accounting V3 selected seal differs")
    record, expected, envelope = _sealed_accounting_v3_artifacts(evidence)
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
