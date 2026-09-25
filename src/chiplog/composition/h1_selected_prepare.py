"""Authenticate the selected V3 Prepare that belongs to an H1 candidate Run.

The reader is intentionally private to the common CLI broker.  In particular,
it does not accept a retained Prepare, a journal path, or a workspace closure
from its caller.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from chiplog.adapters.driven.loop_sqlite import OWNER
from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallSubjectHead,
    SealedResponseRecord,
)
from chiplog.capabilities.agent_loop.delivery_preparation import DeliveryCompletion
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_run_record_contracts import (
    ExecutionRunCanonicalMember,
    decode_execution_run_member,
)
from chiplog.capabilities.agent_loop.execution_transition_contracts import PrepareExecutionRequest
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_preseal_contracts import H1SelectedPrepare, H1SelectedSeal
from chiplog.composition.r13_workspace import R13Workspace
from chiplog.composition.r14_execution_complete_seal_records import (
    EXECUTION_COMPLETE_SEAL_OPERATION,
    ExecutionCompleteSealPhysicalEnvelopeV2,
    RetainedExecutionCompleteSealV2,
    build_complete_seal_envelope,
    complete_seal_physical_command,
)
from chiplog.composition.r14_execution_fanout_contracts import EXECUTION_RUN_SCHEMA
from chiplog.composition.r14_execution_transition_records import (
    RetainedExecutionTransitionV3,
    transition_command,
)
from chiplog.composition.r14_fanout_contracts import SEAL_SCHEMA
from chiplog.composition.r14_h1_workspace_issuance_contracts import H1VerifiedWorkspaceClosure
from chiplog.composition.r14_h1_workspace_sources import reopen_h1_original_workspace
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.publication_readback import inspect_publication


@dataclass(frozen=True, slots=True)
class H1SelectedPostSealPrepare:
    """Selected V2 seal and the exact V3 Prepare it closes."""

    captured_run: ExecutionRunRecord
    sealed_run: ExecutionRunRecord
    prepare: H1SelectedPrepare
    seal: H1SelectedSeal


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_candidate(captured: ExecutionRunRecord, expected_head: str) -> None:
    if captured.head != expected_head:
        raise ValueError("H1 candidate Run head differs from expected head")
    if (
        captured.state != "ACTIVE"
        or captured.event != "ModelResponseCaptured"
        or len(captured.turns) != 1
    ):
        raise ValueError("H1 candidate is not an active first captured response")
    turn = captured.turns[0]
    if (
        turn.state != "RESPONSE_AVAILABLE"
        or turn.initialized_calls not in (None, ())
        or len(turn.attempts) != 1
        or turn.selector != 0
    ):
        raise ValueError("H1 candidate Turn is not a zero-call single attempt")
    attempt = turn.attempts[0]
    if (
        attempt.state != "RESPONSE_CAPTURED"
        or attempt.generation != 0
        or attempt.provider_contract != "hermetic-model.v1"
        or attempt.recipient != "hermetic-model"
        or attempt.live_model is not None
        or attempt.worker_session != captured.worker_session
    ):
        raise ValueError("H1 candidate attempt is not the first hermetic response")
    if not isinstance(attempt.response_base64, str):
        raise ValueError("H1 candidate response bytes are absent")
    try:
        response = DeliveryCompletion.model_validate_json(
            base64.b64decode(attempt.response_base64, validate=True)
        )
    except (ValueError, TypeError) as error:
        raise ValueError("H1 candidate response is not a Complete zero-call response") from error
    if (response.tenant, response.run_id, response.turn_id) != (
        captured.tenant,
        captured.run_id,
        turn.turn_id,
    ):
        raise ValueError("H1 candidate Complete response differs from captured Run")


def _selected_commands(
    runtime: CommonCliExecutionRuntime,
) -> tuple[tuple[str, bytes, PhysicalPublicationCommand], ...]:
    values: list[tuple[str, bytes, PhysicalPublicationCommand]] = []
    for decision_id, _predecessor, raw in runtime._loop_decisions().entries():
        try:
            entry = json.loads(raw)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("H1 selected journal entry is undecodable") from error
        if entry.get("kind") != "DECIDED":
            continue
        try:
            command = runtime._publication(entry)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("H1 selected journal entry has no physical command") from error
        if command.tenant_id != runtime._tenant_id:
            raise ValueError("H1 selected journal entry crosses tenants")
        values.append((decision_id, raw, command))
    heads = tuple(command.expected_head for _, _, command in values)
    if len(heads) != len(set(heads)):
        raise ValueError("H1 selected journal has competing publication predecessors")
    return tuple(sorted(values, key=lambda item: item[2].expected_head))


def _physical_run(
    connection: sqlite3.Connection, command: PhysicalPublicationCommand, member: PhysicalRecord
) -> PhysicalRecord:
    if inspect_publication(connection, command, command.expected_head + 1) != "COMPLETE":
        raise ValueError("H1 selected publication is not exactly materialized")
    row = connection.execute(
        "SELECT owner, schema_id, canonical_bytes, commit_sequence FROM records "
        "WHERE tenant_id=? AND record_id=?",
        (command.tenant_id, member.record_id),
    ).fetchone()
    if row is None:
        raise ValueError("H1 selected physical Run is absent")
    owner, schema_id, raw, sequence = row
    if (
        owner != member.owner
        or schema_id != member.schema_id
        or raw != member.canonical_bytes
        or sequence != command.expected_head + 1
    ):
        raise ValueError("H1 selected physical Run differs from publication")
    return PhysicalRecord(member.record_id, owner, schema_id, raw, _digest(raw))


def _decode_v3_prepare(raw: bytes) -> RetainedExecutionTransitionV3 | None:
    """Decode V3 evidence; a declared malformed V3 is an integrity failure."""
    try:
        entry = json.loads(raw)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("H1 selected journal entry is undecodable") from error
    encoded = entry.get("execution_transition")
    if encoded is None:
        return None
    if not isinstance(encoded, str):
        raise ValueError("H1 selected execution transition is not retained bytes")
    try:
        envelope = json.loads(encoded)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("H1 selected execution transition is malformed") from error
    if envelope.get("kind") != "R14_SELECTED_EXECUTION_TRANSITION_V3":
        return None
    try:
        retained = RetainedExecutionTransitionV3.model_validate_json(encoded)
    except ValueError as error:
        raise ValueError("H1 selected V3 Prepare is malformed") from error
    if retained.canonical_bytes() != encoded.encode():
        raise ValueError("H1 selected V3 Prepare bytes are not canonical")
    return retained


_SelectedRun = tuple[str, bytes, PhysicalPublicationCommand, ExecutionRunRecord, PhysicalRecord]


def _select_prepare_from_runs(
    runtime: CommonCliExecutionRuntime,
    runs: tuple[_SelectedRun, ...],
) -> H1SelectedPrepare:
    """Authenticate the one V3 Prepare in an already authenticated Run prefix."""
    matches: list[H1SelectedPrepare] = []
    for decision_id, decision_bytes, command, run, physical in runs:
        retained = _decode_v3_prepare(decision_bytes)
        if retained is None:
            continue
        if not isinstance(retained.request, PrepareExecutionRequest):
            raise ValueError("H1 selected V3 transition is not Prepare")
        if retained.proposal.run != run or transition_command(retained) != command:
            raise ValueError("H1 selected V3 Prepare differs from physical Run")
        workspace = tuple(
            member
            for member in retained.request.manifest.members
            if member.producer == "projections" and member.surface == "workspace"
        )
        if len(workspace) != 1:
            raise ValueError("H1 selected V3 Prepare lacks one workspace member")
        issuance = (
            R13Workspace(runtime).open_h1_workspace_issuance().load(retained.workspace_issuance)
        )
        started = next(
            (
                candidate
                for *_prefix, candidate, _record in runs
                if candidate.head == issuance.started_run_head
            ),
            None,
        )
        if started is None:
            raise ValueError("H1 selected V3 Prepare lacks its issued started Run")
        source_sequence = int(issuance.snapshot.tenant_head)
        runtime._verify_h1_prepare_issuance(
            retained.request,
            retained.workspace_issuance,
            workspace[0].model_dump_json().encode(),
            workspace[0].content.encode(),
            started,
            source_sequence,
            command.expected_head,
            R13Workspace(runtime),
        )
        matches.append(
            H1SelectedPrepare(
                retained=retained,
                retained_bytes=retained.canonical_bytes(),
                decision_id=decision_id,
                decision_bytes=decision_bytes,
                publication=command,
                physical_run=physical,
                started_run=started,
                source_tenant_sequence=source_sequence,
                workspace_member_bytes=workspace[0].model_dump_json().encode(),
                proposal_context_bytes=workspace[0].content.encode(),
                issuance_ref=retained.workspace_issuance,
            )
        )
    if len(matches) != 1:
        raise ValueError("H1 selected Run lineage has no unique V3 Prepare")
    return matches[0]


def select_h1_v3_prepare_for_candidate(
    runtime: CommonCliExecutionRuntime,
    captured: ExecutionRunRecord,
    *,
    expected_head: str,
) -> H1SelectedPrepare:
    """Return the one physically materialized selected V3 Prepare for *captured*."""
    if type(runtime) is not CommonCliExecutionRuntime:
        raise TypeError("H1 selected Prepare requires the canonical common CLI runtime")
    if type(captured) is not ExecutionRunRecord:
        raise TypeError("H1 selected Prepare requires an exact captured execution Run")
    if not isinstance(expected_head, str) or not expected_head:
        raise TypeError("H1 selected Prepare requires an exact expected head")
    _require_candidate(captured, expected_head)

    with runtime._authority_gate().hold():
        runtime._require_no_pending()
        runtime._check_database_identity()
        database = Path(runtime._database).resolve(strict=True)
        before = database.stat()
        commands = _selected_commands(runtime)
        runs: list[
            tuple[str, bytes, PhysicalPublicationCommand, ExecutionRunRecord, PhysicalRecord]
        ] = []
        with closing(
            sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, isolation_level=None)
        ) as c:
            c.execute("BEGIN")
            for decision_id, decision_bytes, command in commands:
                for member in command.records:
                    if member.owner != OWNER or member.schema_id != EXECUTION_RUN_SCHEMA:
                        continue
                    physical = _physical_run(c, command, member)
                    decoded = decode_execution_run_member(
                        ExecutionRunCanonicalMember(
                            record_id=physical.record_id,
                            schema_id=physical.schema_id,  # type: ignore[arg-type]
                            canonical_record_bytes=physical.canonical_bytes,
                            fingerprint=_digest(physical.canonical_bytes),
                        )
                    ).run
                    if (
                        isinstance(decoded, ExecutionRunRecord)
                        and decoded.run_id == captured.run_id
                    ):
                        runs.append((decision_id, decision_bytes, command, decoded, physical))
        runs.sort(key=lambda item: item[2].expected_head)
        if not runs or runs[-1][3] != captured:
            raise ValueError("H1 selected Run lineage does not end at captured Run")
        if len({run.head for _, _, _, run, _ in runs}) != len(runs):
            raise ValueError("H1 selected Run lineage has duplicate heads")
        for index, (_id, _raw, _command, run, _physical) in enumerate(runs):
            if index == 0:
                if run.predecessor is not None:
                    raise ValueError("H1 selected root has a predecessor")
            elif run.predecessor != runs[index - 1][3].head:
                raise ValueError("H1 selected Run lineage has a gap or competing descendant")

        selected = _select_prepare_from_runs(runtime, tuple(runs))
        runtime._check_database_identity()
        after = database.stat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ValueError("H1 selected Prepare database identity changed during read")
        return selected


def select_h1_v3_prepare_for_seal(
    runtime: CommonCliExecutionRuntime,
    *,
    selected_seal: CallSubjectHead,
    historical: bool = False,
) -> H1SelectedPostSealPrepare:
    """Reopen the V3 Prepare closed by one exact selected H1 V2 seal.

    ``selected_seal`` is only a physical locator.  The retained exchange,
    physical command, predecessor and V3 Prepare are all reselected here.
    """
    if type(runtime) is not CommonCliExecutionRuntime:
        raise TypeError("H1 selected seal requires the canonical common CLI runtime")
    if type(selected_seal) is not CallSubjectHead or type(selected_seal.revision) is not Present:
        raise TypeError("H1 selected seal requires an exact response-seal locator")
    if not isinstance(historical, bool):
        raise TypeError("H1 selected seal historical mode must be boolean")

    with runtime._authority_gate().hold():
        if not historical:
            runtime._require_no_pending()
        runtime._check_database_identity()
        database = Path(runtime._database).resolve(strict=True)
        before = database.stat()
        commands = _selected_commands(runtime)
        selected: (
            tuple[
                str,
                bytes,
                PhysicalPublicationCommand,
                RetainedExecutionCompleteSealV2,
                PhysicalRecord,
            ]
            | None
        ) = None
        with closing(
            sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, isolation_level=None)
        ) as connection:
            connection.execute("BEGIN")
            for decision_id, decision_bytes, command in commands:
                if command.operation_kind != EXECUTION_COMPLETE_SEAL_OPERATION:
                    continue
                try:
                    entry = json.loads(decision_bytes)
                    retained_raw = entry.get("execution_complete_seal")
                    if not isinstance(retained_raw, str):
                        continue
                    retained_wire = json.loads(retained_raw)
                    if retained_wire.get("kind") != "R14_SELECTED_EXECUTION_COMPLETE_SEAL_V2":
                        continue
                    envelope_raw = entry["execution_complete_seal_envelope"]
                    if not isinstance(envelope_raw, str):
                        raise ValueError("missing retained V2 complete seal envelope")
                    retained = RetainedExecutionCompleteSealV2.model_validate_json(retained_raw)
                    envelope = ExecutionCompleteSealPhysicalEnvelopeV2.model_validate_json(
                        envelope_raw
                    )
                except (
                    KeyError,
                    TypeError,
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                    ValueError,
                ) as error:
                    raise ValueError("H1 selected V2 seal decision is invalid") from error
                if (
                    retained.canonical_bytes().decode() != retained_raw
                    or envelope.canonical_bytes().decode() != envelope_raw
                    or build_complete_seal_envelope(retained) != envelope
                    or complete_seal_physical_command(envelope) != command
                ):
                    raise ValueError("H1 selected V2 seal decision differs from physical command")
                seals = tuple(
                    member for member in command.records if member.schema_id == SEAL_SCHEMA
                )
                if len(seals) != 1:
                    raise ValueError("H1 selected V2 seal lacks one physical response seal")
                physical_seal = _physical_run(connection, command, seals[0])
                try:
                    decoded_seal = SealedResponseRecord.model_validate_json(
                        physical_seal.canonical_bytes
                    )
                except ValueError as error:
                    raise ValueError("H1 selected response seal is malformed") from error
                if decoded_seal.canonical_bytes() != physical_seal.canonical_bytes:
                    raise ValueError("H1 selected response seal bytes are not canonical")
                digest = _digest(physical_seal.canonical_bytes)
                actual_locator = CallSubjectHead(
                    subject_id=decoded_seal.response_seal_id,
                    revision=Present(head="record:" + digest, fingerprint=digest),
                )
                if physical_seal.record_id != actual_locator.revision.head:
                    raise ValueError("H1 selected response seal physical ID differs")
                if actual_locator != selected_seal:
                    continue
                if selected is not None:
                    raise ValueError("H1 selected seal has no unique selected response seal")
                selected = (decision_id, decision_bytes, command, retained, physical_seal)
            if selected is None:
                raise ValueError("H1 selected seal has no unique selected response seal")

            decision_id, decision_bytes, seal_command, retained, _physical_seal = selected
            sealed_run = retained.exchange.proposal.sealed_run
            runs: list[_SelectedRun] = []
            for candidate_id, candidate_bytes, command in commands:
                if historical and command.expected_head > seal_command.expected_head:
                    continue
                for member in command.records:
                    if member.owner != OWNER or member.schema_id != EXECUTION_RUN_SCHEMA:
                        continue
                    physical = _physical_run(connection, command, member)
                    decoded = decode_execution_run_member(
                        ExecutionRunCanonicalMember(
                            record_id=physical.record_id,
                            schema_id=physical.schema_id,  # type: ignore[arg-type]
                            canonical_record_bytes=physical.canonical_bytes,
                            fingerprint=_digest(physical.canonical_bytes),
                        )
                    ).run
                    if (
                        isinstance(decoded, ExecutionRunRecord)
                        and decoded.run_id == sealed_run.run_id
                    ):
                        runs.append((candidate_id, candidate_bytes, command, decoded, physical))
        runs.sort(key=lambda item: item[2].expected_head)
        if not runs or runs[-1][3] != sealed_run:
            raise ValueError("H1 selected seal does not close the selected Run lineage")
        if len(runs) < 2:
            raise ValueError("H1 selected seal lacks its exact physical captured predecessor")
        if len({run.head for *_prefix, run, _physical in runs}) != len(runs):
            raise ValueError("H1 selected Run lineage has duplicate heads")
        for index, (_id, _raw, _command, run, _physical) in enumerate(runs):
            if index == 0:
                if run.predecessor is not None:
                    raise ValueError("H1 selected root has a predecessor")
            elif run.predecessor != runs[index - 1][3].head:
                raise ValueError("H1 selected Run lineage has a gap or competing descendant")
        captured = next(
            (
                run
                for _id, _raw, _command, run, _physical in runs
                if run.head == sealed_run.predecessor
            ),
            None,
        )
        if captured is None or captured != runs[-2][3]:
            raise ValueError("H1 selected seal lacks its exact physical captured predecessor")
        _require_candidate(captured, captured.head)
        if retained.exchange.request.captured_run != captured:
            raise ValueError("H1 selected seal retained exchange differs from physical predecessor")
        prefix = tuple(item for item in runs if item[3].head != sealed_run.head)
        prepare = _select_prepare_from_runs(runtime, prefix)
        seal = H1SelectedSeal(
            command=seal_command,
            decision_id=decision_id,
            decision_bytes=decision_bytes,
            commit_sequence=seal_command.expected_head + 1,
        )
        runtime._check_database_identity()
        after = database.stat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ValueError("H1 selected seal database identity changed during read")
        return H1SelectedPostSealPrepare(captured, sealed_run, prepare, seal)


def reopen_selected_h1_workspace(
    runtime: CommonCliExecutionRuntime, selected: H1SelectedPrepare
) -> H1VerifiedWorkspaceClosure:
    """Reopen the original issued workspace using only selected retained evidence."""
    if type(runtime) is not CommonCliExecutionRuntime:
        raise TypeError("H1 workspace reopen requires the canonical common CLI runtime")
    if type(selected) is not H1SelectedPrepare:
        raise TypeError("H1 workspace reopen requires selected H1 Prepare evidence")
    if selected.retained.canonical_bytes() != selected.retained_bytes:
        raise ValueError("H1 selected V3 Prepare bytes differ at workspace reopen")
    if selected.retained.workspace_issuance != selected.issuance_ref:
        raise ValueError("H1 selected V3 Prepare issuance differs at workspace reopen")
    with runtime._authority_gate().hold():
        workspace_port = R13Workspace(runtime)
        return reopen_h1_original_workspace(
            selected.issuance_ref,
            selected.workspace_member_bytes,
            selected.proposal_context_bytes,
            workspace_port.open_h1_workspace_issuance(),
            workspace_port.open_dashboard_issuance(),
        )


__all__ = [
    "H1SelectedPostSealPrepare",
    "reopen_selected_h1_workspace",
    "select_h1_v3_prepare_for_candidate",
    "select_h1_v3_prepare_for_seal",
]
