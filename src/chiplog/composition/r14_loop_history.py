"""Run projection from independently selected, exactly materialized R14 history."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from chiplog.adapters.driven.loop_sqlite import OWNER, SCHEMA, LoopIntegrityError
from chiplog.adapters.driven.r9_fence import CONVERSATION_OWNER, CONVERSATION_SCHEMA
from chiplog.capabilities.agent_loop.contracts import LoopSnapshot, RunRecord
from chiplog.capabilities.agent_loop.domain import validate_record
from chiplog.composition.r14_fanout_contracts import FANOUT_OPERATION, RetainedFanOutPreparation
from chiplog.composition.r14_fanout_records import (
    build_envelope,
    inventory_from_history,
    physical_command,
)
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.publication_readback import inspect_publication
from chiplog.platform.workspace_snapshot import read_connection

if TYPE_CHECKING:
    from chiplog.composition.r14_runtime import R14PlanningRuntime


def read_loop_history(
    runtime: R14PlanningRuntime,
) -> tuple[LoopSnapshot, tuple[RetainedFanOutPreparation, ...]]:
    """Authenticate the caller's read cut, including a joined workspace snapshot.

    Historical reads use selected bytes, never a current owner or reconstructed
    companion proposal. New lifecycle envelopes require an explicit decoder here.
    """
    identity = "<enumeration>"
    try:
        with runtime._authority_gate().hold():
            runtime._require_no_pending()
            tenant = runtime._tenant_id
            entries = [json.loads(raw) for _, _, raw in runtime._loop_decisions().entries()]
            decisions = [entry for entry in entries if entry.get("kind") == "DECIDED"]
            identities = [entry["operation_id"] for entry in decisions]
            if len(identities) != len(set(identities)):
                raise ValueError("duplicate independently selected loop identity")
            selected = [
                (runtime._publication(entry), entry)
                for entry in decisions
                if str(entry.get("operation_kind", "")).startswith("agent_loop")
            ]
            selected.sort(key=lambda item: item[0].expected_head)
            with read_connection(runtime._database) as connection:
                actual = capture_authority_snapshot_commitment(connection, tenant)
                if actual != runtime._commitment_journal.load(tenant):
                    raise ValueError("loop read cut differs from current independent anchor")
                fence = connection.execute(
                    "SELECT generation, frontier FROM deletion_fences WHERE tenant_id=?", (tenant,)
                ).fetchone()
                if fence != ("r6", 0):
                    raise ValueError("missing or stale loop deletion fence")
                head = connection.execute(
                    "SELECT head FROM tenant_heads WHERE tenant_id=?", (tenant,)
                ).fetchone()
                physical_ids = {
                    row[0]
                    for row in connection.execute(
                        "SELECT record_id FROM records WHERE tenant_id=? AND owner=?",
                        (tenant, OWNER),
                    )
                }
                physical_publications = set(
                    connection.execute(
                        "SELECT operation_kind, idempotency_key FROM publications "
                        "WHERE tenant_id=? AND substr(operation_kind, 1, 10)='agent_loop'",
                        (tenant,),
                    )
                )
                expected_ids: set[str] = set()
                expected_publications: set[tuple[str, str]] = set()
                records: list[RunRecord] = []
                latest: dict[str, RunRecord] = {}
                preparations: list[RetainedFanOutPreparation] = []
                captured_heads: set[str] = set()
                for command, entry in selected:
                    identity = command.idempotency_key
                    if command.operation_kind not in ("agent_loop", FANOUT_OPERATION):
                        raise ValueError("unregistered loop publication envelope")
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
                    ):
                        raise ValueError("selected Run identity or canonical bytes differ")
                    if command.operation_kind == FANOUT_OPERATION:
                        raw = entry["fanout_preparation"]
                        evidence = RetainedFanOutPreparation.model_validate_json(raw)
                        if evidence.canonical_bytes().decode() != raw:
                            raise ValueError("noncanonical retained fanout preparation")
                        envelope = build_envelope(evidence)
                        prior = LoopSnapshot(
                            tenant_head=command.expected_head, records=tuple(records)
                        )
                        capture = evidence.request.captured_run
                        if (
                            envelope.canonical_bytes().decode() != entry["fanout_envelope"]
                            or physical_command(envelope) != command
                            or evidence.accepted_run != record
                            or capture != latest.get(record.run_id)
                            or evidence.request.request.cut.materialization_commitment
                            != entry["predecessor"]
                            or evidence.expected_snapshot_fingerprint != prior.digest()
                            or evidence.request.request.cut.predecessor_inventory
                            != inventory_from_history(tenant, prior, tuple(preparations))
                        ):
                            raise ValueError("selected fanout differs from exact retained history")
                        if capture.head in captured_heads:
                            raise ValueError("captured response already has a selected fanout")
                        captured_heads.add(capture.head)
                        preparations.append(evidence)
                    elif command.request_fingerprint != record.digest():
                        raise ValueError("selected legacy Run fingerprint differs")
                    elif record.event == "CompleteAcceptance":
                        if len(companions) != 1 or (
                            companions[0].record_id,
                            companions[0].owner,
                            companions[0].schema_id,
                        ) != (record.run_id + "/accepted", CONVERSATION_OWNER, CONVERSATION_SCHEMA):
                            raise ValueError("selected Complete companion structure differs")
                    elif companions:
                        raise ValueError("unregistered legacy Run publication companions")
                    validate_record(latest.get(record.run_id), record)
                    expected_ids.update(
                        row.record_id for row in command.records if row.owner == OWNER
                    )
                    expected_publications.add((command.operation_kind, identity))
                    latest[record.run_id] = record
                    records.append(record)
                if physical_ids != expected_ids or physical_publications != expected_publications:
                    raise ValueError("orphaned, missing or unknown loop publication members")
                runtime._check_database_identity()
                return (
                    LoopSnapshot(
                        tenant_head=0 if head is None else head[0], records=tuple(records)
                    ),
                    tuple(preparations),
                )
    except (ValueError, TypeError, KeyError, sqlite3.Error) as error:
        raise LoopIntegrityError(
            f"operation=snapshot tenant={runtime._tenant_id} record_id={identity}"
        ) from error


def read_loop_snapshot(runtime: R14PlanningRuntime) -> LoopSnapshot:
    return read_loop_history(runtime)[0]
