"""Complete selected/physical custody equality and original-cut reconstruction."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

from chiplog.composition.r17_ingress_registry import (
    require_registered_profile,
    retained_cli_profile,
)
from chiplog.platform._ingress_domain import CustodySnapshot
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    BrokerIngressAuthentication,
    ExactRecordHead,
    ObservedPresence,
    OwnerRecordBytes,
    SingleOwnerBatch,
)
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.ingress_custody_records import (
    CustodyCommand,
    CustodyRecord,
    canonical,
    digest,
    prepare_custody,
)
from chiplog.platform.owner_decision_journal import OwnerJournalIntegrityError, OwnerJournalSnapshot
from chiplog.platform.owner_publications import SelectedOwnerDecision, source_commands
from chiplog.platform.read_ledger import BrokerReadState
from chiplog.platform.workspace_snapshot import workspace_snapshot

if TYPE_CHECKING:
    from chiplog.composition.r14_runtime import R14PlanningRuntime

COMMAND_SCHEMA = "chiplog.ingress.retained-command.v1"
RECORD_SCHEMA = "chiplog.ingress.custody-record.v1"


def wire_record(record: CustodyRecord) -> OwnerRecordBytes:
    raw = canonical(record)
    return OwnerRecordBytes(
        owner="broker_ingress",
        record_kind="ingress.custody",
        record_id=record.head().head,
        schema_id=RECORD_SCHEMA,
        canonical_bytes=raw,
        fingerprint=digest(raw),
    )


def read_manifest(
    command: CustodyCommand, records: tuple[CustodyRecord, ...]
) -> AuthoritativeReadManifest:
    state = BrokerReadState.model_validate_json(command.read_state_bytes)
    heads = tuple(
        ObservedPresence(
            head=ExactRecordHead(
                owner="broker_ingress",
                record_kind="ingress.custody",
                subject_id=record.command.token.token_id,
                record_id=record.head().head,
                fingerprint=digest(canonical(record)),
            )
        )
        for record in records
    )
    fingerprint = digest(
        canonical(command)
        + b"\x00"
        + json.dumps(
            [head.model_dump(mode="json") for head in heads],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    return AuthoritativeReadManifest(
        tenant_id=command.profile.tenant_id,
        tenant_frontier=command.tenant_frontier,
        expected_materialization_commitment=state.materialization_commitment,
        registry_head=command.profile.head().head,
        registry_fingerprint=command.profile.head().fingerprint,
        ordered_heads=heads,
        complete_manifest_fingerprint=fingerprint,
    )


def _retained_schema(raw: bytes) -> bool:
    try:
        body = json.loads(raw)
    except ValueError, UnicodeDecodeError:
        return False
    return isinstance(body, dict) and body.get("schema_id") in (COMMAND_SCHEMA, RECORD_SCHEMA)


def is_ingress(decision: SelectedOwnerDecision) -> bool:
    batch = decision.prepared.request
    return (
        batch.operation.startswith("ingress.")
        or any(
            command.owner == "broker_ingress"
            or command.schema_id == COMMAND_SCHEMA
            or _retained_schema(command.canonical_bytes)
            for command in source_commands(batch)
        )
        or any(
            row.owner == "broker_ingress"
            or row.schema_id == RECORD_SCHEMA
            or _retained_schema(row.canonical_bytes)
            for row in batch.complete_records
        )
    )


def validate_selected_ingress(
    history: OwnerJournalSnapshot,
) -> tuple[tuple[CustodyRecord, ...], CustodySnapshot]:
    """No current source access: independently selected bytes bind original authority."""
    from chiplog.adapters.driven.ingress_retained_source import decode_retained_observation

    records: list[CustodyRecord] = []
    snapshot = retained_cli_profile(history.tenant_id, "hermetic-database").empty()
    identity = "complete-ingress-history"
    try:
        for decision in history.decisions:
            if not is_ingress(decision):
                continue  # Other owner operations are outside the closed ingress slice.
            batch = decision.prepared.request
            identity = batch.identity.command_id
            if not isinstance(batch, SingleOwnerBatch) or batch.command.schema_id != COMMAND_SCHEMA:
                raise ValueError("unregistered selected custody batch")
            command = CustodyCommand.model_validate_json(batch.command.canonical_bytes)
            require_registered_profile(command.profile)
            observation = decode_retained_observation(command.retention.observation_bytes)
            scope = json.loads(observation.signed_metadata_bytes)["value"]["scope"]
            if (
                scope["tenant_id"] != command.profile.tenant_id
                or scope["database_id"] != command.profile.database_id
                or scope["limits"]
                != [
                    command.profile.maximum_items,
                    command.profile.maximum_total_bytes,
                    command.profile.maximum_item_bytes,
                ]
            ):
                raise ValueError("retained source has another tenant, database or reserve profile")
            state = BrokerReadState.model_validate_json(command.read_state_bytes)
            session, authentication = command.broker_session, batch.authentication
            if (
                batch.command.owner != "broker_ingress"
                or batch.operation != command.operation
                or canonical(command) != batch.command.canonical_bytes
                or batch.command.fingerprint != digest(canonical(command))
                or (
                    batch.identity.tenant_id,
                    batch.identity.command_id,
                    batch.identity.command_fingerprint,
                )
                != (command.profile.tenant_id, command.command_id, digest(canonical(command)))
                or command.profile.tenant_id != history.tenant_id
                or (
                    observation.slot_id,
                    observation.raw_digest,
                    observation.byte_count,
                    observation.proof,
                )
                != (
                    command.retention.slot_id,
                    command.retention.raw_digest,
                    command.retention.byte_count,
                    command.retention.proof,
                )
                or state.canonical_bytes() != command.read_state_bytes
                or state.owner_draining
                or (state.tenant_id, state.broker_epoch, state.owner_generation)
                != (session.tenant_id, session.broker_epoch, session.generation_id)
                or session.tenant_id != command.profile.tenant_id
                or session.owner_id != "broker"
                or not isinstance(authentication, BrokerIngressAuthentication)
            ):
                raise ValueError("historical custody source or broker invocation differs")
            proof = authentication.invocation
            if (
                proof.issuance_fingerprint
                != digest(
                    proof.issuance_id.encode()
                    + batch.command.canonical_bytes
                    + command.retention.observation_bytes
                )
                or (
                    proof.broker_epoch,
                    proof.broker_session,
                    proof.runtime_generation,
                    proof.operation_subject,
                )
                != (
                    str(session.broker_epoch),
                    session.session_id,
                    session.generation_id,
                    command.command_id,
                )
                or authentication.ingress_row != command.profile.source_identity
                or authentication.source_contract_head != command.profile.head().head
                or authentication.admission_epoch_head != command.profile.epoch().head
                or authentication.admission_fence != 0
                or batch.expected != read_manifest(command, tuple(records))
                or decision.tenant_commit_sequence != command.tenant_frontier + 1
                or decision.prepared.predecessor_commitment != state.materialization_commitment
            ):
                raise ValueError("historical ingress envelope or complete predecessor differs")
            if command.operation == "ingress.allocate_receipt_token" and (
                command.token.source.broker_epoch != str(session.broker_epoch)
                or command.token.source.broker_session != session.session_id
            ):
                raise ValueError("original allocated token used another broker session")
            record, snapshot = prepare_custody(
                command,
                records[-1].head() if records else None,
                snapshot,
            )
            wire = wire_record(record)
            if batch.complete_records != (wire,) or batch.complete_batch_fingerprint != digest(
                json.dumps(
                    [wire.model_dump(mode="json")],
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode()
            ):
                raise ValueError("selected custody output differs from exact reducer result")
            records.append(record)
        return tuple(records), snapshot
    except (ValueError, TypeError, KeyError, IndexError) as error:
        raise OwnerJournalIntegrityError("ingress_history", history.tenant_id, identity) from error


@dataclass(frozen=True)
class IngressHistory:
    records: tuple[CustodyRecord, ...]
    custody: CustodySnapshot
    tenant_frontier: int
    commitment: str
    journal_head: str | None


def read_ingress_history(
    runtime: R14PlanningRuntime,
    *,
    allow_pending: bool = False,
) -> IngressHistory:
    """Prove every ingress row/publication, including already-marked materialization."""
    identity = "complete-ingress-materialization"
    try:
        with runtime._authority_gate().hold():
            runtime._check_database_identity()
            history = runtime._owner_decisions().snapshot()
            records, custody = validate_selected_ingress(history)
            pending = tuple(
                d
                for d in history.decisions
                if d.prepared.request.identity.command_id not in history.materialized_command_ids
            )
            if pending and not allow_pending:
                raise ValueError("selected owner decision requires recovery")
            with workspace_snapshot(runtime._database) as physical:
                connection = physical.connection
                actual = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
                anchor = runtime._commitment_journal.load(runtime._tenant_id)
                if not pending and actual != anchor:
                    raise ValueError("physical custody state differs from independent anchor")
                if pending and (
                    len(pending) != 1
                    or anchor
                    not in (
                        pending[0].prepared.predecessor_commitment,
                        pending[0].resulting_commitment,
                    )
                    or actual
                    not in (
                        pending[0].prepared.predecessor_commitment,
                        pending[0].resulting_commitment,
                    )
                ):
                    raise ValueError("pending custody recovery has another physical predecessor")
                expected_ids: set[str] = set()
                expected_publications: set[tuple[str, str]] = set()
                for decision in history.decisions:
                    if not is_ingress(decision):
                        continue
                    batch = decision.prepared.request
                    identity = batch.identity.command_id
                    publication = connection.execute(
                        "SELECT request_fingerprint, commit_sequence, record_ids FROM publications "
                        "WHERE tenant_id=? AND operation_kind=? AND idempotency_key=?",
                        (runtime._tenant_id, batch.operation, identity),
                    ).fetchone()
                    rows = connection.execute(
                        "SELECT record_id,owner,schema_id,canonical_bytes,commit_sequence "
                        "FROM records "
                        "WHERE tenant_id=? AND commit_sequence=?",
                        (runtime._tenant_id, decision.tenant_commit_sequence),
                    ).fetchall()
                    publications = connection.execute(
                        "SELECT operation_kind,idempotency_key FROM publications "
                        "WHERE tenant_id=? AND commit_sequence=?",
                        (runtime._tenant_id, decision.tenant_commit_sequence),
                    ).fetchall()
                    expected = [
                        (
                            row.record_id,
                            row.owner,
                            row.schema_id,
                            row.canonical_bytes,
                            decision.tenant_commit_sequence,
                        )
                        for row in batch.complete_records
                    ]
                    if (
                        decision in pending
                        and publication is None
                        and not rows
                        and not publications
                    ):
                        if actual != decision.prepared.predecessor_commitment:
                            raise ValueError("absent pending custody has another predecessor")
                        # Exact ABSENT is recoverable, not yet materialized.
                        continue
                    if rows != expected or publication != (
                        batch.identity.command_fingerprint,
                        decision.tenant_commit_sequence,
                        "\n".join(row.record_id for row in batch.complete_records),
                    ):
                        raise ValueError("selected custody physical bytes or complete batch differ")
                    if publications != [(batch.operation, identity)]:
                        raise ValueError("selected custody sequence has another publication")
                    if decision in pending and actual != decision.resulting_commitment:
                        raise ValueError(
                            "complete pending custody has another resulting commitment"
                        )
                    expected_ids.update(row.record_id for row in batch.complete_records)
                    expected_publications.add((batch.operation, identity))
                observed_ids = {
                    row[0]
                    for row in connection.execute(
                        "SELECT record_id,owner,schema_id,canonical_bytes FROM records "
                        "WHERE tenant_id=?",
                        (runtime._tenant_id,),
                    )
                    if row[1] == "broker_ingress"
                    or row[2] == RECORD_SCHEMA
                    or _retained_schema(row[3])
                }
                if observed_ids != expected_ids:
                    raise ValueError("orphaned or omitted physical ingress record")
                observed_publications = set(
                    connection.execute(
                        "SELECT operation_kind,idempotency_key FROM publications "
                        "WHERE tenant_id=? AND operation_kind LIKE 'ingress.%'",
                        (runtime._tenant_id,),
                    )
                )
                if observed_publications != expected_publications:
                    raise ValueError("orphaned or omitted physical ingress publication")
                frontier = connection.execute(
                    "SELECT head FROM tenant_heads WHERE tenant_id=?",
                    (runtime._tenant_id,),
                ).fetchone()
                if frontier is None:
                    occupied = connection.execute(
                        "SELECT 1 FROM records WHERE tenant_id=? "
                        "UNION ALL SELECT 1 FROM publications WHERE tenant_id=? LIMIT 1",
                        (runtime._tenant_id, runtime._tenant_id),
                    ).fetchone()
                    if occupied is not None or any(
                        item not in pending or item.tenant_commit_sequence != 1
                        for item in history.decisions
                    ):
                        raise ValueError("missing tenant frontier for nonempty custody history")
                    frontier = (0,)
            if runtime._owner_decisions().snapshot() != history:
                raise ValueError("independent custody history changed during physical read")
            runtime._check_database_identity()
            with workspace_snapshot(runtime._database) as latest:
                if (
                    capture_authority_snapshot_commitment(latest.connection, runtime._tenant_id)
                    != actual
                ):
                    raise ValueError("physical custody changed during history read")
            return IngressHistory(records, custody, frontier[0], actual, history.head)
    except OwnerJournalIntegrityError:
        raise
    except (
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        sqlite3.Error,
    ) as error:
        raise OwnerJournalIntegrityError(
            "ingress_materialization", runtime._tenant_id, identity
        ) from error
