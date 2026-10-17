"""Shared builders extracted without changing their behavior."""

import hashlib
from pathlib import Path
from typing import Literal

from chiplog.platform._owner_publication_contracts import (
    ExactReplayQuery,
    InvocationProofRef,
    PublicationRejected,
    RegisteredPublication,
    SingleOwnerBatch,
)
from chiplog.platform.authority_reads import capture_authority_storage_state
from chiplog.platform.owner_publications import (
    PreparedOwnerPublication,
    SelectedOwnerDecision,
)
from chiplog.platform.workspace_snapshot import read_connection


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def invocation() -> InvocationProofRef:
    return InvocationProofRef(
        issuance_id="fixture-issued",
        issuance_fingerprint=digest(b"issued"),
        broker_epoch="epoch",
        broker_session="session",
        runtime_generation="generation",
        operation_subject="command",
    )


def request(commitment: str) -> SingleOwnerBatch:
    return SingleOwnerBatch.model_validate(
        {
            "operation": "effects.authorize",
            "identity": {
                "tenant_id": "tenant",
                "command_id": "command",
                "command_fingerprint": digest(b"command"),
                "canonicalization_version": "chiplog.owner-publication.v1",
            },
            "authentication": {
                "kind": "WORKER",
                "invocation": invocation(),
                "applicability_schema": "fixture.v1",
                "applicability_bytes": b"fixture-non-authoritative-fence",
                "applicability_fingerprint": digest(b"fixture-non-authoritative-fence"),
            },
            "expected": {
                "tenant_id": "tenant",
                "tenant_frontier": 0,
                "expected_materialization_commitment": commitment,
                "registry_head": "fixture-registry",
                "registry_fingerprint": digest(b"registry"),
                "ordered_heads": (),
                "complete_manifest_fingerprint": digest(b"empty"),
            },
            "command": {
                "owner": "effects",
                "schema_id": "fixture.command.v1",
                "canonical_bytes": b"command",
                "fingerprint": digest(b"command"),
            },
            "complete_records": tuple(
                {
                    "owner": "effects",
                    "record_kind": "fixture",
                    "record_id": name,
                    "schema_id": "fixture.record.v1",
                    "canonical_bytes": name.encode(),
                    "fingerprint": digest(name.encode()),
                }
                for name in ("record1", "record2")
            ),
            "complete_batch_fingerprint": digest(b"complete fixture batch"),
        }
    )


class FixtureAuthority:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.prepare_calls = 0
        self.force_stale = False

    def authenticate_replay(self, query: ExactReplayQuery) -> PublicationRejected | None:
        # Real issuance lives in canonical composition; this exact fixture is
        # intentionally not evidence that a string authenticates a real worker.
        if query.current_invocation != invocation():
            return PublicationRejected(
                kind="DENIED",
                tenant_id=query.identity.tenant_id,
                command_id=query.identity.command_id,
                reason="wrong fixture invocation",
            )
        return None

    async def prepare(self, value: RegisteredPublication) -> PreparedOwnerPublication:
        self.prepare_calls += 1
        return PreparedOwnerPublication(
            value,
            "prepared-fixture",
            "fence",
            0,
            value.expected.expected_materialization_commitment,
        )

    def check_prepared(self, value: PreparedOwnerPublication) -> Literal["STALE"] | None:
        actual, _ = capture_authority_storage_state(self.path)
        if self.force_stale or actual != value.predecessor_commitment:
            return "STALE"
        return None

    def materialization_state(
        self, decision: SelectedOwnerDecision
    ) -> Literal["ABSENT", "COMPLETE", "CONFLICT"]:
        expected = decision.prepared.request
        with read_connection(self.path) as connection:
            rows = connection.execute(
                "SELECT record_id, owner, schema_id, canonical_bytes, commit_sequence FROM records "
                "WHERE tenant_id=? ORDER BY record_id",
                ("tenant",),
            ).fetchall()
        if not rows:
            return "ABSENT"
        wanted = sorted(
            (
                row.record_id,
                row.owner,
                row.schema_id,
                row.canonical_bytes,
                decision.tenant_commit_sequence,
            )
            for row in expected.complete_records
        )
        return "COMPLETE" if rows == wanted else "CONFLICT"

    def check_selected_predecessor(self, decision: SelectedOwnerDecision) -> bool:
        return (
            capture_authority_storage_state(self.path)[0]
            == decision.prepared.predecessor_commitment
        )
