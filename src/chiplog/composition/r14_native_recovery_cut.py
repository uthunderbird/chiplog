"""Current native recovery cut, reenumerated under the mounted authority gate.

Only the selected V4, hermetic, non-scheduler one-call preaccept cancellation
profile is admitted. A supplied DTO is never used to choose inventory members.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallSubjectHead,
    SealedResponseRecord,
)
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_recovery_observations import (
    ExecutionRecoveryCut,
    OriginalRecoveredClosure,
    RecoveryCausalChange,
    RecoverySourceRecord,
    SealedResponseRecoveryView,
)
from chiplog.capabilities.agent_loop.native_recovery_profile_contracts import (
    SCHEMA,
    SCHEMA_V2,
    SCHEMA_V3,
    NativeOneCallRecoveryProfileV1,
    NativeOneCallRecoveryProfileV2,
    NativeOneCallRecoveryProfileV3,
    decode_native_one_call_recovery_profile,
    decode_native_one_call_recovery_profile_v2,
    decode_native_one_call_recovery_profile_v3,
    native_recovery_fingerprint,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    HeadMarker,
    NotApplicable,
    Present,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    CancelledBeforeAcceptResult,
    ConsequentialAcceptedCall,
    DefiniteFailureResult,
    EvidenceReduction,
    FrontierMember,
    FrozenRunBindings,
    InitializedCall,
    RecoveryFrontier,
    RecoveryRequiredResult,
    SuccessResult,
    TerminalCallFrontier,
)
from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
    RECOVERY_FRONTIER_REGISTRY_SCHEMA,
    frontier_registry_reference,
)
from chiplog.capabilities.deployment_trust.native_one_call_scope_contracts import (
    NativeOneCallContextScopeV1,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_inventory_baseline import decode_h1_baseline_scope
from chiplog.composition.h1_inventory_call import decode_h1_call_scope
from chiplog.composition.h1_inventory_completion import decode_h1_completion_scope
from chiplog.composition.h1_inventory_leaf_contracts import (
    H1DecodedInventoryItem,
    H1OwnerInventoryFailure,
    H1RawInventoryItem,
)
from chiplog.composition.h1_inventory_workspace_planning import decode_h1_workspace_planning_scope
from chiplog.composition.r14_execution_cancellation_contracts import (
    RetainedExecutionCancellationPreparation,
)
from chiplog.composition.r14_execution_fanout_records import reference
from chiplog.composition.r14_native_recovery_source_subset import (
    R14NativeRecoverySourceSubset,
    R14NativeRecoverySourceSubsetReader,
)
from chiplog.composition.r14_native_scope_binding import (
    NativePrepareScopeBindingV1,
    bind_native_prepare_scope,
)
from chiplog.composition.r16_native_inventory import (
    R16InventoryIntegrityError,
    collect_r16_native_inventory,
)
from chiplog.platform._owner_publication_contracts import OwnerRecordBytes
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.ingress_authenticated_contracts import AuthenticatedCustodyRecord
from chiplog.platform.ingress_custody_records import CustodyRecord
from chiplog.platform.publication_readback import inspect_publication
from chiplog.platform.workspace_snapshot import join_workspace_snapshot, read_connection

if TYPE_CHECKING:
    from .r14_recovery_publication import RetainedRecoveryPublication


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _ref(identity: str, raw: bytes, head: str | None = None) -> CallSubjectHead:
    digest = _sha(raw)
    return CallSubjectHead(
        subject_id=identity,
        revision=Present(
            head=head or "record:" + digest,
            fingerprint=digest,
        ),
    )


def _member(
    family: str, subject: str, branch: str, heads: tuple[HeadMarker, ...]
) -> FrontierMember:
    row = FrontierMember(
        family=family, subject=subject, branch=branch, ordered_heads=heads, fingerprint="0" * 64
    )
    content = row.model_dump(mode="json")
    del content["fingerprint"]
    return row.model_copy(update={"fingerprint": native_recovery_fingerprint("member", content)})


def _run_ref(run: ExecutionRunRecord) -> CallSubjectHead:
    return _ref(run.run_id, run.canonical_bytes(), run.head)


def classify_native_occurrences(
    occurrences: tuple[H1RawInventoryItem, ...] | list[H1RawInventoryItem],
    decoded: dict[str, H1DecodedInventoryItem],
    known_sources: dict[str, RecoverySourceRecord],
    scope: NativeOneCallContextScopeV1,
) -> bool:
    """Decode first; only the independently reopened original custody is context.

    EVIDENCE absence means absence of recovery evidence. Original authenticated
    CLI custody evidence is explicitly excluded by exact token/profile equality;
    arbitrary source identity or an empty inbox cannot prove that exclusion.
    """
    from chiplog.composition.r16_native_inventory import _decode
    from chiplog.platform.ingress_authenticated_contracts import AuthenticatedCustodyCommand
    from chiplog.platform.ingress_custody_records import CustodyCommand

    admitted = AuthenticatedCustodyRecord.model_validate_json(scope.selected_admission_bytes)
    forbidden = {
        "EFFECT",
        "DISPATCH",
        "DELIVERY",
        "INTENT",
        "MANDATE",
        "OBLIGATION",
        "REDUCTION",
        "OUTCOME",
    }
    for occurrence in occurrences:
        if occurrence.record_id in known_sources:
            if occurrence.raw != known_sources[occurrence.record_id].canonical_record_bytes:
                return False
            continue
        # Selected owner members retain publication record_kind metadata; the
        # registered custody byte decoder uses the SQL physical schema key.
        # Exact selected/physical bytes were independently reconciled above.
        if (
            occurrence.surface == "PHYSICAL"
            and occurrence.owner == "broker_ingress"
            and occurrence.schema
            in {
                "chiplog.ingress.custody-record.v1",
                "chiplog.ingress.authenticated-record.v2",
                "chiplog.ingress.admission-epoch.v1",
                "chiplog.ingress.admission-epoch.v2",
                "chiplog.ingress.admission-capacity.v1",
                "chiplog.ingress.admission-ready.v1",
            }
        ):
            expected_kind = (
                "ingress.admission"
                if occurrence.schema.startswith("chiplog.ingress.admission-")
                else "ingress.authenticated_custody"
                if occurrence.schema == "chiplog.ingress.authenticated-record.v2"
                else "ingress.custody"
            )
            if occurrence.record_kind not in (None, expected_kind):
                return False
            occurrence = replace(occurrence, record_kind=None)
        decoded_row = decoded.get(occurrence.locator)
        if decoded_row is None:
            for decoder in (
                _decode,
                decode_h1_baseline_scope,
                decode_h1_call_scope,
                decode_h1_workspace_planning_scope,
                lambda item: decode_h1_completion_scope(item, None),
            ):
                try:
                    decoded_row = decoder(occurrence)
                    if decoded_row is not None:
                        break
                except H1OwnerInventoryFailure as error:
                    if error.code != "UNSUPPORTED":
                        raise
            if decoded_row is None:
                return False
        if forbidden.intersection(decoded_row.families):
            return False
        if "EVIDENCE" in decoded_row.families:
            types: dict[
                str,
                type[AuthenticatedCustodyRecord]
                | type[CustodyRecord]
                | type[AuthenticatedCustodyCommand]
                | type[CustodyCommand],
            ] = {
                "chiplog.ingress.authenticated-record.v2": AuthenticatedCustodyRecord,
                "chiplog.ingress.custody-record.v1": CustodyRecord,
                "chiplog.ingress.authenticated-command.v2": AuthenticatedCustodyCommand,
                "chiplog.ingress.retained-command.v1": CustodyCommand,
            }
            kind = types.get(occurrence.schema)
            if occurrence.owner != "broker_ingress" or kind is None:
                return False
            value = kind.model_validate_json(occurrence.raw)
            custody = (
                value.command
                if isinstance(value, (CustodyRecord, AuthenticatedCustodyRecord))
                else value
            )
            if (
                custody.token != admitted.command.token
                or custody.profile != admitted.command.profile
                or custody.retention != admitted.command.retention
                or (
                    custody.raw_bytes is not None
                    and custody.raw_bytes != admitted.command.raw_bytes
                )
            ):
                return False
    return True


def _child(
    carrier: RecoverySourceRecord, identity: str, schema: str, raw: bytes, head: str | None = None
) -> RecoverySourceRecord:
    # Child and physical carrier deliberately retain different hash domains.
    return RecoverySourceRecord(
        owner=carrier.owner,
        subject=_ref(identity, raw, head),
        schema_id=schema,
        canonical_record_bytes=raw,
        selected_decision=carrier.selected_decision,
        physical_record=carrier.physical_record,
    )


class NativeRecoveryCutSource:
    """Writer source protocol; every collection starts from installed storage."""

    def __init__(
        self,
        profile_reader: Callable[[CommonCliExecutionRuntime], RecoverySourceRecord | None]
        | None = None,
    ) -> None:
        self._profile_reader = profile_reader

    def collect(self, runtime: CommonCliExecutionRuntime) -> ExecutionRecoveryCut | None:
        with runtime._authority_gate().hold():
            return self.collect_held(runtime)

    def collect_held(self, runtime: CommonCliExecutionRuntime) -> ExecutionRecoveryCut | None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("native recovery cut requires the mounted CLI runtime")
        runtime._authority_gate().require_held()
        try:
            with join_workspace_snapshot(runtime._database):
                return self._collect_held(runtime)
        except (OSError, RuntimeError, TypeError, ValueError, R16InventoryIntegrityError):
            return None

    def _collect_held(self, runtime: CommonCliExecutionRuntime) -> ExecutionRecoveryCut | None:
        runtime._check_database_identity()
        if runtime._pending() or runtime._pending_gate_publications():
            return None
        profile_reader = self._profile_reader
        if profile_reader is None:
            from .r14_recovery_profile_publication import read_native_recovery_profile_held

            profile_reader = cast(
                Callable[[CommonCliExecutionRuntime], RecoverySourceRecord | None],
                read_native_recovery_profile_held,
            )
        profile_source = profile_reader(runtime)
        if profile_source is None or profile_source.schema_id not in (SCHEMA, SCHEMA_V2, SCHEMA_V3):
            return None
        profile: (
            NativeOneCallRecoveryProfileV1
            | NativeOneCallRecoveryProfileV2
            | NativeOneCallRecoveryProfileV3
        )
        if profile_source.schema_id == SCHEMA:
            profile = decode_native_one_call_recovery_profile(profile_source.canonical_record_bytes)
        elif profile_source.schema_id == SCHEMA_V2:
            profile = decode_native_one_call_recovery_profile_v2(
                profile_source.canonical_record_bytes
            )
        else:
            profile = decode_native_one_call_recovery_profile_v3(
                profile_source.canonical_record_bytes
            )
        subset = R14NativeRecoverySourceSubsetReader(runtime).collect()
        if subset is None:
            return None
        accepted_inputs: (
            tuple[
                TerminalCallFrontier,
                OriginalRecoveredClosure,
                EvidenceReduction,
                tuple[RecoverySourceRecord, ...],
            ]
            | None
        ) = None
        direct_terminal_inputs: (
            tuple[TerminalCallFrontier, tuple[RecoverySourceRecord, ...]] | None
        ) = None
        direct_terminal_claimed = False
        if subset.accepted_closed_outcome is not None:
            direct_terminal_claimed, direct_terminal_inputs = (
                self._selected_accepted_direct_terminal_inputs(runtime, subset)
            )
            if direct_terminal_claimed:
                if direct_terminal_inputs is None:
                    return None
            else:
                accepted_inputs = self._selected_accepted_recovery_inputs(runtime, subset)
                if accepted_inputs is None:
                    return None
        facts = subset.selected_facts
        preparation = facts.source.original_prepare
        binding = preparation.original_native_scope_binding
        if binding is None:
            return None  # Historical V3 Prepare never gains a scope retroactively.
        scope = NativeOneCallContextScopeV1.model_validate_json(binding.scope_bytes)
        from .r17_native_one_call_scope import NativeOneCallScopeService

        scope_service = NativeOneCallScopeService(runtime)
        scope_lineage = scope_service._lineage()
        selected_scopes = [item for item in scope_lineage if item.anchor == binding.anchor]
        if len(selected_scopes) != 1:
            return None
        current_scope = selected_scopes[0]
        # The subset's fresh authenticated history already reopened historical
        # scope semantics. Independently retain its exact physical trust join.
        if bind_native_prepare_scope(current_scope) != binding:
            return None
        latest = [
            item
            for item in scope_lineage
            if (item.scope.database_id, item.scope.scope_id) == (scope.database_id, scope.scope_id)
        ]
        captured = scope_service._sources.capture_selected_current(
            scope.selected_resource_observation_ref,
            scope.admitted_authentication,
            scope.authenticated_cli_ref,
        )
        if (
            not latest
            or latest[-1] != current_scope
            or captured is None
            or captured.verified.recipient != scope.recipient
        ):
            return None
        # Historical scope authenticates its issuance inventory; this current
        # anchored inventory independently excludes scheduler/mandate sources.
        scope_service._inventory()
        scope_service._check_cli_current(scope.authenticated_cli_ref)
        snapshot = subset.authenticated_execution_snapshot
        seals = subset.authenticated_seals
        cancellations = subset.authenticated_cancellations
        if (
            snapshot is None
            or len(seals) != 1
            or (
                accepted_inputs is None
                and direct_terminal_inputs is None
                and len(cancellations) != 1
            )
            or (
                (accepted_inputs is not None or direct_terminal_inputs is not None)
                and (cancellations or len(subset.authenticated_acceptances) != 1)
            )
        ):
            return None
        lineage = tuple(
            row
            for row in snapshot.records
            if isinstance(row, ExecutionRunRecord) and row.run_id == scope.planned_run_id
        )
        if (
            not lineage
            or tuple(_run_ref(row) for row in lineage) != facts.source.target_run_lineage
        ):
            return None
        run = lineage[-1]
        if (
            run.state != "ACTIVE"
            or run.worker_session != scope.worker_session_id
            or run.root_binding != "NOT_APPLICABLE"
            or run.suspension_baseline is not None
            or run.original_obligations
            or run.no_retry_references
            or run.delivery_acceptance is not None
            or len(run.turns) != 1
            or run.turns[0].ordinal != 1
        ):
            return None
        inventory = collect_r16_native_inventory(runtime)
        if inventory.commitment != subset.materialization_commitment:
            return None
        # Reconcile every publication and every physical record against both
        # journals. Membership is exact before any relevance classification.
        selected: dict[str, tuple[PhysicalRecord | OwnerRecordBytes, CallSubjectHead]] = {}
        with read_connection(runtime._database) as connection:
            for decision_id, _, raw in runtime._loop_decisions().entries():
                entry = json.loads(raw)
                if entry.get("kind") != "DECIDED":
                    continue
                command = runtime._publication(entry)
                if (
                    inspect_publication(connection, command, command.expected_head + 1)
                    != "COMPLETE"
                ):
                    return None
                decision_ref = _ref(command.idempotency_key, raw, decision_id)
                for record in command.records:
                    if record.record_id in selected:
                        return None
                    selected[record.record_id] = (record, decision_ref)
            for decision in runtime._owner_decisions().snapshot().decisions:
                raw = next(
                    raw for key, _, raw in inventory.owner_entries if key == decision.decision_id
                )
                decision_ref = _ref(
                    decision.prepared.request.identity.command_id, raw, decision.decision_id
                )
                for owner_record in decision.prepared.request.complete_records:
                    if owner_record.record_id in selected:
                        return None
                    selected[owner_record.record_id] = (owner_record, decision_ref)
        if set(selected) != {row.record_id for row in inventory.records}:
            return None
        sources: list[RecoverySourceRecord] = []
        native_sources: dict[str, list[RecoverySourceRecord]] = {}
        for source in subset.execution_sources:
            native_sources.setdefault(source.physical_record.subject_id, []).append(source)
        additional_sources = list(subset.accepted_effect_sources)
        if accepted_inputs is not None:
            additional_sources.extend(accepted_inputs[3])
        if direct_terminal_inputs is not None:
            additional_sources.extend(direct_terminal_inputs[1])
        for source in additional_sources:
            identity = source.physical_record.subject_id
            previous = native_sources.setdefault(identity, [])
            if previous and any(
                (
                    item.owner,
                    item.schema_id,
                    item.canonical_record_bytes,
                    item.physical_record,
                )
                != (
                    source.owner,
                    source.schema_id,
                    source.canonical_record_bytes,
                    source.physical_record,
                )
                for item in previous
            ):
                return None
            if not any(item.subject == source.subject for item in previous):
                previous.append(source)
        physical_ids = {row.record_id for row in inventory.records}
        if not set(native_sources).issubset(physical_ids):
            return None
        for row in inventory.records:
            selected_record, selected_decision = selected[row.record_id]
            if (
                selected_record.owner,
                selected_record.schema_id,
                selected_record.canonical_bytes,
            ) != (
                row.owner,
                row.schema,
                row.raw,
            ):
                return None
            subject = (
                native_sources[row.record_id][0].subject
                if row.record_id in native_sources
                else _ref(row.record_id, row.raw)
            )
            sources.append(
                RecoverySourceRecord(
                    owner=row.owner,
                    subject=subject,
                    schema_id=row.schema,
                    canonical_record_bytes=row.raw,
                    selected_decision=selected_decision,
                    physical_record=_ref(row.record_id, row.raw),
                )
            )
            # One authenticated physical R16 row can support more than one
            # logical head in the selected dispatch/evidence source chains.
            # Rebind each logical preimage to the single physical selection
            # independently established by this inventory pass.
            for native_source in native_sources.get(row.record_id, ())[1:]:
                sources.append(
                    replace(
                        native_source,
                        selected_decision=selected_decision,
                        physical_record=_ref(row.record_id, row.raw),
                    )
                )
        by_id = {row.physical_record.subject_id: row for row in sources}
        if by_id.get(profile_source.physical_record.subject_id) != profile_source:
            return None
        known = set(native_sources) | {profile_source.physical_record.subject_id}
        if subset.selected_accounting is not None:
            known.add(subset.selected_accounting.record_id)
        # These records have already crossed the mounted history decoder's
        # independent historical source/atomic-bridge authentication above.
        known.update(
            row.physical_record.subject_id
            for row in sources
            if row.schema_id
            in {
                "chiplog.execution.sealed-accounting.v1",
                "chiplog.execution.continuation-ready.v1",
            }
        )
        # R16 selected/physical occurrences are decoded before they can be
        # dismissed. The native source/history reader authenticates its closed
        # native members. Other registered leaves use their existing pure codecs.
        if not classify_native_occurrences(
            inventory.occurrences,
            {row.locator: row for row in inventory.decoded},
            {identity: by_id[identity] for identity in known},
            scope,
        ):
            return None
        if inventory.evidence_rows:
            return None
        # Every unsupported publication must be one already authenticated by
        # the mounted selected loop journal; a gap is never ignored by schema.
        operations = {
            runtime._publication(json.loads(raw)).operation_kind
            for _, _, raw in runtime._loop_decisions().entries()
            if json.loads(raw).get("kind") == "DECIDED"
        }
        if any(
            gap.code == "PENDING"
            or (
                gap.code == "UNSUPPORTED_PUBLICATION"
                and next(
                    row.operation
                    for row in inventory.publications
                    if "publications/" + row.command_id == gap.locator
                )
                not in operations
            )
            for gap in inventory.gaps
        ):
            return None
        if accepted_inputs is None and direct_terminal_inputs is None:
            cut = self._assemble(
                runtime,
                subset,
                profile_source,
                profile,
                lineage,
                seals[0].proposal.fan_out.response_seal,
                cancellations[0],
                tuple(sources),
                binding,
                scope,
            )
        elif direct_terminal_inputs is not None:
            call, _ = direct_terminal_inputs
            cut = self._assemble_accepted(
                runtime,
                subset,
                profile_source,
                profile,
                lineage,
                seals[0].proposal.fan_out.response_seal,
                call,
                None,
                None,
                tuple(sources),
                binding,
                scope,
            )
        else:
            assert accepted_inputs is not None
            call, closure, reduction, _ = accepted_inputs
            cut = self._assemble_accepted(
                runtime,
                subset,
                profile_source,
                profile,
                lineage,
                seals[0].proposal.fan_out.response_seal,
                call,
                closure,
                reduction,
                tuple(sources),
                binding,
                scope,
            )
        runtime._check_database_identity()
        if runtime._commitment_journal.load(runtime._tenant_id) != inventory.commitment:
            return None
        return cut

    @staticmethod
    def _selected_accepted_direct_terminal_inputs(
        runtime: CommonCliExecutionRuntime,
        subset: R14NativeRecoverySourceSubset,
    ) -> tuple[
        bool,
        tuple[TerminalCallFrontier, tuple[RecoverySourceRecord, ...]] | None,
    ]:
        """Select one exact direct terminal, without falling through on a rival claim."""
        from chiplog.capabilities.agent_loop.recovery_contracts import Present
        from chiplog.composition.r14_accepted_direct_terminal_publication import (
            validate_selected_accepted_direct_terminal,
        )
        from chiplog.composition.r14_accepted_direct_terminal_records import (
            ACCEPTED_DIRECT_TERMINAL_OPERATION,
            accepted_direct_terminal_command,
            retained_accepted_direct_terminal,
        )
        from chiplog.composition.r14_accepted_open_accounting_records import (
            ACCEPTED_OPEN_ACCOUNTING_OPERATION,
            ACCEPTED_OPEN_RECOVERY_OPERATION,
        )

        entries: list[tuple[str, bytes, dict[str, object], PhysicalPublicationCommand]] = []
        claimed = False
        competing_evidence_fields = {
            "accepted_open_accounting_evidence",
            "accepted_open_recovery_evidence",
            "original_resolver_authority_policy_evidence",
            "original_resolver_invocation_evidence",
            "original_call_resolution_evidence",
            "loop_semantic_reduction_evidence",
        }
        for decision_id, _, raw in runtime._loop_decisions().entries():
            entry = json.loads(raw)
            if entry.get("kind") != "DECIDED":
                continue
            command = runtime._publication(entry)
            competing_schema = any(
                record.schema_id.startswith(
                    (
                        "chiplog.execution.accepted-open-",
                        "chiplog.loop.original-resolver-",
                        "chiplog.loop.semantic-reduction",
                        "chiplog.loop.semantic-reducer-",
                    )
                )
                for record in command.records
            )
            if (
                command.operation_kind
                in {
                    ACCEPTED_OPEN_ACCOUNTING_OPERATION,
                    ACCEPTED_OPEN_RECOVERY_OPERATION,
                    "agent_loop.original-call-resolution.v1",
                    "agent_loop.loop-semantic-reduction.v1",
                }
                or competing_schema
                or any(field in entry for field in competing_evidence_fields)
            ):
                claimed = True
                return True, None
            marker = (
                command.operation_kind == ACCEPTED_DIRECT_TERMINAL_OPERATION
                or "accepted_direct_terminal_evidence" in entry
                or any(
                    record.schema_id.startswith("chiplog.execution.accepted-direct-terminal-")
                    for record in command.records
                )
            )
            if marker:
                claimed = True
                entries.append((decision_id, raw, entry, command))
        if not claimed:
            return False, None
        if len(entries) != 1 or subset.accepted_closed_outcome is None:
            return True, None
        try:
            decision_id, raw, entry, command = entries[0]
            validate_selected_accepted_direct_terminal(runtime)
            evidence = retained_accepted_direct_terminal(entry)
            if runtime._publication(entry) != accepted_direct_terminal_command(evidence):
                return True, None
            accounting = runtime.read_selected_accepted_direct_terminal_frontier()
            if (
                accounting is None
                or len(accounting.calls) != 1
                or accounting.calls[0].original_call_id
                != subset.accepted_closed_outcome.accepted.original_call_id
            ):
                return True, None
            call = accounting.calls[0]
            if (
                not isinstance(call.acceptance, ConsequentialAcceptedCall)
                or not isinstance(call.disposition, (SuccessResult, DefiniteFailureResult))
                or evidence.request.cut.current_run.subject_id
                != subset.accepted_closed_outcome.accepted.source.target_run_lineage[-1].subject_id
                or evidence.request.cut.original_call_id != call.original_call_id
                or evidence.result.result.original_call_id != call.original_call_id
            ):
                return True, None
            selected_decision = CallSubjectHead(
                subject_id=command.idempotency_key,
                revision=Present(
                    head=decision_id,
                    fingerprint=hashlib.sha256(raw).hexdigest(),
                ),
            )
            sources: list[RecoverySourceRecord] = []
            for record in command.records:
                digest = hashlib.sha256(record.canonical_bytes).hexdigest()
                subject = CallSubjectHead(
                    subject_id=record.record_id,
                    revision=Present(head="record:" + digest, fingerprint=digest),
                )
                sources.append(
                    RecoverySourceRecord(
                        owner=record.owner,
                        subject=subject,
                        schema_id=record.schema_id,
                        canonical_record_bytes=record.canonical_bytes,
                        selected_decision=selected_decision,
                        physical_record=subject,
                    )
                )
            # The accepted source reader independently proves the selected R16
            # outcome and ordered evidence. Require its exact outcome source to
            # be present before allowing the direct terminal into this cut.
            outcome_id = subset.accepted_closed_outcome.latest_outcome_record_id
            if not any(
                source.physical_record.subject_id == outcome_id
                for source in subset.accepted_effect_sources
            ):
                return True, None
            return True, (call, tuple(sources))
        except (OSError, RuntimeError, TypeError, ValueError, KeyError, AttributeError):
            return True, None

    @staticmethod
    def _selected_accepted_recovery_inputs(
        runtime: CommonCliExecutionRuntime,
        subset: R14NativeRecoverySourceSubset,
    ) -> (
        tuple[
            TerminalCallFrontier,
            OriginalRecoveredClosure,
            EvidenceReduction,
            tuple[RecoverySourceRecord, ...],
        ]
        | None
    ):
        """Join accepted/open, resolver, reduction, and live evidence under this cut."""
        from chiplog.capabilities.agent_loop.original_recovery_contracts import (
            ConsumableReduction,
        )
        from chiplog.composition import (
            r14_loop_semantic_reduction_publication as reduction_publication,
        )
        from chiplog.composition.r14_accepted_open_accounting_publication import (
            _reduction_matches_current_evidence,
            _reduction_matches_resolution,
        )
        from chiplog.composition.r14_original_call_resolution_publication import (
            read_selected_original_call_resolution_source_held,
        )

        try:
            closed = subset.accepted_closed_outcome
            if closed is None:
                return None
            accounting = runtime.read_selected_accepted_open_frontier()  # type: ignore[no-untyped-call]
            resolution = read_selected_original_call_resolution_source_held(runtime)
            selected_reduction = runtime.read_selected_loop_semantic_reduction()  # type: ignore[no-untyped-call]
            if (
                accounting is None
                or len(accounting.calls) != 1
                or resolution is None
                or selected_reduction is None
                or not isinstance(accounting.calls[0].acceptance, ConsequentialAcceptedCall)
                or not isinstance(accounting.calls[0].disposition, RecoveryRequiredResult)
            ):
                return None
            call = accounting.calls[0]
            reduction_entries = reduction_publication._selected_entries(runtime)
            if len(reduction_entries) != 1:
                return None
            retained_reduction = reduction_publication.validate_selected_command(
                runtime, reduction_entries[0][2], reduction_entries[0][3]
            )  # type: ignore[no-untyped-call]
            current_request = reduction_publication.capture_request(runtime)
            evidence_sources = closed.complete_ordered_evidence_sources
            evidence_refs = tuple(source.physical_record for source in evidence_sources)
            selected_witness = (
                current_request.complete_anchor_sources[-1]
                if current_request.complete_anchor_sources
                else None
            )
            selected_reduction_witness = (
                retained_reduction.request.complete_anchor_sources[-1]
                if retained_reduction.request.complete_anchor_sources
                else None
            )
            if (
                not evidence_refs
                or tuple(
                    source.physical_record for source in current_request.complete_ordered_evidence
                )
                != evidence_refs
                or selected_reduction.record.complete_ordered_evidence != evidence_refs
                or not isinstance(selected_reduction.record.disposition, ConsumableReduction)
                or selected_witness is None
                or selected_reduction_witness is None
                or not _reduction_matches_resolution(resolution, selected_reduction)
                or not _reduction_matches_current_evidence(
                    selected_reduction,
                    evidence_sources,
                    current_witness=selected_witness,
                    reduction_witness=selected_reduction_witness,
                )
            ):
                return None

            from chiplog.composition.r14_execution_fanout_records import reference

            disposition = call.disposition
            closure = OriginalRecoveredClosure(
                original=disposition.obligation,
                terminal_disposition=disposition.terminal,
                recovered_outcome=disposition.recovered_outcome.outcome,
                accepted_evidence=resolution.closure.accepted_witness.revision,
                closure=reference(resolution.closure.closure_id, resolution.closure).revision,
                resolver_batch=reference(resolution.batch.batch_id, resolution.batch).revision,
                reduction_id=selected_reduction.record.reduction_id,
                accepted_semantic_class=selected_reduction.record.disposition.semantic_class,
            )
            reduction = EvidenceReduction(
                stream_id=selected_reduction.record.stream.stream_id,
                reduction_id=selected_reduction.record.reduction_id,
                reducer_id=selected_reduction.record.reducer_id,
                reducer_version=selected_reduction.record.reducer_version,
                current_reduction=selected_reduction.source.subject.revision,
                ordered_consumed_evidence=tuple(item.revision for item in evidence_refs),
                accepted_witness=selected_reduction.record.anchor.accepted_witness.revision,
                accepted_outcome=selected_reduction.record.anchor.recovered_outcome.revision,
                obligation_closure=selected_reduction.record.anchor.closure.revision,
                resolver_batch=selected_reduction.record.anchor.resolver_batch.revision,
                semantic_class=selected_reduction.record.disposition.semantic_class,
                consumability="CONSUMABLE",
            )
            extra_sources = (
                *current_request.complete_ordered_evidence,
                *current_request.complete_anchor_sources,
                current_request.reducer_policy,
                selected_reduction.source,
            )
            return call, closure, reduction, extra_sources
        except (OSError, RuntimeError, TypeError, ValueError, KeyError):
            return None

    def _assemble(
        self,
        runtime: CommonCliExecutionRuntime,
        subset: R14NativeRecoverySourceSubset,
        profile_source: RecoverySourceRecord,
        profile: NativeOneCallRecoveryProfileV1
        | NativeOneCallRecoveryProfileV2
        | NativeOneCallRecoveryProfileV3,
        lineage: tuple[ExecutionRunRecord, ...],
        seal: SealedResponseRecord,
        cancellation: RetainedExecutionCancellationPreparation,
        physical_sources: tuple[RecoverySourceRecord, ...],
        binding: NativePrepareScopeBindingV1,
        scope: NativeOneCallContextScopeV1,
    ) -> ExecutionRecoveryCut:
        run, turn = lineage[-1], lineage[-1].turns[0]
        artifact = subset.selected_facts.source.original_prepare.artifact
        prepared = subset.selected_facts.source.original_prepare.prepared_run
        prepare_source = next(row for row in physical_sources if row.subject == prepared)
        children = []

        def child(
            identity: str,
            schema: str,
            raw: bytes,
            carrier: RecoverySourceRecord = prepare_source,
            head: str | None = None,
        ) -> CallSubjectHead:
            source = _child(carrier, identity, schema, raw, head)
            children.append(source)
            return source.subject

        prompt = child(
            "native-prompt-artifact:" + artifact.digest(),
            "chiplog.native-recovery.prompt-artifact.v1",
            artifact.canonical_bytes(),
        )
        schema = child(
            "native-generated-schema:" + _sha(artifact.response_schema_json.encode()),
            "chiplog.native-recovery.generated-schema.v1",
            artifact.response_schema_json.encode(),
        )
        tools = tuple(
            child(
                "native-tool:" + tool.digest(),
                "chiplog.native-recovery.tool.v1",
                tool.canonical_bytes(),
            )
            for tool in artifact.tools
        )
        policy = child(
            "native-run-policy:" + run.policy.digest(),
            "chiplog.native-recovery.run-policy.v1",
            run.policy.canonical_bytes(),
        )
        scope_source = RecoverySourceRecord(
            owner="deployment_trust",
            subject=CallSubjectHead(
                subject_id=scope.scope_id,
                revision=Present(
                    head=binding.scope_head.head, fingerprint=binding.scope_head.fingerprint
                ),
            ),
            schema_id=scope.schema_id,
            canonical_record_bytes=binding.scope_bytes,
            selected_decision=CallSubjectHead(
                subject_id=binding.anchor.decision.identity,
                revision=Present(
                    head=binding.anchor.decision.head,
                    fingerprint=binding.anchor.decision.fingerprint,
                ),
            ),
            physical_record=CallSubjectHead(
                subject_id=binding.anchor.record.identity,
                revision=Present(
                    head=binding.anchor.record.head, fingerprint=binding.anchor.record.fingerprint
                ),
            ),
        )
        children.append(scope_source)
        admitted = child(
            scope.selected_admission_ref.identity,
            "chiplog.native-recovery.selected-admission.v1",
            scope.selected_admission_bytes,
            scope_source,
            scope.selected_admission_ref.head,
        )
        recipient = child(
            "native-recipient:" + _sha(scope.recipient.canonical_bytes()),
            "chiplog.native-recovery.recipient.v1",
            scope.recipient.canonical_bytes(),
            scope_source,
        )
        semantic = child(
            "native-context-policy:" + scope.disclosure_policy.ref.fingerprint,
            "chiplog.native-one-call-context-policy.v1",
            scope.disclosure_policy.canonical_source_bytes,
            scope_source,
            scope.disclosure_policy.ref.head,
        )
        child(
            profile.registry.registry_id,
            RECOVERY_FRONTIER_REGISTRY_SCHEMA,
            profile.registry.canonical_bytes(),
            profile_source,
            frontier_registry_reference(profile.registry).revision.head,
        )
        terminal, result = cancellation.proposal.terminal, cancellation.proposal.result
        initialized = subset.selected_facts.initialized
        call = TerminalCallFrontier(
            original_call_id=initialized.subject_id,
            response_id=seal.response_seal_id,
            acceptance=InitializedCall(initialized=initialized.revision),
            disposition=CancelledBeforeAcceptResult(
                terminal=reference(terminal.terminal_id, terminal).revision,
                not_executed_result=reference(result.result_id, result).revision,
                initialized_predecessor=initialized.revision,
                recovered_outcome=NotApplicable(),
            ),
        )
        bindings = FrozenRunBindings(
            objective=run.prompt,
            requested_work=run.turns[-1].attempts[run.turns[-1].selector].request,
            prompt_artifact=prompt.revision,
            ordered_tool_specs=tuple(row.revision for row in tools),
            generated_schema=schema.revision,
            semantic_bindings=(semantic.revision,),
            recipient_effect_bindings=(recipient.revision,),
            authority_scope=scope_source.subject.revision,
            authority_mandate_heads=(),
            policy=policy.revision,
            no_retry_boundaries=(),
        )
        current_run_source = next(row for row in physical_sources if row.subject == _run_ref(run))
        turn_ref = child(
            turn.turn_id,
            "chiplog.native-recovery.turn.v1",
            turn.canonical_bytes(),
            current_run_source,
            turn.head,
        )
        captured_ref = subset.selected_facts.selected_seal.captured_run
        captured_run = next(row for row in lineage if _run_ref(row) == captured_ref)
        captured_turn = captured_run.turns[-1]
        captured_turn_ref = child(
            captured_turn.turn_id,
            "chiplog.native-recovery.turn.v1",
            captured_turn.canonical_bytes(),
            next(row for row in physical_sources if row.subject == captured_ref),
            captured_turn.head,
        )
        assert isinstance(call.disposition, CancelledBeforeAcceptResult)
        heads: dict[str, tuple[HeadMarker, ...]] = {
            "RUN": (_run_ref(run).revision,),
            "TURN": (turn_ref.revision,),
            "SEALED_RESPONSE": (subset.selected_facts.selected_seal.response_seal.revision,),
            "CALL": (
                initialized.revision,
                call.disposition.terminal,
                call.disposition.not_executed_result,
            ),
            "AUTHORITY": (bindings.authority_scope,),
            "POLICY": (bindings.policy, semantic.revision),
            "PROMPT": (prompt.revision,),
            "TOOL_SCHEMA": (schema.revision, *(row.revision for row in tools)),
            "RECIPIENT": (recipient.revision,),
            "SEMANTIC_BINDING": (semantic.revision,),
        }
        absence = {"EFFECT", "EVIDENCE", "DELIVERY", "OBLIGATION", "SEMANTIC_REDUCTION"}
        members = tuple(
            _member(
                row.family,
                initialized.subject_id if row.family == "CALL" else run.run_id,
                "ABSENT"
                if row.family in absence
                else "NOT_APPLICABLE"
                if row.family in {"MANDATE", "EXECUTION_LINEAGE"}
                else "PRESENT",
                heads.get(row.family, (Absent(),) if row.family in absence else (NotApplicable(),)),
            )
            for row in profile.registry.ordered_rows
        )
        frontier = RecoveryFrontier(
            tenant_id=run.tenant,
            run_id=run.run_id,
            tenant_commit_sequence=subset.tenant_commit_sequence,
            registry=profile.registry,
            ordered_members=members,
            ordered_calls=(call,),
            canonicalization_version="chiplog.recovery.frontier.v1",
            fingerprint="0" * 64,
        )
        value = frontier.model_dump(mode="json")
        del value["fingerprint"]
        frontier = frontier.model_copy(
            update={"fingerprint": native_recovery_fingerprint("frontier", value)}
        )
        changes = []
        source_by_subject = {row.subject.revision.head: row for row in physical_sources}
        prior = None
        for row in lineage:
            if row.event not in profile.causal_transitions:
                raise ValueError(f"unregistered native causal Run transition: {row.event}")
            successor = _run_ref(row)
            source = source_by_subject[successor.revision.head]
            changes.append(
                RecoveryCausalChange(
                    subject_id=row.run_id,
                    source_family="RUN",
                    predecessor=Absent() if prior is None else prior.revision,
                    successor=successor,
                    causal_parents=(admitted,) if prior is None else (prior,),
                    transition_registry=profile_source.subject,
                    transition_id=row.event,
                    source=source,
                )
            )
            prior = successor
        for subject, family, transition, parents in (
            (
                subset.selected_facts.selected_seal.response_seal,
                "SEALED_RESPONSE",
                "ModelResponseSealed",
                (subset.selected_facts.selected_seal.captured_run,),
            ),
            (
                initialized,
                "CALL",
                "CallInitialized",
                (subset.selected_facts.selected_seal.response_seal,),
            ),
            (
                reference(terminal.terminal_id, terminal),
                "CALL",
                "CancelledBeforeAccept",
                (initialized, _run_ref(run)),
            ),
            (
                reference(result.result_id, result),
                "CALL",
                "NotExecutedCallResult",
                (reference(terminal.terminal_id, terminal),),
            ),
        ):
            changes.append(
                RecoveryCausalChange(
                    subject_id=subject.subject_id,
                    source_family=family,
                    predecessor=Absent(),
                    successor=subject,
                    causal_parents=parents,
                    transition_registry=profile_source.subject,
                    transition_id=transition,
                    source=source_by_subject[subject.revision.head],
                )
            )
        children.sort(
            key=lambda row: (row.schema_id, row.subject.subject_id, row.subject.revision.head)
        )
        cut = ExecutionRecoveryCut(
            tenant_id=run.tenant,
            database_id=runtime._database_identity[0],
            tenant_commit_sequence=subset.tenant_commit_sequence,
            materialization_commitment=subset.materialization_commitment,
            current_run=_run_ref(run),
            complete_ordered_run_lineage=lineage,
            complete_ordered_responses=(
                SealedResponseRecoveryView(
                    original_run=captured_ref,
                    original_turn=captured_turn_ref,
                    selected_seal=subset.selected_facts.selected_seal.response_seal,
                    seal=seal,
                    ordered_calls=(call,),
                    frontier_head=reference("recovery-frontier:" + run.run_id, frontier),
                ),
            ),
            frontier=frontier,
            current_bindings=bindings,
            original_closures=(),
            current_reductions=(),
            complete_sources=(*physical_sources, *children),
            complete_causal_changes=tuple(changes),
            complete_inventory_fingerprint="0" * 64,
        )
        value = cut.model_dump(mode="json")
        del value["complete_inventory_fingerprint"]
        return cut.model_copy(
            update={"complete_inventory_fingerprint": native_recovery_fingerprint("cut", value)}
        )

    def _assemble_accepted(
        self,
        runtime: CommonCliExecutionRuntime,
        subset: R14NativeRecoverySourceSubset,
        profile_source: RecoverySourceRecord,
        profile: NativeOneCallRecoveryProfileV1
        | NativeOneCallRecoveryProfileV2
        | NativeOneCallRecoveryProfileV3,
        lineage: tuple[ExecutionRunRecord, ...],
        seal: SealedResponseRecord,
        call: TerminalCallFrontier,
        closure: OriginalRecoveredClosure | None,
        reduction: EvidenceReduction | None,
        physical_sources: tuple[RecoverySourceRecord, ...],
        binding: NativePrepareScopeBindingV1,
        scope: NativeOneCallContextScopeV1,
    ) -> ExecutionRecoveryCut:
        """Build the typed next-turn cut for one fully recovered accepted call."""
        if profile_source.schema_id != SCHEMA_V3:
            raise ValueError("accepted recovery requires the selected V3 native profile")
        run, turn = lineage[-1], lineage[-1].turns[0]
        artifact = subset.selected_facts.source.original_prepare.artifact
        prepared = subset.selected_facts.source.original_prepare.prepared_run
        prepare_source = next(row for row in physical_sources if row.subject == prepared)
        children: list[RecoverySourceRecord] = []

        def child(
            identity: str,
            schema_id: str,
            raw: bytes,
            carrier: RecoverySourceRecord = prepare_source,
            head: str | None = None,
        ) -> CallSubjectHead:
            source = _child(carrier, identity, schema_id, raw, head)
            children.append(source)
            return source.subject

        prompt = child(
            "native-prompt-artifact:" + artifact.digest(),
            "chiplog.native-recovery.prompt-artifact.v1",
            artifact.canonical_bytes(),
        )
        schema = child(
            "native-generated-schema:" + _sha(artifact.response_schema_json.encode()),
            "chiplog.native-recovery.generated-schema.v1",
            artifact.response_schema_json.encode(),
        )
        tools = tuple(
            child(
                "native-tool:" + tool.digest(),
                "chiplog.native-recovery.tool.v1",
                tool.canonical_bytes(),
            )
            for tool in artifact.tools
        )
        policy = child(
            "native-run-policy:" + run.policy.digest(),
            "chiplog.native-recovery.run-policy.v1",
            run.policy.canonical_bytes(),
        )
        scope_source = RecoverySourceRecord(
            owner="deployment_trust",
            subject=CallSubjectHead(
                subject_id=scope.scope_id,
                revision=Present(
                    head=binding.scope_head.head, fingerprint=binding.scope_head.fingerprint
                ),
            ),
            schema_id=scope.schema_id,
            canonical_record_bytes=binding.scope_bytes,
            selected_decision=CallSubjectHead(
                subject_id=binding.anchor.decision.identity,
                revision=Present(
                    head=binding.anchor.decision.head,
                    fingerprint=binding.anchor.decision.fingerprint,
                ),
            ),
            physical_record=CallSubjectHead(
                subject_id=binding.anchor.record.identity,
                revision=Present(
                    head=binding.anchor.record.head,
                    fingerprint=binding.anchor.record.fingerprint,
                ),
            ),
        )
        children.append(scope_source)
        child(
            scope.selected_admission_ref.identity,
            "chiplog.native-recovery.selected-admission.v1",
            scope.selected_admission_bytes,
            scope_source,
            scope.selected_admission_ref.head,
        )
        recipient = child(
            "native-recipient:" + _sha(scope.recipient.canonical_bytes()),
            "chiplog.native-recovery.recipient.v1",
            scope.recipient.canonical_bytes(),
            scope_source,
        )
        semantic = child(
            "native-context-policy:" + scope.disclosure_policy.ref.fingerprint,
            "chiplog.native-one-call-context-policy.v1",
            scope.disclosure_policy.canonical_source_bytes,
            scope_source,
            scope.disclosure_policy.ref.head,
        )
        child(
            profile.registry.registry_id,
            RECOVERY_FRONTIER_REGISTRY_SCHEMA,
            profile.registry.canonical_bytes(),
            profile_source,
            frontier_registry_reference(profile.registry).revision.head,
        )
        bindings = FrozenRunBindings(
            objective=run.prompt,
            requested_work=run.turns[-1].attempts[run.turns[-1].selector].request,
            prompt_artifact=prompt.revision,
            ordered_tool_specs=tuple(row.revision for row in tools),
            generated_schema=schema.revision,
            semantic_bindings=(semantic.revision,),
            recipient_effect_bindings=(recipient.revision,),
            authority_scope=scope_source.subject.revision,
            authority_mandate_heads=(),
            policy=policy.revision,
            no_retry_boundaries=(),
        )
        current_run_source = next(row for row in physical_sources if row.subject == _run_ref(run))
        turn_ref = child(
            turn.turn_id,
            "chiplog.native-recovery.turn.v1",
            turn.canonical_bytes(),
            current_run_source,
            turn.head,
        )
        captured_ref = subset.selected_facts.selected_seal.captured_run
        captured_run = next(row for row in lineage if _run_ref(row) == captured_ref)
        captured_turn = captured_run.turns[-1]
        captured_turn_ref = child(
            captured_turn.turn_id,
            "chiplog.native-recovery.turn.v1",
            captured_turn.canonical_bytes(),
            next(row for row in physical_sources if row.subject == captured_ref),
            captured_turn.head,
        )

        acceptance = call.acceptance
        disposition = call.disposition
        if not isinstance(acceptance, ConsequentialAcceptedCall):
            raise ValueError("accepted next-turn frontier lacks exact consequential acceptance")
        if isinstance(disposition, (SuccessResult, DefiniteFailureResult)):
            direct_result: SuccessResult | DefiniteFailureResult | None = disposition
            if closure is not None or reduction is not None:
                raise ValueError("direct terminal cannot carry recovery closure or reduction")
            resolver_sources: tuple[RecoverySourceRecord, ...] = ()
            call_result_ref = disposition.result
            obligation_heads: tuple[HeadMarker, ...] = ()
            reduction_heads: tuple[HeadMarker, ...] = ()
            evidence_heads: tuple[HeadMarker, ...]
            closure_records: tuple[OriginalRecoveredClosure, ...] = ()
            reduction_records: tuple[EvidenceReduction, ...] = ()
        elif isinstance(disposition, RecoveryRequiredResult):
            direct_result = None
            if (
                closure is None
                or reduction is None
                or not (
                    closure.original == disposition.obligation
                    and closure.terminal_disposition == disposition.terminal
                    and closure.recovered_outcome == disposition.recovered_outcome.outcome
                    and reduction.reduction_id == closure.reduction_id
                )
            ):
                raise ValueError(
                    "accepted next-turn frontier differs from selected closure/reduction"
                )
            call_result_ref = disposition.recovered_outcome.outcome
            obligation_heads = (
                disposition.terminal,
                disposition.recovered_outcome.outcome,
                closure.closure,
                closure.resolver_batch,
            )
            reduction_heads = (reduction.current_reduction,)
            evidence_heads = tuple(reduction.ordered_consumed_evidence)
            closure_records = (closure,)
            reduction_records = (reduction,)
            resolver_sources = tuple(
                row
                for row in physical_sources
                if row.schema_id.startswith("chiplog.loop.original-resolver-")
                or row.schema_id == "chiplog.loop.semantic-reducer-policy.v1"
            )
        else:
            raise ValueError("accepted next-turn frontier has an unsupported terminal")
        accepted_sources = tuple(subset.accepted_effect_sources)
        if direct_result is not None:
            from chiplog.capabilities.agent_loop.direct_terminal_contracts import (
                AcceptedDirectTerminalResultV1,
            )

            result_sources = tuple(
                row
                for row in physical_sources
                if row.schema_id == "chiplog.execution.accepted-direct-terminal-result.v1"
                and row.subject.revision == direct_result.result
            )
            if len(result_sources) != 1:
                raise ValueError("direct terminal result source is missing or ambiguous")
            direct_result_record = AcceptedDirectTerminalResultV1.model_validate_json(
                result_sources[0].canonical_record_bytes
            )
            evidence_heads = tuple(
                Present(head=item.head, fingerprint=item.fingerprint)
                for item in direct_result_record.classification.ordered_evidence
            )
        else:
            direct_result_record = None
        heads: dict[str, tuple[HeadMarker, ...]] = {
            "RUN": (_run_ref(run).revision,),
            "TURN": (turn_ref.revision,),
            "SEALED_RESPONSE": (reference(seal.response_seal_id, seal).revision,),
            "CALL": (
                acceptance.initialized,
                *acceptance.complete_acceptance_manifest,
                disposition.terminal,
                call_result_ref,
            ),
            "EFFECT": tuple(
                row.subject.revision
                for row in accepted_sources
                if row.schema_id.startswith("chiplog.effects.")
            ),
            "EVIDENCE": evidence_heads,
            "AUTHORITY": tuple(row.subject.revision for row in resolver_sources),
            "POLICY": (
                bindings.policy,
                semantic.revision,
                *(row.subject.revision for row in resolver_sources),
            ),
            "PROMPT": (prompt.revision,),
            "TOOL_SCHEMA": (schema.revision, *(row.revision for row in tools)),
            "RECIPIENT": (recipient.revision,),
            "SEMANTIC_BINDING": (semantic.revision,),
            "OBLIGATION": obligation_heads,
            "SEMANTIC_REDUCTION": reduction_heads,
        }
        absence = {"DELIVERY"}
        not_applicable = {"MANDATE", "EXECUTION_LINEAGE"}
        members = tuple(
            _member(
                row.family,
                call.original_call_id
                if row.family in {"CALL", "EFFECT", "EVIDENCE", "OBLIGATION", "SEMANTIC_REDUCTION"}
                else run.run_id,
                "PRESENT"
                if heads.get(row.family)
                else "ABSENT"
                if row.family in absence or row.family not in not_applicable
                else "NOT_APPLICABLE",
                heads.get(row.family)
                or (
                    (Absent(),)
                    if row.family in absence or row.family not in not_applicable
                    else (NotApplicable(),)
                ),
            )
            for row in profile.registry.ordered_rows
        )
        frontier = RecoveryFrontier(
            tenant_id=run.tenant,
            run_id=run.run_id,
            tenant_commit_sequence=subset.tenant_commit_sequence,
            registry=profile.registry,
            ordered_members=members,
            ordered_calls=(call,),
            canonicalization_version="chiplog.recovery.frontier.v1",
            fingerprint="0" * 64,
        )
        value = frontier.model_dump(mode="json")
        del value["fingerprint"]
        frontier = frontier.model_copy(
            update={"fingerprint": native_recovery_fingerprint("frontier", value)}
        )
        children.sort(
            key=lambda row: (row.schema_id, row.subject.subject_id, row.subject.revision.head)
        )
        cut = ExecutionRecoveryCut(
            tenant_id=run.tenant,
            database_id=runtime._database_identity[0],
            tenant_commit_sequence=subset.tenant_commit_sequence,
            materialization_commitment=subset.materialization_commitment,
            current_run=_run_ref(run),
            complete_ordered_run_lineage=lineage,
            complete_ordered_responses=(
                SealedResponseRecoveryView(
                    original_run=captured_ref,
                    original_turn=captured_turn_ref,
                    selected_seal=reference(seal.response_seal_id, seal),
                    seal=seal,
                    ordered_calls=(call,),
                    frontier_head=reference("recovery-frontier:" + run.run_id, frontier),
                ),
            ),
            frontier=frontier,
            current_bindings=bindings,
            original_closures=closure_records,
            current_reductions=reduction_records,
            complete_sources=(*physical_sources, *children),
            complete_causal_changes=(),
            complete_inventory_fingerprint="0" * 64,
        )
        value = cut.model_dump(mode="json")
        del value["complete_inventory_fingerprint"]
        return cut.model_copy(
            update={"complete_inventory_fingerprint": native_recovery_fingerprint("cut", value)}
        )

    def matches_current_held(
        self, runtime: CommonCliExecutionRuntime, supplied: ExecutionRecoveryCut
    ) -> bool:
        current = self.collect_held(runtime)
        return current is not None and current.canonical_bytes() == supplied.canonical_bytes()

    def authenticate_selected_held(
        self,
        runtime: CommonCliExecutionRuntime,
        evidence: RetainedRecoveryPublication,
        entry: dict[str, object],
    ) -> bool:
        from .r14_native_recovery_historical import authenticate_native_recovery_selected

        return authenticate_native_recovery_selected(self, runtime, evidence, entry)
