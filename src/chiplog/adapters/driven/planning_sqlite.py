"""SQLite driven adapter for the R5 planning persistence and projection ports."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Literal, TypeVar, cast

from chiplog.capabilities.planning import PlanningTrustDecision, PlanningTrustReference
from chiplog.capabilities.planning._planning import (
    _OwnedRecord,
    _PlanningCommittedResult,
    _PlanningOutcome,
    _PlanningPublication,
    _PlanningState,
    _publication_fingerprint,
)
from chiplog.capabilities.projections import PlanningProjectionQueries
from chiplog.domain_primitives import PrincipalId, RecordId, TenantId
from chiplog.domain_primitives.canonical import CanonicalBytes
from chiplog.domain_primitives.codec import fingerprint
from chiplog.domain_primitives.identity import SchemaId
from chiplog.domain_primitives.versions import (
    CanonicalizationVersion,
    CodecVersion,
    ProducingVersions,
)
from chiplog.platform._sqlite import (
    EventAppender,
    FenceAdvanceCommand,
    PhysicalPublicationCommand,
    PhysicalRecord,
)

_T = TypeVar("_T")
_OPERATION = "planning.create_intention_line"
_OWNER = "planning"
_SCHEMA = "chiplog.planning.record.v1"
_FENCE_GENERATION = "r6"
_VERSIONS = ProducingVersions(
    SchemaId("chiplog.planning", "record", 1), CodecVersion(1), CanonicalizationVersion(1)
)


class PlanningProjectionIntegrityError(RuntimeError):
    """Committed planning bytes cannot support a trustworthy projection read."""


def _integrity(
    tenant_id: str, record_id: str, error: BaseException
) -> PlanningProjectionIntegrityError:
    return PlanningProjectionIntegrityError(
        "planning projection integrity failure: "
        f"operation=render tenant={tenant_id} record_id={record_id}"
    )


def _record(tenant: TenantId, record_id: str, payload: bytes) -> _OwnedRecord:
    try:
        envelope = json.loads(payload)
        if not isinstance(envelope, dict):
            raise TypeError("canonical record envelope must be an object")
        fields = envelope.get("fields")
        record_type = envelope.get("record_type")
        if not isinstance(fields, dict) or not isinstance(record_type, dict):
            raise TypeError("canonical planning record is malformed")
        namespace, name = record_type.get("namespace"), record_type.get("name")
        if not isinstance(namespace, str) or not isinstance(name, str):
            raise TypeError("canonical planning record type is malformed")
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
        raise _integrity(tenant.value, record_id, error) from error
    return _OwnedRecord(
        RecordId(tenant, record_id),
        f"{namespace}.{name}",
        MappingProxyType(fields),
        payload,
        fingerprint(CanonicalBytes(payload, _VERSIONS)).digest.hex(),
    )


def _rid(tenant: TenantId, value: object, *, record_id: str) -> RecordId:
    if not isinstance(value, dict) or value.get("tenant_id") != tenant.value:
        raise _integrity(tenant.value, record_id, TypeError("foreign or malformed record id"))
    identifier = value.get("value")
    if not isinstance(identifier, str):
        raise _integrity(tenant.value, record_id, TypeError("malformed record id value"))
    return RecordId(tenant, identifier)


def _batch(manifest: tuple[tuple[RecordId, str, str], ...]) -> str:
    return hashlib.sha256(
        json.dumps(
            [
                {
                    "record_id": {"tenant_id": item.tenant_id.value, "value": item.value},
                    "record_type_id": kind,
                    "fingerprint": digest,
                }
                for item, kind, digest in manifest
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _publication(
    tenant: TenantId,
    command_id: str,
    request_fingerprint: str,
    commit_sequence: int,
    raw_records: tuple[tuple[str, bytes], ...],
) -> _PlanningPublication:
    records = tuple(_record(tenant, record_id, payload) for record_id, payload in raw_records)
    if len(records) != 5 or records[-1].record_type_id != "chiplog.planning.committed_result":
        raise _integrity(
            tenant.value, command_id, TypeError("planning publication record set is invalid")
        )
    fields = records[-1].fields
    try:
        result = _PlanningCommittedResult(
            _rid(tenant, fields["command_id"], record_id=records[-1].record_id.value),
            records[-1].record_id,
            records[2].record_id,
            records[3].record_id,
            _rid(
                tenant, fields["authorization_evidence_id"], record_id=records[-1].record_id.value
            ),
            int(cast(str | int, fields["commit_sequence"])),
            (
                (0, records[2].record_type_id, records[2].record_id),
                (1, records[3].record_type_id, records[3].record_id),
            ),
            tuple((item.record_id, item.record_type_id, item.fingerprint) for item in records),
            "",
            records[-1].fingerprint,
            "",
        )
    except (KeyError, TypeError, ValueError) as error:
        raise _integrity(tenant.value, records[-1].record_id.value, error) from error
    manifest = tuple((item.record_id, item.record_type_id, item.fingerprint) for item in records)
    result = replace(
        result,
        batch_fingerprint=_batch(manifest),
        publication_fingerprint=_publication_fingerprint(
            request_fingerprint, commit_sequence, manifest
        ),
    )
    return _PlanningPublication(
        tenant,
        RecordId(tenant, command_id),
        request_fingerprint,
        commit_sequence,
        records,
        result,
        manifest,
        _batch(manifest),
    )


class SQLitePlanningRepository:
    """R5 repository port backed by R3's single-writer SQLite publication log."""

    def __init__(
        self, database: Path, appender: EventAppender, loop: asyncio.AbstractEventLoop
    ) -> None:
        self._database = database
        self._appender = appender
        self._loop = loop
        self._trace: Callable[[str], None] | None = None
        self._commit_revalidator: (
            Callable[[PlanningTrustReference, str, RecordId], PlanningTrustDecision] | None
        ) = None

    def set_trace(self, trace: Callable[[str], None]) -> None:
        """R6 V2 observation hook installed only by executable composition."""
        self._trace = trace

    def set_commit_revalidator(
        self,
        revalidate: Callable[[PlanningTrustReference, str, RecordId], PlanningTrustDecision],
    ) -> None:
        """Install the R4 bridge used by the R3 commit boundary."""
        self._commit_revalidator = revalidate

    def _commit_guard(
        self, publication: _PlanningPublication
    ) -> Callable[[], Literal["DENIED", "STALE", "INDETERMINATE"] | None]:
        def guard() -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
            if self._commit_revalidator is None:
                return "INDETERMINATE"
            fields = publication.records[0].fields
            raw = fields.get("trust_reference")
            try:
                if not isinstance(raw, Mapping):
                    raise TypeError("missing trust reference")
                reference = PlanningTrustReference(
                    publication.tenant_id,
                    PrincipalId(str(raw["principal_id"])),
                    str(raw["contour"]),
                    str(raw["credential_head"]),
                    str(raw["session_head"]),
                    str(raw["source_head"]),
                    str(raw["trust_head"]),
                    str(raw["materialization_head"]),
                    int(raw["freshness_sequence"]),
                    str(raw["peer_credential"]),
                )
            except KeyError, TypeError, ValueError:
                return "INDETERMINATE"
            decision = self._commit_revalidator(
                reference, "CREATE_INTENTION_LINE", publication.result.intention_line_id
            )
            return None if decision.disposition == "VALID" else decision.disposition

        return guard

    def committed_publications(self, tenant_id: TenantId) -> tuple[_PlanningPublication, ...]:
        with closing(sqlite3.connect(self._database)) as connection:
            rows = connection.execute(
                "SELECT idempotency_key, request_fingerprint, commit_sequence, record_ids "
                "FROM publications WHERE tenant_id = ? AND operation_kind = ? "
                "ORDER BY commit_sequence",
                (tenant_id.value, _OPERATION),
            ).fetchall()
            result: list[_PlanningPublication] = []
            for command_id, request_fingerprint, sequence, raw_ids in rows:
                ids = tuple(str(raw_ids).split("\n"))
                placeholders = ",".join("?" for _ in ids)
                records_by_id = {
                    str(record_id): bytes(payload)
                    for record_id, payload in connection.execute(
                        "SELECT record_id, canonical_bytes FROM records "
                        f"WHERE tenant_id = ? AND record_id IN ({placeholders})",
                        (tenant_id.value, *ids),
                    )
                }
                if set(records_by_id) != set(ids):
                    raise _integrity(
                        tenant_id.value, str(command_id), TypeError("publication record is missing")
                    )
                result.append(
                    _publication(
                        tenant_id,
                        str(command_id),
                        str(request_fingerprint),
                        int(sequence),
                        tuple((record_id, records_by_id[record_id]) for record_id in ids),
                    )
                )
        return tuple(result)

    def ensure_fence(self, tenant_id: TenantId) -> None:
        asyncio.run_coroutine_threadsafe(
            self._appender.advance_fence(
                FenceAdvanceCommand(tenant_id.value, _FENCE_GENERATION, 0, allow_exact_replay=True)
            ),
            self._loop,
        ).result()

    def transact(self, tenant_id: TenantId, body: Callable[[_PlanningState], _T]) -> _T:
        if self._trace is not None:
            self._trace("planning.repository")
        existing = self.committed_publications(tenant_id)
        state = _PlanningState(tenant_id)
        for publication in existing:
            state.publications.append(publication)
            state.by_command[publication.command_id.value] = publication
            state.record_ids.update(record.record_id.value for record in publication.records)
            state.head = publication.commit_sequence
            state.version = publication.commit_sequence
        result = body(state)
        if len(state.publications) == len(existing):
            return result
        publication = state.publications[-1]
        if self._trace is not None:
            self._trace("r3.event_appender")
        physical = asyncio.run_coroutine_threadsafe(
            self._appender.submit(
                PhysicalPublicationCommand(
                    tenant_id.value,
                    _OPERATION,
                    publication.command_id.value,
                    publication.request_fingerprint,
                    len(existing),
                    _FENCE_GENERATION,
                    0,
                    0,
                    tuple(
                        PhysicalRecord(
                            item.record_id.value,
                            _OWNER,
                            _SCHEMA,
                            item.canonical_bytes,
                            hashlib.sha256(item.canonical_bytes).hexdigest(),
                        )
                        for item in publication.records
                    ),
                    admission_guard=self._commit_guard(publication),
                )
            ),
            self._loop,
        ).result()
        if physical.disposition == "COMMITTED":
            return result
        if isinstance(result, _PlanningOutcome):
            if physical.disposition == "REPLAY":
                replay = next(
                    (
                        item
                        for item in self.committed_publications(tenant_id)
                        if item.command_id == publication.command_id
                    ),
                    None,
                )
                return _PlanningOutcome("REPLAY", None if replay is None else replay.result, None)  # type: ignore[return-value]
            if physical.disposition == "STALE":
                return _PlanningOutcome("STALE", None, "durable planning head changed")  # type: ignore[return-value]
            if physical.disposition == "CONFLICT":
                return _PlanningOutcome("CONFLICT", None, "durable command identity conflict")  # type: ignore[return-value]
            if physical.disposition in {"DENIED", "INDETERMINATE"}:
                return _PlanningOutcome(
                    physical.disposition, None, "commit-time trust revalidation failed"
                )  # type: ignore[return-value]
        return result


class SQLitePlanningProjection:
    """Independent projection adapter that rebuilds only from committed publications."""

    def __init__(
        self,
        repository: SQLitePlanningRepository,
        rebuild: Callable[[TenantId], PlanningProjectionQueries],
    ) -> None:
        self._repository = repository
        self._rebuild = rebuild

    def render(self, tenant_id: str) -> tuple[str, ...]:
        tenant = TenantId(tenant_id)
        publications = self._repository.committed_publications(tenant)
        projection = self._rebuild(tenant)
        rendered = [f"tenant={tenant_id} records={sum(len(item.records) for item in publications)}"]
        for publication in publications:
            line_id = publication.result.intention_line_id
            view = projection.get(tenant, line_id)
            if view is None:
                raise PlanningProjectionIntegrityError(
                    "planning projection integrity failure: "
                    f"operation=render tenant={tenant_id} record_id={line_id.value}"
                )
            rendered.extend((line_id.value, f"purpose={view.purpose}"))
        return tuple(rendered)
