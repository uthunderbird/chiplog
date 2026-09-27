"""Versioned authority post-image bound to a selected native V2 seal."""

from __future__ import annotations

import base64
import hashlib

from pydantic import BaseModel, ConfigDict, Field

from chiplog.architecture.r7_storage_surface import AUTHORITY_STORAGE_SURFACE_DIGEST
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.r14_execution_complete_seal_records import (
    ExecutionCompleteSealPhysicalEnvelopeV2,
    RetainedExecutionCompleteSealV2,
    build_complete_seal_envelope,
    complete_seal_physical_command,
)
from chiplog.platform._sqlite import PhysicalPublicationCommand
from chiplog.platform.authority_checkpoint import (
    AuthorityCheckpointRefV1,
    AuthorityCheckpointStore,
    AuthorityCheckpointVerificationError,
    VerifiedAuthoritySnapshot,
)

H1_V2_AUTHORITY_CUT_FIELD = "h1_v2_authority_cut_v1"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class H1V2AuthorityCutV1(_StrictModel):
    kind: str
    version: int
    tenant_id: str
    operation_kind: str
    operation_id: str
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_head: int
    commit_sequence: int
    selected_response_seal: dict[str, object]
    envelope_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    database_binding: dict[str, object]
    predecessor: str = Field(pattern=r"^[0-9a-f]{64}$")
    resulting: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference: AuthorityCheckpointRefV1


def build_h1_v2_authority_cut(
    *,
    command: PhysicalPublicationCommand,
    retained: RetainedExecutionCompleteSealV2,
    envelope: ExecutionCompleteSealPhysicalEnvelopeV2,
    database_identity: tuple[str, int, int],
    predecessor: str,
    resulting: str,
    reference: AuthorityCheckpointRefV1,
) -> H1V2AuthorityCutV1:
    """Bind an already-staged exact post-image to its native V2 decision."""

    response_seal = retained.exchange.proposal.fan_out.response_seal
    selected = CallSubjectHead(
        subject_id=response_seal.response_seal_id,
        revision=Present(
            head="record:" + response_seal.digest(),
            fingerprint=response_seal.digest(),
        ),
    )
    path, device, inode = database_identity
    return H1V2AuthorityCutV1(
        kind="H1_V2_AUTHORITY_CUT_V1",
        version=1,
        tenant_id=command.tenant_id,
        operation_kind=command.operation_kind,
        operation_id=command.idempotency_key,
        request_fingerprint=command.request_fingerprint,
        expected_head=command.expected_head,
        commit_sequence=command.expected_head + 1,
        selected_response_seal=selected.model_dump(mode="json"),
        envelope_sha256=hashlib.sha256(envelope.canonical_bytes()).hexdigest(),
        database_binding={"canonical_path": path, "st_dev": device, "st_ino": inode},
        predecessor=predecessor,
        resulting=resulting,
        reference=reference,
    )


def resolve_h1_v2_authority_cut(
    selected_authenticated_decision: dict[str, object],
    store: AuthorityCheckpointStore,
    admitted_database_identity: tuple[str, int, int],
    command: PhysicalPublicationCommand,
) -> VerifiedAuthoritySnapshot:
    """Resolve a selected V2 post-image, rejecting every other checkpoint form."""

    raw_descriptor = selected_authenticated_decision.get(H1_V2_AUTHORITY_CUT_FIELD)
    try:
        descriptor = H1V2AuthorityCutV1.model_validate(raw_descriptor)
        retained_raw = selected_authenticated_decision["execution_complete_seal"]
        envelope_raw = selected_authenticated_decision["execution_complete_seal_envelope"]
        if not isinstance(retained_raw, str) or not isinstance(envelope_raw, str):
            raise ValueError("V2 retained seal bytes are absent")
        retained = RetainedExecutionCompleteSealV2.model_validate_json(retained_raw)
        envelope = ExecutionCompleteSealPhysicalEnvelopeV2.model_validate_json(envelope_raw)
        if (
            build_complete_seal_envelope(retained) != envelope
            or complete_seal_physical_command(envelope) != command
            or len(command.records) != 3
            or descriptor.kind != "H1_V2_AUTHORITY_CUT_V1"
            or descriptor.version != 1
            or descriptor.tenant_id != command.tenant_id
            or descriptor.operation_kind != command.operation_kind
            or descriptor.operation_id != command.idempotency_key
            or descriptor.request_fingerprint != command.request_fingerprint
            or descriptor.expected_head != command.expected_head
            or descriptor.commit_sequence != command.expected_head + 1
            or descriptor.envelope_sha256 != hashlib.sha256(envelope.canonical_bytes()).hexdigest()
            or descriptor.database_binding
            != {
                "canonical_path": admitted_database_identity[0],
                "st_dev": admitted_database_identity[1],
                "st_ino": admitted_database_identity[2],
            }
            or descriptor.predecessor != selected_authenticated_decision.get("predecessor")
            or descriptor.resulting != selected_authenticated_decision.get("resulting")
            or descriptor.reference.blob_sha256 != descriptor.resulting
        ):
            raise ValueError("H1 V2 authority-cut binding differs")
        response_seal = retained.exchange.proposal.fan_out.response_seal
        expected_selected = CallSubjectHead(
            subject_id=response_seal.response_seal_id,
            revision=Present(
                head="record:" + response_seal.digest(),
                fingerprint=response_seal.digest(),
            ),
        ).model_dump(mode="json")
        if descriptor.selected_response_seal != expected_selected:
            raise ValueError("H1 V2 authority-cut seal differs")
        snapshot = store.resolve_verified(
            descriptor.reference,
            expected_resulting=descriptor.resulting,
            expected_surface_digest=AUTHORITY_STORAGE_SURFACE_DIGEST,
        )
        _verify_selected_membership(snapshot, command)
        return snapshot
    except (KeyError, TypeError, ValueError, AuthorityCheckpointVerificationError) as error:
        raise ValueError("H1 V2 authority cut cannot be verified") from error


def _verify_selected_membership(
    snapshot: VerifiedAuthoritySnapshot, command: PhysicalPublicationCommand
) -> None:
    tables = {table.table: (table.columns, table.rows) for table in snapshot.tables}
    try:
        publication_columns, publication_rows = tables["publications"]
        record_columns, record_rows = tables["records"]
        publication = dict(
            zip(
                publication_columns,
                next(
                    row
                    for row in publication_rows
                    if row[0] == command.tenant_id
                    and row[1] == command.operation_kind
                    and row[2] == command.idempotency_key
                ),
                strict=True,
            )
        )
        if (
            publication["request_fingerprint"] != command.request_fingerprint
            or publication["commit_sequence"] != command.expected_head + 1
            or publication["record_ids"]
            != "\n".join(record.record_id for record in command.records)
        ):
            raise ValueError("selected publication differs")
        physical = {
            row[1]: dict(zip(record_columns, row, strict=True))
            for row in record_rows
            if row[0] == command.tenant_id
        }
        for member in command.records:
            row = physical[member.record_id]
            encoded = row["canonical_bytes"]
            if (
                row["owner"] != member.owner
                or row["schema_id"] != member.schema_id
                or row["commit_sequence"] != command.expected_head + 1
                or not isinstance(encoded, dict)
                or set(encoded) != {"base64"}
                or encoded["base64"] != base64.b64encode(member.canonical_bytes).decode()
            ):
                raise ValueError("selected record differs")
    except (KeyError, StopIteration, TypeError, ValueError) as error:
        raise ValueError("authority cut lacks exact selected physical membership") from error
