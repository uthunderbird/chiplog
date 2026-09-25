"""Authenticate the selected native H0/R17 inbound occurrences for H1 PRE_SEAL.

This is a private broker composition seam.  It deliberately replays retained
H0/R17 evidence only; it does not ask a current source, dispatch resource, or
provider to re-authorize the old input.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from chiplog.adapters.driven.loop_sqlite import OWNER as LOOP_OWNER
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_run_record_contracts import (
    ExecutionRunCanonicalMember,
    decode_execution_run_member,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.common_execution_driver_contracts import DriveInputRequestV1
from chiplog.composition.h1_preseal_contracts import H1SelectedPrepare
from chiplog.composition.r14_execution_fanout_contracts import EXECUTION_RUN_SCHEMA
from chiplog.composition.r14_execution_inbox_records import (
    EXECUTION_INBOX_INITIALIZATION_OPERATION,
    RetainedInboxExecutionInitialization,
    inbox_initialization_command,
)
from chiplog.composition.r17_authenticated_records import COMMAND_SCHEMA, decode_authentication
from chiplog.composition.r17_ingress_history import (
    is_ingress,
    record_head,
    validate_selected_ingress,
    wire_record,
)
from chiplog.platform._owner_publication_contracts import SingleOwnerBatch
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.ingress_authenticated_contracts import (
    AuthenticatedCustodyCommand,
    AuthenticatedCustodyRecord,
)
from chiplog.platform.ingress_custody_records import CustodyRecord, canonical, digest
from chiplog.platform.owner_publications import SelectedOwnerDecision
from chiplog.platform.publication_readback import inspect_publication


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _call_subject(value: object) -> tuple[str, str, str]:
    subject = getattr(value, "subject_id", None)
    revision = getattr(value, "revision", None)
    if not isinstance(subject, str) or not isinstance(revision, Present):
        raise ValueError("selected H0 admission has no exact selected head")
    return subject, revision.head, revision.fingerprint


@dataclass(frozen=True, slots=True)
class InputOccurrenceKey:
    """One exact materialized input occurrence, including its publication slot."""

    tenant: str
    owner: str
    schema: str
    record_id: str
    canonical_bytes: bytes
    fingerprint: str
    operation: str
    command_id: str
    command_fingerprint: str
    commit_sequence: int
    reason: str


@dataclass(frozen=True, slots=True)
class H1SelectedInputRoleWitness:
    """Immutable raw H0/R17 evidence; construction gives no caller authority."""

    initialization_decision_id: str
    initialization_bytes: bytes
    initialization: RetainedInboxExecutionInitialization
    driver_request: DriveInputRequestV1
    admitted_decision: SelectedOwnerDecision
    admitted_command: AuthenticatedCustodyCommand
    admitted_record: AuthenticatedCustodyRecord
    inbound_owner_decisions: tuple[SelectedOwnerDecision, ...]
    occurrences: tuple[InputOccurrenceKey, ...]
    fingerprint: str


def _physical(
    connection: sqlite3.Connection,
    command: PhysicalPublicationCommand,
    member: PhysicalRecord,
) -> InputOccurrenceKey:
    sequence = command.expected_head + 1
    if inspect_publication(connection, command, sequence) != "COMPLETE":
        raise ValueError("selected input publication is not exactly materialized")
    row = connection.execute(
        "SELECT owner,schema_id,canonical_bytes,commit_sequence FROM records "
        "WHERE tenant_id=? AND record_id=?",
        (command.tenant_id, member.record_id),
    ).fetchone()
    if row is None or tuple(row) != (
        member.owner,
        member.schema_id,
        member.canonical_bytes,
        sequence,
    ):
        raise ValueError("selected input physical member differs from publication")
    publication = connection.execute(
        "SELECT request_fingerprint,record_ids FROM publications "
        "WHERE tenant_id=? AND operation_kind=? AND idempotency_key=? AND commit_sequence=?",
        (command.tenant_id, command.operation_kind, command.idempotency_key, sequence),
    ).fetchone()
    if publication != (command.request_fingerprint, member.record_id):
        raise ValueError("selected input SQL publication differs from command")
    return InputOccurrenceKey(
        command.tenant_id,
        member.owner,
        member.schema_id,
        member.record_id,
        member.canonical_bytes,
        member.fingerprint,
        command.operation_kind,
        command.idempotency_key,
        command.request_fingerprint,
        sequence,
        "",
    )


def _run_chain(
    connection: sqlite3.Connection, tenant: str, captured: ExecutionRunRecord
) -> tuple[ExecutionRunRecord, ...]:
    rows = connection.execute(
        "SELECT record_id,canonical_bytes FROM records "
        "WHERE tenant_id=? AND owner=? AND schema_id=?",
        (tenant, LOOP_OWNER, EXECUTION_RUN_SCHEMA),
    ).fetchall()
    by_head: dict[str, ExecutionRunRecord] = {}
    for record_id, raw in rows:
        raw = bytes(raw)
        run = decode_execution_run_member(
            ExecutionRunCanonicalMember(
                record_id=str(record_id),
                schema_id=cast(
                    Literal[
                        "chiplog.agent-loop.execution-record.v2",
                        "chiplog.agent-loop.execution-record.v3",
                    ],
                    EXECUTION_RUN_SCHEMA,
                ),
                canonical_record_bytes=raw,
                fingerprint=_digest(raw),
            )
        ).run
        if (
            not isinstance(run, ExecutionRunRecord)
            or run.canonical_bytes() != raw
            or run.head != record_id
        ):
            raise ValueError("native Run physical member is not canonical")
        if run.head in by_head:
            raise ValueError("native Run history has duplicate heads")
        by_head[run.head] = run
    if by_head.get(captured.head) != captured:
        raise ValueError("captured Run is absent from the physical native history")
    chain: list[ExecutionRunRecord] = []
    current = captured
    while True:
        chain.append(current)
        if current.predecessor is None:
            break
        predecessor = by_head.get(current.predecessor)
        if predecessor is None or predecessor.run_id != current.run_id:
            raise ValueError("selected native Run ancestry has a gap")
        current = predecessor
    chain.reverse()
    return tuple(chain)


def _h0(
    runtime: CommonCliExecutionRuntime, root: ExecutionRunRecord
) -> tuple[
    str,
    bytes,
    RetainedInboxExecutionInitialization,
    DriveInputRequestV1,
    PhysicalPublicationCommand,
]:
    matches: list[
        tuple[
            str,
            bytes,
            RetainedInboxExecutionInitialization,
            DriveInputRequestV1,
            PhysicalPublicationCommand,
        ]
    ] = []
    for decision_id, _predecessor, raw in runtime._loop_decisions().entries():
        try:
            entry = json.loads(raw)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("selected H0 journal entry is undecodable") from error
        if not isinstance(entry, dict) or entry.get("kind") != "DECIDED":
            continue
        if entry.get("operation_kind") != EXECUTION_INBOX_INITIALIZATION_OPERATION:
            continue
        evidence_raw = entry.get("inbox_initialization")
        if not isinstance(evidence_raw, str):
            raise ValueError("selected H0 envelope lacks initialization evidence")
        evidence = RetainedInboxExecutionInitialization.model_validate_json(evidence_raw)
        command = inbox_initialization_command(evidence)
        if (
            evidence.canonical_bytes() != evidence_raw.encode()
            or runtime._publication(entry) != command
            or entry.get("operation_id") != command.idempotency_key
            or entry.get("expected_head") != evidence.expected_head
            or entry.get("predecessor") != evidence.predecessor_commitment
            or evidence.proposal.run != root
            or len(command.records) != 1
            or command.records[0].canonical_bytes != root.canonical_bytes()
        ):
            raise ValueError("selected H0 command, root Run, or journal envelope differs")
        wire = DriveInputRequestV1.model_validate_json(evidence.driver_request_bytes)
        if (
            wire.canonical_bytes() != evidence.driver_request_bytes
            or evidence.driver_request_fingerprint != wire.original_driver_command_fingerprint()
        ):
            raise ValueError("selected H0 driver request is noncanonical")
        matches.append((decision_id, raw, evidence, wire, command))
    if len(matches) != 1:
        raise ValueError("selected native root has no unique H0 initialization")
    return matches[0]


def _r17(
    runtime: CommonCliExecutionRuntime,
    wire: DriveInputRequestV1,
    evidence: RetainedInboxExecutionInitialization,
) -> tuple[
    SelectedOwnerDecision,
    AuthenticatedCustodyCommand,
    AuthenticatedCustodyRecord,
    PhysicalPublicationCommand,
    tuple[tuple[SelectedOwnerDecision, PhysicalPublicationCommand, str], ...],
]:
    history = runtime._owner_decisions().snapshot()
    # This is retained-history validation, not a current inbox/source read.
    records, _custody = validate_selected_ingress(history)
    ingress = tuple(decision for decision in history.decisions if is_ingress(decision))
    if len(records) != len(ingress):
        raise ValueError("selected R17 history has unpaired custody decisions")
    selected = wire.selected_source.selected_ingress_decision
    matches: list[
        tuple[
            SelectedOwnerDecision,
            AuthenticatedCustodyCommand,
            AuthenticatedCustodyRecord,
            PhysicalPublicationCommand,
            tuple[tuple[SelectedOwnerDecision, PhysicalPublicationCommand, str], ...],
        ]
    ] = []
    for decision in history.decisions:
        batch = decision.prepared.request
        if not isinstance(batch, SingleOwnerBatch) or batch.command.schema_id != COMMAND_SCHEMA:
            continue
        if (decision.decision_id, decision.decision_head, decision.decision_fingerprint) != (
            selected.identity,
            selected.head,
            selected.fingerprint,
        ):
            continue
        command = AuthenticatedCustodyCommand.model_validate_json(batch.command.canonical_bytes)
        if canonical(
            command
        ) != batch.command.canonical_bytes or batch.command.fingerprint != digest(
            canonical(command)
        ):
            raise ValueError("selected R17 command is noncanonical")
        # validate_selected_ingress above authenticated the reducer for the whole selected stream;
        # obtain this exact selected physical member from its batch without reopening a live reader.
        if len(batch.complete_records) != 1:
            raise ValueError("selected R17 batch has non-singleton physical membership")
        member = batch.complete_records[0]
        record = AuthenticatedCustodyRecord.model_validate_json(member.canonical_bytes)
        if (
            canonical(record) != member.canonical_bytes
            or record.command != command
            or wire_record(record) != member
        ):
            raise ValueError("selected R17 record differs from its exact owner member")
        owner_call, authenticated = decode_authentication(command)
        admitted = evidence.request.admitted
        if (
            command.token.token_id != wire.identity.original_ingress_identity.command_id
            or command.profile.tenant_id != wire.identity.tenant_id
            or batch.identity.tenant_id != command.profile.tenant_id
            or decision.tenant_commit_sequence != command.tenant_frontier + 1
            or (*_call_subject(admitted.selected_decision),)
            != (selected.identity, selected.head, selected.fingerprint)
            or (*_call_subject(admitted.physical_record),)
            != (
                record_head(record).identity,
                record_head(record).head,
                record_head(record).fingerprint,
            )
            or admitted.canonical_custody_record != canonical(record)
            or admitted.raw_input_bytes != command.raw_bytes
            or admitted.principal_id != authenticated.reference.principal_id
            or owner_call.request.offer.scope.replay_identity != command.command_id
        ):
            raise ValueError("selected R17 admission differs from retained H0 request")
        physical = PhysicalPublicationCommand(
            batch.identity.tenant_id,
            batch.operation,
            batch.identity.command_id,
            batch.identity.command_fingerprint,
            command.tenant_frontier,
            "r6",
            0,
            0,
            (
                PhysicalRecord(
                    member.record_id,
                    member.owner,
                    member.schema_id,
                    member.canonical_bytes,
                    member.fingerprint,
                ),
            ),
        )
        candidates: list[tuple[int, SelectedOwnerDecision, CustodyRecord]] = []
        for index, (ancestor, replayed) in enumerate(zip(ingress, records, strict=True)):
            if isinstance(replayed, CustodyRecord):
                candidates.append((index, ancestor, replayed))
        stages = [
            item
            for item in candidates
            if item[2].command.operation == "ingress.stage_raw_bytes"
            and item[2].command.token == command.token
            and item[2].command.profile == command.profile
            and item[2].command.retention == command.retention
            and item[2].command.raw_bytes == command.raw_bytes
            and item[2].resulting_entry.state_head == command.staged_head
            and item[2].resulting_entry.staged_bytes == command.raw_bytes
            and item[2].resulting_entry.staged_digest == digest(command.raw_bytes)
        ]
        if len(stages) != 1:
            raise ValueError("selected admission has no unique exact staging predecessor")
        stage_index, stage_decision, stage_record = stages[0]
        allocations = [
            item
            for item in candidates
            if item[0] < stage_index
            and item[2].command.operation == "ingress.allocate_receipt_token"
            and item[2].command.token == command.token
            and item[2].command.profile == command.profile
            and item[2].command.retention == command.retention
            and item[2].command.raw_bytes is None
            and item[2].command.token.predecessor is None
            and item[2].resulting_entry.staged_bytes is None
            and item[2].resulting_entry.custody is None
        ]
        if len(allocations) != 1 or stage_index >= next(
            index for index, candidate in enumerate(records) if candidate == record
        ):
            raise ValueError("selected staging has no unique original allocation")
        allocation_index, allocation_decision, allocation_record = allocations[0]
        if allocation_index >= stage_index:
            raise ValueError("selected allocation is not before exact staging")

        def physical_for(
            selected_decision: SelectedOwnerDecision,
            replayed: CustodyRecord | AuthenticatedCustodyRecord,
        ) -> PhysicalPublicationCommand:
            selected_batch = selected_decision.prepared.request
            if not isinstance(
                selected_batch, SingleOwnerBatch
            ) or selected_batch.complete_records != (wire_record(replayed),):
                raise ValueError("selected custody predecessor has non-exact physical member")
            member = selected_batch.complete_records[0]
            return PhysicalPublicationCommand(
                selected_batch.identity.tenant_id,
                selected_batch.operation,
                selected_batch.identity.command_id,
                selected_batch.identity.command_fingerprint,
                selected_decision.tenant_commit_sequence - 1,
                "r6",
                0,
                0,
                (
                    PhysicalRecord(
                        member.record_id,
                        member.owner,
                        member.schema_id,
                        member.canonical_bytes,
                        member.fingerprint,
                    ),
                ),
            )

        matches.append(
            (
                decision,
                command,
                record,
                physical,
                (
                    (
                        allocation_decision,
                        physical_for(allocation_decision, allocation_record),
                        "R17_ALLOCATION_INPUT",
                    ),
                    (
                        stage_decision,
                        physical_for(stage_decision, stage_record),
                        "R17_STAGED_INPUT",
                    ),
                    (decision, physical, "R17_ADMITTED_INPUT"),
                ),
            )
        )
    if len(matches) != 1:
        raise ValueError("selected H0 driver request has no unique selected R17 decision")
    return matches[0]


def read_h1_selected_input_role(
    runtime: CommonCliExecutionRuntime,
    selected: H1SelectedPrepare,
    captured: ExecutionRunRecord,
) -> H1SelectedInputRoleWitness:
    """Return exact original inbound H0/R17 occurrences under the registered gate."""
    if type(runtime) is not CommonCliExecutionRuntime:
        raise TypeError("selected input role requires the canonical common CLI runtime")
    if type(selected) is not H1SelectedPrepare or type(captured) is not ExecutionRunRecord:
        raise TypeError("selected input role requires selected Prepare and captured native Run")
    with runtime._authority_gate().hold():
        runtime._require_no_pending()
        runtime._check_database_identity()
        database = Path(runtime._database).resolve(strict=True)
        before = database.stat()
        with closing(
            sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, isolation_level=None)
        ) as connection:
            connection.execute("BEGIN")
            chain = _run_chain(connection, runtime._tenant_id, captured)
            if selected.started_run not in chain or selected.started_run.predecessor is None:
                raise ValueError("selected Prepare does not belong to the captured native ancestry")
            decision_id, decision_bytes, initialization, wire, h0_command = _h0(runtime, chain[0])
            h0 = _physical(connection, h0_command, h0_command.records[0])
            decision, command, record, _r17_command, inbound = _r17(runtime, wire, initialization)
            inbound_rows = tuple(
                _physical(connection, item[1], item[1].records[0]) for item in inbound
            )
        runtime._check_database_identity()
        after = database.stat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ValueError("selected input role database identity changed during read")
    occurrences = (
        InputOccurrenceKey(
            h0.tenant,
            h0.owner,
            h0.schema,
            h0.record_id,
            h0.canonical_bytes,
            h0.fingerprint,
            h0.operation,
            h0.command_id,
            h0.command_fingerprint,
            h0.commit_sequence,
            "H0_NATIVE_INPUT",
        ),
        *(
            InputOccurrenceKey(
                item.tenant,
                item.owner,
                item.schema,
                item.record_id,
                item.canonical_bytes,
                item.fingerprint,
                item.operation,
                item.command_id,
                item.command_fingerprint,
                item.commit_sequence,
                reason,
            )
            for item, (_decision, _command, reason) in zip(inbound_rows, inbound, strict=True)
        ),
    )
    preimage = json.dumps(
        {
            "h0": base64.b64encode(decision_bytes).decode(),
            "r17": base64.b64encode(canonical(record)).decode(),
            "occurrences": [
                {
                    name: (
                        base64.b64encode(getattr(item, name)).decode()
                        if name == "canonical_bytes"
                        else getattr(item, name)
                    )
                    for name in item.__dataclass_fields__
                }
                for item in occurrences
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return H1SelectedInputRoleWitness(
        decision_id,
        decision_bytes,
        initialization,
        wire,
        decision,
        command,
        record,
        tuple(item[0] for item in inbound),
        occurrences,
        _digest(preimage),
    )


__all__ = ["H1SelectedInputRoleWitness", "InputOccurrenceKey", "read_h1_selected_input_role"]
