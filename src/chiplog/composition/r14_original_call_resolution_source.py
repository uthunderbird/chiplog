"""Capture one exact selected R14 obligation and closed R16 witness."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    IndependentResolverAuthority,
    LoopRecoveryStream,
    OriginalResolverCut,
    PrepareOriginalCallResolution,
    original_effect_evidence_reducer_policy_v1,
    original_resolver_authority_policy_v1,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.composition.r14_call_preview import execution_run_head
from chiplog.composition.r14_loop_history import read_execution_recovery_history
from chiplog.composition.r14_native_preparation_bindings import (
    bind_native_execution_preparation_v2,
)
from chiplog.composition.r14_native_selected_accounting_source import (
    R14NativeAcceptedOpenOutcome,
    R14NativeSelectedAccountingSource,
)
from chiplog.composition.r14_original_prepare_source import R14OriginalNativePrepareSource
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.workspace_snapshot import read_connection

if TYPE_CHECKING:
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime

_RESOLUTION_OPERATION = "agent_loop.original-call-resolution.v1"
_RESOLUTION_SCHEMAS = frozenset(
    {
        "chiplog.loop.original-resolution-basis.v1",
        "chiplog.loop.original-obligation-closure.v1",
        "chiplog.loop.recovered-call-outcome.v1",
        "chiplog.loop.original-resolver-batch.v1",
    }
)


def _require_resolution_absent(runtime: CommonCliExecutionRuntime) -> None:
    for _, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        if entry.get("kind") != "DECIDED":
            continue
        if entry.get("operation_kind") == _RESOLUTION_OPERATION:
            raise LoopRejected("selected original call resolution already exists")
        records = entry.get("records")
        if isinstance(records, list) and any(
            isinstance(row, dict) and row.get("schema") in _RESOLUTION_SCHEMAS
            for row in records
        ):
            raise LoopRejected("original resolution records exist without a selected resolution")

    placeholders = ",".join("?" for _ in _RESOLUTION_SCHEMAS)
    with read_connection(runtime._database) as connection:
        rows = tuple(
            connection.execute(
                "SELECT record_id FROM records WHERE tenant_id=? AND schema_id IN ("
                + placeholders
                + ") LIMIT 1",
                (runtime._tenant_id, *_RESOLUTION_SCHEMAS),
            ).fetchall()
        )
    if rows:
        raise LoopRejected("unselected original resolution physical records exist")


def _selected_witness_source(closed: R14NativeAcceptedOpenOutcome) -> RecoverySourceRecord:
    outcome = closed.latest_outcome
    member = closed.latest_selected_member
    raw = outcome.canonical_bytes()
    if (
        member.record_bytes != raw
            or member.record != outcome.record
            or member.kind != outcome.kind
            or member.intent.subject_id != outcome.snapshot.intent.intent_id
        ):
        raise LoopRejected("closed R16 selected witness differs from retained physical record")
    record = CallSubjectHead(
        subject_id=member.record.subject_id,
        revision=Present(head=member.record.head, fingerprint=member.record.fingerprint),
    )
    decision = CallSubjectHead(
        subject_id=member.selected_decision.subject_id,
        revision=Present(
            head=member.selected_decision.head,
            fingerprint=member.selected_decision.fingerprint,
        ),
    )
    return RecoverySourceRecord(
        owner="effects",
        subject=record,
        schema_id=outcome.schema_id,
        canonical_record_bytes=raw,
        selected_decision=decision,
        physical_record=record,
    )


def capture_original_call_resolution_request(
    runtime: CommonCliExecutionRuntime,
) -> PrepareOriginalCallResolution:
    """Capture revalidated R14 sources and the complete selected closed R16 cut.

    The effects reader reopens the full materialized history under its closed
    verifier. Unknown or unsupported effects rows fail closed; therefore the
    Effects-owned semantic-reduction inventory is empty in this profile.
    """
    from chiplog.composition.r14_original_resolver_invocation_publication import (
        read_selected_original_resolver_invocation_source_held,
    )
    from chiplog.composition.r14_original_resolver_policy_publication import (
        read_selected_original_effect_evidence_reducer_policy_source_held,
        read_selected_original_resolver_authority_policy_source_held,
    )

    runtime._pending()
    with runtime._authority_gate().hold():
        runtime._check_database_identity()
        database_identity = runtime._database_identity
        with read_connection(runtime._database) as connection:
            before = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
            head_row = connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
            ).fetchone()
            tenant_commit_sequence = 0 if head_row is None else int(head_row[0])
        anchored = runtime._commitment_journal.load(runtime._tenant_id)
        if before != anchored:
            raise LoopRejected("original resolution source cut differs from authority anchor")

        opened = runtime.read_selected_original_obligation_sources()  # type: ignore[no-untyped-call]
        resolver = read_selected_original_resolver_authority_policy_source_held(  # type: ignore[no-untyped-call]
            runtime
        )
        reducer = read_selected_original_effect_evidence_reducer_policy_source_held(  # type: ignore[no-untyped-call]
            runtime
        )
        invocation = read_selected_original_resolver_invocation_source_held(  # type: ignore[no-untyped-call]
            runtime
        )
        if opened is None or resolver is None or reducer is None or invocation is None:
            raise LoopRejected(
                "selected original obligation authority sources are incomplete; HOLD"
            )
        opening_source, terminal_source = opened
        resolver_policy, resolver_source = resolver
        reducer_policy, reducer_source = reducer
        authenticated_invocation, invocation_source = invocation

        history = read_execution_recovery_history(runtime, selected_only=True)
        snapshot, _, _, executions, _, _ = history
        if len(executions) != 1 or not snapshot.records:
            raise LoopRejected("original resolution requires one selected native execution")
        seal = executions[0].proposal.fan_out.response_seal
        seal_digest = seal.digest()
        seal_ref = CallSubjectHead(
            subject_id=seal.response_seal_id,
            revision=Present(head="record:" + seal_digest, fingerprint=seal_digest),
        )
        projection = R14OriginalNativePrepareSource(runtime).collect(seal_ref)
        if projection is None:
            raise LoopRejected("original resolution native Prepare source unavailable")
        accounting = R14NativeSelectedAccountingSource(
            runtime, projection, bind_native_execution_preparation_v2(projection)
        )
        closed = accounting.collect_one_accepted_closed_outcome(
            execution_run_head(snapshot.records[-1]), selected_only=True
        )
        if closed is None:
            raise LoopRejected("exact selected closed R16 outcome unavailable; HOLD")

        from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
            AcceptedOpenRecoveryOpeningV1,
            AcceptedOpenTerminalDispositionV1,
        )

        opening = AcceptedOpenRecoveryOpeningV1.model_validate_json(
            opening_source.canonical_record_bytes
        )
        terminal = AcceptedOpenTerminalDispositionV1.model_validate_json(
            terminal_source.canonical_record_bytes
        )
        original = opening.original
        if (
            terminal.kind != "RECOVERY_REQUIRED"
            or resolver_policy != original_resolver_authority_policy_v1(runtime._tenant_id)
            or reducer_policy != original_effect_evidence_reducer_policy_v1(runtime._tenant_id)
            or authenticated_invocation.original != original
            or authenticated_invocation.accepted_opening_source != opening_source
            or authenticated_invocation.accepted_terminal_source != terminal_source
            or authenticated_invocation.resolver_policy_source != resolver_source
            or authenticated_invocation.reducer_policy_source != reducer_source
            or closed.tenant_id != runtime._tenant_id
            or closed.database_identity != database_identity
            or closed.tenant_commit_sequence != tenant_commit_sequence
            or closed.materialization_commitment != before
        ):
            raise LoopRejected("selected R14 and R16 resolution sources do not share one cut")
        _require_resolution_absent(runtime)

        stream = LoopRecoveryStream(
            stream_id=original.obligation_stream_id,
            registry=reducer_source.physical_record,
            subject=opening_source.subject,
            schema_id="chiplog.loop.original-obligation-stream.v1",
        )
        command_id = (
            "r14-original-resolution:"
            + runtime._tenant_id
            + ":"
            + original.obligation_id
            + ":"
            + closed.latest_outcome.record.head
        )
        cut = OriginalResolverCut(
            tenant_id=runtime._tenant_id,
            database_id=str(database_identity[0]),
            tenant_commit_sequence=tenant_commit_sequence,
            materialization_commitment=before,
            original=original,
            obligation_stream=stream,
            current_obligation=opening_source,
            call_terminal=terminal_source,
            selected_witness=_selected_witness_source(closed),
            complete_ordered_evidence=closed.complete_ordered_evidence_sources,
            complete_selected_effects_reductions=(),
            expected_closure=Absent(),
            expected_recovered_outcome=Absent(),
            resolver_policy=resolver_source,
            reducer_policy=reducer_source,
            authority=IndependentResolverAuthority(
                registered_authority=resolver_source,
                authenticated_invocation=invocation_source,
            ),
        )
        request = PrepareOriginalCallResolution(command_id=command_id, cut=cut)
        with read_connection(runtime._database) as connection:
            after = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
        runtime._check_database_identity()
        if (
            after != before
            or runtime._commitment_journal.load(runtime._tenant_id) != before
            or runtime._database_identity != database_identity
        ):
            raise LoopRejected("original resolution source cut changed during capture")
        return request
