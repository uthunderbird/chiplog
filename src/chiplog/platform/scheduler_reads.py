"""Broker-private scheduler extraction from one physical and protected journal cut.

This reader deliberately returns unresolved historical source admission. The root
registry must bind the retained exact registry heads to admitted source closures
before invoking the historical startup validator or releasing any authority.
"""

from __future__ import annotations

import base64
import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import TypeAdapter

from chiplog.capabilities.agent_loop.contracts import RunRecord
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    DecideIntervalCommand,
    LeaseTransitionCommand,
    PhysicalRootRolloverCommand,
    ResolveIntervalCommand,
)
from chiplog.capabilities.agent_loop.scheduler_materialization import SchedulerCanonicalMember
from chiplog.capabilities.agent_loop.scheduler_preparation import (
    ConfigurationBoundCommand,
    ConfigurationCommand,
    ConfigurationPreparationRequest,
    IntervalPreparationRequest,
    LeasePreparationRequest,
    RolloverPreparationRequest,
)
from chiplog.capabilities.agent_loop.scheduler_startup import (
    MaterializedSchedulerRow,
    SelectedSchedulerBatch,
)
from chiplog.platform.authority_reads import (
    AuthorityCommitmentJournal,
    capture_authority_snapshot_commitment,
)
from chiplog.platform.owner_decision_journal import IndependentOwnerDecisionJournal
from chiplog.platform.owner_publications import SelectedOwnerDecision
from chiplog.platform.workspace_snapshot import read_connection

Preparation = (
    ConfigurationPreparationRequest
    | IntervalPreparationRequest
    | LeasePreparationRequest
    | RolloverPreparationRequest
)


class SchedulerReadIntegrityError(RuntimeError):
    def __init__(self, tenant: str, identity: str) -> None:
        super().__init__(
            f"scheduler integrity: operation=read_materialized_scheduler "
            f"tenant={tenant} record={identity}"
        )


@dataclass(frozen=True)
class HistoricalSchedulerRequest:
    """Protected original selection and decoded exact bytes, not fresh issuance."""

    selection: SelectedOwnerDecision
    preparation: Preparation
    registry: Present


@dataclass(frozen=True)
class SchedulerSourceAdmissionUnresolved:
    tenant_id: str
    tenant_frontier: int
    materialization_commitment: str
    independent_journal_head: str | None
    physical_database_path: str
    physical_device: int
    physical_inode: int
    protected_decisions: tuple[SelectedOwnerDecision, ...]
    historical_requests: tuple[HistoricalSchedulerRequest, ...]
    selected: tuple[SelectedSchedulerBatch, ...]
    materialized: tuple[MaterializedSchedulerRow, ...]
    disposition: Literal["SOURCE_ADMISSION_UNRESOLVED"] = "SOURCE_ADMISSION_UNRESOLVED"


_CONFIGURATION: TypeAdapter[ConfigurationCommand] = TypeAdapter(ConfigurationCommand)


