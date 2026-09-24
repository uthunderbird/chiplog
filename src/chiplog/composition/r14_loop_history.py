"""Run projection from independently selected, exactly materialized R14 history."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Literal, cast

from chiplog.adapters.driven.loop_sqlite import OWNER, SCHEMA, LoopIntegrityError
from chiplog.adapters.driven.r9_fence import CONVERSATION_OWNER, CONVERSATION_SCHEMA
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallInventorySnapshot
from chiplog.capabilities.agent_loop.contracts import LoopSnapshot, RunRecord
from chiplog.capabilities.agent_loop.domain import validate_record
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_run_record_contracts import (
    ExecutionRunCanonicalMember,
    decode_execution_run_member,
)
from chiplog.capabilities.agent_loop.execution_transition_contracts import (
    CreateExecutionRun,
    PrepareExecutionRequest,
)
from chiplog.composition.r13_workspace import R13Workspace
from chiplog.composition.r14_acceptance_v2_contracts import RetainedAcceptancePreparationV2
from chiplog.composition.r14_cancellation_contracts import (
    CANCELLATION_OPERATION,
    CANCELLATION_SCHEMA,
    NOT_EXECUTED_SCHEMA,
    RetainedCancellationPreparation,
)
from chiplog.composition.r14_cancellation_records import (
    build_cancellation_envelope,
    cancellation_command,
)
from chiplog.composition.r14_execution_complete_seal_records import (
    EXECUTION_COMPLETE_SEAL_OPERATION,
    RetainedExecutionCompleteSeal,
    RetainedExecutionCompleteSealV2,
    build_complete_seal_envelope,
    complete_seal_physical_command,
)
from chiplog.composition.r14_execution_completion_records import (
    COMPLETE_ACCEPTANCE_OPERATION,
    complete_acceptance_command,
)
from chiplog.composition.r14_execution_fanout_contracts import (
    EXECUTION_FANOUT_OPERATION,
    EXECUTION_RUN_SCHEMA,
    RetainedExecutionFanOutPreparation,
)
from chiplog.composition.r14_execution_fanout_records import (
    build_envelope as execution_envelope,
)
from chiplog.composition.r14_execution_fanout_records import (
    physical_command as execution_command,
)
from chiplog.composition.r14_execution_inbox_records import (
    EXECUTION_INBOX_INITIALIZATION_OPERATION,
    RetainedInboxExecutionInitialization,
    inbox_initialization_command,
)
from chiplog.composition.r14_execution_inventory import execution_inventory
from chiplog.composition.r14_execution_transition_records import (
    EXECUTION_TRANSITION_OPERATION,
    ExecutionHistorySnapshot,
    RetainedExecutionTransition,
    RetainedExecutionTransitionEvidence,
    RetainedExecutionTransitionV3,
    transition_command,
)
from chiplog.composition.r14_fanout_contracts import FANOUT_OPERATION, RetainedFanOutPreparation
from chiplog.composition.r14_fanout_records import (
    build_envelope,
    physical_command,
)
from chiplog.composition.r14_h1_workspace_issuance import verify_h1_original_workspace
from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2
from chiplog.platform._sqlite import PhysicalPublicationCommand
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.publication_readback import inspect_publication
from chiplog.platform.workspace_snapshot import read_connection

if TYPE_CHECKING:
    from chiplog.composition.r14_runtime import R14PlanningRuntime


def completion_v2_terminal_run(command: PhysicalPublicationCommand) -> ExecutionRunRecord:
    """Decode the sole native-v2 terminal Run in a selected V2 completion batch.

    The surrounding selected-issuance verifier establishes the batch's owner
    exchanges. This small projection keeps the history reader from treating a
    schema-compatible non-Run companion as the terminal state.
    """
    members = tuple(
        record
        for record in command.records
        if record.owner == OWNER and record.schema_id == EXECUTION_RUN_SCHEMA
    )
    if len(members) != 1:
        raise ValueError("selected complete delivery has no unique native terminal Run")
    record = members[0]
    decoded = decode_execution_run_member(
        ExecutionRunCanonicalMember(
            record_id=record.record_id,
            schema_id=cast(
                "Literal['chiplog.agent-loop.execution-record.v2', "
                "'chiplog.agent-loop.execution-record.v3']",
                record.schema_id,
            ),
            canonical_record_bytes=record.canonical_bytes,
            fingerprint=record.fingerprint,
        )
    )
    if not isinstance(decoded.run, ExecutionRunRecord) or decoded.run.state != "SUCCEEDED":
        raise ValueError("selected complete delivery Run is not a native succeeded terminal")
    return decoded.run


def _completion_v2_schema_dispatch(batch: CompleteDeliveryBatchV2) -> Literal["H1"]:
    """Route every V2 applicability schema once, for startup and history reads."""
    from chiplog.composition.h1_completion_issuance import SCHEMA

    if batch.authentication.applicability_schema == SCHEMA:
        return "H1"
    raise ValueError(
        "unsupported complete delivery v2 applicability schema: "
        + batch.authentication.applicability_schema
    )


def _selected_h1_completion(
    runtime: R14PlanningRuntime, decision: object
) -> tuple[PhysicalPublicationCommand, ExecutionRunRecord, ExecutionRunRecord]:
    """Authenticate and project one owners-journal H1 completion selection.

    The issuance validator reads the selected source journals directly.  It is
    deliberately called before the transient command projection, so no
    schema-valid batch can stand in for retained owner evidence.
    """
    import chiplog.composition.h1_completion_issuance as h1_issuance
    import chiplog.composition.h1_historical_selected_sources as historical_sources
    from chiplog.platform.owner_publications import SelectedOwnerDecision

    if not isinstance(decision, SelectedOwnerDecision):
        raise ValueError("selected H1 completion lacks an owner-journal decision")
    batch = decision.prepared.request
    if not isinstance(batch, CompleteDeliveryBatchV2):
        raise ValueError("selected H1 completion has a substituted owner batch")
    if _completion_v2_schema_dispatch(batch) != "H1":  # pragma: no cover - total dispatcher
        raise ValueError("selected complete delivery did not route to H1")
    bound = historical_sources.bind_selected_h1_completion(batch, runtime)
    if bound.decision != decision:
        raise ValueError("selected H1 completion differs from authenticated owner decision")
    issuance = bound.issuance
    expected = complete_acceptance_command(h1_issuance.h1_completion_exchange(batch))
    command = runtime._owner_command(decision)
    if (
        command != expected
        or decision.tenant_commit_sequence != command.expected_head + 1
        or decision.prepared.predecessor_commitment
        != batch.expected.expected_materialization_commitment
        or decision.prepared.fence_generation != "r6"
        or decision.prepared.fence_frontier != 0
    ):
        raise ValueError("selected H1 completion header differs from retained issuance")
    terminal = completion_v2_terminal_run(command)
    predecessor = issuance.assembly.original_completion_request.run
    if terminal != issuance.assembly.prepared_completion.run:
        raise ValueError("selected H1 terminal Run differs from retained issuance")
    return command, terminal, predecessor


def _read_call_history(
    runtime: R14PlanningRuntime,
    *,
    selected_only: bool = False,
) -> tuple[
    LoopSnapshot,
    tuple[RetainedFanOutPreparation, ...],
    tuple[RetainedCancellationPreparation, ...],
    ExecutionHistorySnapshot,
    tuple[RetainedExecutionFanOutPreparation, ...],
    tuple[RetainedAcceptancePreparationV2, ...],
]:
    """Authenticate the caller's read cut, including a joined workspace snapshot.

    Historical reads use selected bytes, never a current owner or reconstructed
    companion proposal. New lifecycle envelopes require an explicit decoder here.
    """
    identity = "<enumeration>"
    try:
        with runtime._authority_gate().hold():
            if selected_only:
                runtime._pending()  # Authenticate the original journal before semantic replay.
            else:
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
            call_values = {}
            for decision in runtime._owner_decisions().snapshot().decisions:
                batch = decision.prepared.request
                if batch.kind != "CALL_EFFECT_ATOMIC":
                    if isinstance(batch, CompleteDeliveryBatchV2):
                        route = _completion_v2_schema_dispatch(batch)
                        if batch.operation != COMPLETE_ACCEPTANCE_OPERATION:
                            raise ValueError("complete delivery v2 has an unregistered operation")
                        if route != "H1":  # pragma: no cover - total dispatcher
                            raise ValueError("complete delivery v2 has an unsupported route")
                        command, terminal, predecessor = _selected_h1_completion(runtime, decision)
                        selected.append(
                            (
                                command,
                                {
                                    "h1_completion_terminal": terminal,
                                    "h1_completion_predecessor": predecessor,
                                },
                            )
                        )
                    continue
                from chiplog.composition.r14_call_issuance import validate_call_issuance
                from chiplog.composition.r16_dispatch_runtime import ExecutionDispatchRuntime

                if not isinstance(runtime, ExecutionDispatchRuntime):
                    raise ValueError("call acceptance requires the registered executable runtime")
                value = validate_call_issuance(batch, runtime)
                command = runtime._owner_command(decision)
                if (
                    decision.tenant_commit_sequence != command.expected_head + 1
                    or decision.prepared.predecessor_commitment
                    != batch.expected.expected_materialization_commitment
                    or decision.prepared.fence_generation != "r6"
                    or decision.prepared.fence_frontier != 0
                    or command.idempotency_key in call_values
                ):
                    raise ValueError("selected acceptance header differs from original cut")
                call_values[command.idempotency_key] = value
                selected.append((command, {}))
            selected.sort(key=lambda item: item[0].expected_head)
            if len({item[0].expected_head for item in selected}) != len(selected):
                raise ValueError("multiple selected loop publications at one predecessor")
            with read_connection(runtime._database) as connection:
                actual = capture_authority_snapshot_commitment(connection, tenant)
                if not selected_only and actual != runtime._commitment_journal.load(tenant):
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
                        "WHERE tenant_id=? AND (substr(operation_kind, 1, 10)='agent_loop' "
                        "OR operation_kind='effects.accept_call')",
                        (tenant,),
                    )
                )
                expected_ids: set[str] = set()
                expected_publications: set[tuple[str, str]] = set()
                records: list[RunRecord] = []
                mixed_records: list[RunRecord | ExecutionRunRecord] = []
                latest: dict[str, RunRecord | ExecutionRunRecord] = {}
                preparations: list[RetainedFanOutPreparation] = []
                execution_preparations: list[RetainedExecutionFanOutPreparation] = []
                cancellations: list[RetainedCancellationPreparation] = []
                acceptances: list[RetainedAcceptancePreparationV2] = []
                captured_heads: set[str] = set()
                for command, entry in selected:
                    identity = command.idempotency_key
                    if command.operation_kind not in (
                        "agent_loop",
                        FANOUT_OPERATION,
                        CANCELLATION_OPERATION,
                        EXECUTION_TRANSITION_OPERATION,
                        EXECUTION_INBOX_INITIALIZATION_OPERATION,
                        EXECUTION_FANOUT_OPERATION,
                        EXECUTION_COMPLETE_SEAL_OPERATION,
                        "effects.accept_call",
                        COMPLETE_ACCEPTANCE_OPERATION,
                    ):
                        raise ValueError("unregistered loop publication envelope")
                    if (
                        not selected_only
                        and inspect_publication(connection, command, command.expected_head + 1)
                        != "COMPLETE"
                    ):
                        raise ValueError("selected loop publication is not exactly materialized")
                    if command.operation_kind == "effects.accept_call":
                        value = call_values[identity]
                        accepted_preparation = value.retained
                        before = ExecutionHistorySnapshot(
                            tenant_head=command.expected_head, records=tuple(mixed_records)
                        )
                        worker = value.captured.cut.worker
                        if (
                            worker is None
                            or latest.get(worker.run.run_id) != worker.run
                            or value.preview.initialization not in execution_preparations
                            or accepted_preparation.loop_request.binding.cut.predecessor_inventory
                            != execution_inventory(
                                tenant,
                                before,
                                tuple(preparations),
                                tuple(cancellations),
                                tuple(execution_preparations),
                                tuple(acceptances),
                            )
                        ):
                            raise ValueError("acceptance lacks its exact causal call/Run history")
                        acceptances.append(accepted_preparation)
                        expected_ids.update(
                            row.record_id for row in command.records if row.owner == OWNER
                        )
                        expected_publications.add((command.operation_kind, identity))
                        continue
                    if command.operation_kind == COMPLETE_ACCEPTANCE_OPERATION:
                        terminal = entry.get("h1_completion_terminal")
                        predecessor = entry.get("h1_completion_predecessor")
                        if not isinstance(terminal, ExecutionRunRecord) or not isinstance(
                            predecessor, ExecutionRunRecord
                        ):
                            raise ValueError("H1 completion lacks its retained native Run lineage")
                        if (
                            terminal.predecessor != predecessor.head
                            or latest.get(terminal.run_id) != predecessor
                            or predecessor.event != "ModelCompletionPrepared"
                            or terminal.event != "ExecutionCompleted"
                        ):
                            raise ValueError(
                                "selected H1 completion differs from selected sealed predecessor"
                            )
                        expected_ids.update(
                            row.record_id for row in command.records if row.owner == OWNER
                        )
                        expected_publications.add((command.operation_kind, identity))
                        latest[terminal.run_id] = terminal
                        mixed_records.append(terminal)
                        continue
                    first, *companions = command.records
                    if (
                        command.operation_kind == EXECUTION_COMPLETE_SEAL_OPERATION
                        or "execution_complete_seal" in entry
                    ):
                        if (
                            command.operation_kind != EXECUTION_COMPLETE_SEAL_OPERATION
                            or "execution_fanout" in entry
                            or "execution_fanout_envelope" in entry
                        ):
                            raise ValueError("execution complete seal has a substituted operation")
                        raw_complete = entry["execution_complete_seal"]
                        complete_kind = json.loads(raw_complete).get("kind")
                        if complete_kind == "R14_SELECTED_EXECUTION_COMPLETE_SEAL_V1":
                            complete: (
                                RetainedExecutionCompleteSeal | RetainedExecutionCompleteSealV2
                            ) = RetainedExecutionCompleteSeal.model_validate_json(raw_complete)
                        elif complete_kind == "R14_SELECTED_EXECUTION_COMPLETE_SEAL_V2":
                            complete = RetainedExecutionCompleteSealV2.model_validate_json(
                                raw_complete
                            )
                        else:
                            raise ValueError("unregistered execution complete seal profile")
                        complete_envelope = build_complete_seal_envelope(complete)
                        sealed = complete.exchange.proposal.sealed_run
                        before_execution = ExecutionHistorySnapshot(
                            tenant_head=command.expected_head, records=tuple(mixed_records)
                        )
                        capture_execution = complete.exchange.request.captured_run
                        if (
                            complete.canonical_bytes().decode() != raw_complete
                            or complete_seal_physical_command(complete_envelope) != command
                            or complete_envelope.canonical_bytes().decode()
                            != entry["execution_complete_seal_envelope"]
                            or sealed.tenant != tenant
                            or capture_execution != latest.get(sealed.run_id)
                            or complete.exchange.request.request.cut.materialization_commitment
                            != entry["predecessor"]
                            or complete.exchange.expected_snapshot_fingerprint
                            != before_execution.digest()
                            or complete.exchange.request.request.cut.predecessor_inventory
                            != execution_inventory(
                                tenant,
                                before_execution,
                                tuple(preparations),
                                tuple(cancellations),
                                tuple(execution_preparations),
                                tuple(acceptances),
                            )
                            or capture_execution.head in captured_heads
                        ):
                            raise ValueError(
                                "selected execution complete seal differs from original history"
                            )
                        captured_heads.add(capture_execution.head)
                        execution_preparations.append(complete.exchange)
                        expected_ids.update(
                            row.record_id for row in command.records if row.owner == OWNER
                        )
                        expected_publications.add((command.operation_kind, identity))
                        latest[sealed.run_id] = sealed
                        mixed_records.append(sealed)
                        continue
                    if (
                        command.operation_kind == EXECUTION_FANOUT_OPERATION
                        or "execution_fanout" in entry
                    ):
                        if command.operation_kind != EXECUTION_FANOUT_OPERATION:
                            raise ValueError("execution fanout has a substituted operation")
                        raw_execution = entry["execution_fanout"]
                        fanout = RetainedExecutionFanOutPreparation.model_validate_json(
                            raw_execution
                        )
                        envelope_execution = execution_envelope(fanout)
                        sealed = fanout.proposal.sealed_run
                        before_execution = ExecutionHistorySnapshot(
                            tenant_head=command.expected_head, records=tuple(mixed_records)
                        )
                        capture_execution = fanout.request.captured_run
                        if (
                            fanout.canonical_bytes().decode() != raw_execution
                            or execution_command(envelope_execution) != command
                            or envelope_execution.canonical_bytes().decode()
                            != entry["execution_fanout_envelope"]
                            or sealed.tenant != tenant
                            or capture_execution != latest.get(sealed.run_id)
                            or fanout.request.request.cut.materialization_commitment
                            != entry["predecessor"]
                            or fanout.expected_snapshot_fingerprint != before_execution.digest()
                            or fanout.request.request.cut.predecessor_inventory
                            != execution_inventory(
                                tenant,
                                before_execution,
                                tuple(preparations),
                                tuple(cancellations),
                                tuple(execution_preparations),
                                tuple(acceptances),
                            )
                            or capture_execution.head in captured_heads
                        ):
                            raise ValueError(
                                "selected execution fanout differs from original history"
                            )
                        captured_heads.add(capture_execution.head)
                        execution_preparations.append(fanout)
                        expected_ids.update(
                            row.record_id for row in command.records if row.owner == OWNER
                        )
                        expected_publications.add((command.operation_kind, identity))
                        latest[sealed.run_id] = sealed
                        mixed_records.append(sealed)
                        continue
                    if (
                        first.schema_id == EXECUTION_RUN_SCHEMA
                        or command.operation_kind == EXECUTION_INBOX_INITIALIZATION_OPERATION
                        or command.operation_kind == EXECUTION_TRANSITION_OPERATION
                        or "execution_transition" in entry
                    ):
                        if (
                            command.operation_kind == EXECUTION_INBOX_INITIALIZATION_OPERATION
                            or "inbox_initialization" in entry
                        ):
                            if command.operation_kind != EXECUTION_INBOX_INITIALIZATION_OPERATION:
                                raise ValueError("inbox initialization has a substituted operation")
                            retained_raw = entry["inbox_initialization"]
                            retained_init = (
                                RetainedInboxExecutionInitialization.model_validate_json(
                                    retained_raw
                                )
                            )
                            execution = retained_init.proposal.run
                            if not isinstance(execution, ExecutionRunRecord):
                                raise ValueError("inbox initialization is not a native v2 Run")
                            if (
                                retained_init.canonical_bytes().decode() != retained_raw
                                or inbox_initialization_command(retained_init) != command
                                or execution.tenant != tenant
                                or retained_init.predecessor_commitment != entry["predecessor"]
                                or latest.get(execution.run_id) is not None
                            ):
                                raise ValueError(
                                    "selected inbox initialization differs from history"
                                )
                            expected_ids.update(
                                row.record_id for row in command.records if row.owner == OWNER
                            )
                            expected_publications.add((command.operation_kind, identity))
                            latest[execution.run_id] = execution
                            mixed_records.append(execution)
                            continue
                        if command.operation_kind != EXECUTION_TRANSITION_OPERATION:
                            raise ValueError("execution transition has a substituted operation")
                        retained_raw = entry["execution_transition"]
                        retained_kind = json.loads(retained_raw).get("kind")
                        if retained_kind == "R14_SELECTED_EXECUTION_TRANSITION_V2":
                            retained_transition: RetainedExecutionTransitionEvidence = (
                                RetainedExecutionTransition.model_validate_json(retained_raw)
                            )
                        elif retained_kind == "R14_SELECTED_EXECUTION_TRANSITION_V3":
                            retained_transition = RetainedExecutionTransitionV3.model_validate_json(
                                retained_raw
                            )
                            if not isinstance(retained_transition.request, PrepareExecutionRequest):
                                raise ValueError("H1 retained transition is not Prepare")
                            workspace_members = tuple(
                                member
                                for member in retained_transition.request.manifest.members
                                if member.producer == "projections"
                                and member.surface == "workspace"
                            )
                            if len(workspace_members) != 1:
                                raise ValueError("H1 retained transition lacks unique workspace")
                            workspace = workspace_members[0]
                            workspace_port = R13Workspace(runtime)
                            original = workspace_port.open_h1_workspace_issuance().load(
                                retained_transition.workspace_issuance
                            )
                            verify_h1_original_workspace(
                                retained_transition.workspace_issuance,
                                workspace.model_dump_json().encode(),
                                workspace.content.encode(),
                                workspace_port.open_h1_workspace_issuance(),
                                workspace_port.open_dashboard_issuance(),
                            )
                            started = next(
                                (
                                    row
                                    for row in mixed_records
                                    if isinstance(row, ExecutionRunRecord)
                                    and row.head == retained_transition.request.run.predecessor
                                ),
                                None,
                            )
                            if (
                                started is None
                                or original.tenant != retained_transition.request.run.tenant
                                or original.run_id != retained_transition.request.run.run_id
                                or original.started_run_head != started.head
                                or original.turn_id != retained_transition.request.manifest.turn_id
                                or original.worker_session
                                != retained_transition.request.manifest.worker_session
                                or original.snapshot.tenant_head + 1
                                != retained_transition.expected_head
                            ):
                                raise ValueError("H1 retained transition source cut differs")
                        else:
                            raise ValueError("unregistered retained execution transition")
                        execution = retained_transition.proposal.run
                        if not isinstance(execution, ExecutionRunRecord):
                            raise ValueError("execution transition is not a native v2 Run")
                        execution_prior = ExecutionHistorySnapshot(
                            tenant_head=command.expected_head, records=tuple(mixed_records)
                        )
                        previous_execution = latest.get(execution.run_id)
                        if (
                            retained_transition.canonical_bytes().decode() != retained_raw
                            or transition_command(retained_transition) != command
                            or execution.tenant != tenant
                            or retained_transition.predecessor_commitment != entry["predecessor"]
                            or retained_transition.expected_snapshot_fingerprint
                            != execution_prior.digest()
                            or (
                                isinstance(retained_transition.request, CreateExecutionRun)
                                and previous_execution is not None
                            )
                            or (
                                not isinstance(retained_transition.request, CreateExecutionRun)
                                and retained_transition.request.run != previous_execution
                            )
                        ):
                            raise ValueError("selected execution differs from original history")
                        expected_ids.update(
                            row.record_id for row in command.records if row.owner == OWNER
                        )
                        expected_publications.add((command.operation_kind, identity))
                        latest[execution.run_id] = execution
                        mixed_records.append(execution)
                        continue
                    record = RunRecord.model_validate_json(first.canonical_bytes)
                    if (
                        first.owner != OWNER
                        or first.schema_id != SCHEMA
                        or first.record_id != record.head
                        or record.tenant != tenant
                        or (
                            command.operation_kind != CANCELLATION_OPERATION
                            and record.head != identity
                        )
                        or first.canonical_bytes != record.canonical_bytes()
                    ):
                        raise ValueError("selected Run identity or canonical bytes differ")
                    if command.operation_kind == CANCELLATION_OPERATION:
                        raw = entry["cancellation_preparation"]
                        cancellation = RetainedCancellationPreparation.model_validate_json(raw)
                        if cancellation.canonical_bytes().decode() != raw:
                            raise ValueError("noncanonical retained cancellation")
                        cancelled_envelope = build_cancellation_envelope(cancellation)
                        prior = LoopSnapshot(
                            tenant_head=command.expected_head, records=tuple(records)
                        )
                        if (
                            cancelled_envelope.canonical_bytes().decode()
                            != entry["cancellation_envelope"]
                            or cancellation_command(cancellation) != command
                            or cancellation.run_companion != record
                            or cancellation.run_predecessor != latest.get(record.run_id)
                            or cancellation.request.cut.materialization_commitment
                            != entry["predecessor"]
                            or cancellation.expected_snapshot_fingerprint != prior.digest()
                            or cancellation.request.cut.predecessor_inventory
                            != execution_inventory(
                                tenant,
                                ExecutionHistorySnapshot(
                                    tenant_head=command.expected_head, records=tuple(mixed_records)
                                ),
                                tuple(preparations),
                                tuple(cancellations),
                                tuple(execution_preparations),
                                tuple(acceptances),
                            )
                        ):
                            raise ValueError("selected cancellation differs from original history")
                        cancellations.append(cancellation)
                    elif command.operation_kind == FANOUT_OPERATION:
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
                            != execution_inventory(
                                tenant,
                                ExecutionHistorySnapshot(
                                    tenant_head=command.expected_head, records=tuple(mixed_records)
                                ),
                                tuple(preparations),
                                tuple(cancellations),
                                tuple(execution_preparations),
                                tuple(acceptances),
                            )
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
                    previous_legacy = latest.get(record.run_id)
                    if isinstance(previous_legacy, ExecutionRunRecord):
                        raise ValueError("Run lineage changed its registered schema")
                    validate_record(previous_legacy, record)
                    expected_ids.update(
                        row.record_id for row in command.records if row.owner == OWNER
                    )
                    expected_publications.add((command.operation_kind, identity))
                    latest[record.run_id] = record
                    records.append(record)
                    mixed_records.append(record)
                if not selected_only and (
                    physical_ids != expected_ids or physical_publications != expected_publications
                ):
                    raise ValueError("orphaned, missing or unknown loop publication members")
                runtime._check_database_identity()
                return (
                    LoopSnapshot(
                        tenant_head=0 if head is None else head[0], records=tuple(records)
                    ),
                    tuple(preparations),
                    tuple(cancellations),
                    ExecutionHistorySnapshot(
                        tenant_head=0 if head is None else head[0], records=tuple(mixed_records)
                    ),
                    tuple(execution_preparations),
                    tuple(acceptances),
                )
    except (ValueError, TypeError, KeyError, sqlite3.Error) as error:
        raise LoopIntegrityError(
            f"operation=snapshot tenant={runtime._tenant_id} record_id={identity}"
        ) from error


def read_call_history(
    runtime: R14PlanningRuntime,
) -> tuple[
    LoopSnapshot, tuple[RetainedFanOutPreparation, ...], tuple[RetainedCancellationPreparation, ...]
]:
    snapshot, fanout, cancellations, _, _, _ = _read_call_history(runtime)
    return snapshot, fanout, cancellations


def read_execution_history(runtime: R14PlanningRuntime) -> ExecutionHistorySnapshot:
    """Validate every selected/physical Run, retaining both registered schemas."""
    return _read_call_history(runtime)[3]


def read_execution_call_history(
    runtime: R14PlanningRuntime,
) -> tuple[
    ExecutionHistorySnapshot, CallInventorySnapshot, tuple[RetainedExecutionFanOutPreparation, ...]
]:
    _, legacy, cancellations, snapshot, executions, acceptances = _read_call_history(runtime)
    return (
        snapshot,
        execution_inventory(
            runtime._tenant_id, snapshot, legacy, cancellations, executions, acceptances
        ),
        executions,
    )


def read_call_inventory(runtime: R14PlanningRuntime) -> CallInventorySnapshot:
    return read_execution_call_history(runtime)[1]


def validate_selected_executions(runtime: R14PlanningRuntime) -> None:
    """Reject execution semantic corruption before recovery can materialize its tail."""
    runtime._pending()
    found = False
    for _, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        if entry.get("kind") != "DECIDED":
            continue
        command = runtime._publication(entry)
        claimed = (
            command.operation_kind
            in (
                EXECUTION_TRANSITION_OPERATION,
                EXECUTION_FANOUT_OPERATION,
                EXECUTION_COMPLETE_SEAL_OPERATION,
                EXECUTION_INBOX_INITIALIZATION_OPERATION,
            )
            or "execution_transition" in entry
            or "execution_fanout" in entry
            or "execution_complete_seal" in entry
            or "inbox_initialization" in entry
            or any(record.schema_id == EXECUTION_RUN_SCHEMA for record in command.records)
        )
        if claimed:
            if command.operation_kind not in (
                EXECUTION_TRANSITION_OPERATION,
                EXECUTION_FANOUT_OPERATION,
                EXECUTION_COMPLETE_SEAL_OPERATION,
                EXECUTION_INBOX_INITIALIZATION_OPERATION,
            ):
                raise LoopIntegrityError(
                    f"operation=startup_execution tenant={runtime._tenant_id} "
                    f"record_id={command.idempotency_key}"
                ) from ValueError("execution publication has a substituted operation")
            found = True
    if found:
        _read_call_history(runtime, selected_only=True)


def validate_selected_h1_completions(runtime: R14PlanningRuntime) -> None:
    """Fail closed on retained H1 selection before owner recovery can write SQL.

    A wholly absent selected batch is the only recoverable pending state.  The
    readback inspector classifies any prefix, altered companion, or conflicting
    publication as ``CONFLICT``; it must never reach ``_recover_exact``.
    """
    runtime._pending()
    for decision in runtime._owner_decisions().snapshot().decisions:
        batch = decision.prepared.request
        if not isinstance(batch, CompleteDeliveryBatchV2):
            continue
        _completion_v2_schema_dispatch(batch)
        if batch.operation != COMPLETE_ACCEPTANCE_OPERATION:
            raise LoopIntegrityError(
                f"operation=startup_h1_completion tenant={runtime._tenant_id} "
                f"record_id={batch.identity.command_id}"
            ) from ValueError("unregistered complete delivery v2 operation")
        try:
            command, _, _ = _selected_h1_completion(runtime, decision)
            with read_connection(runtime._database) as connection:
                state = inspect_publication(connection, command, decision.tenant_commit_sequence)
            if state == "CONFLICT":
                raise ValueError("selected H1 completion has partial or corrupt physical members")
        except (ValueError, TypeError, KeyError, sqlite3.Error) as error:
            raise LoopIntegrityError(
                f"operation=startup_h1_completion tenant={runtime._tenant_id} "
                f"record_id={batch.identity.command_id}"
            ) from error


def read_loop_history(
    runtime: R14PlanningRuntime,
) -> tuple[LoopSnapshot, tuple[RetainedFanOutPreparation, ...]]:
    snapshot, fanout, _ = read_call_history(runtime)
    return snapshot, fanout


def validate_selected_cancellations(runtime: R14PlanningRuntime) -> None:
    """Validate original semantics before any selected cancellation can be recovered."""
    runtime._pending()
    found = False
    for _, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        if entry.get("kind") != "DECIDED":
            continue
        command = runtime._publication(entry)
        claims_cancellation = (
            command.operation_kind == CANCELLATION_OPERATION
            or "cancellation_preparation" in entry
            or any(
                record.schema_id in (CANCELLATION_SCHEMA, NOT_EXECUTED_SCHEMA)
                for record in command.records
            )
        )
        if claims_cancellation:
            if command.operation_kind != CANCELLATION_OPERATION:
                raise LoopIntegrityError("cancellation publication has a substituted operation")
            found = True
    if found:
        _read_call_history(runtime, selected_only=True)


def read_loop_snapshot(runtime: R14PlanningRuntime) -> LoopSnapshot:
    return read_call_history(runtime)[0]
