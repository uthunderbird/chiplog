"""Fail-closed V3 checkpoint descriptor dispatch."""

import base64
from typing import cast

import pytest

from chiplog.architecture.r7_storage_surface import AUTHORITY_STORAGE_SURFACE_DIGEST
from chiplog.composition.r14_loop_history import resolve_h1_checkpoint
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.authority_checkpoint import (
    AuthorityCheckpointRefV1,
    AuthorityCheckpointStore,
    AuthoritySnapshotTableV1,
    VerifiedAuthoritySnapshot,
)


class _Store:
    def __init__(self, snapshot: VerifiedAuthoritySnapshot) -> None:
        self.snapshot = snapshot

    def resolve_verified(
        self, ref: AuthorityCheckpointRefV1, expected_resulting: str, expected_surface_digest: str
    ) -> VerifiedAuthoritySnapshot:
        assert ref == self.snapshot.ref
        assert expected_resulting == ref.blob_sha256
        assert expected_surface_digest == AUTHORITY_STORAGE_SURFACE_DIGEST
        return self.snapshot


def _command() -> PhysicalPublicationCommand:
    record = PhysicalRecord("record-1", "loop", "chiplog.loop.record.v1", b"payload", "digest")
    return PhysicalPublicationCommand(
        tenant_id="tenant",
        operation_kind="agent_loop.execution-complete-seal.v1",
        idempotency_key="operation",
        request_fingerprint="fingerprint",
        expected_head=4,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=(record,),
    )


def _snapshot(command: PhysicalPublicationCommand) -> VerifiedAuthoritySnapshot:
    ref = AuthorityCheckpointRefV1(
        format="authority-preimage-v1",
        commitment_algorithm="authority-json-v1",
        authority_surface_digest=AUTHORITY_STORAGE_SURFACE_DIGEST,
        blob_sha256="a" * 64,
        byte_length=1,
    )
    return VerifiedAuthoritySnapshot(
        ref=ref,
        preimage=b"x",
        tables=(
            AuthoritySnapshotTableV1(
                table="publications",
                columns=(
                    "tenant_id",
                    "operation_kind",
                    "idempotency_key",
                    "request_fingerprint",
                    "commit_sequence",
                    "record_ids",
                ),
                rows=(
                    (
                        command.tenant_id,
                        command.operation_kind,
                        command.idempotency_key,
                        command.request_fingerprint,
                        command.expected_head + 1,
                        command.records[0].record_id,
                    ),
                ),
            ),
            AuthoritySnapshotTableV1(
                table="records",
                columns=(
                    "tenant_id",
                    "record_id",
                    "owner",
                    "schema_id",
                    "canonical_bytes",
                    "commit_sequence",
                ),
                rows=(
                    (
                        command.tenant_id,
                        command.records[0].record_id,
                        command.records[0].owner,
                        command.records[0].schema_id,
                        {"base64": base64.b64encode(command.records[0].canonical_bytes).decode()},
                        command.expected_head + 1,
                    ),
                ),
            ),
        ),
    )


def _decision(command: PhysicalPublicationCommand) -> dict[str, object]:
    snapshot = _snapshot(command)
    return {
        "resulting": snapshot.ref.blob_sha256,
        "h1_historical_checkpoint": {
            "version": 1,
            "reference": snapshot.ref.model_dump(mode="json"),
            "tenant_id": command.tenant_id,
            "operation_id": command.idempotency_key,
            "commit_sequence": command.expected_head + 1,
            "database_binding": {"canonical_path": "/db", "st_dev": 1, "st_ino": 2},
            "resulting": snapshot.ref.blob_sha256,
        },
    }


def test_v3_checkpoint_rejects_missing_or_foreign_descriptor_before_readback() -> None:
    command = _command()
    snapshot = _snapshot(command)
    with pytest.raises(ValueError, match="descriptor"):
        resolve_h1_checkpoint(
            {}, cast(AuthorityCheckpointStore, _Store(snapshot)), ("/db", 1, 2), command
        )

    decision = _decision(command)
    descriptor = decision["h1_historical_checkpoint"]
    assert isinstance(descriptor, dict)
    descriptor["operation_id"] = "foreign"
    with pytest.raises(ValueError, match="binding"):
        resolve_h1_checkpoint(
            decision, cast(AuthorityCheckpointStore, _Store(snapshot)), ("/db", 1, 2), command
        )


def test_v3_checkpoint_requires_full_selected_physical_membership() -> None:
    command = _command()
    snapshot = _snapshot(command)
    incomplete = snapshot.model_copy(update={"tables": snapshot.tables[:1]})
    with pytest.raises(ValueError, match="cannot be verified"):
        resolve_h1_checkpoint(
            _decision(command),
            cast(AuthorityCheckpointStore, _Store(incomplete)),
            ("/db", 1, 2),
            command,
        )