def _historical_request(decision: SelectedOwnerDecision) -> HistoricalSchedulerRequest:
    request = decision.prepared.request
    if request.kind != "SINGLE_OWNER" or request.command.owner != "agent_loop":
        raise ValueError("scheduler selection is not one exact owner request")
    raw = request.command.canonical_bytes
    operation = request.operation
    parsed: Preparation
    if operation in {
        "scheduler.genesis",
        "scheduler.amend_schedule",
        "scheduler.amend_policy",
        "scheduler.replace_bound",
    }:
        schema = "chiplog.scheduler.configuration-preparation.v1"
        parsed = ConfigurationPreparationRequest.model_validate_json(raw)
        configuration = _CONFIGURATION.validate_json(parsed.command_bytes)
        if operation != "scheduler." + configuration.kind.lower():
            raise ValueError("configuration original operation differs from command")
        identity = (
            configuration.command.identity
            if isinstance(configuration, ConfigurationBoundCommand)
            else configuration.identity
        )
        inner_bytes = configuration.canonical_bytes()
    elif operation in {"scheduler.decide_interval", "scheduler.resolve_interval"}:
        schema = "chiplog.scheduler.interval-preparation.v1"
        parsed = IntervalPreparationRequest.model_validate_json(raw)
        interval = (
            DecideIntervalCommand.model_validate_json(parsed.command_bytes)
            if operation == "scheduler.decide_interval"
            else ResolveIntervalCommand.model_validate_json(parsed.command_bytes)
        )
        identity, inner_bytes = interval.identity, interval.canonical_bytes()
    elif operation in {"scheduler.acquire", "scheduler.renew", "scheduler.takeover"}:
        schema = "chiplog.scheduler.lease-preparation.v1"
        parsed = LeasePreparationRequest.model_validate_json(raw)
        lease = LeaseTransitionCommand.model_validate_json(parsed.command_bytes)
        identity, inner_bytes = lease.identity, lease.canonical_bytes()
        if operation != "scheduler." + lease.kind.lower():
            raise ValueError("lease original operation differs from command")
    elif operation == "scheduler.rollover":
        schema = "chiplog.scheduler.rollover-preparation.v1"
        parsed = RolloverPreparationRequest.model_validate_json(raw)
        rollover = PhysicalRootRolloverCommand.model_validate_json(parsed.command_bytes)
        identity, inner_bytes = rollover.identity, rollover.canonical_bytes()
    else:
        raise ValueError("unknown scheduler operation source mapping")
    if (
        request.command.schema_id != schema
        or parsed.canonical_bytes() != raw
        or parsed.operation != operation
        or inner_bytes != parsed.command_bytes
    ):
        raise ValueError("original request schema/operation/canonical bytes differ")
    if (
        identity.command_id != request.identity.command_id
        or parsed.context.tenant_id != request.identity.tenant_id
    ):
        raise ValueError("protected command identity or context cannot be remapped")
    if isinstance(parsed, LeasePreparationRequest):
        tenant = parsed.snapshot.tenant_id
        frontier = parsed.snapshot.tenant_frontier
        commitment = parsed.snapshot.materialization_commitment
        registry = Present(
            head=parsed.snapshot.registry_head, fingerprint=parsed.snapshot.registry_fingerprint
        )
    else:
        cut = parsed.snapshot.cut
        tenant, frontier, commitment = (
            cut.tenant_id,
            cut.tenant_frontier,
            cut.materialization_commitment,
        )
        registry = Present(head=cut.registry_head, fingerprint=cut.registry_fingerprint)
        if (
            cut.authorized_context != parsed.context
            or cut.authorized_command_fingerprint != hashlib.sha256(inner_bytes).hexdigest()
        ):
            raise ValueError("original prepared cut does not bind its context/command")
    expected = request.expected
    if (
        tenant != request.identity.tenant_id
        or frontier != expected.tenant_frontier
        or commitment != expected.expected_materialization_commitment
        or registry
        != Present(head=expected.registry_head, fingerprint=expected.registry_fingerprint)
    ):
        raise ValueError("selected read manifest differs from original preparation cut")
    return HistoricalSchedulerRequest(decision, parsed, registry)


def _scheduler_record(owner: str, schema: str, payload: bytes) -> bool:
    if schema.startswith("chiplog.scheduler."):
        if owner != "agent_loop":
            raise ValueError("scheduler schema has foreign semantic owner")
        return True
    if owner != "agent_loop":
        return False
    if schema != "chiplog.agent-loop.record.v1":
        raise ValueError("unknown agent-loop schema requires registered external mapper")
    run = RunRecord.model_validate_json(payload)
    if run.canonical_bytes() != payload:
        raise ValueError("noncanonical authoritative Run record")
    return run.root_binding != "NOT_APPLICABLE"


