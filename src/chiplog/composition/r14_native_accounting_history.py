"""Historical decoder for one selected native cancellation accounting record."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from chiplog.capabilities.agent_loop.contracts import RunRecord
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.composition.r14_cancellation_contracts import CANCELLATION_SCHEMA, NOT_EXECUTED_SCHEMA
from chiplog.composition.r14_execution_fanout_contracts import EXECUTION_RUN_SCHEMA
from chiplog.composition.r14_execution_fanout_records import reference
from chiplog.composition.r14_execution_inventory import execution_inventory
from chiplog.composition.r14_execution_transition_records import ExecutionHistorySnapshot
from chiplog.composition.r14_fanout_contracts import INITIALIZED_SCHEMA, SEAL_SCHEMA
from chiplog.composition.r14_h1_workspace_issuance import H1OriginalWorkspaceIssuance
from chiplog.composition.r14_h1_workspace_issuance_contracts import H1VerifiedWorkspaceClosure
from chiplog.composition.r14_native_accounting_records import (
    NATIVE_ACCOUNTING_OPERATION,
    NATIVE_ACCOUNTING_SCHEMA,
    RetainedNativeOneCallAccountingPublication,
    _native_one_call_accounting_artifacts,
)
from chiplog.composition.r14_native_execution_record_source import (
    R14NativeExecutionRecordSourceManifest,
    _fingerprint,
)
from chiplog.composition.r14_native_scope_binding import NativePrepareScopeBindingV1
from chiplog.composition.r14_native_selected_accounting_source import (
    R14NativeOneCallCancelledFacts,
    _run_reference,
)
from chiplog.platform._sqlite import PhysicalPublicationCommand

if TYPE_CHECKING:
    from chiplog.composition.r14_execution_cancellation_contracts import (
        RetainedExecutionCancellationPreparation,
    )
    from chiplog.composition.r14_execution_fanout_contracts import (
        RetainedExecutionFanOutPreparation,
    )
    from chiplog.composition.r14_runtime import R14PlanningRuntime


def retained_selected_native_accounting(
    entry: dict[str, object],
) -> RetainedNativeOneCallAccountingPublication:
    raw = entry.get("native_one_call_accounting_evidence")
    if not isinstance(raw, str):
        raise ValueError("selected native accounting evidence is absent")
    evidence = RetainedNativeOneCallAccountingPublication.model_validate_json(raw)
    if evidence.canonical_bytes().decode() != raw:
        raise ValueError("selected native accounting evidence is noncanonical")
    return evidence


def _verify_original_prepare_projection(
    runtime: R14PlanningRuntime,
    prepare: object,
    retained_bytes: bytes,
    decision_id: str,
    decision_bytes: bytes,
    seal: object,
    fanout: RetainedExecutionFanOutPreparation,
    run_records: list[RunRecord | ExecutionRunRecord],
    request_bindings: object,
    verified_h1_prepare: tuple[
        H1OriginalWorkspaceIssuance,
        H1VerifiedWorkspaceClosure,
        NativePrepareScopeBindingV1 | None,
    ]
    | None,
) -> None:
    """Rebuild every retained Prepare projection field from selected owner sources."""
    from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
    from chiplog.capabilities.agent_loop.execution_transition_contracts import (
        PrepareExecutionRequest,
    )
    from chiplog.capabilities.agent_loop.recovery_contracts import Present
    from chiplog.composition.r13_workspace import R13Workspace
    from chiplog.composition.r14_execution_transition_records import (
        RetainedExecutionTransitionV3,
        RetainedExecutionTransitionV4,
    )
    from chiplog.composition.r14_h1_workspace_issuance import (
        _verify_loaded_h1_original_workspace,
    )
    from chiplog.composition.r14_native_preparation_bindings import (
        bind_native_execution_preparation_v2,
    )
    from chiplog.composition.r14_original_prepare_source import (
        R14OriginalNativePrepareProjection,
        R14OriginalNativePrepareSource,
    )

    if not isinstance(prepare, R14OriginalNativePrepareProjection):
        raise ValueError("native accounting original Prepare projection has wrong type")
    retained_type = (
        RetainedExecutionTransitionV4
        if json.loads(retained_bytes).get("kind") == "R14_SELECTED_EXECUTION_TRANSITION_V4"
        else RetainedExecutionTransitionV3
    )
    retained = retained_type.model_validate_json(retained_bytes)
    if not isinstance(retained.request, PrepareExecutionRequest):
        raise ValueError("native accounting original Prepare has another transition role")
    if isinstance(retained, RetainedExecutionTransitionV4):
        if verified_h1_prepare is None:
            from .r14_native_scope_binding import verify_native_prepare_scope

            verify_native_prepare_scope(
                runtime, retained.request, retained.native_scope_binding, historical=True
            )
        elif verified_h1_prepare[2] != retained.native_scope_binding:
            raise ValueError("native accounting cached Prepare scope differs")
        if prepare.original_native_scope_binding != retained.native_scope_binding:
            raise ValueError("native accounting original scope differs")
    elif prepare.original_native_scope_binding is not None:
        raise ValueError("native scope retroactively attached to old Prepare")
    run = retained.proposal.run
    captured = fanout.request.captured_run
    started = next(
        (
            row
            for row in run_records
            if isinstance(row, ExecutionRunRecord) and row.head == retained.request.run.predecessor
        ),
        None,
    )
    prepared_turns = (
        tuple(turn for turn in run.turns if turn.turn_id == seal.original_turn_id)
        if isinstance(run, ExecutionRunRecord)
        else ()
    )
    captured_turns = tuple(
        turn for turn in captured.turns if turn.turn_id == seal.original_turn_id
    )
    if (
        not isinstance(run, ExecutionRunRecord)
        or started is None
        or run.tenant != runtime._tenant_id
        or run.run_id != captured.run_id
        or run.policy != started.policy
        or len(prepared_turns) != 1
        or len(captured_turns) != 1
        or len(prepared_turns[0].attempts) != 1
        or len(captured_turns[0].attempts) != 1
        or prepared_turns[0].selector != 0
        or captured_turns[0].selector != 0
        or prepared_turns[0].turn_id != captured_turns[0].turn_id
    ):
        raise ValueError("native accounting original Prepare Run differs")
    manifest = captured_turns[0].attempts[0].manifest
    if (
        manifest != retained.request.manifest
        or manifest != prepared_turns[0].attempts[0].manifest
        or len(manifest.members) != 4
        or tuple(item.surface for item in manifest.members)
        != ("workspace", "context", "prompt", "schema")
    ):
        raise ValueError("native accounting original Prepare manifest differs")
    workspace_bytes = manifest.members[0].model_dump_json().encode()
    if verified_h1_prepare is None:
        workspace = R13Workspace(runtime)
        journal = workspace.open_h1_workspace_issuance()
        issuance = journal.load(retained.workspace_issuance)
        if issuance.workspace_member_json.encode() != workspace_bytes:
            raise ValueError("native accounting original Prepare workspace member differs")
        _verify_loaded_h1_original_workspace(
            retained.workspace_issuance,
            workspace_bytes,
            issuance.proposal_context_json.encode(),
            issuance,
            workspace.open_dashboard_issuance(),
        )
    else:
        issuance, closure, _ = verified_h1_prepare
        if (
            issuance.workspace_member_json.encode() != workspace_bytes
            or closure.issuance != retained.workspace_issuance
            or closure.workspace_member_bytes != workspace_bytes
            or closure.proposal_context_bytes != issuance.proposal_context_json.encode()
        ):
            raise ValueError("native accounting cached Prepare closure differs")
    issuance_bytes = issuance.canonical_bytes()
    scope = R14OriginalNativePrepareSource(runtime)._descriptive_scope_held(
        issuance=issuance,
        issuance_bytes=issuance_bytes,
        run=run,
        started_worker_session=started.worker_session,
        manifest=manifest,
        gate=runtime._authority_gate(),
    )
    artifact = manifest.artifact
    run_bytes = run.canonical_bytes()
    expected = R14OriginalNativePrepareProjection(
        tenant_id=run.tenant,
        database_identity=runtime._database_identity,
        selected_response_seal=reference(seal.response_seal_id, seal),
        selected_prepare_decision_id=decision_id,
        selected_prepare_decision_bytes=decision_bytes,
        selected_prepare_decision_fingerprint=hashlib.sha256(decision_bytes).hexdigest(),
        retained_prepare_bytes=retained_bytes,
        prepared_run=CallSubjectHead(
            subject_id=run.run_id,
            revision=Present(head=run.head, fingerprint=hashlib.sha256(run_bytes).hexdigest()),
        ),
        prepared_run_bytes=run_bytes,
        selected_turn_id=seal.original_turn_id,
        workspace_issuance=retained.workspace_issuance,
        workspace_issuance_bytes=issuance_bytes,
        manifest=manifest,
        artifact=artifact,
        artifact_bytes=artifact.canonical_bytes(),
        rendered_prompt_bytes=artifact.rendered.encode(),
        generated_schema_bytes=artifact.response_schema_json.encode(),
        ordered_tool_spec_bytes=tuple(tool.canonical_bytes() for tool in artifact.tools),
        policy_bytes=started.policy.canonical_bytes(),
        descriptive_original_h1_output_scope=scope,
        original_native_scope_binding=(
            retained.native_scope_binding
            if isinstance(retained, RetainedExecutionTransitionV4)
            else None
        ),
    )
    if prepare != expected or bind_native_execution_preparation_v2(expected) != request_bindings:
        raise ValueError("native accounting original Prepare projection differs")


def decode_selected_native_accounting(
    runtime: R14PlanningRuntime,
    connection: sqlite3.Connection,
    command: PhysicalPublicationCommand,
    entry: dict[str, object],
    fanouts: list[RetainedExecutionFanOutPreparation],
    cancellations: list[RetainedExecutionCancellationPreparation],
    run_records: list[RunRecord | ExecutionRunRecord],
    *,
    verified_h1_prepares: dict[
        tuple[bytes, str],
        tuple[
            H1OriginalWorkspaceIssuance,
            H1VerifiedWorkspaceClosure,
            NativePrepareScopeBindingV1 | None,
        ],
    ]
    | None = None,
) -> RetainedNativeOneCallAccountingPublication:
    """Verify selected source, exact prior physical rows and one new member."""
    if command.operation_kind != NATIVE_ACCOUNTING_OPERATION:
        raise ValueError("native accounting has a substituted operation")
    evidence = retained_selected_native_accounting(entry)
    source_adapter = TypeAdapter(R14NativeOneCallCancelledFacts)
    physical_adapter = TypeAdapter(R14NativeExecutionRecordSourceManifest)
    selected = source_adapter.validate_json(evidence.selected_source_bytes)
    source = physical_adapter.validate_json(evidence.physical_source_bytes)
    if len(fanouts) != 1 or len(cancellations) != 1:
        raise ValueError("native accounting requires one selected seal and cancellation")
    seal = fanouts[0].proposal.fan_out.response_seal
    initialized = fanouts[0].proposal.fan_out.initialized_records
    cancellation = cancellations[0].proposal
    target_run_id = selected.source.original_prepare.prepared_run.subject_id
    target_lineage = tuple(
        _run_reference(run)
        for run in run_records
        if isinstance(run, ExecutionRunRecord) and run.run_id == target_run_id
    )
    inventory = execution_inventory(
        runtime._tenant_id,
        ExecutionHistorySnapshot(tenant_head=command.expected_head, records=tuple(run_records)),
        (),
        (),
        tuple(fanouts),
        tuple(cancellations),
        (),
    )
    if len(initialized) != 1:
        raise ValueError("native accounting requires one initialized call")
    expected_members = (
        *(
            (EXECUTION_RUN_SCHEMA, item.revision.head)
            for item in source.complete_ordered_run_lineage
        ),
        (SEAL_SCHEMA, "record:" + seal.digest()),
        (INITIALIZED_SCHEMA, "record:" + initialized[0].digest()),
        (CANCELLATION_SCHEMA, cancellation.terminal.terminal_id),
        (NOT_EXECUTED_SCHEMA, cancellation.result.result_id),
    )
    if (
        source_adapter.dump_json(selected) != evidence.selected_source_bytes
        or physical_adapter.dump_json(source) != evidence.physical_source_bytes
        or source.source_stamp != evidence.request.source_stamp
        or source.source_stamp.tenant_id != runtime._tenant_id
        or source.source_stamp.database_identity != runtime._database_identity
        or source.source_stamp.tenant_commit_sequence != command.expected_head
        or source.source_stamp.authority_materialization_commitment != entry.get("predecessor")
        or source.tenant_id != runtime._tenant_id
        or selected.source.tenant_id != runtime._tenant_id
        or selected.source.preparation_bindings != evidence.request.preparation_bindings
        or selected.initialized != evidence.request.request.initialized
        or selected.selected_seal.response_seal != evidence.request.request.selected_response
        or source.selected_response_seal != selected.selected_seal.response_seal
        or source.initialized != selected.initialized
        or source.terminal != reference(cancellation.terminal.terminal_id, cancellation.terminal)
        or source.result != reference(cancellation.result.result_id, cancellation.result)
        or source.original_prepared_run != selected.source.original_prepare.prepared_run
        or source.complete_ordered_run_lineage != selected.source.target_run_lineage
        or source.complete_ordered_run_lineage != target_lineage
        or selected.source.target_inventory_rows != inventory.ordered_calls
        or selected.source.selected_native_seals != (selected.selected_seal,)
        or selected.selected_seal.captured_run != _run_reference(fanouts[0].request.captured_run)
        or selected.selected_seal.sealed_run != _run_reference(fanouts[0].proposal.sealed_run)
        or selected.selected_seal.physical_sealed_run != selected.selected_seal.sealed_run
        or len(selected.source.selected_native_seals) != 1
        or len(selected.source.target_cancellations) != 1
        or len(selected.source.target_acceptances) != 0
        or selected.source.target_cancellations[0] != cancellations[0]
        or selected.selected_seal.response_seal != reference(seal.response_seal_id, seal)
        or tuple((item.schema_id, item.record_id) for item in source.ordered_members)
        != expected_members
        or source.fingerprint
        != _fingerprint(
            source.tenant_id,
            source.source_stamp,
            source.original_prepared_run,
            source.selected_response_seal,
            source.initialized,
            source.terminal,
            source.result,
            source.complete_ordered_run_lineage,
            source.ordered_members,
        )
    ):
        raise ValueError("selected native accounting source differs from historical cut")
    decision_rows = {
        str(decision_id): raw
        for decision_id, _predecessor, raw in runtime._loop_decisions().entries()
    }
    prepare = selected.source.original_prepare
    prepare_decision_bytes = decision_rows.get(prepare.selected_prepare_decision_id)
    if (
        prepare_decision_bytes != prepare.selected_prepare_decision_bytes
        or prepare_decision_bytes is None
        or hashlib.sha256(prepare_decision_bytes).hexdigest()
        != prepare.selected_prepare_decision_fingerprint
    ):
        raise ValueError("native accounting original Prepare selection differs")
    prepare_entry = json.loads(prepare_decision_bytes)
    prepare_command = runtime._publication(prepare_entry)
    if (
        prepare_entry.get("kind") != "DECIDED"
        or prepare_entry.get("execution_transition") != prepare.retained_prepare_bytes.decode()
        or prepare_command.expected_head >= command.expected_head
        or not any(
            member.owner == "agent_loop"
            and member.schema_id == EXECUTION_RUN_SCHEMA
            and member.record_id == prepare.prepared_run.revision.head
            and member.canonical_bytes == prepare.prepared_run_bytes
            for member in prepare_command.records
        )
    ):
        raise ValueError("native accounting original Prepare locator differs")
    verified_h1_prepare = (
        None
        if verified_h1_prepares is None
        else verified_h1_prepares.get(
            (
                prepare.retained_prepare_bytes,
                prepare.workspace_issuance.model_dump_json(),
            )
        )
    )
    _verify_original_prepare_projection(
        runtime,
        prepare,
        prepare.retained_prepare_bytes,
        prepare.selected_prepare_decision_id,
        prepare_decision_bytes,
        seal,
        fanouts[0],
        run_records,
        evidence.request.preparation_bindings,
        verified_h1_prepare,
    )
    if len(source.ordered_members) != len({item.record_id for item in source.ordered_members}):
        raise ValueError("native accounting source has duplicate physical members")
    for item in source.ordered_members:
        raw = decision_rows.get(item.selected_decision_id)
        if (
            raw is None
            or raw != item.selected_decision_bytes
            or hashlib.sha256(raw).hexdigest() != item.selected_decision_fingerprint
        ):
            raise ValueError("native accounting prior decision differs")
        prior_entry = json.loads(raw)
        if prior_entry.get("kind") != "DECIDED":
            raise ValueError("native accounting prior decision is not selected")
        prior_command = runtime._publication(prior_entry)
        matches = tuple(row for row in prior_command.records if row.record_id == item.record_id)
        row = connection.execute(
            "SELECT owner,schema_id,canonical_bytes,commit_sequence FROM records "
            "WHERE tenant_id=? AND record_id=?",
            (runtime._tenant_id, item.record_id),
        ).fetchone()
        if (
            len(matches) != 1
            or prior_command.operation_kind != item.selected_operation
            or prior_command.expected_head + 1 != item.physical_commit_sequence
            or item.physical_commit_sequence > command.expected_head
            or row
            != (
                item.owner,
                item.schema_id,
                item.canonical_record_bytes,
                item.physical_commit_sequence,
            )
            or matches[0].owner != item.owner
            or matches[0].schema_id != item.schema_id
            or matches[0].canonical_bytes != item.canonical_record_bytes
        ):
            raise ValueError("native accounting prior physical member differs")
    expected_command, expected_envelope = _native_one_call_accounting_artifacts(evidence, source)
    if (
        expected_command != command
        or entry.get("native_one_call_accounting_envelope")
        != expected_envelope.canonical_bytes().decode()
        or len(command.records) != 1
        or command.records[0].schema_id != NATIVE_ACCOUNTING_SCHEMA
        or command.records[0].owner != "agent_loop"
    ):
        raise ValueError("selected native accounting physical envelope differs")
    return evidence
