"""Separately owned, rebuildable planning projection."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from chiplog.domain_primitives.canonical import CanonicalBytes
from chiplog.domain_primitives.codec import fingerprint
from chiplog.domain_primitives.identity import RecordId, SchemaId
from chiplog.domain_primitives.principal import PrincipalId
from chiplog.domain_primitives.tenant import TenantId
from chiplog.domain_primitives.versions import (
    CanonicalizationVersion,
    CodecVersion,
    ProducingVersions,
)

PROJECTION_SINK = "chiplog.projections.planning.v1"
PROJECTION_REBUILD_SURFACE = "projections.rebuild"
REDUCER_VERSION = 1
PROJECTION_SCHEMA_VERSION = 1
PLANNING_RECORD_TYPES = frozenset(
    {
        "chiplog.planning.authorization_evidence",
        "chiplog.planning.committed_result",
        "chiplog.planning.intention_line",
        "chiplog.planning.intention_line_revision",
        "chiplog.planning.planning_command",
    }
)
_VERSIONS = ProducingVersions(
    SchemaId("chiplog.planning", "record", 1), CodecVersion(1), CanonicalizationVersion(1)
)


class PlanningProjectionQueries(Protocol):
    def get(
        self, tenant_id: TenantId, intention_line_id: RecordId
    ) -> _IntentionLineView | None: ...


class _CommittedRecord(Protocol):
    record_id: RecordId
    record_type_id: str
    canonical_bytes: bytes
    fingerprint: str


class _CommittedPublication(Protocol):
    tenant_id: TenantId
    command_id: RecordId
    request_fingerprint: str
    commit_sequence: int
    records: tuple[_CommittedRecord, ...]
    result: _CommittedResultBinding
    record_manifest: tuple[tuple[RecordId, str, str], ...]
    batch_fingerprint: str


class _CommittedResultBinding(Protocol):
    command_id: RecordId
    result_id: RecordId
    intention_line_id: RecordId
    revision_id: RecordId
    authorization_evidence_id: RecordId
    commit_sequence: int
    allocation_manifest: tuple[tuple[int, str, RecordId], ...]
    record_manifest: tuple[tuple[RecordId, str, str], ...]
    batch_fingerprint: str
    result_fingerprint: str
    publication_fingerprint: str


class _PlanningReadStore(Protocol):
    def committed_publications(self, tenant_id: TenantId) -> tuple[_CommittedPublication, ...]: ...


@dataclass(frozen=True)
class _IntentionLineView:
    tenant_id: TenantId
    intention_line_id: RecordId
    revision_id: RecordId
    principal_id: PrincipalId
    purpose: str
    activity: str
    personal_outcome: str
    commit_sequence: int


@dataclass(frozen=True)
class _ProjectionCheckpoint:
    tenant_id: TenantId
    log_identity: str
    inclusive_frontier: int
    reducer_version: int
    schema_version: int
    source_digest: str
    rows: tuple[_IntentionLineView, ...]


def _log_identity(tenant_id: TenantId) -> str:
    return f"planning:{tenant_id.value}"


def _source_digest(publications: tuple[_CommittedPublication, ...]) -> str:
    encoded = json.dumps(
        [
            {
                "commit_sequence": item.commit_sequence,
                "record_fingerprints": [record.fingerprint for record in item.records],
                "request_fingerprint": item.request_fingerprint,
            }
            for item in publications
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _batch_fingerprint(manifest: tuple[tuple[RecordId, str, str], ...]) -> str:
    encoded = json.dumps(
        [
            {
                "fingerprint": item_fingerprint,
                "record_id": {
                    "tenant_id": record_id.tenant_id.value,
                    "value": record_id.value,
                },
                "record_type_id": record_type_id,
            }
            for record_id, record_type_id, item_fingerprint in manifest
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _publication_fingerprint(
    request_fingerprint: str,
    commit_sequence: int,
    manifest: tuple[tuple[RecordId, str, str], ...],
) -> str:
    encoded = json.dumps(
        {
            "commit_sequence": commit_sequence,
            "record_manifest": [
                {
                    "fingerprint": item_fingerprint,
                    "record_id": {
                        "tenant_id": record_id.tenant_id.value,
                        "value": record_id.value,
                    },
                    "record_type_id": record_type_id,
                }
                for record_id, record_type_id, item_fingerprint in manifest
            ],
            "request_fingerprint": request_fingerprint,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _decode_record(record: _CommittedRecord) -> dict[str, object] | None:
    if record.record_type_id not in PLANNING_RECORD_TYPES:
        return None
    observed = fingerprint(CanonicalBytes(record.canonical_bytes, _VERSIONS)).digest.hex()
    if observed != record.fingerprint:
        return None
    try:
        envelope: object = json.loads(record.canonical_bytes)
    except UnicodeDecodeError, json.JSONDecodeError:
        return None
    if not isinstance(envelope, dict):
        return None
    namespace, name = record.record_type_id.rsplit(".", 1)
    fields = envelope.get("fields")
    expected_metadata = {
        "owner": "planning",
        "record_id": {
            "tenant_id": record.record_id.tenant_id.value,
            "value": record.record_id.value,
        },
        "record_type": {"name": name, "namespace": namespace},
        "versions": {
            "canonicalization": 1,
            "codec": 1,
            "schema": {"name": "record", "namespace": "chiplog.planning", "version": 1},
        },
    }
    if not isinstance(fields, dict) or any(
        envelope.get(key) != value for key, value in expected_metadata.items()
    ):
        return None
    return fields


def _verify_publication(publication: _CommittedPublication) -> bool:
    expected_types = (
        "chiplog.planning.planning_command",
        "chiplog.planning.authorization_evidence",
        "chiplog.planning.intention_line",
        "chiplog.planning.intention_line_revision",
        "chiplog.planning.committed_result",
    )
    if tuple(record.record_type_id for record in publication.records) != expected_types:
        return False
    if any(record.record_id.tenant_id != publication.tenant_id for record in publication.records):
        return False
    command, evidence, line, revision, result = publication.records
    decoded = tuple(_decode_record(record) for record in publication.records)
    if any(fields is None for fields in decoded):
        return False
    command_fields, evidence_fields, line_fields, revision_fields, result_fields = decoded
    assert command_fields is not None
    assert evidence_fields is not None
    assert line_fields is not None
    assert revision_fields is not None
    assert result_fields is not None
    expected_manifest = [
        {
            "ordinal": 0,
            "record_id": {
                "tenant_id": line.record_id.tenant_id.value,
                "value": line.record_id.value,
            },
            "record_type_id": line.record_type_id,
        },
        {
            "ordinal": 1,
            "record_id": {
                "tenant_id": revision.record_id.tenant_id.value,
                "value": revision.record_id.value,
            },
            "record_type_id": revision.record_type_id,
        },
    ]
    command_id = {"tenant_id": command.record_id.tenant_id.value, "value": command.record_id.value}
    line_id = {"tenant_id": line.record_id.tenant_id.value, "value": line.record_id.value}
    revision_id = {
        "tenant_id": revision.record_id.tenant_id.value,
        "value": revision.record_id.value,
    }
    evidence_id = {
        "tenant_id": evidence.record_id.tenant_id.value,
        "value": evidence.record_id.value,
    }
    trust = command_fields.get("trust_reference")
    common = {
        "authority_act_id": command_fields.get("authority_act_id"),
        "principal_id": command_fields.get("principal_id"),
        "tenant_id": publication.tenant_id.value,
    }
    request = {
        "command": {
            "authority_act_id": command_fields.get("authority_act_id"),
            "command_id": command_id,
            "intention_line_id": line_id,
            "purpose": revision_fields.get("purpose"),
            "revision_id": revision_id,
        },
        "context": {
            "permission_scope": command_fields.get("permission_scope"),
            "principal_id": command_fields.get("principal_id"),
            "tenant_id": publication.tenant_id.value,
            "trust_reference": trust,
        },
        "operation": "CREATE_INTENTION_LINE",
        "predecessor_result_id": None,
    }
    expected_request_fingerprint = hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    committed_manifest = tuple(
        (record.record_id, record.record_type_id, record.fingerprint)
        for record in publication.records[:-1]
    )
    committed_manifest_fields = [
        {
            "fingerprint": item_fingerprint,
            "record_id": {
                "tenant_id": record_id.tenant_id.value,
                "value": record_id.value,
            },
            "record_type_id": record_type_id,
        }
        for record_id, record_type_id, item_fingerprint in committed_manifest
    ]
    committed_batch_fingerprint = _batch_fingerprint(committed_manifest)
    record_manifest = tuple(
        (record.record_id, record.record_type_id, record.fingerprint)
        for record in publication.records
    )
    batch_fingerprint = _batch_fingerprint(record_manifest)
    publication_fingerprint = _publication_fingerprint(
        publication.request_fingerprint, publication.commit_sequence, record_manifest
    )
    allocation_binding = (
        (0, "chiplog.planning.intention_line", line.record_id),
        (1, "chiplog.planning.intention_line_revision", revision.record_id),
    )
    return (
        publication.command_id == command.record_id
        and command.record_id.value + ".authorization" == evidence.record_id.value
        and command.record_id.value + ".result" == result.record_id.value
        and command_fields
        == {
            **common,
            "allocation_manifest": expected_manifest,
            "operation": "CREATE_INTENTION_LINE",
            "permission_scope": "planning.create_intention_line",
            "trust_reference": trust,
        }
        and result_fields
        == {
            "allocation_manifest": expected_manifest,
            "authorization_evidence_id": evidence_id,
            "batch_fingerprint": committed_batch_fingerprint,
            "command_id": command_id,
            "commit_sequence": publication.commit_sequence,
            "record_manifest": committed_manifest_fields,
            "tenant_id": publication.tenant_id.value,
        }
        and line_fields.get("initial_revision_id") == revision_id
        and evidence_fields
        == {**common, "operation": "CREATE_INTENTION_LINE", "trust_reference": trust}
        and line_fields == {**common, "initial_revision_id": revision_id}
        and revision_fields
        == {
            **common,
            "activity": "ACTIVE",
            "intention_line_id": line_id,
            "ordinal": 0,
            "personal_outcome": "OPEN",
            "predecessor_revision_id": None,
            "purpose": revision_fields.get("purpose"),
        }
        and publication.request_fingerprint == expected_request_fingerprint
        and publication.record_manifest == record_manifest
        and publication.result.record_manifest == record_manifest
        and publication.batch_fingerprint == batch_fingerprint
        and publication.result.batch_fingerprint == batch_fingerprint
        and publication.result.command_id == command.record_id
        and publication.result.result_id == result.record_id
        and publication.result.intention_line_id == line.record_id
        and publication.result.revision_id == revision.record_id
        and publication.result.authorization_evidence_id == evidence.record_id
        and publication.result.commit_sequence == publication.commit_sequence
        and publication.result.allocation_manifest == allocation_binding
        and publication.result.result_fingerprint == result.fingerprint
        and publication.result.publication_fingerprint == publication_fingerprint
    )


def _reduce(rows: dict[str, _IntentionLineView], publication: _CommittedPublication) -> None:
    if not _verify_publication(publication):
        raise ValueError("unverified planning publication")
    line = publication.records[2]
    revision = publication.records[3]
    line_id = line.record_id
    if line_id.value in rows:
        raise ValueError("duplicate intention-line allocation")
    fields = _decode_record(revision)
    if fields is None:
        raise ValueError("unverified planning revision")
    rows[line_id.value] = _IntentionLineView(
        publication.tenant_id,
        line_id,
        revision.record_id,
        PrincipalId(str(fields["principal_id"])),
        str(fields["purpose"]),
        str(fields["activity"]),
        str(fields["personal_outcome"]),
        publication.commit_sequence,
    )


class _PlanningProjection:
    def __init__(
        self,
        tenant_id: TenantId,
        rows: dict[str, _IntentionLineView],
        checkpoint: _ProjectionCheckpoint,
    ) -> None:
        self._tenant_id = tenant_id
        self._rows = rows
        self.checkpoint = checkpoint

    def get(self, tenant_id: TenantId, intention_line_id: RecordId) -> _IntentionLineView | None:
        if tenant_id != self._tenant_id or intention_line_id.tenant_id != self._tenant_id:
            return None
        return self._rows.get(intention_line_id.value)


class _PlanningProjectionRebuilder:
    def __init__(self, repository: _PlanningReadStore) -> None:
        self._repository = repository

    def rebuild(
        self, tenant_id: TenantId, checkpoint: _ProjectionCheckpoint | None = None
    ) -> _PlanningProjection:
        publications = self._repository.committed_publications(tenant_id)
        for index, publication in enumerate(publications, start=1):
            if publication.tenant_id != tenant_id or publication.commit_sequence != index:
                raise ValueError("planning log is not a verified contiguous tenant stream")
            if not _verify_publication(publication):
                raise ValueError("planning log contains an unverified publication")
        rows: dict[str, _IntentionLineView] = {}
        frontier = 0
        checkpoint_valid = False
        if checkpoint is not None and 0 <= checkpoint.inclusive_frontier <= len(publications):
            prefix = publications[: checkpoint.inclusive_frontier]
            checkpoint_valid = (
                checkpoint.tenant_id == tenant_id
                and checkpoint.log_identity == _log_identity(tenant_id)
                and checkpoint.reducer_version == REDUCER_VERSION
                and checkpoint.schema_version == PROJECTION_SCHEMA_VERSION
                and checkpoint.source_digest == _source_digest(prefix)
            )
            if checkpoint_valid:
                candidate = {row.intention_line_id.value: row for row in checkpoint.rows}
                rebuilt: dict[str, _IntentionLineView] = {}
                for publication in prefix:
                    _reduce(rebuilt, publication)
                checkpoint_valid = candidate == rebuilt
                if checkpoint_valid:
                    rows = candidate
                    frontier = checkpoint.inclusive_frontier
        if not checkpoint_valid:
            rows = {}
            frontier = 0
        for publication in publications[frontier:]:
            _reduce(rows, publication)
        new_checkpoint = _ProjectionCheckpoint(
            tenant_id,
            _log_identity(tenant_id),
            len(publications),
            REDUCER_VERSION,
            PROJECTION_SCHEMA_VERSION,
            _source_digest(publications),
            tuple(rows[key] for key in sorted(rows)),
        )
        return _PlanningProjection(tenant_id, rows, new_checkpoint)