def read_materialized_scheduler(
    database: Path,
    tenant_id: str,
    journal: IndependentOwnerDecisionJournal,
    commitments: AuthorityCommitmentJournal,
) -> SchedulerSourceAdmissionUnresolved:
    """Extract exact history; source admission and action release remain unavailable."""
    identity = "complete-scheduler-cut"
    try:
        physical_path = database.resolve(strict=True)
        stat = physical_path.stat()
        physical_identity = (stat.st_dev, stat.st_ino)
        before = journal.snapshot()
        if before.tenant_id != tenant_id or {
            decision.prepared.request.identity.command_id for decision in before.decisions
        } != set(before.materialized_command_ids):
            raise ValueError("foreign or pending independent selected history")
        anchored = commitments.load(tenant_id)
        with read_connection(database) as connection:
            commitment = capture_authority_snapshot_commitment(connection, tenant_id)
            if anchored is None or commitment != anchored:
                raise ValueError("physical authority differs from independent AMR anchor")
            head = connection.execute(
                "SELECT head FROM main.tenant_heads WHERE tenant_id = ?", (tenant_id,)
            ).fetchone()
            frontier = 0 if head is None else head[0]
            if type(frontier) is not int or frontier < 0:
                raise ValueError("invalid physical tenant frontier")
            actual_scheduler: set[str] = set()
            for row in connection.execute(
                "SELECT record_id, owner, schema_id, canonical_bytes "
                "FROM main.records WHERE tenant_id = ?",
                (tenant_id,),
            ):
                identity = row[0]
                if _scheduler_record(row[1], row[2], row[3]):
                    actual_scheduler.add(identity)
            expected_scheduler: set[str] = set()
            physical_rows: list[MaterializedSchedulerRow] = []
            batches: list[SelectedSchedulerBatch] = []
            historical: list[HistoricalSchedulerRequest] = []
            physical_id = f"{physical_path}:{physical_identity[0]}:{physical_identity[1]}"
            for decision in before.decisions:
                request = decision.prepared.request
                identity = request.identity.command_id
                manifest = tuple(record.record_id for record in request.complete_records)
                publication = connection.execute(
                    "SELECT request_fingerprint, commit_sequence, record_ids "
                    "FROM main.publications WHERE tenant_id = ? "
                    "AND operation_kind = ? AND idempotency_key = ?",
                    (tenant_id, request.operation, identity),
                ).fetchone()
                if (
                    publication
                    != (
                        request.identity.command_fingerprint,
                        decision.tenant_commit_sequence,
                        "\n".join(manifest),
                    )
                    or decision.tenant_commit_sequence > frontier
                ):
                    raise ValueError("selected decision differs from physical publication")
                actual_ids = {
                    row[0]
                    for row in connection.execute(
                        "SELECT record_id FROM main.records "
                        "WHERE tenant_id = ? AND commit_sequence = ?",
                        (tenant_id, decision.tenant_commit_sequence),
                    )
                }
                if actual_ids != set(manifest):
                    raise ValueError("whole selected commit has missing or additional members")
                is_scheduler = request.operation.startswith("scheduler.")
                original = _historical_request(decision) if is_scheduler else None
                members: list[SchedulerCanonicalMember] = []
                for ordinal, record in enumerate(request.complete_records):
                    identity = record.record_id
                    actual = connection.execute(
                        "SELECT owner, schema_id, canonical_bytes, commit_sequence "
                        "FROM main.records WHERE tenant_id = ? AND record_id = ?",
                        (tenant_id, identity),
                    ).fetchone()
                    if actual != (
                        record.owner,
                        record.schema_id,
                        record.canonical_bytes,
                        decision.tenant_commit_sequence,
                    ):
                        raise ValueError("selected owner bytes differ from physical row")
                    relevant = _scheduler_record(
                        record.owner, record.schema_id, record.canonical_bytes
                    )
                    if relevant != is_scheduler:
                        raise ValueError(
                            "scheduler membership needs missing external lineage mapper"
                        )
                    if not relevant:
                        continue
                    if identity in expected_scheduler:
                        raise ValueError("duplicate scheduler identity in selected batches")
                    expected_scheduler.add(identity)
                    member = SchedulerCanonicalMember(
                        record_kind=record.record_kind,
                        record_id=identity,
                        schema_id=record.schema_id,
                        canonical_base64=base64.b64encode(actual[2]).decode(),
                        fingerprint=hashlib.sha256(actual[2]).hexdigest(),
                    )
                    if member.fingerprint != record.fingerprint:
                        raise ValueError("physical selected fingerprint mismatch")
                    members.append(member)
                    physical_rows.append(
                        MaterializedSchedulerRow(
                            tenant_id=tenant_id,
                            physical_database_id=physical_id,
                            publication_ordinal=decision.tenant_commit_sequence,
                            member_ordinal=ordinal,
                            decision=Present(
                                head=decision.decision_head,
                                fingerprint=decision.decision_fingerprint,
                            ),
                            record=member,
                        )
                    )
                if original is not None:
                    historical.append(original)
                    batches.append(
                        SelectedSchedulerBatch(
                            tenant_id=tenant_id,
                            physical_database_id=physical_id,
                            publication_ordinal=decision.tenant_commit_sequence,
                            decision=Present(
                                head=decision.decision_head,
                                fingerprint=decision.decision_fingerprint,
                            ),
                            historical_validator="chiplog.scheduler.selected-records.v1",
                            context=original.preparation.context,
                            command_id=request.identity.command_id,
                            operation=request.operation,
                            records=tuple(members),
                        )
                    )
            if expected_scheduler != actual_scheduler:
                raise ValueError("scheduler scope has omitted tail or unknown physical rows")
            if journal.snapshot() != before or commitments.load(tenant_id) != anchored:
                raise ValueError("independent authority changed during cut acquisition")
            stat = database.stat()
            if (
                database.resolve(strict=True) != physical_path
                or (stat.st_dev, stat.st_ino) != physical_identity
            ):
                raise ValueError("physical database changed during cut acquisition")
            return SchedulerSourceAdmissionUnresolved(
                tenant_id,
                frontier,
                commitment,
                before.head,
                str(physical_path),
                physical_identity[0],
                physical_identity[1],
                before.decisions,
                tuple(historical),
                tuple(batches),
                tuple(physical_rows),
            )
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        raise SchedulerReadIntegrityError(tenant_id, identity) from error
