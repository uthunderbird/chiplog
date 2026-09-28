"""Version-pinned replay of the H1 V2 completion predecessor read plan.

This module uses only authenticated historical carriers: a retained registry,
the owner prefix selected by its raw predecessor head, the verified authority
checkpoint, and a replayed native first-path cut.  It intentionally has no
dependency on the live read-plan source or the current registry.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast

from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
    RECOVERY_FRONTIER_REGISTRY_SCHEMA,
)
from chiplog.composition.h1_completion_readplan_registry import require_registry_identity
from chiplog.composition.h1_first_path_sources import H1HistoricalFirstPathNativeCut
from chiplog.composition.h1_verified_snapshot_rows import H1VerifiedSnapshotRows
from chiplog.composition.r14_execution_fanout_contracts import EXECUTION_RUN_SCHEMA
from chiplog.composition.r14_fanout_contracts import SEAL_SCHEMA
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    CompleteDeliveryBatchV2,
    ExactRecordHead,
    ObservedAbsence,
    ObservedPresence,
)
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.owner_decision_journal import OwnerJournalSnapshot


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _command(request: Any) -> PhysicalPublicationCommand:
    """Reconstruct the exact physical publication from one decoded owner decision."""
    try:
        expected = request.expected
        identity = request.identity
        records = tuple(
            PhysicalRecord(
                member.record_id,
                member.owner,
                member.schema_id,
                member.canonical_bytes,
                member.fingerprint,
            )
            for member in request.complete_records
        )
        return PhysicalPublicationCommand(
            identity.tenant_id,
            request.operation,
            identity.command_id,
            identity.command_fingerprint,
            expected.tenant_frontier,
            "r6",
            0,
            0,
            records,
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("H1 historical read-plan owner prefix publication is malformed") from error


def _require_prefix_reconciled(
    *,
    batch: CompleteDeliveryBatchV2,
    rows: H1VerifiedSnapshotRows,
    predecessor_owner: OwnerJournalSnapshot,
) -> tuple[PhysicalPublicationCommand, ...]:
    if predecessor_owner.tenant_id != batch.identity.tenant_id:
        raise ValueError("H1 historical read-plan owner prefix tenant differs")
    commands: list[PhysicalPublicationCommand] = []
    for decision in predecessor_owner.decisions:
        request = decision.prepared.request
        command = _command(request)
        command_id = request.identity.command_id
        if (
            command.tenant_id != batch.identity.tenant_id
            or command.expected_head + 1 != decision.tenant_commit_sequence
            or decision.tenant_commit_sequence > batch.expected.tenant_frontier
            or command_id not in predecessor_owner.materialized_command_ids
        ):
            raise ValueError("H1 historical read-plan owner prefix is not materialized")
        try:
            rows.require_complete(command)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "H1 historical read-plan owner prefix differs from checkpoint"
            ) from error
        commands.append(command)

    # A V2 completion row omitted from the owner prefix could otherwise forge
    # historical RunCompletion absence.  Every such checkpoint publication must
    # be one exact materialized prefix command.
    complete_rows = [
        row
        for row in rows.publications(batch.identity.tenant_id)
        if row[0] == "agent_loop.complete_acceptance.v2"
    ]
    complete_commands = [
        command
        for command in commands
        if command.operation_kind == "agent_loop.complete_acceptance.v2"
    ]
    for row in complete_rows:
        operation, command_id, fingerprint, sequence, record_ids = cast(
            tuple[str, str, str, int, str], row
        )
        matches = [
            command
            for command in complete_commands
            if (
                command.operation_kind,
                command.idempotency_key,
                command.request_fingerprint,
                command.expected_head + 1,
                "\n".join(record.record_id for record in command.records),
            )
            == (operation, command_id, fingerprint, sequence, record_ids)
        ]
        if len(matches) != 1:
            raise ValueError("H1 historical read-plan completion publication lacks owner prefix")
    if len(complete_rows) != len(complete_commands):
        raise ValueError("H1 historical read-plan owner completion is absent from checkpoint")
    return tuple(commands)


def _require_native_seal_members(
    *,
    batch: CompleteDeliveryBatchV2,
    rows: H1VerifiedSnapshotRows,
    native: H1HistoricalFirstPathNativeCut,
) -> tuple[PhysicalRecord, ...]:
    members = tuple(
        member
        for member in native.physical_members
        if member.decision_id == native.seal.decision_id
    )
    if not members:
        raise ValueError("H1 historical read-plan selected seal lacks physical members")
    keys = {
        (member.operation_kind, member.publication_id, member.commit_sequence) for member in members
    }
    if len(keys) != 1:
        raise ValueError("H1 historical read-plan selected seal has competing publications")
    operation, publication_id, sequence = keys.pop()
    publication = [
        item
        for item in rows.publications(batch.identity.tenant_id)
        if item[:2] == (operation, publication_id)
    ]
    if len(publication) != 1:
        raise ValueError("H1 historical read-plan selected seal publication is absent")
    fingerprint, observed_sequence, record_ids = publication[0][2:]
    records = tuple(
        PhysicalRecord(
            member.record_id,
            member.owner,
            member.schema_id,
            member.canonical_bytes,
            hashlib.sha256(member.canonical_bytes).hexdigest(),
        )
        for member in members
    )
    command = PhysicalPublicationCommand(
        batch.identity.tenant_id,
        operation,
        publication_id,
        cast(str, fingerprint),
        sequence - 1,
        "r6",
        0,
        0,
        records,
    )
    if observed_sequence != sequence or record_ids != "\n".join(
        record.record_id for record in records
    ):
        raise ValueError("H1 historical read-plan selected seal membership differs")
    try:
        rows.require_complete(command)
    except (TypeError, ValueError) as error:
        raise ValueError("H1 historical read-plan selected seal differs from checkpoint") from error
    return records


def _head(
    records: tuple[PhysicalRecord, ...], *, schema: str, kind: str, subject: str
) -> ExactRecordHead:
    candidates = [
        record for record in records if record.owner == "agent_loop" and record.schema_id == schema
    ]
    if len(candidates) != 1:
        raise ValueError(f"H1 historical read-plan {kind} selector is absent or ambiguous")
    record = candidates[0]
    return ExactRecordHead(
        owner="agent_loop",
        record_kind=kind,
        subject_id=subject,
        record_id=record.record_id,
        fingerprint=hashlib.sha256(record.canonical_bytes).hexdigest(),
    )


def reconstruct_h1_historical_read_manifest(
    *,
    batch: CompleteDeliveryBatchV2,
    issuance: Any,
    native: H1HistoricalFirstPathNativeCut,
    predecessor_rows: H1VerifiedSnapshotRows,
    predecessor_owner: OwnerJournalSnapshot,
) -> AuthoritativeReadManifest:
    """Recompute the frozen V2 manifest from historical physical evidence only."""
    registry = require_registry_identity(
        canonical_bytes=issuance.read_plan.registry_bytes,
        expected_head=batch.expected.registry_head,
        expected_fingerprint=batch.expected.registry_fingerprint,
    )
    if predecessor_rows.commitment != batch.expected.expected_materialization_commitment:
        raise ValueError("H1 historical read-plan checkpoint commitment differs")
    if predecessor_rows.tenant_head(batch.identity.tenant_id) != batch.expected.tenant_frontier:
        raise ValueError("H1 historical read-plan checkpoint frontier differs")
    prefix_commands = _require_prefix_reconciled(
        batch=batch, rows=predecessor_rows, predecessor_owner=predecessor_owner
    )
    records = _require_native_seal_members(batch=batch, rows=predecessor_rows, native=native)
    run_id = issuance.assembly.original_completion_request.run.run_id
    seal_id = native.source.selected_response_seal.subject_id

    # Decode only known completion wires in the authenticated owner prefix;
    # malformed/unknown representations hold instead of becoming absence.
    from chiplog.composition.h1_completion_issuance import decode_h1_completion_issuance

    for command in prefix_commands:
        if command.operation_kind != "agent_loop.complete_acceptance.v2":
            continue
        matching = [
            decision
            for decision in predecessor_owner.decisions
            if decision.prepared.request.identity.command_id == command.idempotency_key
        ]
        if len(matching) != 1 or type(matching[0].prepared.request) is not CompleteDeliveryBatchV2:
            raise ValueError("H1 historical read-plan completion representation is unknown")
        prior = decode_h1_completion_issuance(matching[0].prepared.request)
        if prior.assembly.original_completion_request.run.run_id == run_id:
            raise ValueError("H1 historical read-plan RunCompletion is present")

    known: dict[str, ObservedAbsence | ObservedPresence] = {
        "sealed_run": ObservedPresence(
            head=_head(records, schema=EXECUTION_RUN_SCHEMA, kind="Run", subject=run_id)
        ),
        "selected_seal": ObservedPresence(
            head=_head(records, schema=SEAL_SCHEMA, kind="ResponseSeal", subject=seal_id)
        ),
        "recovery_frontier_registry": ObservedPresence(
            head=_head(
                records,
                schema=RECOVERY_FRONTIER_REGISTRY_SCHEMA,
                kind="RecoveryFrontierRegistry",
                subject=seal_id,
            )
        ),
        "run_completion": ObservedAbsence(
            owner="agent_loop", record_kind="RunCompletion", subject_id=run_id
        ),
    }
    if tuple(role.name for role in registry.roles) != tuple(known):
        raise ValueError("H1 historical read-plan registry selector order is unsupported")
    ordered: tuple[ObservedAbsence | ObservedPresence, ...] = tuple(
        known[role.name] for role in registry.roles
    )
    manifest = AuthoritativeReadManifest(
        tenant_id=batch.identity.tenant_id,
        tenant_frontier=batch.expected.tenant_frontier,
        expected_materialization_commitment=batch.expected.expected_materialization_commitment,
        registry_head=registry.head,
        registry_fingerprint=registry.fingerprint,
        ordered_heads=ordered,
        complete_manifest_fingerprint=hashlib.sha256(
            _canonical(
                {
                    "expected_materialization_commitment": (
                        batch.expected.expected_materialization_commitment
                    ),
                    "ordered_heads": [item.model_dump(mode="json") for item in ordered],
                    "registry_fingerprint": registry.fingerprint,
                    "registry_head": registry.head,
                    "tenant_frontier": batch.expected.tenant_frontier,
                    "tenant_id": batch.identity.tenant_id,
                }
            )
        ).hexdigest(),
    )
    if manifest != batch.expected or manifest != issuance.capture.expected:
        raise ValueError("H1 historical read-plan manifest differs from retained evidence")
    return manifest
