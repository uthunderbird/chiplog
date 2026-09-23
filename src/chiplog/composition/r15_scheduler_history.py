"""Mechanical physical membership at a caller-owned transaction cut.

The caller must independently authenticate selection, database identity and the
current authority anchor. COMPLETE establishes none of those on its own.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import nullcontext
from typing import TYPE_CHECKING, Literal

from chiplog.adapters.driven.loop_sqlite import OWNER, SCHEMA, LoopIntegrityError
from chiplog.adapters.driven.r9_fence import CONVERSATION_OWNER, CONVERSATION_SCHEMA
from chiplog.capabilities.agent_loop.contracts import LoopSnapshot, RunRecord
from chiplog.capabilities.agent_loop.domain import validate_record
from chiplog.capabilities.agent_loop.recovery_contracts import Absent
from chiplog.capabilities.agent_loop.scheduler_configuration import ConfigurationSnapshot
from chiplog.composition.scheduler_source_registry import (
    AdmittedSchedulerStartup,
    read_admitted_scheduler_startup,
)
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.workspace_snapshot import _CURRENT, read_connection, workspace_snapshot

if TYPE_CHECKING:
    from chiplog.composition.r14_runtime import R14PlanningRuntime

_GENESIS_SCHEMAS = (
    "chiplog.scheduler.schedule-definition.v1",
    "chiplog.scheduler.missed-policy.v1",
    "chiplog.scheduler.interval-bound.v1",
)
_CONFIGURATION_SCHEMAS = {
    "scheduler.genesis": _GENESIS_SCHEMAS,
    "scheduler.amend_schedule": (_GENESIS_SCHEMAS[0],),
    "scheduler.amend_policy": (_GENESIS_SCHEMAS[1],),
    "scheduler.replace_bound": (_GENESIS_SCHEMAS[2],),
}


def configuration_snapshot(
    startup: AdmittedSchedulerStartup, schedule_id: str
) -> ConfigurationSnapshot:
    """Project a caller-authenticated startup index; this function issues nothing."""
    index = startup.index
    schedules = tuple(item for item in index.schedules if item.schedule_id == schedule_id)
    policies = tuple(item for item in index.policies if item.schedule_id == schedule_id)
    bounds = tuple(item for key, item in index.bounds if key == schedule_id)
    holds = tuple(
        item
        for item in index.active_holds
        if item.boundary.schedule_definition_head.schedule_id == schedule_id
    )
    if len(schedules) != 1 or len(policies) != 1 or len(bounds) != 1 or len(holds) > 1:
        raise ValueError("configuration requires one complete registered schedule")
    return ConfigurationSnapshot(
        schedule=schedules[0],
        policy=policies[0],
        bound=bounds[0],
        active_hold=holds[0].hold if holds else Absent(),
    )


def inspect_publication(
    connection: sqlite3.Connection,
    command: PhysicalPublicationCommand,
    selected_sequence: int,
) -> Literal["ABSENT", "COMPLETE", "CONFLICT"]:
    """Compare an exact selected command without writing or invoking its guards.

    Invalid expected input or a missing read transaction raises ValueError.
    Valid expectations with partial, extra or changed rows return CONFLICT.
    """
    if not connection.in_transaction:
        raise ValueError("publication readback requires an active transaction")
    if not isinstance(command, PhysicalPublicationCommand):
        raise ValueError("publication readback requires an exact physical command")
    if (
        type(command.expected_head) is not int
        or command.expected_head < 0
        or type(selected_sequence) is not int
        or selected_sequence != command.expected_head + 1
    ):
        raise ValueError("selected sequence must equal the exact predecessor plus one")
    if any(
        type(value) is not str or not value
        for value in (
            command.tenant_id,
            command.operation_kind,
            command.idempotency_key,
            command.request_fingerprint,
        )
    ):
        raise ValueError("publication identity fields must be nonempty strings")
    if type(command.records) is not tuple or not command.records:
        raise ValueError("publication records must be a nonempty tuple")
    for record in command.records:
        if not isinstance(record, PhysicalRecord) or any(
            type(value) is not str or not value
            for value in (record.record_id, record.owner, record.schema_id)
        ):
            raise ValueError("publication record identity fields must be nonempty strings")
        if "\n" in record.record_id or "\r" in record.record_id:
            raise ValueError("publication record identity contains a newline")
        if (
            type(record.canonical_bytes) is not bytes
            or record.fingerprint != hashlib.sha256(record.canonical_bytes).hexdigest()
        ):
            raise ValueError("publication record canonical bytes or fingerprint differ")
    record_ids = tuple(record.record_id for record in command.records)
    if len(set(record_ids)) != len(record_ids):
        raise ValueError("publication record identities must be unique")

    identity_rows = connection.execute(
        """SELECT tenant_id, operation_kind, idempotency_key, request_fingerprint,
                  commit_sequence, record_ids FROM main.publications
           WHERE tenant_id = ? AND operation_kind = ? AND idempotency_key = ?""",
        (command.tenant_id, command.operation_kind, command.idempotency_key),
    ).fetchall()
    expected_rows = tuple(
        (
            command.tenant_id,
            record.record_id,
            record.owner,
            record.schema_id,
            record.canonical_bytes,
            selected_sequence,
        )
        for record in command.records
    )
    individual_rows = tuple(
        tuple(row)
        for record_id in record_ids
        for row in connection.execute(
            """SELECT tenant_id, record_id, owner, schema_id, canonical_bytes, commit_sequence
               FROM main.records WHERE tenant_id = ? AND record_id = ?""",
            (command.tenant_id, record_id),
        )
    )
    sequence_records = connection.execute(
        """SELECT tenant_id, record_id, owner, schema_id, canonical_bytes, commit_sequence
           FROM main.records WHERE tenant_id = ? AND commit_sequence = ? ORDER BY record_id""",
        (command.tenant_id, selected_sequence),
    ).fetchall()
    sequence_publications = connection.execute(
        """SELECT tenant_id, operation_kind, idempotency_key, request_fingerprint,
                  commit_sequence, record_ids FROM main.publications
           WHERE tenant_id = ? AND commit_sequence = ?""",
        (command.tenant_id, selected_sequence),
    ).fetchall()
    if not (identity_rows or individual_rows or sequence_records or sequence_publications):
        return "ABSENT"
    expected_publication = (
        command.tenant_id,
        command.operation_kind,
        command.idempotency_key,
        command.request_fingerprint,
        selected_sequence,
        "\n".join(record_ids),
    )
    if (
        tuple(map(tuple, identity_rows)) == (expected_publication,)
        and individual_rows == expected_rows
        and tuple(map(tuple, sequence_records))
        == tuple(sorted(expected_rows, key=lambda row: row[1]))
        and tuple(map(tuple, sequence_publications)) == (expected_publication,)
    ):
        return "COMPLETE"
    return "CONFLICT"


def read_loop_snapshot(runtime: R14PlanningRuntime) -> LoopSnapshot:
    """Project only authenticated legacy Runs from the joint scheduler/Run cut.

    The private ambient-cut observation avoids opening a second SQL transaction
    when invoked inside the workspace reader. Only workspace_snapshot owns its
    ContextVar; read_connection verifies a joined database and live transaction.
    Historical companions are compared to selected bytes, never regenerated.
    """
    identity = "<enumeration>"
    try:
        with runtime._authority_gate().hold():
            runtime._require_no_pending()
            tenant = runtime._tenant_id
            journal = runtime._loop_decisions()
            before = tuple(journal.entries())
            decisions = [json.loads(raw) for _, _, raw in before]
            decisions = [entry for entry in decisions if entry.get("kind") == "DECIDED"]
            identities = [entry["operation_id"] for entry in decisions]
            if len(identities) != len(set(identities)):
                raise ValueError("duplicate independently selected loop identity")
            selected = [
                runtime._publication(entry)
                for entry in decisions
                if str(entry.get("operation_kind", "")).startswith("agent_loop")
            ]
            selected.sort(key=lambda command: command.expected_head)
            context = (
                nullcontext()
                if _CURRENT.get() is not None
                else workspace_snapshot(runtime._database)
            )
            with context, read_connection(runtime._database) as connection:
                anchored = runtime._commitment_journal.load(tenant)
                if anchored != capture_authority_snapshot_commitment(connection, tenant):
                    raise ValueError("loop read cut differs from independent anchor")
                startup = read_admitted_scheduler_startup(
                    runtime._database,
                    tenant,
                    runtime._owner_decisions(),
                    runtime._commitment_journal,
                )
                if not isinstance(startup, AdmittedSchedulerStartup):
                    raise ValueError("scheduler source admission unresolved")
                if (
                    sum(batch.operation == "scheduler.genesis" for batch in startup.cut.selected)
                    > 1
                ):
                    raise ValueError("only one global scheduler GENESIS is registered")
                expected_ids: set[str] = set()
                expected_publications: set[tuple[str, str]] = set()
                for batch, historical in zip(
                    startup.cut.selected, startup.cut.historical_requests, strict=True
                ):
                    identity = batch.command_id
                    if (
                        batch.operation not in _CONFIGURATION_SCHEMAS
                        or tuple(member.schema_id for member in batch.records)
                        != _CONFIGURATION_SCHEMAS[batch.operation]
                    ):
                        raise ValueError("unregistered scheduler history requires explicit mapper")
                    command = runtime._owner_command(historical.selection)
                    if (
                        inspect_publication(
                            connection, command, historical.selection.tenant_commit_sequence
                        )
                        != "COMPLETE"
                    ):
                        raise ValueError("scheduler publication membership differs")
                    expected_ids.update(member.record_id for member in batch.records)
                    expected_publications.add((batch.operation, identity))
                if expected_ids != set(startup.index.selected_record_ids):
                    raise ValueError("scheduler index differs from selected membership")
                fence = connection.execute(
                    "SELECT generation, frontier FROM main.deletion_fences WHERE tenant_id=?",
                    (tenant,),
                ).fetchone()
                if fence != ("r6", 0):
                    raise ValueError("missing or stale loop deletion fence")
                records: list[RunRecord] = []
                latest: dict[str, RunRecord] = {}
                for command in selected:
                    identity = command.idempotency_key
                    if (
                        command.operation_kind != "agent_loop"
                        or command.expected_head + 1 > startup.cut.tenant_frontier
                    ):
                        raise ValueError(
                            "unregistered loop publication envelope or future sequence"
                        )
                    if (
                        inspect_publication(connection, command, command.expected_head + 1)
                        != "COMPLETE"
                    ):
                        raise ValueError("selected loop publication is not exactly materialized")
                    first, *companions = command.records
                    record = RunRecord.model_validate_json(first.canonical_bytes)
                    if (
                        first.owner != OWNER
                        or first.schema_id != SCHEMA
                        or first.record_id != record.head
                        or record.tenant != tenant
                        or record.head != identity
                        or first.canonical_bytes != record.canonical_bytes()
                        or command.request_fingerprint != record.digest()
                        or record.root_binding != "NOT_APPLICABLE"
                    ):
                        raise ValueError("selected ordinary Run identity or canonical bytes differ")
                    if record.event == "CompleteAcceptance":
                        if len(companions) != 1 or (
                            companions[0].record_id,
                            companions[0].owner,
                            companions[0].schema_id,
                        ) != (record.run_id + "/accepted", CONVERSATION_OWNER, CONVERSATION_SCHEMA):
                            raise ValueError("selected Complete companion structure differs")
                    elif companions:
                        raise ValueError("unregistered legacy Run publication companions")
                    validate_record(latest.get(record.run_id), record)
                    if record.head in expected_ids:
                        raise ValueError("duplicate selected record identity")
                    expected_ids.add(record.head)
                    expected_publications.add((command.operation_kind, identity))
                    latest[record.run_id] = record
                    records.append(record)
                physical_ids = {
                    row[0]
                    for row in connection.execute(
                        "SELECT record_id FROM main.records WHERE tenant_id=? AND owner=?",
                        (tenant, OWNER),
                    )
                }
                physical_publications = set(
                    connection.execute(
                        "SELECT operation_kind, idempotency_key FROM main.publications "
                        "WHERE tenant_id=? AND (substr(operation_kind,1,10)='agent_loop' "
                        "OR substr(operation_kind,1,10)='scheduler.')",
                        (tenant,),
                    )
                )
                if physical_ids != expected_ids or physical_publications != expected_publications:
                    raise ValueError("orphaned, missing or unknown loop/scheduler membership")
                if (
                    tuple(journal.entries()) != before
                    or runtime._commitment_journal.load(tenant) != anchored
                ):
                    raise ValueError("independent loop authority changed during read")
                runtime._check_database_identity()
                return LoopSnapshot(tenant_head=startup.cut.tenant_frontier, records=tuple(records))
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        raise LoopIntegrityError(
            f"operation=snapshot tenant={runtime._tenant_id} record_id={identity}"
        ) from error
