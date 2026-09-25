"""Raw owner-journal reconciliation for the private H1 inventory reader.

This module deliberately accepts captured bytes rather than opening a runtime.
The inventory orchestrator authenticates the common read cut, then supplies the
complete tenant journal and SQL rows here.  In particular, a present-day owner
journal snapshot is never an historical owner-cut witness.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, get_args

from pydantic import TypeAdapter, ValidationError

from chiplog.composition.h1_preseal_contracts import H1OwnerAsOfV1, H1SelectedSeal
from chiplog.composition.r14_execution_complete_seal_records import RetainedExecutionCompleteSealV2
from chiplog.platform._owner_publication_contracts import (
    BrokerOperation,
    OwnerCommandBytes,
    OwnerRecordBytes,
    RegisteredPublication,
)
from chiplog.platform.owner_decision_journal import (
    IndependentOwnerDecisionJournal,
    OwnerJournalIntegrityError,
    OwnerJournalSnapshot,
)
from chiplog.platform.owner_publications import source_commands

Phase = Literal["PRE_SEAL", "POST_SEAL", "HISTORICAL"]

# Keep this explicit.  A change to RegisteredPublication must change this
# registry and its test before a H1 empty receipt can cover the new envelope.
OWNER_BATCH_KINDS = frozenset(
    {
        "SINGLE_OWNER",
        "PLAN_EFFECT_ATOMIC",
        "CALL_EFFECT_ATOMIC",
        "COMPLETE_DELIVERY_ATOMIC",
        "COMPLETE_DELIVERY_ATOMIC_V2",
        "REJECTED_COMPLETION_ATOMIC_V1",
    }
)
OWNER_PUBLICATION_OPERATIONS = frozenset(get_args(BrokerOperation))
_REGISTERED_PUBLICATION: TypeAdapter[RegisteredPublication] = TypeAdapter(RegisteredPublication)


class H1OwnerCutFailure(RuntimeError):
    """Typed fail-closed result for the owner-cut leaf."""

    def __init__(self, code: Literal["UNSUPPORTED", "INCOMPLETE", "CORRUPT"], locator: str) -> None:
        self.code = code
        self.locator = locator
        super().__init__(f"{code}: owner-cut locator={locator}")


@dataclass(frozen=True, slots=True)
class OwnerJournalSqlPublication:
    operation: str
    command_id: str
    fingerprint: str
    commit_sequence: int
    record_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OwnerJournalSqlRecord:
    record_id: str
    owner: str
    schema: str
    raw: bytes
    commit_sequence: int


@dataclass(frozen=True, slots=True)
class H1OwnerDecision:
    decision_id: str
    decision_fingerprint: str
    command_id: str
    batch_kind: str
    tenant_commit_sequence: int
    source_command_bytes: tuple[bytes, ...]
    member_bytes: tuple[bytes, ...]
    materialized: bool


@dataclass(frozen=True, slots=True)
class H1OwnerCut:
    """Complete decoded owner prefix for a current, authenticated capture."""

    decisions: tuple[H1OwnerDecision, ...]
    materialized_command_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class _ParsedDecision:
    evidence: H1OwnerDecision
    request: RegisteredPublication
    resulting_commitment: str


def h1_owner_batch_registry() -> frozenset[str]:
    """Return every envelope kind this leaf exhaustively parses."""
    return OWNER_BATCH_KINDS


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _strict_json(raw: bytes) -> object:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique)


def _corrupt(locator: str) -> H1OwnerCutFailure:
    return H1OwnerCutFailure("CORRUPT", locator)


def _parse_raw_entries(
    entries: tuple[tuple[str, str | None, bytes], ...], tenant: str
) -> tuple[dict[str, _ParsedDecision], frozenset[str]]:
    selected: dict[str, _ParsedDecision] = {}
    materialized: set[str] = set()
    for index, (entry_id, predecessor, raw) in enumerate(entries):
        locator = f"owner-journal/{index}"
        if not isinstance(entry_id, str) or (
            predecessor is not None and not isinstance(predecessor, str)
        ):
            raise _corrupt(locator)
        if not isinstance(raw, bytes):
            raise _corrupt(locator)
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise _corrupt(locator) from error
        if not isinstance(decoded, dict) or _canonical(decoded) != raw:
            raise _corrupt(locator)
        if decoded.get("schema_id") != "chiplog.owner-decision.v1":
            raise _corrupt(locator)
        kind = decoded.get("kind")
        if kind == "SELECTED":
            try:
                request = _REGISTERED_PUBLICATION.validate_json(_canonical(decoded["request"]))
            except (KeyError, TypeError, ValidationError) as error:
                raise _corrupt(locator) from error
            command_id = request.identity.command_id
            if (
                request.kind not in OWNER_BATCH_KINDS
                or request.identity.tenant_id != tenant
                or request.expected.tenant_id != tenant
                or command_id in selected
                or not isinstance(decoded.get("issuance_id"), str)
                or not isinstance(decoded.get("fence_generation"), str)
                or type(decoded.get("fence_frontier")) is not int
                or not isinstance(decoded.get("predecessor_commitment"), str)
                or not isinstance(decoded.get("resulting_commitment"), str)
            ):
                raise _corrupt(locator)
            if selected:
                if set(selected) - materialized:
                    raise _corrupt(locator)
                previous = next(reversed(selected.values()))
                frontier = request.expected.tenant_frontier
                if frontier < previous.evidence.tenant_commit_sequence or (
                    frontier == previous.evidence.tenant_commit_sequence
                    and request.expected.expected_materialization_commitment
                    != previous.resulting_commitment
                ):
                    raise _corrupt(locator)
            source: tuple[OwnerCommandBytes, ...] = source_commands(request)
            members: tuple[OwnerRecordBytes, ...] = request.complete_records
            source_has_bad_fingerprint = any(
                hashlib.sha256(item.canonical_bytes).hexdigest() != item.fingerprint
                for item in source
            )
            members_have_bad_fingerprint = any(
                hashlib.sha256(item.canonical_bytes).hexdigest() != item.fingerprint
                for item in members
            )
            if (
                source_has_bad_fingerprint
                or members_have_bad_fingerprint
                or len({item.record_id for item in members}) != len(members)
            ):
                raise _corrupt(locator)
            selected[command_id] = _ParsedDecision(
                H1OwnerDecision(
                    entry_id,
                    hashlib.sha256(raw).hexdigest(),
                    command_id,
                    request.kind,
                    request.expected.tenant_frontier + 1,
                    tuple(item.canonical_bytes for item in source),
                    tuple(item.canonical_bytes for item in members),
                    False,
                ),
                request,
                str(decoded["resulting_commitment"]),
            )
        elif kind == "MATERIALIZED":
            raw_command_id = decoded.get("command_id")
            if not isinstance(raw_command_id, str):
                raise _corrupt(locator)
            command_id = str(raw_command_id)
            decision = selected.get(command_id)
            if (
                decision is None
                or command_id in materialized
                or decoded.get("tenant_id") != tenant
                or decoded.get("decision_id") != decision.evidence.decision_id
                or decoded.get("decision_fingerprint") != decision.evidence.decision_fingerprint
                or decoded.get("resulting_commitment") != decision.resulting_commitment
            ):
                raise _corrupt(locator)
            materialized.add(command_id)
        else:
            raise _corrupt(locator)
    return selected, frozenset(materialized)


def _validate_sql_membership(
    publications: tuple[OwnerJournalSqlPublication, ...], records: tuple[OwnerJournalSqlRecord, ...]
) -> dict[int, OwnerJournalSqlPublication]:
    records_by_sequence: dict[int, dict[str, OwnerJournalSqlRecord]] = {}
    for record in records:
        if (
            not isinstance(record.record_id, str)
            or not isinstance(record.owner, str)
            or not isinstance(record.schema, str)
            or not isinstance(record.raw, bytes)
            or type(record.commit_sequence) is not int
            or record.commit_sequence < 0
            or record.record_id in records_by_sequence.setdefault(record.commit_sequence, {})
        ):
            raise _corrupt(f"sql-record/{record.record_id}")
        records_by_sequence[record.commit_sequence][record.record_id] = record
    by_sequence: dict[int, OwnerJournalSqlPublication] = {}
    for publication in publications:
        if (
            not isinstance(publication.operation, str)
            or not isinstance(publication.command_id, str)
            or not isinstance(publication.fingerprint, str)
            or type(publication.commit_sequence) is not int
            or publication.commit_sequence < 0
            or publication.commit_sequence in by_sequence
            or len(publication.record_ids) != len(set(publication.record_ids))
            or any(not isinstance(record_id, str) for record_id in publication.record_ids)
            or set(publication.record_ids)
            != set(records_by_sequence.get(publication.commit_sequence, {}))
        ):
            raise _corrupt(f"sql-publication/{publication.command_id}")
        by_sequence[publication.commit_sequence] = publication
    if set(records_by_sequence) - set(by_sequence):
        raise _corrupt("sql-publication/orphan-record-sequence")
    return by_sequence


def _reconcile_materialized(
    decisions: dict[str, _ParsedDecision],
    materialized: frozenset[str],
    publications: dict[int, OwnerJournalSqlPublication],
    records: tuple[OwnerJournalSqlRecord, ...],
) -> tuple[H1OwnerDecision, ...]:
    record_map = {record.record_id: record for record in records}
    reconciled: list[H1OwnerDecision] = []
    for parsed in decisions.values():
        decision = parsed.evidence
        publication = publications.get(decision.tenant_commit_sequence)
        is_materialized = decision.command_id in materialized
        if is_materialized:
            expected_records = parsed.request.complete_records
            if (
                publication is None
                or publication.operation != parsed.request.operation
                or publication.command_id != decision.command_id
                or publication.fingerprint != parsed.request.identity.command_fingerprint
                or publication.record_ids != tuple(item.record_id for item in expected_records)
            ):
                raise _corrupt(f"selected/{decision.command_id}/missing-publication")
            for member in expected_records:
                physical = record_map.get(member.record_id)
                if (
                    physical is None
                    or physical.owner != member.owner
                    or physical.schema != member.schema_id
                    or physical.raw != member.canonical_bytes
                    or physical.commit_sequence != decision.tenant_commit_sequence
                ):
                    raise _corrupt(f"selected/{decision.command_id}/member/{member.record_id}")
        elif publication is not None and publication.command_id == decision.command_id:
            raise _corrupt(f"selected/{decision.command_id}/unmarked-publication")
        reconciled.append(
            H1OwnerDecision(
                decision.decision_id,
                decision.decision_fingerprint,
                decision.command_id,
                decision.batch_kind,
                decision.tenant_commit_sequence,
                decision.source_command_bytes,
                decision.member_bytes,
                is_materialized,
            )
        )
    return tuple(reconciled)


def _parse_h1_owner_asof(selected_seal: H1SelectedSeal, tenant: str) -> H1OwnerAsOfV1:
    """Decode the V2-only locator from the already authenticated loop decision."""
    try:
        decision = _strict_json(selected_seal.decision_bytes)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise _corrupt("selected-loop-decision") from error
    command = selected_seal.command
    if (
        not isinstance(decision, dict)
        or decision.get("version") != 1
        or decision.get("kind") != "DECIDED"
        or decision.get("operation_id") != command.idempotency_key
        or decision.get("operation_kind") != command.operation_kind
        or decision.get("expected_head") != command.expected_head
        or decision.get("fingerprint") != command.request_fingerprint
    ):
        raise _corrupt("selected-loop-decision")
    retained_raw = decision.get("execution_complete_seal")
    if not isinstance(retained_raw, str):
        raise H1OwnerCutFailure("UNSUPPORTED", "HISTORICAL_OWNER")
    try:
        retained = RetainedExecutionCompleteSealV2.model_validate_json(retained_raw)
    except ValueError as error:
        raise _corrupt("selected-loop-decision/v2-retained") from error
    if retained.canonical_bytes() != retained_raw.encode():
        raise _corrupt("selected-loop-decision/v2-retained")
    if "h1_owner_asof" not in decision:
        raise H1OwnerCutFailure("UNSUPPORTED", "HISTORICAL_OWNER")
    locator_value = decision["h1_owner_asof"]
    if locator_value is None:
        raise _corrupt("selected-loop-decision/h1_owner_asof")
    try:
        locator_raw = _canonical(locator_value)
        locator = H1OwnerAsOfV1.model_validate_json(locator_raw)
    except (TypeError, ValueError, ValidationError) as error:
        raise _corrupt("selected-loop-decision/h1_owner_asof") from error
    if locator.canonical_bytes() != locator_raw or locator.tenant_id != tenant:
        raise _corrupt("selected-loop-decision/h1_owner_asof")
    return locator


def _validate_selected_seal_membership(
    selected_seal: H1SelectedSeal,
    publications: dict[int, OwnerJournalSqlPublication],
    records: tuple[OwnerJournalSqlRecord, ...],
) -> None:
    command = selected_seal.command
    publication = publications.get(selected_seal.commit_sequence)
    if (
        publication is None
        or publication.operation != command.operation_kind
        or publication.command_id != command.idempotency_key
        or publication.fingerprint != command.request_fingerprint
        or publication.record_ids != tuple(record.record_id for record in command.records)
    ):
        raise _corrupt("selected-seal/publication")
    physical = {record.record_id: record for record in records}
    for member in command.records:
        row = physical.get(member.record_id)
        if (
            row is None
            or row.owner != member.owner
            or row.schema != member.schema_id
            or row.raw != member.canonical_bytes
            or row.commit_sequence != selected_seal.commit_sequence
        ):
            raise _corrupt(f"selected-seal/member/{member.record_id}")


def _parsed_snapshot(snapshot: OwnerJournalSnapshot, tenant: str) -> dict[str, _ParsedDecision]:
    if snapshot.tenant_id != tenant:
        raise _corrupt("owner-prefix/tenant")
    parsed: dict[str, _ParsedDecision] = {}
    for decision in snapshot.decisions:
        request = decision.prepared.request
        command_id = request.identity.command_id
        source: tuple[OwnerCommandBytes, ...] = source_commands(request)
        members: tuple[OwnerRecordBytes, ...] = request.complete_records
        if (
            request.kind not in OWNER_BATCH_KINDS
            or request.identity.tenant_id != tenant
            or request.expected.tenant_id != tenant
            or command_id in parsed
            or decision.tenant_commit_sequence != request.expected.tenant_frontier + 1
            or any(
                hashlib.sha256(item.canonical_bytes).hexdigest() != item.fingerprint
                for item in source
            )
            or any(
                hashlib.sha256(item.canonical_bytes).hexdigest() != item.fingerprint
                for item in members
            )
        ):
            raise _corrupt(f"owner-prefix/{command_id}")
        parsed[command_id] = _ParsedDecision(
            H1OwnerDecision(
                decision.decision_id,
                decision.decision_fingerprint,
                command_id,
                request.kind,
                decision.tenant_commit_sequence,
                tuple(item.canonical_bytes for item in source),
                tuple(item.canonical_bytes for item in members),
                command_id in snapshot.materialized_command_ids,
            ),
            request,
            decision.resulting_commitment,
        )
    return parsed


def reconcile_h1_historical_owner_cut(
    *,
    tenant: str,
    owner_journal: IndependentOwnerDecisionJournal,
    selected_seal: H1SelectedSeal,
    publications: tuple[OwnerJournalSqlPublication, ...],
    records: tuple[OwnerJournalSqlRecord, ...],
    known_non_owner_operations: frozenset[str],
) -> H1OwnerCut:
    """Reconcile the exact V2 locator prefix with the SQL view through its seal.

    ``publications`` and ``records`` must be the complete tenant view ending at
    the selected seal.  The caller supplies registered non-owner operation
    kinds; an unclassified operation is not silently treated as irrelevant.
    """
    locator = _parse_h1_owner_asof(selected_seal, tenant)
    try:
        snapshot = owner_journal.snapshot_at(locator.owner_head)
    except OwnerJournalIntegrityError as error:
        raise _corrupt("owner-prefix") from error
    if snapshot.head != locator.owner_head:
        raise _corrupt("owner-prefix/head")
    parsed = _parsed_snapshot(snapshot, tenant)
    pending = set(parsed) - set(snapshot.materialized_command_ids)
    if pending:
        raise H1OwnerCutFailure("INCOMPLETE", f"owner-prefix/pending/{sorted(pending)}")
    sql = _validate_sql_membership(publications, records)
    if any(sequence > selected_seal.commit_sequence for sequence in sql):
        raise _corrupt("sql-publication/beyond-selected-seal")
    _validate_selected_seal_membership(selected_seal, sql, records)
    for decision in parsed.values():
        if decision.evidence.tenant_commit_sequence >= selected_seal.commit_sequence:
            raise _corrupt(f"selected/{decision.evidence.command_id}/at-or-after-seal")
    decisions = _reconcile_materialized(parsed, snapshot.materialized_command_ids, sql, records)
    claimed = {decision.tenant_commit_sequence for decision in decisions}
    for sequence, publication in sql.items():
        if sequence == selected_seal.commit_sequence:
            continue
        if publication.operation in OWNER_PUBLICATION_OPERATIONS:
            if sequence not in claimed:
                raise _corrupt(f"owner-publication/unclaimed/{sequence}")
        elif publication.operation not in known_non_owner_operations:
            raise H1OwnerCutFailure(
                "UNSUPPORTED", f"sql-publication/operation/{publication.operation}"
            )
    return H1OwnerCut(decisions, snapshot.materialized_command_ids)


def reconcile_h1_owner_cut(
    *,
    tenant: str,
    raw_entries: tuple[tuple[str, str | None, bytes], ...],
    publications: tuple[OwnerJournalSqlPublication, ...],
    records: tuple[OwnerJournalSqlRecord, ...],
    phase: Phase,
) -> H1OwnerCut:
    """Decode all registered owner envelopes and reconcile current SQL membership.

    Historical calls authenticate the supplied complete raw/SQL inputs first,
    then fail explicitly: the platform does not retain a marker-to-SQL-cut
    witness, so today's MATERIALIZED markers cannot be projected to an old cut.
    """
    if phase not in ("PRE_SEAL", "POST_SEAL", "HISTORICAL"):
        raise ValueError("unknown H1 owner-cut phase")
    selected, materialized = _parse_raw_entries(raw_entries, tenant)
    sql = _validate_sql_membership(publications, records)
    decisions = _reconcile_materialized(selected, materialized, sql, records)
    if phase == "HISTORICAL":
        raise H1OwnerCutFailure("UNSUPPORTED", "HISTORICAL_OWNER")
    return H1OwnerCut(decisions, materialized)


__all__ = [
    "OWNER_BATCH_KINDS",
    "OWNER_PUBLICATION_OPERATIONS",
    "H1OwnerCut",
    "H1OwnerCutFailure",
    "H1OwnerDecision",
    "OwnerJournalSqlPublication",
    "OwnerJournalSqlRecord",
    "h1_owner_batch_registry",
    "reconcile_h1_historical_owner_cut",
    "reconcile_h1_owner_cut",
]
