"""Run projection from independently selected, exactly materialized R14 history."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from dataclasses import replace
from typing import TYPE_CHECKING, Literal, cast

from chiplog.adapters.driven.loop_sqlite import OWNER, LoopIntegrityError
from chiplog.adapters.driven.loop_sqlite import SCHEMA as RUN_SCHEMA
from chiplog.adapters.driven.r9_fence import CONVERSATION_OWNER, CONVERSATION_SCHEMA
from chiplog.architecture.r7_storage_surface import AUTHORITY_STORAGE_SURFACE_DIGEST
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallInventorySnapshot
from chiplog.capabilities.agent_loop.contracts import LoopRejected, LoopSnapshot, RunRecord
from chiplog.capabilities.agent_loop.domain import validate_record
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_history_contracts import ExecutionRunRecordV3
from chiplog.capabilities.agent_loop.execution_run_record_contracts import (
    EXECUTION_RUN_V2_SCHEMA,
    EXECUTION_RUN_V3_SCHEMA,
    ExecutionRunCanonicalMember,
    decode_execution_run_member,
)
from chiplog.capabilities.agent_loop.execution_transition_contracts import (
    CreateExecutionRun,
    PrepareExecutionRequest,
)
from chiplog.capabilities.agent_loop.recovery_domain import SealedAccounting
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    ConsequentialAcceptedCall,
)
from chiplog.composition.h1_v2_authority_cut import (
    H1_V2_AUTHORITY_CUT_FIELD,
    resolve_h1_v2_authority_cut,
)
from chiplog.composition.h1_verified_snapshot_rows import H1VerifiedSnapshotRows
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
from chiplog.composition.r14_execution_cancellation import (
    build_execution_cancellation_envelope,
    execution_cancellation_command,
)
from chiplog.composition.r14_execution_cancellation_contracts import (
    EXECUTION_CANCELLATION_OPERATION,
    RetainedExecutionCancellationPreparation,
    RetainedExecutionCancellationPreparationV2,
    decode_execution_cancellation_preparation,
)
from chiplog.composition.r14_execution_complete_seal_records import (
    EXECUTION_COMPLETE_SEAL_OPERATION,
    RetainedExecutionCompleteSeal,
    RetainedExecutionCompleteSealV2,
    RetainedExecutionCompleteSealV3,
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
from chiplog.composition.r14_execution_fanout_records import reference
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
    RetainedExecutionTransitionV4,
    transition_command,
)
from chiplog.composition.r14_fanout_contracts import FANOUT_OPERATION, RetainedFanOutPreparation
from chiplog.composition.r14_fanout_records import (
    build_envelope,
    physical_command,
)
from chiplog.composition.r14_h1_workspace_issuance import (
    H1OriginalWorkspaceIssuance,
    _verify_loaded_h1_original_workspace,
)
from chiplog.composition.r14_h1_workspace_issuance_contracts import H1VerifiedWorkspaceClosure
from chiplog.composition.r14_native_scope_binding import NativePrepareScopeBindingV1
from chiplog.platform._owner_publication_contracts import (
    CompleteDeliveryBatchV2,
    DeliveryWorkClaimBatchV1,
    DeliveryWorkGenesisBatchV1,
)
from chiplog.platform._sqlite import PhysicalPublicationCommand
from chiplog.platform.authority_checkpoint import (
    AuthorityCheckpointRefV1,
    AuthorityCheckpointStore,
    AuthorityCheckpointVerificationError,
    VerifiedAuthoritySnapshot,
)
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.publication_readback import inspect_publication
from chiplog.platform.workspace_snapshot import read_connection

if TYPE_CHECKING:
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
    from chiplog.composition.r14_runtime import R14PlanningRuntime


def _strict_json(raw: bytes | str, *, canonical: bool = True) -> dict[str, object]:
    """Decode a selected JSON object, always rejecting duplicate keys."""

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("selected decision has duplicate JSON key")
            value[key] = item
        return value

    value = json.loads(raw, object_pairs_hook=unique)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    original = raw.encode() if isinstance(raw, str) else raw
    if not isinstance(value, dict) or (canonical and encoded != original):
        raise ValueError("selected decision is noncanonical")
    return value


def resolve_h1_checkpoint(
    selected_authenticated_decision: dict[str, object],
    store: AuthorityCheckpointStore,
    admitted_database_identity: tuple[str, int, int],
    command: PhysicalPublicationCommand,
) -> VerifiedAuthoritySnapshot:
    """Resolve the V3 checkpoint bound to one authenticated selected command.

    This authenticates the descriptor and immutable post-image only.  The E
    historical adapter owns queries over ``snapshot.tables``; callers must not
    substitute current SQLite rows for this source.
    """

    descriptor = selected_authenticated_decision.get("h1_historical_checkpoint")
    if not isinstance(descriptor, dict) or set(descriptor) != {
        "version",
        "reference",
        "tenant_id",
        "operation_id",
        "commit_sequence",
        "database_binding",
        "resulting",
    }:
        raise ValueError("H1 V3 selected checkpoint descriptor is absent or malformed")
    binding = descriptor["database_binding"]
    reference = descriptor["reference"]
    if (
        descriptor["version"] != 1
        or not isinstance(binding, dict)
        or set(binding) != {"canonical_path", "st_dev", "st_ino"}
        or not isinstance(reference, dict)
        or descriptor["tenant_id"] != command.tenant_id
        or descriptor["operation_id"] != command.idempotency_key
        or descriptor["commit_sequence"] != command.expected_head + 1
        or descriptor["resulting"] != selected_authenticated_decision.get("resulting")
        or (
            binding.get("canonical_path"),
            binding.get("st_dev"),
            binding.get("st_ino"),
        )
        != admitted_database_identity
        or not isinstance(descriptor["resulting"], str)
    ):
        raise ValueError("H1 V3 selected checkpoint binding differs")
    try:
        ref = AuthorityCheckpointRefV1.model_validate(reference)
        snapshot = store.resolve_verified(
            ref,
            expected_resulting=descriptor["resulting"],
            expected_surface_digest=AUTHORITY_STORAGE_SURFACE_DIGEST,
        )
        _verify_checkpoint_selected_membership(snapshot, command)
        return snapshot
    except (TypeError, ValueError, AuthorityCheckpointVerificationError) as error:
        raise ValueError("H1 V3 selected checkpoint cannot be verified") from error


def _verify_checkpoint_selected_membership(
    snapshot: VerifiedAuthoritySnapshot, command: PhysicalPublicationCommand
) -> None:
    """Bind the V3 descriptor to the exact selected post-image membership."""

    tables = {table.table: (table.columns, table.rows) for table in snapshot.tables}
    try:
        publication_columns, publication_rows = tables["publications"]
        record_columns, record_rows = tables["records"]
        publication = dict(
            zip(
                publication_columns,
                next(
                    row
                    for row in publication_rows
                    if row[0] == command.tenant_id
                    and row[1] == command.operation_kind
                    and row[2] == command.idempotency_key
                ),
                strict=True,
            )
        )
        if (
            publication["request_fingerprint"] != command.request_fingerprint
            or publication["commit_sequence"] != command.expected_head + 1
            or publication["record_ids"]
            != "\n".join(record.record_id for record in command.records)
        ):
            raise ValueError("checkpoint selected publication differs")
        physical = {
            row[1]: dict(zip(record_columns, row, strict=True))
            for row in record_rows
            if row[0] == command.tenant_id
        }
        for member in command.records:
            row = physical[member.record_id]
            encoded = row["canonical_bytes"]
            if (
                row["owner"] != member.owner
                or row["schema_id"] != member.schema_id
                or row["commit_sequence"] != command.expected_head + 1
                or not isinstance(encoded, dict)
                or set(encoded) != {"base64"}
                or encoded["base64"] != base64.b64encode(member.canonical_bytes).decode()
            ):
                raise ValueError("checkpoint selected record differs")
    except (KeyError, StopIteration, TypeError, ValueError) as error:
        raise ValueError("checkpoint lacks exact selected physical membership") from error


def completion_v2_terminal_run(
    command: PhysicalPublicationCommand,
) -> ExecutionRunRecord | ExecutionRunRecordV3:
    """Decode the sole native terminal Run in a selected H1 completion batch.

    The surrounding selected-issuance verifier establishes the batch's owner
    exchanges. This small projection keeps the history reader from treating a
    schema-compatible non-Run companion as the terminal state.
    """
    members = tuple(
        record
        for record in command.records
        if record.owner == OWNER
        and record.schema_id in (EXECUTION_RUN_SCHEMA, EXECUTION_RUN_V3_SCHEMA)
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
    if type(decoded.run) not in (ExecutionRunRecord, ExecutionRunRecordV3) or (
        decoded.run.state != "SUCCEEDED"
    ):
        raise ValueError("selected complete delivery Run is not a native succeeded terminal")
    return decoded.run


def _successor_run_pair(
    command: PhysicalPublicationCommand,
    expected_superseded: ExecutionRunRecord,
    expected_created: ExecutionRunRecord,
) -> tuple[ExecutionRunRecord, ExecutionRunRecord]:
    """Decode the exact ordered V2 Run pair in one selected successor batch."""
    members = tuple(
        record
        for record in command.records
        if record.owner == OWNER
        and record.schema_id in (EXECUTION_RUN_V2_SCHEMA, EXECUTION_RUN_V3_SCHEMA)
    )
    if len(members) != 2 or any(row.schema_id != EXECUTION_RUN_SCHEMA for row in members):
        raise ValueError("selected successor must contain exactly two native V2 Run rows")
    decoded = tuple(
        decode_execution_run_member(
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
        ).run
        for record in members
    )
    if decoded != (expected_superseded, expected_created):
        raise ValueError("selected successor Run pair is missing, reversed, or substituted")
    return cast("tuple[ExecutionRunRecord, ExecutionRunRecord]", decoded)


def _completion_v2_schema_dispatch(batch: CompleteDeliveryBatchV2) -> Literal["H1"]:
    """Route selected H1 V1/V2/V3/V4 applicability without granting authority."""
    from chiplog.composition.h1_completion_issuance import dispatch_h1_completion_issuance_schema

    if dispatch_h1_completion_issuance_schema(
        batch.authentication.applicability_schema,
        batch.authentication.applicability_bytes,
    ) in ("V1", "V2", "V3", "V4"):
        return "H1"
    raise AssertionError("H1 completion issuance schema dispatcher returned an unknown route")


def _selected_h1_completion(
    runtime: R14PlanningRuntime, decision: object
) -> tuple[
    PhysicalPublicationCommand,
    ExecutionRunRecord | ExecutionRunRecordV3,
    ExecutionRunRecord | ExecutionRunRecordV3,
]:
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
    exact_historical_sources: bool = False,
) -> tuple[
    LoopSnapshot,
    tuple[RetainedFanOutPreparation, ...],
    tuple[RetainedCancellationPreparation, ...],
    ExecutionHistorySnapshot,
    tuple[RetainedExecutionFanOutPreparation, ...],
    tuple[RetainedExecutionCancellationPreparation, ...],
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
            entries = []
            loop_journal_entries = runtime._loop_decisions().entries()
            selected_decision_envelopes = {}
            loop_decision_bytes_by_id = {}
            selected_loop_commands_by_id = {}
            selected = []
            for decision_id, _, raw in loop_journal_entries:
                loop_decision_bytes_by_id[str(decision_id)] = raw
                entry = _strict_json(raw)
                entries.append(entry)
                if entry.get("kind") == "DECIDED":
                    selected_decision_envelopes[entry["operation_id"]] = (decision_id, raw)
                    if str(entry.get("operation_kind", "")).startswith("agent_loop"):
                        command = runtime._publication(entry)
                        selected.append((command, entry))
                        selected_loop_commands_by_id[str(decision_id)] = command
            decisions = [entry for entry in entries if entry.get("kind") == "DECIDED"]
            identities = [entry["operation_id"] for entry in decisions]
            if len(identities) != len(set(identities)):
                raise ValueError("duplicate independently selected loop identity")
            authenticated_loop_decisions = tuple(
                (
                    decision_id,
                    predecessor,
                    raw,
                    entry,
                    selected_loop_commands_by_id.get(str(decision_id)),
                )
                for (decision_id, predecessor, raw), entry in zip(
                    loop_journal_entries, entries, strict=True
                )
            )
            from chiplog.composition.r14_native_recovery_historical import (
                _make_authenticated_loop_decision_cut,
            )

            authenticated_loop_decisions = _make_authenticated_loop_decision_cut(
                runtime, authenticated_loop_decisions
            )
            call_values = {}
            owner_snapshot = runtime._owner_decisions().snapshot()
            for decision in owner_snapshot.decisions:
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
                pending_source_publication = None
                deferred_open_pending_publication = None
                authenticated_source_prefix = None
                require_native_open_fold = False
                has_native_open_candidate = any(
                    command.operation_kind
                    in (
                        "agent_loop.accepted-open-accounting.v1",
                        "agent_loop.accepted-open-recovery.v1",
                    )
                    for command, _entry in selected
                )
                pending = (
                    runtime._pending()
                    if exact_historical_sources or (selected_only and has_native_open_candidate)
                    else ()
                )
                if exact_historical_sources or (
                    selected_only
                    and has_native_open_candidate
                    and len(pending) == 1
                    and pending[0].get("operation_kind") == "agent_loop.execution-suspension.v1"
                ):
                    anchor = runtime._commitment_journal.load(tenant)
                    if pending:
                        operation = pending[0].get("operation_kind") if len(pending) == 1 else None
                        if operation == "agent_loop.original-resolver-invocation.v1":
                            from chiplog.composition.r14_invocation_pending import (
                                authenticate_selected_invocation_prefix_held,
                            )

                            authenticated = authenticate_selected_invocation_prefix_held(
                                runtime, actual, anchor
                            )
                        elif operation == "agent_loop.original-call-resolution.v1":
                            from chiplog.composition.r14_original_call_resolution_source import (
                                authenticate_selected_resolution_prefix_held,
                            )

                            authenticated = authenticate_selected_resolution_prefix_held(
                                runtime, actual, anchor
                            )
                        else:
                            authenticated = False
                        from chiplog.composition.r14_historical_suffix import (
                            NATIVE_OPEN_PENDING_LIFECYCLE_OPERATIONS,
                        )

                        defer_open_pending = (
                            has_native_open_candidate
                            and operation in NATIVE_OPEN_PENDING_LIFECYCLE_OPERATIONS
                        )
                        if not authenticated and not defer_open_pending:
                            raise ValueError(
                                "historical source read has an unverified pending write"
                            )
                        if authenticated:
                            authenticated_source_prefix = (actual, anchor)
                        if defer_open_pending:
                            deferred_open_pending_publication = pending[0]
                            require_native_open_fold = True
                        pending_source_publication = pending[0]
                    elif actual != anchor:
                        if not has_native_open_candidate:
                            raise ValueError(
                                "historical source read differs from independent anchor"
                            )
                        require_native_open_fold = True
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
                for owner_decision in owner_snapshot.decisions:
                    owner_request = owner_decision.prepared.request
                    if not isinstance(
                        owner_request, (DeliveryWorkGenesisBatchV1, DeliveryWorkClaimBatchV1)
                    ):
                        continue
                    owner_command = runtime._owner_command(owner_decision)
                    state = inspect_publication(
                        connection, owner_command, owner_decision.tenant_commit_sequence
                    )
                    if state == "CONFLICT" or (state == "ABSENT" and not selected_only):
                        raise ValueError("selected delivery-work physical state differs")
                    if state == "COMPLETE":
                        expected_ids.update(
                            row.record_id for row in owner_command.records if row.owner == OWNER
                        )
                        expected_publications.add(
                            (owner_command.operation_kind, owner_command.idempotency_key)
                        )
                records: list[RunRecord] = []
                mixed_records: list[RunRecord | ExecutionRunRecord] = []
                latest: dict[str, RunRecord | ExecutionRunRecord] = {}
                successor_publications = {}
                preparations: list[RetainedFanOutPreparation] = []
                execution_preparations: list[RetainedExecutionFanOutPreparation] = []
                execution_cancellations: list[RetainedExecutionCancellationPreparation] = []
                cancellations: list[RetainedCancellationPreparation] = []
                acceptances: list[RetainedAcceptancePreparationV2] = []
                native_accounting_count = 0
                native_sealed_accounting_seals: set[str] = set()
                deferred_native_sealed_accounting: list[
                    tuple[
                        PhysicalPublicationCommand,
                        dict[str, object],
                        ExecutionRecoveryHistory,
                        tuple,
                    ]
                ] = []
                accepted_open_recoveries = []
                deferred_native_open_sources = []
                captured_heads: set[str] = set()
                native_prepares: dict[tuple[str, str], str] = {}
                verified_h1_prepares: dict[
                    tuple[bytes, str],
                    tuple[
                        H1OriginalWorkspaceIssuance,
                        H1VerifiedWorkspaceClosure,
                        NativePrepareScopeBindingV1 | None,
                    ],
                ] = {}
                original_sources = None
                original_resolution_evidence = None
                original_resolution_resulting = None
                current_semantic_reduction = None
                current_semantic_reduction_evidence = None
                current_semantic_reduction_entry = None
                resolver_policy_sources = None
                for command, entry in selected:
                    identity = command.idempotency_key
                    allow_pending_absent = (
                        deferred_open_pending_publication is not None
                        and deferred_open_pending_publication == entry
                        and runtime._publication(deferred_open_pending_publication) == command
                    )
                    if command.operation_kind not in (
                        "agent_loop",
                        FANOUT_OPERATION,
                        CANCELLATION_OPERATION,
                        EXECUTION_CANCELLATION_OPERATION,
                        "agent_loop.native-one-call-cancelled-accounting.v1",
                        "agent_loop.native-sealed-accounting.v3",
                        "agent_loop.execution-accounting-readiness.v1",
                        "agent_loop.execution-next-turn.v1",
                        "agent_loop.execution-suspension.v1",
                        "agent_loop.execution-resume.v1",
                        "agent_loop.execution-successor.v1",
                        "agent_loop.execution-successor-start.v1",
                        "agent_loop.accepted-open-accounting.v1",
                        "agent_loop.accepted-open-recovery.v1",
                        "agent_loop.accepted-direct-terminal.v1",
                        "agent_loop.original-call-resolution.v1",
                        "agent_loop.loop-semantic-reduction.v1",
                        "agent_loop.original-resolver-authority-policy.v1",
                        "agent_loop.original-resolver-invocation.v1",
                        "agent_loop.native-one-call-recovery-profile.v1",
                        "agent_loop.native-one-call-recovery-profile.v2",
                        "agent_loop.native-one-call-recovery-profile.v3",
                        "agent_loop.native-one-call-recovery-profile.v4",
                        "agent_loop.native-one-call-recovery-profile.v5",
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
                    if (
                        command.operation_kind == "agent_loop.original-call-resolution.v1"
                        or "original_call_resolution_evidence" in entry
                    ):
                        from chiplog.composition.r14_original_call_resolution_publication import (
                            OPERATION,
                            validate_selected_command,
                        )

                        if command.operation_kind != OPERATION:
                            raise ValueError("original resolution has a substituted operation")
                        evidence = validate_selected_command(runtime, entry, command)
                        cut = evidence.request.cut
                        if original_sources is None or resolver_policy_sources is None:
                            raise ValueError("original resolution precedes its selected sources")
                        if (cut.current_obligation, cut.call_terminal) != original_sources or (
                            cut.resolver_policy,
                            cut.reducer_policy,
                        ) != resolver_policy_sources:
                            raise ValueError(
                                "original resolution source references differ from selected history"
                            )
                        original_resolution_evidence = evidence
                        original_resolution_resulting = entry.get("resulting")
                        expected_ids.update(
                            row.record_id for row in command.records if row.owner == OWNER
                        )
                        expected_publications.add((command.operation_kind, identity))
                        continue
                    if (
                        command.operation_kind == "agent_loop.loop-semantic-reduction.v1"
                        or "loop_semantic_reduction_evidence" in entry
                    ):
                        from chiplog.capabilities.agent_loop.recovery_contracts import Absent
                        from chiplog.composition import (
                            r14_loop_semantic_reduction_publication as reduction_publication,
                        )
                        from chiplog.composition import (
                            r14_original_call_resolution_publication as resolution_publication,
                        )

                        if command.operation_kind != reduction_publication.OPERATION:
                            raise ValueError("semantic reduction has a substituted operation")
                        evidence = reduction_publication.validate_selected_command(
                            runtime, entry, command
                        )
                        if (
                            original_resolution_evidence is None
                            or not isinstance(original_resolution_resulting, str)
                            or len(evidence.request.complete_ordered_evidence)
                            < len(
                                original_resolution_evidence.request.cut.complete_ordered_evidence
                            )
                            or evidence.request.complete_ordered_evidence[
                                : len(
                                    original_resolution_evidence.request.cut.complete_ordered_evidence
                                )
                            ]
                            != original_resolution_evidence.request.cut.complete_ordered_evidence
                        ):
                            raise ValueError(
                                "semantic reduction does not extend selected original resolution"
                            )
                        from chiplog.composition.r14_original_call_resolution_source import (
                            _authenticate_append_evidence_suffix,
                        )

                        if current_semantic_reduction_evidence is None:
                            base_sequence = (
                                original_resolution_evidence.request.cut.tenant_commit_sequence + 1
                            )
                            base_commitment = original_resolution_resulting
                            base_witness = original_resolution_evidence.request.cut.selected_witness
                        else:
                            previous_reduction = current_semantic_reduction_evidence
                            previous_reduction_batch = runtime._publication(
                                current_semantic_reduction_entry
                            )
                            base_sequence = previous_reduction_batch.expected_head + 1
                            base_commitment = str(current_semantic_reduction_entry.get("resulting"))
                            base_witness = previous_reduction.request.complete_anchor_sources[-1]
                        _authenticate_append_evidence_suffix(
                            runtime,
                            base_sequence=base_sequence,
                            base_commitment=base_commitment,
                            end_sequence=evidence.request.tenant_commit_sequence,
                            end_commitment=evidence.request.materialization_commitment,
                            base_witness=base_witness,
                            owner_snapshot=owner_snapshot,
                            loop_entries=loop_journal_entries,
                        )
                        selected_resolution = resolution_publication._selected_entries(runtime)
                        if len(selected_resolution) != 1:
                            raise ValueError("semantic reduction has no unique resolver batch")
                        retained_resolution = resolution_publication.validate_selected_command(
                            runtime, selected_resolution[0][2], selected_resolution[0][3]
                        )
                        expected_anchors = reduction_publication._selected_anchor_sources(
                            runtime, selected_resolution[0], retained_resolution
                        )
                        if evidence.request.complete_anchor_sources[:-1] != expected_anchors[:-1]:
                            raise ValueError("semantic reduction resolver anchors differ")
                        reduction_publication.validate_selected_witness_at_cut(
                            runtime,
                            evidence.request.complete_anchor_sources[-1],
                            command.expected_head,
                            snapshot=owner_snapshot,
                        )
                        expected_reduction = (
                            Absent()
                            if current_semantic_reduction is None
                            else current_semantic_reduction
                        )
                        if evidence.request.expected != expected_reduction:
                            raise ValueError("semantic reduction selected predecessor differs")
                        selected_decision_id, selected_decision_raw = selected_decision_envelopes[
                            identity
                        ]
                        _, current_semantic_reduction = reduction_publication._selected_value(
                            runtime, (selected_decision_id, selected_decision_raw, entry, command)
                        )
                        current_semantic_reduction_evidence = evidence
                        current_semantic_reduction_entry = entry
                        expected_ids.update(
                            row.record_id for row in command.records if row.owner == OWNER
                        )
                        expected_publications.add((command.operation_kind, identity))
                        continue
                    if (
                        command.operation_kind == "agent_loop.execution-successor.v1"
                        or "execution_successor_evidence" in entry
                    ):
                        from chiplog.composition import r14_execution_successor as successor

                        if command.operation_kind != successor.OPERATION:
                            raise ValueError("execution successor has a substituted operation")
                        retained_entry = successor._entry_for(runtime, identity)
                        if retained_entry is None:
                            raise ValueError("selected execution successor entry is absent")
                        retained = successor._retained(retained_entry[2])
                        request = retained.request
                        result = retained.result
                        predecessor = request.run
                        superseded = result.superseded_run
                        created = result.successor_run
                        prior_lineage = tuple(
                            row
                            for row in mixed_records
                            if isinstance(row, ExecutionRunRecord)
                            and row.run_id == predecessor.run_id
                        )
                        if (
                            replace(successor._command(retained), expected_preimage_commitment=None)
                            != command
                            or request.cut.tenant_id != tenant
                            or request.cut.tenant_commit_sequence != command.expected_head
                            or request.cut.materialization_commitment != entry.get("predecessor")
                            or request.cut.complete_ordered_run_lineage != prior_lineage
                            or predecessor != latest.get(predecessor.run_id)
                            or predecessor.state != "SUSPENDED"
                            or superseded.state != "SUPERSEDED"
                            or superseded.run_id != predecessor.run_id
                            or superseded.predecessor != predecessor.head
                            or created.state != "CREATED"
                            or created.run_id == predecessor.run_id
                            or created.predecessor != predecessor.head
                            or created.tenant != predecessor.tenant
                            or created.principal != predecessor.principal
                        ):
                            raise ValueError(
                                "selected execution successor differs from exact Run history"
                            )
                        _successor_run_pair(command, superseded, created)
                        from chiplog.composition import r14_execution_suspension as suspension

                        selected_suspension = request.original_suspension
                        suspension_entry = suspension._entry_for(
                            runtime, selected_suspension.selected_decision.subject_id
                        )
                        if suspension_entry is None:
                            raise ValueError("successor selected suspension evidence is absent")
                        suspension_evidence = suspension._retained(suspension_entry[2])
                        _, authenticated_suspension = suspension._validate_selected(
                            runtime,
                            suspension_entry[2],
                            suspension._command(suspension_evidence),
                            check_history=False,
                        )
                        if authenticated_suspension != selected_suspension:
                            raise ValueError(
                                "successor does not reference the selected suspension evidence"
                            )
                        successor._selected_matches(
                            runtime,
                            retained_entry[2],
                            command,
                            retained,
                            allow_pending_absent=allow_pending_absent,
                        )
                        expected_ids.update(
                            row.record_id for row in command.records if row.owner == OWNER
                        )
                        expected_publications.add((command.operation_kind, identity))
                        latest[predecessor.run_id] = superseded
                        latest[created.run_id] = created
                        successor_publications[created.run_id] = (retained, entry.get("resulting"))
                        mixed_records.extend((superseded, created))
                        continue
                    if (
                        command.operation_kind == "agent_loop.execution-successor-start.v1"
                        or "execution_successor_start_evidence" in entry
                    ):
                        from chiplog.composition import r14_execution_successor as successor

                        if command.operation_kind != successor.START_OPERATION:
                            raise ValueError(
                                "execution successor start has a substituted operation"
                            )
                        retained_entry = successor._entry_for(
                            runtime, identity, successor.START_OPERATION
                        )
                        if retained_entry is None:
                            raise ValueError("selected execution successor start entry is absent")
                        retained = successor._retained(
                            retained_entry[2],
                            successor.START_EVIDENCE_FIELD,
                            successor.RetainedExecutionSuccessorStart,
                        )
                        request = retained.request
                        created = request.created_run
                        active = retained.result.run
                        selected_source = successor_publications.get(created.run_id)
                        if (
                            replace(
                                successor._start_command(retained),
                                expected_preimage_commitment=None,
                            )
                            != command
                            or selected_source is None
                            or retained.source != selected_source[0]
                            or retained.source_resulting_commitment != selected_source[1]
                            or created != latest.get(created.run_id)
                            or created.state != "CREATED"
                            or active.state != "ACTIVE"
                            or active.run_id != created.run_id
                            or active.predecessor != created.head
                            or active.tenant != created.tenant
                            or active.principal != created.principal
                            or active.prompt != created.prompt
                            or active.policy != created.policy
                            or active.origin != created.origin
                            or active.original_obligations != created.original_obligations
                            or active.no_retry_references != created.no_retry_references
                            or active.suspension_baseline is not None
                        ):
                            raise ValueError(
                                "selected execution successor start differs from exact CREATED Run"
                            )
                        successor._selected_matches(
                            runtime,
                            retained_entry[2],
                            command,
                            retained,
                            start=True,
                            source_validated=True,
                        )
                        expected_ids.update(
                            row.record_id for row in command.records if row.owner == OWNER
                        )
                        expected_publications.add((command.operation_kind, identity))
                        latest[active.run_id] = active
                        mixed_records.append(active)
                        continue
                    if (
                        command.operation_kind == "agent_loop.execution-resume.v1"
                        or "execution_resume_evidence" in entry
                    ):
                        from chiplog.composition.r14_execution_resume import (
                            OPERATION as RESUME_OPERATION,
                        )
                        from chiplog.composition.r14_execution_resume import (
                            _command as resume_command,
                        )
                        from chiplog.composition.r14_execution_resume import (
                            _entry_for as resume_entry_for,
                        )
                        from chiplog.composition.r14_execution_resume import (
                            _retained as retained_resume,
                        )
                        from chiplog.composition.r14_execution_resume import (
                            _run_ref as resume_run_ref,
                        )
                        from chiplog.composition.r14_execution_resume import (
                            _validate_selected as validate_resume,
                        )

                        if command.operation_kind != RESUME_OPERATION:
                            raise ValueError("execution resume has a substituted operation")
                        retained_entry = resume_entry_for(runtime, identity)
                        if retained_entry is None:
                            raise ValueError("selected execution resume entry is absent")
                        retained = retained_resume(retained_entry[2])
                        request = retained.request
                        before = request.run
                        proposed = retained.result.run
                        previous = latest.get(before.run_id)
                        prior_lineage = tuple(
                            row
                            for row in mixed_records
                            if isinstance(row, ExecutionRunRecord) and row.run_id == before.run_id
                        )
                        if (
                            replace(resume_command(retained), expected_preimage_commitment=None)
                            != command
                            or request.cut.tenant_id != tenant
                            or request.cut.tenant_commit_sequence != command.expected_head
                            or request.cut.materialization_commitment != entry.get("predecessor")
                            or request.cut.current_run != resume_run_ref(before)
                            or request.cut.complete_ordered_run_lineage != prior_lineage
                            or before != previous
                            or before.state != "SUSPENDED"
                            or proposed.state != "ACTIVE"
                            or proposed.run_id != before.run_id
                            or proposed.predecessor != before.head
                            or proposed.turns != before.turns
                            or proposed.original_obligations != before.original_obligations
                            or proposed.no_retry_references != before.no_retry_references
                            or proposed.suspension_baseline != before.suspension_baseline
                            or proposed.root_binding != "NOT_APPLICABLE"
                        ):
                            raise ValueError(
                                "selected execution resume differs from exact Run history"
                            )
                        validate_resume(runtime, retained_entry[2], command, check_history=False)
                        expected_ids.update(
                            row.record_id for row in command.records if row.owner == OWNER
                        )
                        expected_publications.add((command.operation_kind, identity))
                        latest[proposed.run_id] = proposed
                        mixed_records.append(proposed)
                        continue
                    if (
                        command.operation_kind == "agent_loop.execution-suspension.v1"
                        or "execution_suspension_evidence" in entry
                    ):
                        from chiplog.composition.r14_execution_suspension import (
                            OPERATION as SUSPENSION_OPERATION,
                        )
                        from chiplog.composition.r14_execution_suspension import (
                            _command as suspension_command,
                        )
                        from chiplog.composition.r14_execution_suspension import (
                            _entry_for as suspension_entry_for,
                        )
                        from chiplog.composition.r14_execution_suspension import (
                            _retained as retained_suspension,
                        )
                        from chiplog.composition.r14_execution_suspension import (
                            _run_ref as suspension_run_ref,
                        )
                        from chiplog.composition.r14_execution_suspension import (
                            _validate_selected as validate_suspension,
                        )

                        if command.operation_kind != SUSPENSION_OPERATION:
                            raise ValueError("execution suspension has a substituted operation")
                        retained_entry = suspension_entry_for(runtime, identity)
                        if retained_entry is None:
                            raise ValueError("selected execution suspension entry is absent")
                        retained = retained_suspension(retained_entry[2])
                        evidence = retained.request
                        proposed = retained.result.run
                        previous = latest.get(proposed.run_id)
                        prior_lineage = tuple(
                            row
                            for row in mixed_records
                            if isinstance(row, ExecutionRunRecord) and row.run_id == proposed.run_id
                        )
                        previous_run_ref = (
                            suspension_run_ref(previous)
                            if isinstance(previous, ExecutionRunRecord)
                            else None
                        )
                        suspension_checks = {
                            "command": replace(
                                suspension_command(retained),
                                expected_preimage_commitment=None,
                            )
                            == command,
                            "tenant": evidence.cut.tenant_id == tenant,
                            "sequence": evidence.cut.tenant_commit_sequence
                            == command.expected_head,
                            "predecessor": evidence.cut.materialization_commitment
                            == entry.get("predecessor"),
                            "run": evidence.run == previous,
                            "previous_type": isinstance(previous, ExecutionRunRecord),
                            "current_ref": evidence.cut.current_run == previous_run_ref,
                            "lineage": evidence.cut.complete_ordered_run_lineage == prior_lineage,
                            "suspended": proposed.state == "SUSPENDED",
                            "run_predecessor": proposed.predecessor == previous.head,
                            "root": proposed.root_binding == "NOT_APPLICABLE",
                            "lineage_root": all(
                                row.root_binding == "NOT_APPLICABLE" for row in prior_lineage
                            ),
                        }
                        if not all(suspension_checks.values()):
                            failed = ",".join(
                                name for name, passed in suspension_checks.items() if not passed
                            )
                            raise ValueError(
                                "selected execution suspension source lineage differs: " + failed
                            )
                        validate_suspension(
                            runtime,
                            retained_entry[2],
                            command,
                            check_history=False,
                            allow_pending_absent=allow_pending_absent,
                        )
                        expected_ids.update(
                            row.record_id for row in command.records if row.owner == OWNER
                        )
                        expected_publications.add((command.operation_kind, identity))
                        latest[proposed.run_id] = proposed
                        mixed_records.append(proposed)
                        continue
                    if command.operation_kind == "agent_loop.original-resolver-authority-policy.v1":
                        from chiplog.capabilities.agent_loop import execution_recovery_observations
                        from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
                            CallSubjectHead,
                        )
                        from chiplog.capabilities.agent_loop.original_recovery_contracts import (
                            original_effect_evidence_reducer_policy_v1,
                        )
                        from chiplog.capabilities.agent_loop.recovery_contracts import Present
                        from chiplog.composition.r14_original_resolver_policy_publication import (
                            EVIDENCE_FIELD,
                            REDUCER_SCHEMA,
                        )
                        from chiplog.composition.r14_original_resolver_policy_publication import (
                            SCHEMA as RESOLVER_POLICY_SCHEMA,
                        )
                        from chiplog.composition.r14_original_resolver_policy_publication import (
                            validate_selected as validate_policy_selected,
                        )

                        evidence = validate_policy_selected(runtime, entry, command)
                        if len(command.records) != 2 or tuple(
                            r.schema_id for r in command.records
                        ) != (RESOLVER_POLICY_SCHEMA, REDUCER_SCHEMA):
                            raise ValueError("selected resolver policy record schemas differ")
                        decision_id, decision_bytes = selected_decision_envelopes[identity]
                        selected_digest = hashlib.sha256(
                            decision_bytes.encode()
                            if isinstance(decision_bytes, str)
                            else decision_bytes
                        ).hexdigest()
                        sources = []
                        for record in command.records:
                            digest = hashlib.sha256(record.canonical_bytes).hexdigest()
                            subject_id = (
                                evidence.request.policy.policy_id
                                if record.schema_id == RESOLVER_POLICY_SCHEMA
                                else original_effect_evidence_reducer_policy_v1(
                                    runtime._tenant_id
                                ).policy_id
                            )
                            sources.append(
                                execution_recovery_observations.RecoverySourceRecord(
                                    owner=record.owner,
                                    subject=CallSubjectHead(
                                        subject_id=subject_id,
                                        revision=Present(
                                            head="record:" + digest, fingerprint=digest
                                        ),
                                    ),
                                    schema_id=record.schema_id,
                                    canonical_record_bytes=record.canonical_bytes,
                                    selected_decision=CallSubjectHead(
                                        subject_id=decision_id,
                                        revision=Present(
                                            head=decision_id, fingerprint=selected_digest
                                        ),
                                    ),
                                    physical_record=CallSubjectHead(
                                        subject_id=record.record_id,
                                        revision=Present(
                                            head="record:" + digest, fingerprint=digest
                                        ),
                                    ),
                                )
                            )
                        resolver_policy_sources = tuple(sources)
                        if EVIDENCE_FIELD not in entry:
                            raise ValueError("selected resolver policy lacks retained evidence")
                        expected_ids.update(
                            row.record_id for row in command.records if row.owner == OWNER
                        )
                        expected_publications.add((command.operation_kind, identity))
                        continue
                    if command.operation_kind == "agent_loop.original-resolver-invocation.v1":
                        from chiplog.composition import (
                            r14_original_resolver_invocation_publication as inv_pub,
                        )

                        raw = entry.get(inv_pub.EVIDENCE_FIELD)
                        if (
                            not isinstance(raw, str)
                            or original_sources is None
                            or resolver_policy_sources is None
                        ):
                            raise ValueError(
                                "selected resolver invocation lacks historical sources"
                            )
                        evidence_type = inv_pub.RetainedOriginalResolverInvocationPublication
                        evidence = evidence_type.model_validate_json(raw)
                        invocation = evidence.prepared.invocation
                        if (
                            evidence.canonical_bytes().decode() != raw
                            or inv_pub.command(evidence) != command
                            or tuple(record.schema_id for record in command.records)
                            != (inv_pub.SCHEMA,)
                            or (
                                invocation.accepted_opening_source,
                                invocation.accepted_terminal_source,
                            )
                            != original_sources
                            or (invocation.resolver_policy_source, invocation.reducer_policy_source)
                            != resolver_policy_sources
                            or entry.get("operation_id") != identity
                        ):
                            raise ValueError(
                                "selected resolver invocation differs from historical source cut"
                            )
                        inv_pub._validate_invocation_actor(runtime, evidence)
                        expected_ids.update(
                            row.record_id for row in command.records if row.owner == OWNER
                        )
                        expected_publications.add((command.operation_kind, identity))
                        continue
                    if (
                        command.operation_kind
                        in (
                            "agent_loop.native-one-call-recovery-profile.v1",
                            "agent_loop.native-one-call-recovery-profile.v2",
                            "agent_loop.native-one-call-recovery-profile.v3",
                            "agent_loop.native-one-call-recovery-profile.v4",
                            "agent_loop.native-one-call-recovery-profile.v5",
                        )
                        or "native_recovery_profile_evidence" in entry
                    ):
                        from chiplog.composition.r14_recovery_profile_publication import (
                            validate_profile_selected,
                        )

                        validate_profile_selected(runtime, entry, command)
                        expected_ids.update(
                            row.record_id for row in command.records if row.owner == OWNER
                        )
                        expected_publications.add((command.operation_kind, identity))
                        continue
                    if (
                        command.operation_kind == "agent_loop.execution-accounting-readiness.v1"
                        or "execution_recovery_evidence" in entry
                    ):
                        from chiplog.composition.r14_recovery_publication import validate_selected

                        validate_selected(
                            runtime,
                            entry,
                            command,
                            authenticated_loop_decisions=authenticated_loop_decisions,
                            verified_h1_prepares=verified_h1_prepares,
                        )
                        expected_ids.update(
                            row.record_id for row in command.records if row.owner == OWNER
                        )
                        expected_publications.add((command.operation_kind, identity))
                        continue
                    if (
                        command.operation_kind == "agent_loop.execution-next-turn.v1"
                        or "execution_next_turn_evidence" in entry
                    ):
                        from chiplog.composition.r14_next_turn_records import (
                            NEXT_TURN_PUBLICATION,
                            validate_selected_next_turn,
                        )

                        if command.operation_kind != NEXT_TURN_PUBLICATION:
                            raise ValueError("next Turn retained under substituted operation")
                        evidence = validate_selected_next_turn(
                            runtime,
                            entry,
                            command,
                            authenticated_loop_decisions=authenticated_loop_decisions,
                            verified_h1_prepares=verified_h1_prepares,
                        )
                        before, after = evidence.request.run, evidence.result.run
                        if (
                            not isinstance(before, ExecutionRunRecord)
                            or not isinstance(after, ExecutionRunRecord)
                            or latest.get(before.run_id) != before
                            or after.predecessor != before.head
                            or evidence.request.cut.tenant_commit_sequence != command.expected_head
                            or after.event != "TurnStarted"
                        ):
                            raise ValueError("selected next Turn differs from exact Run history")
                        expected_ids.update(
                            row.record_id for row in command.records if row.owner == OWNER
                        )
                        expected_publications.add((command.operation_kind, identity))
                        latest[after.run_id] = after
                        mixed_records.append(after)
                        continue
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
                                tuple(execution_cancellations),
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
                    if (
                        command.operation_kind == "agent_loop.native-sealed-accounting.v3"
                        or "native_sealed_accounting_v3_evidence" in entry
                        or "native_sealed_accounting_v3_envelope" in entry
                    ):
                        from chiplog.composition.r14_native_sealed_accounting_v3_history import (
                            decode_selected_native_sealed_accounting_v3,
                            retained_selected_native_sealed_accounting_v3,
                        )

                        evidence = retained_selected_native_sealed_accounting_v3(entry)
                        seal_fingerprint = evidence.request.selected_response.revision.fingerprint
                        if seal_fingerprint in native_sealed_accounting_seals:
                            raise ValueError(
                                "duplicate V3 native accounting for selected response seal"
                            )
                        snapshot = ExecutionHistorySnapshot(
                            tenant_head=command.expected_head,
                            records=tuple(mixed_records),
                        )
                        current_inventory = execution_inventory(
                            tenant,
                            snapshot,
                            tuple(preparations),
                            tuple(cancellations),
                            tuple(execution_preparations),
                            tuple(execution_cancellations),
                            tuple(acceptances),
                        )
                        authenticated_history: ExecutionRecoveryHistory = (
                            snapshot,
                            current_inventory,
                            tuple(preparations),
                            tuple(execution_preparations),
                            tuple(execution_cancellations),
                            tuple(acceptances),
                        )
                        deferred_native_sealed_accounting.append(
                            (
                                command,
                                entry,
                                authenticated_history,
                                tuple(accepted_open_recoveries),
                            )
                        )
                        expected_ids.update(
                            row.record_id for row in command.records if row.owner == OWNER
                        )
                        expected_publications.add((command.operation_kind, identity))
                        native_sealed_accounting_seals.add(seal_fingerprint)
                        continue
                    if (
                        command.operation_kind
                        == "agent_loop.native-one-call-cancelled-accounting.v1"
                        or "native_one_call_accounting_evidence" in entry
                        or "native_one_call_accounting_envelope" in entry
                    ):
                        if native_accounting_count:
                            raise ValueError("duplicate bounded native accounting selection")
                        from chiplog.composition.r14_native_accounting_history import (
                            decode_selected_native_accounting,
                        )

                        decode_selected_native_accounting(
                            runtime,
                            connection,
                            command,
                            entry,
                            execution_preparations,
                            execution_cancellations,
                            mixed_records,
                            verified_h1_prepares=verified_h1_prepares,
                        )
                        expected_ids.update(
                            row.record_id for row in command.records if row.owner == OWNER
                        )
                        expected_publications.add((command.operation_kind, identity))
                        native_accounting_count += 1
                        continue
                    if command.operation_kind == COMPLETE_ACCEPTANCE_OPERATION:
                        terminal = entry.get("h1_completion_terminal")
                        predecessor = entry.get("h1_completion_predecessor")
                        if type(terminal) not in (ExecutionRunRecord, ExecutionRunRecordV3) or type(
                            predecessor
                        ) not in (ExecutionRunRecord, ExecutionRunRecordV3):
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
                        command.operation_kind == "agent_loop.accepted-open-recovery.v1"
                        or "accepted_open_recovery_evidence" in entry
                        or any(
                            item.schema_id
                            == "chiplog.execution.accepted-open-terminal-disposition.v1"
                            for item in command.records
                        )
                    ):
                        from chiplog.composition.r14_accepted_open_accounting_records import (
                            ACCEPTED_OPEN_RECOVERY_OPERATION,
                            accepted_open_recovery_command,
                            retained_accepted_open_recovery,
                        )

                        evidence = retained_accepted_open_recovery(entry)
                        expected = accepted_open_recovery_command(evidence)
                        if (
                            command.operation_kind != ACCEPTED_OPEN_RECOVERY_OPERATION
                            or entry.get("operation_kind") != ACCEPTED_OPEN_RECOVERY_OPERATION
                            or entry.get("operation_id") != command.idempotency_key
                            or command != expected
                            or len(command.records) != 3
                            or command.records[0].canonical_bytes
                            != evidence.result.accounting.canonical_bytes()
                            or command.records[1].canonical_bytes
                            != evidence.result.opening.canonical_bytes()
                            or command.records[2].canonical_bytes
                            != evidence.result.terminal.canonical_bytes()
                            or entry.get("fingerprint") != command.request_fingerprint
                            or entry.get("expected_head") != command.expected_head
                        ):
                            raise ValueError("selected accepted/open recovery differs")
                        from chiplog.composition.r14_historical_suffix import (
                            validate_native_open_selected_source_stamp_held,
                        )

                        validate_native_open_selected_source_stamp_held(
                            runtime,
                            connection,
                            command=command,
                            entry=entry,
                            cut=evidence.request.cut,
                        )
                        deferred_native_open_sources.append(
                            (
                                command,
                                entry,
                                evidence.request.cut,
                                ExecutionHistorySnapshot(
                                    tenant_head=command.expected_head, records=tuple(mixed_records)
                                ),
                                tuple(execution_preparations),
                                tuple(acceptances),
                            )
                        )
                        from chiplog.composition.r14_original_obligation_sources import (
                            accepted_open_recovery_sources,
                        )

                        selected_decision_id, raw_decision = selected_decision_envelopes[identity]
                        original_sources = accepted_open_recovery_sources(
                            evidence,
                            selected_decision_id=selected_decision_id,
                            selected_decision_bytes=raw_decision.encode()
                            if isinstance(raw_decision, str)
                            else raw_decision,
                        )
                        accepted_open_recoveries.append(evidence)
                        expected_ids.update(record.record_id for record in command.records)
                        expected_publications.add((command.operation_kind, identity))
                        continue
                    if (
                        command.operation_kind == "agent_loop.accepted-direct-terminal.v1"
                        or "accepted_direct_terminal_evidence" in entry
                        or any(
                            item.schema_id.startswith("chiplog.execution.accepted-direct-terminal-")
                            for item in command.records
                        )
                    ):
                        from chiplog.composition.r14_accepted_direct_terminal_records import (
                            ACCEPTED_DIRECT_TERMINAL_ACCOUNTING_SCHEMA,
                            ACCEPTED_DIRECT_TERMINAL_OPERATION,
                            ACCEPTED_DIRECT_TERMINAL_RESULT_SCHEMA,
                            ACCEPTED_DIRECT_TERMINAL_SCHEMA,
                            accepted_direct_terminal_command,
                            retained_accepted_direct_terminal,
                        )

                        evidence = retained_accepted_direct_terminal(entry)
                        expected = accepted_direct_terminal_command(evidence)
                        if (
                            command.operation_kind != ACCEPTED_DIRECT_TERMINAL_OPERATION
                            or entry.get("operation_kind") != ACCEPTED_DIRECT_TERMINAL_OPERATION
                            or entry.get("operation_id") != identity
                            or identity != command.idempotency_key
                            or command != expected
                            or entry.get("predecessor")
                            != evidence.request.cut.materialization_commitment
                            or entry.get("fingerprint") != command.request_fingerprint
                            or entry.get("expected_head") != command.expected_head
                            or tuple(record.schema_id for record in command.records)
                            != (
                                ACCEPTED_DIRECT_TERMINAL_ACCOUNTING_SCHEMA,
                                ACCEPTED_DIRECT_TERMINAL_SCHEMA,
                                ACCEPTED_DIRECT_TERMINAL_RESULT_SCHEMA,
                            )
                            or tuple(record.canonical_bytes for record in command.records)
                            != (
                                evidence.result.accounting.canonical_bytes(),
                                evidence.result.terminal.canonical_bytes(),
                                evidence.result.result.canonical_bytes(),
                            )
                        ):
                            raise ValueError("selected accepted/direct terminal differs")
                        expected_ids.update(
                            record.record_id for record in command.records if record.owner == OWNER
                        )
                        expected_publications.add((command.operation_kind, identity))
                        continue
                    if (
                        command.operation_kind == "agent_loop.accepted-open-accounting.v1"
                        or "accepted_open_accounting_evidence" in entry
                        or any(
                            item.schema_id == "chiplog.execution.accepted-open-accounting.v1"
                            for item in command.records
                        )
                    ):
                        from chiplog.composition.r14_accepted_open_accounting_records import (
                            ACCEPTED_OPEN_ACCOUNTING_OPERATION,
                            ACCEPTED_OPEN_ACCOUNTING_SCHEMA,
                            accepted_open_accounting_command,
                            retained_accepted_open_accounting,
                        )

                        evidence = retained_accepted_open_accounting(entry)
                        expected = accepted_open_accounting_command(evidence)
                        if (
                            command.operation_kind != ACCEPTED_OPEN_ACCOUNTING_OPERATION
                            or entry.get("operation_kind") != ACCEPTED_OPEN_ACCOUNTING_OPERATION
                            or entry.get("operation_id") != command.idempotency_key
                            or command != expected
                            or len(command.records) != 1
                            or command.records[0].schema_id != ACCEPTED_OPEN_ACCOUNTING_SCHEMA
                            or command.records[0].canonical_bytes
                            != evidence.result.accounting.canonical_bytes()
                            or entry.get("fingerprint") != command.request_fingerprint
                            or entry.get("expected_head") != command.expected_head
                        ):
                            raise ValueError("selected accepted/open accounting differs")
                        from chiplog.composition.r14_historical_suffix import (
                            validate_native_open_selected_source_stamp_held,
                        )

                        validate_native_open_selected_source_stamp_held(
                            runtime,
                            connection,
                            command=command,
                            entry=entry,
                            cut=evidence.request.cut,
                        )
                        deferred_native_open_sources.append(
                            (
                                command,
                                entry,
                                evidence.request.cut,
                                ExecutionHistorySnapshot(
                                    tenant_head=command.expected_head, records=tuple(mixed_records)
                                ),
                                tuple(execution_preparations),
                                tuple(acceptances),
                            )
                        )
                        expected_ids.add(command.records[0].record_id)
                        expected_publications.add((command.operation_kind, identity))
                        continue
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
                        if not isinstance(raw_complete, str):
                            raise ValueError("execution complete seal retained bytes are absent")
                        complete_kind = _strict_json(raw_complete, canonical=False).get("kind")
                        if complete_kind == "R14_SELECTED_EXECUTION_COMPLETE_SEAL_V1":
                            if "h1_historical_checkpoint" in entry:
                                raise ValueError("V1 complete seal cannot carry an H1 checkpoint")
                            complete: (
                                RetainedExecutionCompleteSeal
                                | RetainedExecutionCompleteSealV2
                                | RetainedExecutionCompleteSealV3
                            ) = RetainedExecutionCompleteSeal.model_validate_json(raw_complete)
                        elif complete_kind == "R14_SELECTED_EXECUTION_COMPLETE_SEAL_V2":
                            complete = RetainedExecutionCompleteSealV2.model_validate_json(
                                raw_complete
                            )
                            if "h1_historical_checkpoint" in entry:
                                raise ValueError("V2 complete seal cannot carry an H1 checkpoint")
                            if H1_V2_AUTHORITY_CUT_FIELD in entry:
                                checkpoint_rows = H1VerifiedSnapshotRows.from_verified(
                                    resolve_h1_v2_authority_cut(
                                        entry,
                                        cast(
                                            AuthorityCheckpointStore,
                                            runtime._h1_checkpoint_store(),
                                        ),
                                        runtime._database_identity,
                                        command,
                                    )
                                )
                                if checkpoint_rows.tenant_head(tenant) != command.expected_head + 1:
                                    raise ValueError("V2 authority-cut tenant head differs")
                                checkpoint_rows.require_complete(command)
                        elif complete_kind == "R14_SELECTED_EXECUTION_COMPLETE_SEAL_V3":
                            complete = RetainedExecutionCompleteSealV3.model_validate_json(
                                raw_complete
                            )
                            checkpoint_rows = H1VerifiedSnapshotRows.from_verified(
                                resolve_h1_checkpoint(
                                    entry,
                                    cast(AuthorityCheckpointStore, runtime._h1_checkpoint_store()),
                                    runtime._database_identity,
                                    command,
                                )
                            )
                            if checkpoint_rows.tenant_head(tenant) != command.expected_head + 1:
                                raise ValueError("V3 checkpoint tenant head differs")
                            checkpoint_rows.require_complete(command)
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
                                tuple(execution_cancellations),
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
                                tuple(execution_cancellations),
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
                        elif retained_kind in (
                            "R14_SELECTED_EXECUTION_TRANSITION_V3",
                            "R14_SELECTED_EXECUTION_TRANSITION_V4",
                        ):
                            retained_type = (
                                RetainedExecutionTransitionV4
                                if retained_kind.endswith("V4")
                                else RetainedExecutionTransitionV3
                            )
                            retained_transition = retained_type.model_validate_json(retained_raw)
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
                            issuance_journal = workspace_port.open_h1_workspace_issuance()
                            original = issuance_journal.load(retained_transition.workspace_issuance)
                            verified_workspace = _verify_loaded_h1_original_workspace(
                                retained_transition.workspace_issuance,
                                workspace.model_dump_json().encode(),
                                workspace.content.encode(),
                                original,
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
                        if isinstance(retained_transition, RetainedExecutionTransitionV4):
                            from .r14_native_scope_binding import verify_native_prepare_scope

                            if not isinstance(retained_transition.request, PrepareExecutionRequest):
                                raise ValueError("V4 retained transition is not Prepare")
                            verify_native_prepare_scope(
                                runtime,
                                retained_transition.request,
                                retained_transition.native_scope_binding,
                                historical=True,
                            )
                            native_prepares[
                                (
                                    retained_transition.request.run.run_id,
                                    retained_transition.request.manifest.turn_id,
                                )
                            ] = retained_transition.digest()
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
                        if isinstance(
                            retained_transition,
                            (RetainedExecutionTransitionV3, RetainedExecutionTransitionV4),
                        ):
                            verified_h1_prepares[
                                (
                                    retained_raw.encode(),
                                    retained_transition.workspace_issuance.model_dump_json(),
                                )
                            ] = (
                                original,
                                verified_workspace,
                                retained_transition.native_scope_binding
                                if isinstance(retained_transition, RetainedExecutionTransitionV4)
                                else None,
                            )
                        expected_ids.update(
                            row.record_id for row in command.records if row.owner == OWNER
                        )
                        expected_publications.add((command.operation_kind, identity))
                        latest[execution.run_id] = execution
                        mixed_records.append(execution)
                        continue
                    if (
                        command.operation_kind == EXECUTION_CANCELLATION_OPERATION
                        or "execution_cancellation_preparation" in entry
                        or "execution_cancellation_envelope" in entry
                    ):
                        if command.operation_kind != EXECUTION_CANCELLATION_OPERATION:
                            raise ValueError("execution cancellation has a substituted operation")
                        raw_cancellation = entry["execution_cancellation_preparation"]
                        if not isinstance(raw_cancellation, str):
                            raise ValueError("execution cancellation retained bytes are absent")
                        execution_cancellation = decode_execution_cancellation_preparation(
                            raw_cancellation
                        )
                        if (
                            isinstance(
                                execution_cancellation, RetainedExecutionCancellationPreparationV2
                            )
                            and native_prepares.get(
                                (
                                    execution_cancellation.request.original.original_run_id,
                                    execution_cancellation.request.original.original_turn_id,
                                )
                            )
                            != execution_cancellation.original_prepare_fingerprint
                        ):
                            raise ValueError("V2 cancellation lacks exact selected V4 Prepare")
                        execution_cancellation_envelope = build_execution_cancellation_envelope(
                            execution_cancellation
                        )
                        before_execution = ExecutionHistorySnapshot(
                            tenant_head=command.expected_head, records=tuple(mixed_records)
                        )
                        initialized = tuple(
                            record
                            for fanout in execution_preparations
                            for record in fanout.proposal.fan_out.initialized_records
                            if record.original_call_id
                            == execution_cancellation.request.original_call_id
                        )
                        if (
                            execution_cancellation.canonical_bytes().decode() != raw_cancellation
                            or entry.get("execution_cancellation_envelope")
                            != execution_cancellation_envelope.canonical_bytes().decode()
                            or execution_cancellation_command(execution_cancellation) != command
                            or execution_cancellation.run_predecessor
                            != latest.get(execution_cancellation.run_predecessor.run_id)
                            or execution_cancellation.request.cut.materialization_commitment
                            != entry["predecessor"]
                            or execution_cancellation.expected_snapshot_fingerprint
                            != before_execution.digest()
                            or execution_cancellation.request.cut.predecessor_inventory
                            != execution_inventory(
                                tenant,
                                before_execution,
                                tuple(preparations),
                                tuple(cancellations),
                                tuple(execution_preparations),
                                tuple(execution_cancellations),
                                tuple(acceptances),
                            )
                            or len(initialized) != 1
                            or execution_cancellation.request.initialized_record != initialized[0]
                            or execution_cancellation.request.initialized
                            != reference(initialized[0].original_call_id, initialized[0])
                        ):
                            raise ValueError(
                                "selected execution cancellation differs from original history"
                            )
                        execution_cancellations.append(execution_cancellation)
                        expected_ids.update(
                            row.record_id for row in command.records if row.owner == OWNER
                        )
                        expected_publications.add((command.operation_kind, identity))
                        continue
                    record = RunRecord.model_validate_json(first.canonical_bytes)
                    if (
                        first.owner != OWNER
                        or first.schema_id != RUN_SCHEMA
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
                                tuple(execution_cancellations),
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
                                tuple(execution_cancellations),
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
                if exact_historical_sources and pending_source_publication is not None:
                    pending_command = runtime._publication(pending_source_publication)
                    with read_connection(runtime._database) as current_connection:
                        pending_state = inspect_publication(
                            current_connection,
                            pending_command,
                            pending_command.expected_head + 1,
                        )
                    if pending_state == "ABSENT":
                        expected_ids.difference_update(
                            row.record_id for row in pending_command.records if row.owner == OWNER
                        )
                        expected_publications.discard(
                            (pending_command.operation_kind, pending_command.idempotency_key)
                        )
                    elif pending_state != "COMPLETE":
                        raise ValueError("pending historical source publication conflicts")
                if (not selected_only or exact_historical_sources) and (
                    physical_ids != expected_ids or physical_publications != expected_publications
                ):
                    raise ValueError("orphaned, missing or unknown loop publication members")
                if require_native_open_fold and not deferred_native_open_sources:
                    raise ValueError("deferred historical source has no authenticated OPEN cause")
                if deferred_native_open_sources:
                    from chiplog.composition.r14_historical_suffix import (
                        validate_native_open_sources_held,
                    )

                    validate_native_open_sources_held(
                        runtime,
                        connection,
                        sources=tuple(deferred_native_open_sources),
                        snapshot=ExecutionHistorySnapshot(
                            tenant_head=0 if head is None else head[0], records=tuple(mixed_records)
                        ),
                        selected=selected,
                        expected_ids=expected_ids,
                        expected_publications=expected_publications,
                        acceptances=acceptances,
                        pending_publication=deferred_open_pending_publication,
                        authenticated_source_prefix=authenticated_source_prefix,
                    )
                if deferred_native_sealed_accounting:
                    current_snapshot = ExecutionHistorySnapshot(
                        tenant_head=0 if head is None else head[0],
                        records=tuple(mixed_records),
                    )
                    from chiplog.composition.r14_native_sealed_accounting_v3_history import (
                        decode_selected_native_sealed_accounting_v3,
                    )

                    for (
                        command,
                        entry,
                        authenticated_history,
                        open_recoveries,
                    ) in deferred_native_sealed_accounting:
                        decode_selected_native_sealed_accounting_v3(
                            runtime,
                            connection,
                            command,
                            entry,
                            loop_decision_bytes_by_id,
                            authenticated_history,
                            current_snapshot,
                            execution_preparations,
                            execution_cancellations,
                            mixed_records,
                            verified_h1_prepares=verified_h1_prepares,
                            accepted_open_recoveries=open_recoveries,
                        )
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
                    tuple(execution_cancellations),
                    tuple(acceptances),
                )
    except (ValueError, TypeError, KeyError, sqlite3.Error) as error:
        raise LoopIntegrityError(
            f"operation=snapshot tenant={runtime._tenant_id} record_id={identity}"
            + (f" historical_source_error={error}" if exact_historical_sources else "")
        ) from error


def read_call_history(
    runtime: R14PlanningRuntime,
) -> tuple[
    LoopSnapshot, tuple[RetainedFanOutPreparation, ...], tuple[RetainedCancellationPreparation, ...]
]:
    snapshot, fanout, cancellations, _, _, _, _ = _read_call_history(runtime)
    return snapshot, fanout, cancellations


def read_execution_history(runtime: R14PlanningRuntime) -> ExecutionHistorySnapshot:
    """Validate every selected/physical Run, retaining both registered schemas."""
    return _read_call_history(runtime)[3]


def read_execution_call_history(
    runtime: R14PlanningRuntime,
) -> tuple[
    ExecutionHistorySnapshot, CallInventorySnapshot, tuple[RetainedExecutionFanOutPreparation, ...]
]:
    _, legacy, cancellations, snapshot, executions, execution_cancellations, acceptances = (
        _read_call_history(runtime)
    )
    return (
        snapshot,
        execution_inventory(
            runtime._tenant_id,
            snapshot,
            legacy,
            cancellations,
            executions,
            execution_cancellations,
            acceptances,
        ),
        executions,
    )


ExecutionRecoveryHistory = tuple[
    ExecutionHistorySnapshot,
    CallInventorySnapshot,
    tuple[RetainedFanOutPreparation, ...],
    tuple[RetainedExecutionFanOutPreparation, ...],
    tuple[RetainedExecutionCancellationPreparation, ...],
    tuple[RetainedAcceptancePreparationV2, ...],
]


def read_execution_recovery_history(
    runtime: R14PlanningRuntime,
    *,
    selected_only: bool = False,
    exact_historical_sources: bool = False,
) -> tuple[
    ExecutionHistorySnapshot,
    CallInventorySnapshot,
    tuple[RetainedFanOutPreparation, ...],
    tuple[RetainedExecutionFanOutPreparation, ...],
    tuple[RetainedExecutionCancellationPreparation, ...],
    tuple[RetainedAcceptancePreparationV2, ...],
]:
    """Return the complete authenticated native execution history for a reader.

    This is deliberately a read-only projection.  It exposes the retained
    cancellation and acceptance selections that a recovery-source collector must
    inspect alongside every retained response seal; it does not construct a
    recovery cut or confer writer authority.
    """
    _, legacy, cancellations, snapshot, executions, execution_cancellations, acceptances = (
        _read_call_history(
            runtime,
            selected_only=selected_only,
            exact_historical_sources=exact_historical_sources,
        )
    )
    return (
        snapshot,
        execution_inventory(
            runtime._tenant_id,
            snapshot,
            legacy,
            cancellations,
            executions,
            execution_cancellations,
            acceptances,
        ),
        legacy,
        executions,
        execution_cancellations,
        acceptances,
    )


def read_selected_native_accounting(runtime: R14PlanningRuntime):
    """Canonical read-only observation of the selected bounded accounting record."""
    from chiplog.composition.r14_native_accounting_history import (
        retained_selected_native_accounting,
    )

    with runtime._authority_gate().hold():
        _read_call_history(runtime)
        found = [
            retained_selected_native_accounting(entry)
            for _, _, raw in runtime._loop_decisions().entries()
            if (entry := _strict_json(raw)).get("kind") == "DECIDED"
            and (
                entry.get("operation_kind") == "agent_loop.native-one-call-cancelled-accounting.v1"
                or "native_one_call_accounting_evidence" in entry
                or "native_one_call_accounting_envelope" in entry
            )
        ]
        return found[0].record if len(found) == 1 else None


def read_selected_native_sealed_accounting_v3(
    runtime: R14PlanningRuntime, selected_response_fingerprint: str
):
    """Read one exact V3 per-seal record after full selected-history validation."""
    from chiplog.composition.r14_native_sealed_accounting_v3_history import (
        retained_selected_native_sealed_accounting_v3,
    )

    with runtime._authority_gate().hold():
        _read_call_history(runtime)
        found = [
            retained_selected_native_sealed_accounting_v3(entry).record
            for _, _, raw in runtime._loop_decisions().entries()
            if (entry := _strict_json(raw)).get("kind") == "DECIDED"
            and entry.get("operation_kind") == "agent_loop.native-sealed-accounting.v3"
            and retained_selected_native_sealed_accounting_v3(
                entry
            ).request.selected_response.revision.fingerprint
            == selected_response_fingerprint
        ]
        return found[0] if len(found) == 1 else None


def read_selected_accepted_open_accounting(
    runtime: R14PlanningRuntime, *, original_call_id: str | None = None
):
    """Read the unique effects-specific account record after selected-source validation."""
    from chiplog.composition.r14_accepted_open_accounting_publication import (
        validate_selected_accepted_open_accounting,
    )
    from chiplog.composition.r14_accepted_open_accounting_records import (
        ACCEPTED_OPEN_ACCOUNTING_OPERATION,
        retained_accepted_open_accounting,
    )

    with runtime._authority_gate().hold():
        _read_call_history(runtime)
        validate_selected_accepted_open_accounting(runtime)
        found = [
            retained_accepted_open_accounting(entry)
            for _, _, raw in runtime._loop_decisions().entries()
            if (entry := _strict_json(raw)).get("kind") == "DECIDED"
            and entry.get("operation_kind") == ACCEPTED_OPEN_ACCOUNTING_OPERATION
        ]
        matching = tuple(
            value
            for value in found
            if original_call_id is None or value.request.cut.original_call_id == original_call_id
        )
        return matching[0].result.accounting if len(matching) == 1 else None


def read_selected_accepted_open_recovery(
    runtime: R14PlanningRuntime, *, original_call_id: str | None = None
):
    """Read the atomic accepted/open terminal and obligation publication."""
    from chiplog.composition.r14_accepted_open_accounting_publication import (
        validate_selected_accepted_open_recovery,
    )
    from chiplog.composition.r14_accepted_open_accounting_records import (
        ACCEPTED_OPEN_RECOVERY_OPERATION,
        retained_accepted_open_recovery,
    )

    with runtime._authority_gate().hold():
        _read_call_history(runtime)
        validate_selected_accepted_open_recovery(runtime)
        found = [
            retained_accepted_open_recovery(entry)
            for _, _, raw in runtime._loop_decisions().entries()
            if (entry := _strict_json(raw)).get("kind") == "DECIDED"
            and entry.get("operation_kind") == ACCEPTED_OPEN_RECOVERY_OPERATION
        ]
        matching = tuple(
            value
            for value in found
            if original_call_id is None or value.request.cut.original_call_id == original_call_id
        )
        return matching[0].result if len(matching) == 1 else None


def read_selected_original_obligation_sources(
    runtime: R14PlanningRuntime, *, historical: bool = False
):
    """Return exact selected R14 opening and terminal source records, or no cut."""
    from chiplog.composition.r14_accepted_open_accounting_publication import (
        validate_selected_accepted_open_recovery,
    )
    from chiplog.composition.r14_accepted_open_accounting_records import (
        ACCEPTED_OPEN_RECOVERY_OPERATION,
        accepted_open_recovery_command,
        retained_accepted_open_recovery,
    )
    from chiplog.composition.r14_original_obligation_sources import (
        accepted_open_recovery_sources,
    )

    with runtime._authority_gate().hold():
        _read_call_history(
            runtime,
            selected_only=historical,
            exact_historical_sources=historical,
        )
        if not historical:
            validate_selected_accepted_open_recovery(runtime)
        selected = [
            (decision_id, raw)
            for decision_id, _, raw in runtime._loop_decisions().entries()
            if (entry := _strict_json(raw)).get("kind") == "DECIDED"
            and entry.get("operation_kind") == ACCEPTED_OPEN_RECOVERY_OPERATION
        ]
        if len(selected) != 1:
            return None
        decision_id, raw = selected[0]
        evidence = retained_accepted_open_recovery(_strict_json(raw))
        command = runtime._publication(_strict_json(raw))
        expected = accepted_open_recovery_command(evidence)
        if command != expected:
            raise LoopIntegrityError("selected original obligation physical command differs")
        return accepted_open_recovery_sources(
            evidence,
            selected_decision_id=decision_id,
            selected_decision_bytes=raw.encode() if isinstance(raw, str) else raw,
        )


def read_selected_accepted_direct_terminal(
    runtime: R14PlanningRuntime, *, original_call_id: str | None = None
):
    """Read one exact selected terminal; unqualified reads require a singleton."""
    from chiplog.composition.r14_accepted_direct_terminal_publication import (
        validate_selected_accepted_direct_terminal,
    )
    from chiplog.composition.r14_accepted_direct_terminal_records import (
        ACCEPTED_DIRECT_TERMINAL_OPERATION,
        retained_accepted_direct_terminal,
    )

    with runtime._authority_gate().hold():
        authenticated_history = _read_call_history(runtime)
        validate_selected_accepted_direct_terminal(
            runtime,
            original_call_id=original_call_id,
            authenticated_effects_history=authenticated_history[3],
        )
        found = [
            retained_accepted_direct_terminal(entry)
            for _, _, raw in runtime._loop_decisions().entries()
            if (entry := _strict_json(raw)).get("kind") == "DECIDED"
            and entry.get("operation_kind") == ACCEPTED_DIRECT_TERMINAL_OPERATION
            and (
                original_call_id is None
                or retained_accepted_direct_terminal(entry).result.accounting.original_call_id
                == original_call_id
            )
        ]
        return found[0].result if len(found) == 1 else None


def read_selected_accepted_direct_terminal_frontier(
    runtime: CommonCliExecutionRuntime,
    *,
    authenticated_history: ExecutionRecoveryHistory | None = None,
) -> SealedAccounting | None:
    """Return sealed accounting only after the selected terminal batch is COMPLETE."""
    from chiplog.composition.r14_accepted_direct_terminal_frontier import (
        accepted_direct_terminal_sealed_accounting,
        require_complete_direct_terminal_publication,
    )
    from chiplog.composition.r14_accepted_direct_terminal_publication import (
        validate_selected_accepted_direct_terminal,
    )
    from chiplog.composition.r14_accepted_direct_terminal_records import (
        ACCEPTED_DIRECT_TERMINAL_OPERATION,
        accepted_direct_terminal_command,
        retained_accepted_direct_terminal,
    )
    from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
    from chiplog.platform.workspace_snapshot import read_connection

    with runtime._authority_gate().hold():
        if authenticated_history is None:
            authenticated_history = read_execution_recovery_history(runtime)
        entries = [
            _strict_json(raw)
            for _, _, raw in runtime._loop_decisions().entries()
            if (entry := _strict_json(raw)).get("kind") == "DECIDED"
            and entry.get("operation_kind") == ACCEPTED_DIRECT_TERMINAL_OPERATION
        ]
        if not entries:
            return None
        if len(entries) != 1:
            raise LoopRejected("ambiguous selected accepted/direct terminal frontier")
        entry = entries[0]
        current = validate_selected_accepted_direct_terminal(
            runtime, authenticated_history=authenticated_history
        )
        if current is None:
            raise LoopRejected("selected accepted/direct terminal source cut is unavailable")
        evidence = retained_accepted_direct_terminal(entry)
        command = accepted_direct_terminal_command(evidence)
        if runtime._publication(entry) != command:
            raise LoopRejected("selected accepted/direct terminal physical command differs")

        runtime._check_database_identity()
        with read_connection(runtime._database) as connection:
            current_snapshot = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
        journal_anchor = runtime._commitment_journal.load(runtime._tenant_id)
        state, selected_snapshot, selected_anchor = runtime._selected_physical_state(command)
        require_complete_direct_terminal_publication(
            state,
            current_snapshot,
            journal_anchor,
            selected_snapshot,
            selected_anchor,
        )

        facts, cut = current
        seal = facts.accepted.selected_seal
        ordered_call_ids = tuple(item.original_call_id for item in seal.initialized_records)
        matching_rows = tuple(
            row
            for row in facts.accepted.source.target_inventory_rows
            if row.original_call_id == facts.accepted.original_call_id
        )
        if len(matching_rows) != 1:
            raise LoopRejected("selected direct terminal call inventory member is ambiguous")
        acceptance = matching_rows[0].acceptance
        if (
            facts.accepted.original_call_id != cut.original_call_id
            or seal.response_seal.subject_id != cut.response_id
            or not isinstance(acceptance, ConsequentialAcceptedCall)
        ):
            raise LoopRejected("selected direct terminal acceptance branch differs")
        return accepted_direct_terminal_sealed_accounting(
            cut,
            evidence.result,
            acceptance,
            ordered_call_ids,
            seal.response_seal.revision.fingerprint,
        )


def validate_selected_native_accounting(runtime: R14PlanningRuntime) -> None:
    """Validate selected semantics before startup materializes pending SQL."""
    runtime._pending()
    found = False
    for _, _, raw in runtime._loop_decisions().entries():
        entry = _strict_json(raw)
        if entry.get("kind") != "DECIDED":
            continue
        command = runtime._publication(entry)
        if (
            command.operation_kind == "agent_loop.native-one-call-cancelled-accounting.v1"
            or "native_one_call_accounting_evidence" in entry
            or "native_one_call_accounting_envelope" in entry
            or any(
                record.schema_id == "chiplog.agent-loop.native-one-call-cancelled-accounting.v1"
                for record in command.records
            )
        ):
            found = True
    if found:
        _read_call_history(runtime, selected_only=True)


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
                "agent_loop.execution-next-turn.v1",
                "agent_loop.execution-suspension.v1",
                "agent_loop.execution-resume.v1",
                "agent_loop.execution-successor.v1",
                "agent_loop.execution-successor-start.v1",
            )
            or "execution_transition" in entry
            or "execution_fanout" in entry
            or "execution_complete_seal" in entry
            or "execution_next_turn_evidence" in entry
            or "inbox_initialization" in entry
            or "execution_successor_evidence" in entry
            or "execution_successor_start_evidence" in entry
            or any(record.schema_id == EXECUTION_RUN_SCHEMA for record in command.records)
        )
        if claimed:
            if command.operation_kind not in (
                EXECUTION_TRANSITION_OPERATION,
                EXECUTION_FANOUT_OPERATION,
                EXECUTION_COMPLETE_SEAL_OPERATION,
                EXECUTION_INBOX_INITIALIZATION_OPERATION,
                "agent_loop.execution-next-turn.v1",
                "agent_loop.execution-suspension.v1",
                "agent_loop.execution-resume.v1",
                "agent_loop.execution-successor.v1",
                "agent_loop.execution-successor-start.v1",
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


def validate_selected_delivery_work_genesis(runtime: R14PlanningRuntime) -> None:
    """Reject partial, orphan, or foreign g0/g1 before startup recovery writes SQL."""
    selected = runtime._owner_decisions().snapshot().decisions
    complete_claim_ids: set[str] = set()
    for decision in selected:
        batch = decision.prepared.request
        if not isinstance(batch, DeliveryWorkClaimBatchV1):
            continue
        try:
            command = runtime._owner_command(decision)
            with read_connection(runtime._database) as connection:
                state = inspect_publication(connection, command, decision.tenant_commit_sequence)
            if state == "CONFLICT":
                raise ValueError("selected delivery-work claim has corrupt physical members")
            if state == "COMPLETE":
                complete_claim_ids.add(batch.complete_records[0].record_id)
        except (ValueError, TypeError, KeyError, sqlite3.Error) as error:
            raise LoopIntegrityError(
                f"operation=startup_delivery_work_claim tenant={runtime._tenant_id} "
                f"record_id={batch.identity.command_id}"
            ) from error
    for decision in selected:
        batch = decision.prepared.request
        if not isinstance(batch, DeliveryWorkGenesisBatchV1):
            continue
        try:
            if batch.operation != "agent_loop.delivery_work.genesis.v1":
                raise ValueError("unknown delivery-work genesis operation")
            command = runtime._owner_command(decision)
            with read_connection(runtime._database) as connection:
                state = inspect_publication(connection, command, decision.tenant_commit_sequence)
                subject_id = batch.complete_records[0].record_id.removesuffix("g0")
                occupied = connection.execute(
                    "SELECT 1 FROM records WHERE tenant_id=? AND record_id=?",
                    (batch.identity.tenant_id, subject_id + "g1"),
                ).fetchone()
            if state == "CONFLICT" or (
                occupied is not None and subject_id + "g1" not in complete_claim_ids
            ):
                raise ValueError("selected delivery-work genesis has corrupt physical members")
        except (ValueError, TypeError, KeyError, sqlite3.Error) as error:
            raise LoopIntegrityError(
                f"operation=startup_delivery_work_genesis tenant={runtime._tenant_id} "
                f"record_id={batch.identity.command_id}"
            ) from error
    try:
        with read_connection(runtime._database) as connection:
            physical_claim_ids = {
                row[0]
                for row in connection.execute(
                    "SELECT record_id FROM records WHERE tenant_id=? "
                    "AND record_id LIKE 'delivery-work:%:g1'",
                    (runtime._tenant_id,),
                )
            }
        if physical_claim_ids != complete_claim_ids:
            raise ValueError("delivery-work claim physical membership differs")
    except (ValueError, TypeError, sqlite3.Error) as error:
        raise LoopIntegrityError(
            f"operation=startup_delivery_work_claim tenant={runtime._tenant_id} record_id=all"
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
        claims_legacy_cancellation = (
            command.operation_kind == CANCELLATION_OPERATION or "cancellation_preparation" in entry
        )
        claims_execution_cancellation = (
            command.operation_kind == EXECUTION_CANCELLATION_OPERATION
            or "execution_cancellation_preparation" in entry
            or "execution_cancellation_envelope" in entry
            or tuple(record.schema_id for record in command.records)
            == (CANCELLATION_SCHEMA, NOT_EXECUTED_SCHEMA)
        )
        schemas = tuple(record.schema_id for record in command.records)
        if claims_legacy_cancellation:
            if command.operation_kind != CANCELLATION_OPERATION:
                raise LoopIntegrityError("cancellation publication has a substituted operation")
            found = True
        if claims_execution_cancellation:
            if (
                command.operation_kind != EXECUTION_CANCELLATION_OPERATION
                or schemas != (CANCELLATION_SCHEMA, NOT_EXECUTED_SCHEMA)
                or "execution_cancellation_preparation" not in entry
                or "execution_cancellation_envelope" not in entry
            ):
                raise LoopIntegrityError(
                    "execution cancellation publication has a substituted shape"
                )
            found = True
    if found:
        _read_call_history(runtime, selected_only=True)


def read_loop_snapshot(runtime: R14PlanningRuntime) -> LoopSnapshot:
    return read_call_history(runtime)[0]
