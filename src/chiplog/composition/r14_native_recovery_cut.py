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
    SelectedExecutionSuspension,
)
from chiplog.capabilities.agent_loop.native_recovery_profile_contracts import (
    SCHEMA,
    SCHEMA_V2,
    SCHEMA_V3,
    SCHEMA_V4,
    SCHEMA_V5,
    NativeOneCallRecoveryProfileV1,
    NativeOneCallRecoveryProfileV2,
    NativeOneCallRecoveryProfileV3,
    NativeOneCallRecoveryProfileV4,
    NativeOneCallRecoveryProfileV5,
    decode_native_one_call_recovery_profile,
    decode_native_one_call_recovery_profile_v2,
    decode_native_one_call_recovery_profile_v3,
    decode_native_one_call_recovery_profile_v4,
    decode_native_one_call_recovery_profile_v5,
    native_recovery_fingerprint,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    HeadMarker,
    NotApplicable,
    OriginalObligationBinding,
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
from chiplog.capabilities.effects.dispatch_authority_contracts import SelectedEffectHistoryMember
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
    R16NativeInventory,
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


_R16_PHYSICAL_SOURCE_SCHEMAS = frozenset(
    {
        "chiplog.effects.dispatch-record.v2",
        "chiplog.effects.dispatch-outcome-record.v2",
    }
)


def _physical_inventory_id(source: RecoverySourceRecord) -> str:
    """Return the inventory row ID under either source-reference convention.

    Native execution records put the physical row ID in ``subject_id``. R16
    effect records preserve their logical subject there and put the selected
    physical row ID in ``revision.head``.
    """
    if source.schema_id in _R16_PHYSICAL_SOURCE_SCHEMAS:
        if not isinstance(source.physical_record.revision, Present):
            raise ValueError("selected R16 source has no exact physical revision")
        return source.physical_record.revision.head
    return source.physical_record.subject_id


def _recovery_source_subject(source: RecoverySourceRecord) -> CallSubjectHead:
    """Keep authenticated logical child heads distinct from their physical carrier."""
    revision = source.subject.revision
    if not isinstance(revision, Present):
        raise ValueError("selected source has no exact logical revision")
    # R16 evidence and nested intents are logical children of an outcome or
    # dispatch row. Their fingerprints identify the child bytes, while
    # ``physical_record`` identifies the selected carrier bytes. The selected
    # reader authenticates that relation; replacing the child fingerprint with
    # the carrier digest would break the exact semantic reference.
    if source.subject.subject_id != source.physical_record.subject_id:
        return source.subject
    return CallSubjectHead(
        subject_id=source.subject.subject_id,
        revision=Present(
            head=revision.head,
            fingerprint=_sha(source.canonical_record_bytes),
        ),
    )


def _accepted_source_preimages(
    selected_sources: tuple[RecoverySourceRecord, ...],
    physical_sources: tuple[RecoverySourceRecord, ...],
) -> tuple[RecoverySourceRecord, ...]:
    accepted_source_rows: list[RecoverySourceRecord] = []
    for selected_source in selected_sources:
        matches = tuple(
            source
            for source in physical_sources
            if source.owner == selected_source.owner
            and source.schema_id == selected_source.schema_id
            and source.canonical_record_bytes == selected_source.canonical_record_bytes
            and source.subject == _recovery_source_subject(selected_source)
        )
        if len(matches) != 1:
            raise ValueError("selected effects source lacks one exact recovery preimage")
        accepted_source_rows.append(matches[0])
    return tuple(accepted_source_rows)


def _original_source_matches_selection(
    source: RecoverySourceRecord,
    record: PhysicalRecord | OwnerRecordBytes,
    decision: CallSubjectHead,
) -> bool:
    """Retain original resolver provenance only under its exact journal selection."""
    return (
        (source.owner, source.schema_id, source.canonical_record_bytes)
        == (record.owner, record.schema_id, record.canonical_bytes)
        and source.physical_record == _ref(record.record_id, record.canonical_bytes)
        and source.selected_decision.revision == decision.revision
    )


def _original_source_decisions(
    sources: tuple[RecoverySourceRecord, ...],
    selected: dict[str, tuple[PhysicalRecord | OwnerRecordBytes, CallSubjectHead]],
) -> dict[str, CallSubjectHead] | None:
    """Give every alias of an original physical row the same verified provenance."""
    decisions: dict[str, CallSubjectHead] = {}
    for source in sources:
        identity = _physical_inventory_id(source)
        selection = selected.get(identity)
        if selection is None or not _original_source_matches_selection(source, *selection):
            return None
        prior = decisions.setdefault(identity, source.selected_decision)
        if prior != source.selected_decision:
            return None
    return decisions


def _selected_source_revision(
    reference: CallSubjectHead | Present,
    sources: tuple[RecoverySourceRecord, ...],
) -> Present | None:
    """Resolve a logical marker through its exact selected source preimage."""
    revision = reference if isinstance(reference, Present) else reference.revision
    if not isinstance(revision, Present):
        return None
    subject_id = reference.subject_id if isinstance(reference, CallSubjectHead) else None
    relevant = tuple(
        source.subject.revision
        for source in sources
        if (subject_id is None or source.subject.subject_id == subject_id)
        and isinstance(source.subject.revision, Present)
        and source.subject.revision.head == revision.head
    )
    if any(candidate != revision for candidate in relevant):
        return None
    matches = tuple(
        source.subject.revision
        for source in sources
        if (subject_id is None or source.subject.subject_id == subject_id)
        and isinstance(source.subject.revision, Present)
        and source.subject.revision == revision
    )
    if not matches or any(match != matches[0] for match in matches[1:]):
        return None
    return matches[0]


def _selected_external_intent_preimage(
    reference: CallSubjectHead,
    expected_dispatch: object,
    selected_dispatch_sources: tuple[RecoverySourceRecord, ...],
    selected_dispatch_members: tuple[object, ...],
) -> RecoverySourceRecord | None:
    """Project the exact nested intent from its authenticated selected carrier.

    ``selected_dispatch_sources`` must come from the mounted R16 history reader.
    The intent bytes are a logical source preimage; the dispatch row remains its
    independently selected physical carrier.
    """
    from chiplog.capabilities.effects.dispatch_v2 import DispatchRecordV2
    from chiplog.capabilities.effects.dispatch_v2 import reference as effects_reference

    if type(expected_dispatch) is not DispatchRecordV2:
        return None
    raw_dispatch = expected_dispatch.canonical_bytes()
    candidates = tuple(
        source
        for source in selected_dispatch_sources
        if source.owner == "effects"
        and source.schema_id == expected_dispatch.schema_id
        and source.canonical_record_bytes == raw_dispatch
    )
    if len(candidates) != 1:
        return None
    carrier = candidates[0]
    try:
        selected = DispatchRecordV2.model_validate_json(carrier.canonical_record_bytes)
        intent = selected.snapshot.intent
        raw_intent = intent.canonical_bytes()
        intent_reference = effects_reference(intent.intent_id, raw_intent)
        selected_record = CallSubjectHead(
            subject_id=selected.record.subject_id,
            revision=Present(
                head=selected.record.head,
                fingerprint=selected.record.fingerprint,
            ),
        )
    except TypeError, ValueError:
        return None
    expected_reference = CallSubjectHead(
        subject_id=intent_reference.subject_id,
        revision=Present(
            head=intent_reference.head,
            fingerprint=intent_reference.fingerprint,
        ),
    )
    members = tuple(
        member
        for member in selected_dispatch_members
        if type(member) is SelectedEffectHistoryMember
        and member.record_bytes == raw_dispatch
        and member.record.subject_id == selected.record.subject_id
        and member.record.head == selected.record.head
        and member.record.fingerprint == selected.record.fingerprint
        and member.intent.subject_id == selected.snapshot.intent.intent_id
        and member.intent.fingerprint == intent_reference.fingerprint
        and member.intent.head == intent_reference.head
        and member.selected_decision.subject_id == carrier.selected_decision.subject_id
        and member.selected_decision.head == carrier.selected_decision.revision.head
        and member.selected_decision.fingerprint == carrier.selected_decision.revision.fingerprint
    )
    if len(members) != 1:
        return None
    member = members[0]
    if (
        selected.canonical_bytes() != raw_dispatch
        or carrier.subject != selected_record
        or carrier.physical_record != selected_record
        or not isinstance(carrier.selected_decision.revision, Present)
        or reference != expected_reference
    ):
        return None
    return RecoverySourceRecord(
        owner="effects",
        subject=reference,
        schema_id=intent.schema_id,
        canonical_record_bytes=raw_intent,
        selected_decision=CallSubjectHead(
            subject_id=member.selected_decision.subject_id,
            revision=Present(
                head=member.selected_decision.head,
                fingerprint=member.selected_decision.fingerprint,
            ),
        ),
        physical_record=CallSubjectHead(
            subject_id=member.record.subject_id,
            revision=Present(head=member.record.head, fingerprint=member.record.fingerprint),
        ),
    )


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


def _selected_no_retry_sources(
    references: tuple[CallSubjectHead, ...],
    sources: tuple[RecoverySourceRecord, ...] | list[RecoverySourceRecord],
    active_run_references: tuple[CallSubjectHead, ...],
) -> bool:
    """Require each inherited no-retry boundary to name one selected source.

    ``sources`` must already have passed mounted physical inventory and
    selected-decision reconciliation in ``_collect_held``. This helper adds no
    authority; it rejects missing, duplicated, or still-active references.
    """
    encoded_references = tuple(item.canonical_bytes() for item in references)
    if len(set(encoded_references)) != len(encoded_references):
        return False
    active = {item.canonical_bytes() for item in active_run_references}
    for no_retry_ref in references:
        if no_retry_ref.canonical_bytes() in active:
            return False
        matches = [source for source in sources if source.subject == no_retry_ref]
        if len(matches) != 1:
            return False
        source = matches[0]
        if (
            not isinstance(source.subject.revision, Present)
            or not isinstance(source.physical_record.revision, Present)
            or not source.canonical_record_bytes
        ):
            return False
    return True


def classify_native_occurrences(
    occurrences: tuple[H1RawInventoryItem, ...] | list[H1RawInventoryItem],
    decoded: dict[str, H1DecodedInventoryItem],
    known_sources: dict[str, RecoverySourceRecord],
    scope: NativeOneCallContextScopeV1,
    authenticated_commands: dict[str, tuple[str, str, bytes]] | None = None,
    selected_admission_records: dict[str, tuple[str, str, bytes]] | None = None,
) -> bool:
    """Decode first; only the independently reopened original custody is context.

    EVIDENCE absence means absence of recovery evidence. Original authenticated
    CLI custody evidence is explicitly excluded by exact token/profile equality;
    arbitrary source identity or an empty inbox cannot prove that exclusion.
    """
    from chiplog.capabilities.effects.dispatch_outcome_contracts import (
        DispatchOutcomePreparationV2,
        DispatchOutcomeRecordV2,
    )
    from chiplog.capabilities.effects.dispatch_v2 import DispatchPreparationV2, DispatchRecordV2
    from chiplog.composition.r16_dispatch_outbox import (
        CONSUME_SCHEMA,
        CONSUMPTION_SCHEMA,
        ConsumeRequest,
        ConsumptionRecord,
    )
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
        authenticated_command = (authenticated_commands or {}).get(occurrence.locator)
        if authenticated_command is not None:
            if authenticated_command != (occurrence.owner, occurrence.schema, occurrence.raw):
                return False
            continue
        selected_admission_record = (selected_admission_records or {}).get(
            occurrence.record_id or ""
        )
        if selected_admission_record is not None and occurrence.surface == "PHYSICAL":
            if selected_admission_record != (occurrence.owner, occurrence.schema, occurrence.raw):
                return False
            continue
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
                # R16's independently authenticated selected history carries
                # its owner command as well as the physical result. Admit only
                # that exact command/result pair under the same decision.
                parts = occurrence.locator.split("/")
                selected_id = parts[1] if len(parts) == 4 and parts[0] == "selected" else None
                if (
                    occurrence.surface == "OWNER_COMMAND"
                    and occurrence.owner == "effects"
                    and selected_id is not None
                    and parts[2:] in (["command", "0"], ["command", "1"])
                    and occurrence.schema
                    in {
                        "chiplog.effects.dispatch-preparation.v2",
                        "chiplog.effects.dispatch-outcome-preparation.v2",
                    }
                ):
                    matches = [
                        source
                        for source in known_sources.values()
                        if source.owner == "effects"
                        and source.schema_id in _R16_PHYSICAL_SOURCE_SCHEMAS
                        and isinstance(source.selected_decision.revision, Present)
                        and source.selected_decision.revision.head == selected_id
                    ]
                    try:
                        if occurrence.schema == "chiplog.effects.dispatch-preparation.v2":
                            preparation = DispatchPreparationV2.model_validate_json(occurrence.raw)
                            matched = any(
                                source.schema_id == "chiplog.effects.dispatch-record.v2"
                                and preparation.command.canonical_bytes()
                                == DispatchRecordV2.model_validate_json(
                                    source.canonical_record_bytes
                                ).command.canonical_bytes()
                                for source in matches
                            )
                        else:
                            preparation = DispatchOutcomePreparationV2.model_validate_json(
                                occurrence.raw
                            )
                            matched = any(
                                source.schema_id == "chiplog.effects.dispatch-outcome-record.v2"
                                and preparation.command.canonical_bytes()
                                == DispatchOutcomeRecordV2.model_validate_json(
                                    source.canonical_record_bytes
                                ).command.canonical_bytes()
                                for source in matches
                            )
                    except TypeError, ValueError:
                        matched = False
                    if matched:
                        continue
                if (
                    occurrence.surface == "OWNER_COMMAND"
                    and occurrence.owner == "broker_dispatch"
                    and occurrence.schema == CONSUME_SCHEMA
                    and selected_id is not None
                    and parts[2:] == ["command", "0"]
                ):
                    try:
                        request = ConsumeRequest.model_validate_json(occurrence.raw)
                        matched = False
                        for source in known_sources.values():
                            if (
                                source.owner != "broker_dispatch"
                                or source.schema_id != CONSUMPTION_SCHEMA
                                or not isinstance(source.selected_decision.revision, Present)
                                or source.selected_decision.revision.head != selected_id
                            ):
                                continue
                            source_record = ConsumptionRecord.model_validate_json(
                                source.canonical_record_bytes
                            )
                            if request.canonical_bytes() == source_record.request.canonical_bytes():
                                matched = True
                                break
                    except TypeError, ValueError:
                        matched = False
                if matched:
                    continue
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


def _current_successor_ingress_occurrences(
    runtime: CommonCliExecutionRuntime,
    inventory: R16NativeInventory,
    driver_request,
    admitted,
) -> tuple[dict[str, tuple[str, str, bytes]], dict[str, tuple[str, str, bytes]]] | None:
    """Return only the exact selected R17 chain for this current CLI request.

    The owner and physical views come from the same captured inventory. The
    selected ingress reducer is replayed over that captured owner prefix, and
    runtime selection is repeated against the supplied request before any
    matching ingress occurrence is exempted from historical-scope comparison.
    """
    from chiplog.composition.r17_ingress_history import (
        is_custody_ingress,
        validate_selected_ingress,
    )
    from chiplog.platform._owner_publication_contracts import SingleOwnerBatch
    from chiplog.platform.ingress_authenticated_contracts import AuthenticatedCustodyRecord
    from chiplog.platform.ingress_custody_records import canonical
    from chiplog.platform.owner_decision_journal import snapshot_from_captured_entries

    try:
        runtime._authority_gate().require_held()
        runtime._check_database_identity()
        current = runtime._selected_input(
            driver_request, runtime._require_dispatch_resources().observe()
        )
        if current != admitted:
            return None
        owner_snapshot = snapshot_from_captured_entries(inventory.owner_entries, inventory.tenant)
        if owner_snapshot != runtime._owner_decisions().snapshot():
            return None
        records, _ = validate_selected_ingress(owner_snapshot)
        decisions = tuple(
            decision for decision in owner_snapshot.decisions if is_custody_ingress(decision)
        )
        if len(records) != len(decisions):
            return None

        admitted_record = AuthenticatedCustodyRecord.model_validate_json(
            current.canonical_custody_record
        )
        selected_custody_bytes = json.dumps(
            admitted_record.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
        if (
            current.tenant_id != inventory.tenant
            or selected_custody_bytes != current.canonical_custody_record
            or admitted_record.command.token.token_id
            != driver_request.identity.original_ingress_identity.command_id
            or admitted_record.command.profile.tenant_id != driver_request.identity.tenant_id
            or admitted_record.command.profile.database_id != driver_request.identity.database_id
            or admitted_record.inbox.authentication_request_fingerprint
            != driver_request.identity.original_ingress_request_fingerprint
            or admitted_record.inbox.raw_bytes != current.raw_input_bytes
        ):
            return None

        matching: list[tuple[object, object]] = []
        for decision, record in zip(decisions, records, strict=True):
            if record.command.token.token_id == admitted_record.command.token.token_id:
                matching.append((decision, record))
        final = [
            (decision, record)
            for decision, record in matching
            if isinstance(record, AuthenticatedCustodyRecord)
            and canonical(record) == current.canonical_custody_record
            and decision.decision_id == current.selected_decision.subject_id
        ]
        if len(final) != 1 or len(matching) != 3:
            return None
        if tuple(record.command.operation for _, record in matching) != (
            "ingress.allocate_receipt_token",
            "ingress.stage_raw_bytes",
            "ingress.publish_custody_successor",
        ):
            return None
        physical_by_id = {row.record_id: row for row in inventory.records}
        commands: dict[str, tuple[str, str, bytes]] = {}
        physical: dict[str, tuple[str, str, bytes]] = {}
        for decision, _record in matching:
            batch = decision.prepared.request
            if not isinstance(batch, SingleOwnerBatch) or not batch.complete_records:
                return None
            commands[f"selected/{decision.decision_id}/command/0"] = (
                batch.command.owner,
                batch.command.schema_id,
                batch.command.canonical_bytes,
            )
            for member in batch.complete_records:
                selected_row = physical_by_id.get(member.record_id)
                expected = (member.owner, member.schema_id, member.canonical_bytes)
                if selected_row is None or (
                    selected_row.owner,
                    selected_row.schema,
                    selected_row.raw,
                ) != expected:
                    return None
                previous = physical.setdefault(member.record_id, expected)
                if previous != expected:
                    return None
        return commands, physical
    except OSError, RuntimeError, TypeError, ValueError, KeyError, AttributeError:
        return None


def _selected_accepted_open_recovery_preimages(
    runtime: CommonCliExecutionRuntime,
    inventory: R16NativeInventory,
    expected_obligation: OriginalObligationBinding,
) -> tuple[tuple[RecoverySourceRecord, ...], frozenset[str]] | None:
    """Retain authenticated opening sources across the obligation's closure."""
    from chiplog.composition.r14_accepted_open_accounting_records import (
        ACCEPTED_OPEN_RECOVERY_OPERATION,
        accepted_open_recovery_command,
        retained_accepted_open_recovery,
    )
    from chiplog.composition.r14_original_obligation_sources import (
        accepted_open_recovery_sources,
    )

    try:
        selected = [
            (decision_id, raw, json.loads(raw))
            for decision_id, _, raw in runtime._loop_decisions().entries()
            if (entry := json.loads(raw)).get("kind") == "DECIDED"
            and entry.get("operation_kind") == ACCEPTED_OPEN_RECOVERY_OPERATION
        ]
        if len(selected) != 1:
            return None
        decision_id, decision_raw, entry = selected[0]
        evidence = retained_accepted_open_recovery(entry)
        command = accepted_open_recovery_command(evidence)
        if (
            evidence.result.opening.original != expected_obligation
            or runtime._publication(entry) != command
        ):
            return None
        sources = accepted_open_recovery_sources(
            evidence,
            selected_decision_id=decision_id,
            selected_decision_bytes=decision_raw,
        )
        physical_by_id = {row.record_id: row for row in inventory.records}
        for expected_record in command.records:
            physical = physical_by_id.get(expected_record.record_id)
            if physical is None or (
                physical.owner,
                physical.schema,
                physical.raw,
            ) != (
                expected_record.owner,
                expected_record.schema_id,
                expected_record.canonical_bytes,
            ):
                return None
        return sources, frozenset(record.record_id for record in command.records)
    except OSError, RuntimeError, TypeError, ValueError, KeyError, AttributeError:
        return None


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


def _selected_reduction_sources(runtime, selected_entries) -> tuple[RecoverySourceRecord, ...]:
    from chiplog.composition import r14_loop_semantic_reduction_publication as publication

    return tuple(
        publication._selected_value(runtime, entry)[1].source for entry in selected_entries
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
                result = self._collect_held(runtime)
                return result if isinstance(result, ExecutionRecoveryCut) else None
        except OSError, RuntimeError, TypeError, ValueError, R16InventoryIntegrityError:
            return None

    def collect_for_resume_held(
        self, runtime: CommonCliExecutionRuntime
    ) -> tuple[ExecutionRecoveryCut, SelectedExecutionSuspension] | None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("native recovery cut requires the mounted CLI runtime")
        runtime._authority_gate().require_held()
        try:
            with join_workspace_snapshot(runtime._database):
                result = self._collect_held(runtime, allow_suspended=True)
            if (
                isinstance(result, tuple)
                and len(result) == 2
                and isinstance(result[1], SelectedExecutionSuspension)
            ):
                return result
        except OSError, RuntimeError, TypeError, ValueError, R16InventoryIntegrityError:
            return None
        return None

    def collect_for_successor_held(
        self,
        runtime: CommonCliExecutionRuntime,
        driver_request,
        admitted,
    ) -> tuple[ExecutionRecoveryCut, SelectedExecutionSuspension] | None:
        """Collect a suspended cut with the exact current selected CLI admission."""
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("native recovery cut requires the mounted CLI runtime")
        runtime._authority_gate().require_held()
        try:
            with join_workspace_snapshot(runtime._database):
                result = self._collect_held(
                    runtime,
                    allow_suspended=True,
                    successor_input=(driver_request, admitted),
                )
            if (
                isinstance(result, tuple)
                and len(result) == 2
                and isinstance(result[1], SelectedExecutionSuspension)
            ):
                return result
        except OSError, RuntimeError, TypeError, ValueError, R16InventoryIntegrityError:
            return None
        return None

    def _collect_held(
        self,
        runtime: CommonCliExecutionRuntime,
        *,
        allow_suspended: bool = False,
        successor_input=None,
    ) -> ExecutionRecoveryCut | tuple[ExecutionRecoveryCut, SelectedExecutionSuspension] | None:
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
        if profile_source is None or profile_source.schema_id not in (
            SCHEMA, SCHEMA_V2, SCHEMA_V3, SCHEMA_V4, SCHEMA_V5
        ):
            return None
        profile: (
            NativeOneCallRecoveryProfileV1
            | NativeOneCallRecoveryProfileV2
            | NativeOneCallRecoveryProfileV3
            | NativeOneCallRecoveryProfileV4
            | NativeOneCallRecoveryProfileV5
        )
        if profile_source.schema_id == SCHEMA:
            profile = decode_native_one_call_recovery_profile(profile_source.canonical_record_bytes)
        elif profile_source.schema_id == SCHEMA_V2:
            profile = decode_native_one_call_recovery_profile_v2(
                profile_source.canonical_record_bytes
            )
        elif profile_source.schema_id == SCHEMA_V3:
            profile = decode_native_one_call_recovery_profile_v3(
                profile_source.canonical_record_bytes
            )
        elif profile_source.schema_id == SCHEMA_V4:
            profile = decode_native_one_call_recovery_profile_v4(
                profile_source.canonical_record_bytes
            )
        else:
            profile = decode_native_one_call_recovery_profile_v5(
                profile_source.canonical_record_bytes
            )
        subset = R14NativeRecoverySourceSubsetReader(runtime).collect()
        if subset is None:
            return None
        accepted_inputs: (
            tuple[
                TerminalCallFrontier,
                OriginalRecoveredClosure | None,
                EvidenceReduction | None,
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
            if direct_terminal_claimed and direct_terminal_inputs is None:
                return None
        if not direct_terminal_claimed and (
            subset.accepted_closed_outcome is not None or subset.accepted_open_outcome is not None
        ):
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
        if not latest or latest[-1] != current_scope:
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
        suspension_suffix = bool(lineage and lineage[-1].state == "SUSPENDED")
        # Cancellation accounting retains its pre-suspension source. Accepted
        # facts are freshly read against the current authenticated Run history.
        source_lineage = (
            lineage[:-1]
            if suspension_suffix and subset.selected_accounting is not None
            else lineage
        )
        if (
            not lineage
            or tuple(_run_ref(row) for row in source_lineage)
            != facts.source.target_run_lineage
        ):
            return None
        run = lineage[-1]
        selected_suspension = None
        selected_resume = None
        if run.state == "SUSPENDED":
            if not allow_suspended:
                return None
            selected_suspension = self._selected_suspension_for_run(runtime, run)
            if selected_suspension is None or (
                selected_suspension.pair.suspended_run != _run_ref(run)
                or run.suspension_baseline != selected_suspension.pair.baseline
                or selected_suspension.baseline.activation_blocking_predicates
                or selected_suspension.baseline.original_obligations
                != run.original_obligations
            ):
                return None
        elif run.state != "ACTIVE":
            return None
        elif run.suspension_baseline is not None:
            selected_resume = self._selected_resume_for_run(runtime, run, accepted_inputs)
            if selected_resume is None:
                return None
        if (
            selected_suspension is None
            and subset.accepted_closed_outcome is None
            and subset.accepted_open_outcome is None
        ):
            captured = scope_service._sources.capture_selected_current(
                scope.selected_resource_observation_ref,
                scope.admitted_authentication,
                scope.authenticated_cli_ref,
            )
            if captured is None or captured.verified.recipient != scope.recipient:
                return None
        # A selected suspension or selected SEND recovery authenticates the
        # frozen native context; it does not authorize a new external effect.
        # Such a SEND may consume or rotate the H1 observation, so only these
        # recovery cuts may rely on the immutable historical scope binding.
        # The current CLI identity and no-scheduler inventory remain live.
        if (
            run.worker_session != scope.worker_session_id
            or run.root_binding != "NOT_APPLICABLE"
            or (
                selected_suspension is None
                and selected_resume is None
                and run.original_obligations
            )
            or (selected_suspension is None and run.no_retry_references)
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
        owner_journal = runtime._owner_decisions().snapshot()
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
            for decision in owner_journal.decisions:
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
            native_sources.setdefault(_physical_inventory_id(source), []).append(source)
        if suspension_suffix:
            suspended_record = lineage[-1]
            selection = selected.get(suspended_record.head)
            raw = suspended_record.canonical_bytes()
            if selection is None or (
                selection[0].owner,
                selection[0].schema_id,
                selection[0].canonical_bytes,
            ) != ("agent_loop", suspended_record.schema_id, raw):
                return None
            native_sources.setdefault(suspended_record.head, []).append(
                RecoverySourceRecord(
                    owner="agent_loop",
                    subject=_ref(suspended_record.run_id, raw, suspended_record.head),
                    schema_id=suspended_record.schema_id,
                    canonical_record_bytes=raw,
                    selected_decision=selection[1],
                    physical_record=_ref(suspended_record.head, raw),
                )
            )
        additional_sources = list(subset.accepted_effect_sources)
        exact_original_sources = (
            tuple(source for source in accepted_inputs[3] if source.owner == "agent_loop")
            if accepted_inputs is not None else ()
        )
        original_decisions = _original_source_decisions(exact_original_sources, selected)
        if original_decisions is None:
            return None
        if accepted_inputs is not None:
            additional_sources.extend(accepted_inputs[3])
            selected_open_sources = _selected_accepted_open_recovery_preimages(
                runtime, inventory, accepted_inputs[0].disposition.obligation
            )
            if selected_open_sources is None:
                return None
            opening_sources, accepted_open_ids = selected_open_sources
            additional_sources.extend(opening_sources)
        else:
            accepted_open_ids = frozenset()
        if direct_terminal_inputs is not None:
            additional_sources.extend(direct_terminal_inputs[1])
        if (
            subset.accepted_closed_outcome is not None or subset.accepted_open_outcome is not None
        ) and snapshot is not None:
            # The accepted execution history reader authenticated these exact
            # run preimages. Bind them to the selected owner decision already
            # reconciled with the physical inventory before classifying them.
            for record in snapshot.records:
                if not isinstance(record, ExecutionRunRecord):
                    continue
                selection = selected.get(record.head)
                raw = record.canonical_bytes()
                if selection is None or (
                    selection[0].owner,
                    selection[0].schema_id,
                    selection[0].canonical_bytes,
                ) != ("agent_loop", record.schema_id, raw):
                    return None
                additional_sources.append(
                    RecoverySourceRecord(
                        owner="agent_loop",
                        subject=_ref(record.run_id, raw, record.head),
                        schema_id=record.schema_id,
                        canonical_record_bytes=raw,
                        selected_decision=selection[1],
                        physical_record=_ref(record.head, raw),
                    )
                )
        if subset.accepted_closed_outcome is not None or subset.accepted_open_outcome is not None:
            from chiplog.composition.r14_fanout_contracts import INITIALIZED_SCHEMA, SEAL_SCHEMA

            for retained_seal in subset.authenticated_seals:
                fanout = retained_seal.proposal.fan_out
                seal = fanout.response_seal
                seal_raw = seal.canonical_bytes()
                physical_members = [
                    ("record:" + hashlib.sha256(seal_raw).hexdigest(), SEAL_SCHEMA, seal_raw)
                ]
                physical_members.extend(
                    (
                        "record:" + hashlib.sha256(record.canonical_bytes()).hexdigest(),
                        INITIALIZED_SCHEMA,
                        record.canonical_bytes(),
                    )
                    for record in fanout.initialized_records
                )
                for record_id, schema_id, raw in physical_members:
                    selection = selected.get(record_id)
                    if selection is None or (
                        selection[0].owner,
                        selection[0].schema_id,
                        selection[0].canonical_bytes,
                    ) != ("agent_loop", schema_id, raw):
                        return None
                    additional_sources.append(
                        RecoverySourceRecord(
                            owner="agent_loop",
                            subject=_ref(record_id, raw),
                            schema_id=schema_id,
                            canonical_record_bytes=raw,
                            selected_decision=selection[1],
                            physical_record=_ref(record_id, raw),
                        )
                    )
        if subset.accepted_closed_outcome is not None:
            # validate_consumptions authenticates the whole owner snapshot,
            # including the exact SEND child and retained ticket. Retain only
            # the consumption for this accepted intent as a recovery source.
            from chiplog.capabilities.effects.dispatch_v2 import reference as effects_reference
            from chiplog.composition.r16_dispatch_outbox import (
                CONSUME_SCHEMA,
                CONSUMPTION_SCHEMA,
                ConsumeRequest,
                ConsumptionRecord,
                validate_consumptions,
            )

            validate_consumptions(runtime, owner_journal)
            accepted_intent = subset.accepted_closed_outcome.latest_outcome.snapshot.intent
            accepted_intent_ref = effects_reference(
                accepted_intent.intent_id, accepted_intent.canonical_bytes()
            )
            owner_raw = {key: raw for key, _, raw in inventory.owner_entries}
            for decision in owner_journal.decisions:
                batch = decision.prepared.request
                if batch.operation != "dispatch.consume_effect_send":
                    continue
                request = ConsumeRequest.model_validate_json(batch.command.canonical_bytes)
                if request.intent != accepted_intent_ref:
                    continue
                if (
                    batch.command.schema_id != CONSUME_SCHEMA
                    or len(batch.complete_records) != 1
                    or batch.complete_records[0].schema_id != CONSUMPTION_SCHEMA
                ):
                    return None
                row = batch.complete_records[0]
                record = ConsumptionRecord.model_validate_json(row.canonical_bytes)
                if record.request != request or record.canonical_bytes() != row.canonical_bytes:
                    return None
                raw_decision = owner_raw.get(decision.decision_id)
                if raw_decision is None:
                    return None
                selected_ref = _ref(
                    decision.prepared.request.identity.command_id,
                    raw_decision,
                    decision.decision_id,
                )
                additional_sources.append(
                    RecoverySourceRecord(
                        owner="broker_dispatch",
                        subject=_ref(row.record_id, row.canonical_bytes),
                        schema_id=CONSUMPTION_SCHEMA,
                        canonical_record_bytes=row.canonical_bytes,
                        selected_decision=selected_ref,
                        physical_record=_ref(row.record_id, row.canonical_bytes),
                    )
                )
        for source in additional_sources:
            identity = _physical_inventory_id(source)
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
        from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2

        outcome_record_sources: dict[tuple[str, bytes], RecoverySourceRecord] = {}
        for row in inventory.records:
            selected_record, selected_decision = selected[row.record_id]
            source_decision = original_decisions.get(row.record_id, selected_decision)
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
            subject = _ref(row.record_id, row.raw)
            physical_source_index = len(sources)
            sources.append(
                RecoverySourceRecord(
                    owner=row.owner,
                    subject=subject,
                    schema_id=row.schema,
                    canonical_record_bytes=row.raw,
                    selected_decision=source_decision,
                    physical_record=_ref(row.record_id, row.raw),
                )
            )
            # One authenticated physical R16 row can support more than one
            # logical head in the selected dispatch/evidence source chains.
            # Rebind each logical preimage to the single physical selection
            # independently established by this inventory pass.
            for native_source in native_sources.get(row.record_id, ()):
                rebound_source = native_source.model_copy(
                    update={
                        "subject": _recovery_source_subject(native_source),
                        "selected_decision": (
                            native_source.selected_decision
                            if native_source.schema_id in _R16_PHYSICAL_SOURCE_SCHEMAS
                            else source_decision
                        ),
                        "physical_record": _ref(row.record_id, row.raw),
                    }
                )
                if (
                    native_source in exact_original_sources
                    and rebound_source.subject == sources[physical_source_index].subject
                ):
                    # One role, one preimage: retain the resolver's exact
                    # provenance in place of the generic physical projection.
                    sources[physical_source_index] = rebound_source
                elif rebound_source not in sources:
                    sources.append(rebound_source)
                if rebound_source.schema_id == "chiplog.effects.dispatch-outcome-record.v2":
                    outcome_record = DispatchOutcomeRecordV2.model_validate_json(row.raw)
                    if (
                        (outcome_record.kind, outcome_record.command.operation)
                        in {
                            ("EVIDENCE_RECORDED", "APPEND_EVIDENCE"),
                            ("RECONCILED", "RESOLVE_OBLIGATION"),
                        }
                    ):
                        marker = outcome_record.record
                        record_source = rebound_source.model_copy(
                            update={
                                "subject": CallSubjectHead(
                                    subject_id=marker.subject_id,
                                    revision=Present(
                                        head=marker.head, fingerprint=marker.fingerprint
                                    ),
                                )
                            }
                        )
                        key = (record_source.schema_id, record_source.subject.canonical_bytes())
                        previous = outcome_record_sources.setdefault(key, record_source)
                        if previous != record_source:
                            return None
        by_id = {row.physical_record.subject_id: row for row in sources}
        # Immutable reductions name the R16 record's body-domain marker. Keep
        # it alongside the raw carrier and evidence child, without changing
        # the physical representative used by occurrence classification.
        by_subject = {
            (source.schema_id, source.subject.canonical_bytes()): source for source in sources
        }
        for key, record_source in outcome_record_sources.items():
            previous = by_subject.get(key)
            if previous is not None and previous != record_source:
                return None
            if previous is None:
                sources.append(record_source)
        if by_id.get(profile_source.physical_record.subject_id) != profile_source:
            return None
        known = set(native_sources) | {profile_source.physical_record.subject_id}
        known.update(accepted_open_ids)
        provenance_suspension = (
            selected_suspension
            if selected_suspension is not None
            else selected_resume.request.original_suspension
            if selected_resume is not None
            else None
        )
        if provenance_suspension is not None:
            from .r14_execution_suspension import BASELINE_SCHEMA, PAIR_SCHEMA

            if (
                provenance_suspension.baseline.bindings.no_retry_boundaries
                != tuple(reference.revision for reference in run.no_retry_references)
                or not _selected_no_retry_sources(
                    run.no_retry_references,
                    sources,
                    tuple(_run_ref(item) for item in lineage if item.state == "ACTIVE"),
                )
            ):
                return None

            selected_members = (
                (
                    provenance_suspension.baseline.baseline_id,
                    BASELINE_SCHEMA,
                    provenance_suspension.baseline.canonical_bytes(),
                ),
                (
                    provenance_suspension.selected_pair.subject_id,
                    PAIR_SCHEMA,
                    provenance_suspension.canonical_pair_bytes,
                ),
            )
            for record_id, schema_id, raw in selected_members:
                selection = selected.get(record_id)
                if selection is None or (
                    selection[0].owner,
                    selection[0].schema_id,
                    selection[0].canonical_bytes,
                ) != ("agent_loop", schema_id, raw):
                    return None
                native_sources.setdefault(record_id, []).append(
                    RecoverySourceRecord(
                        owner="agent_loop",
                        subject=_ref(record_id, raw),
                        schema_id=schema_id,
                        canonical_record_bytes=raw,
                        selected_decision=provenance_suspension.selected_decision,
                        physical_record=_ref(record_id, raw),
                    )
                )
                known.add(record_id)
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
        authenticated_commands: dict[str, tuple[str, str, bytes]] = {}
        authenticated_call_publication_ids: set[str] = set()
        if selected_suspension is not None:
            suspension_entry = next(
                (
                    json.loads(raw)
                    for _, _, raw in runtime._loop_decisions().entries()
                    if json.loads(raw).get("kind") == "DECIDED"
                    and json.loads(raw).get("operation_kind")
                    == "agent_loop.execution-suspension.v1"
                    and json.loads(raw).get("operation_id")
                    == selected_suspension.pair.suspension_command_id
                ),
                None,
            )
            if suspension_entry is None:
                return None
            from .r14_execution_suspension import _retained as retained_suspension

            suspension_evidence = retained_suspension(suspension_entry)
            selected_suspension_command = suspension_evidence.owner_call
            selected_decision_id = selected_suspension.selected_decision.revision.head
            authenticated_commands[
                f"selected/{selected_decision_id}/command/0"
            ] = (
                selected_suspension_command.callee.owner_id,
                selected_suspension_command.schema_id,
                selected_suspension_command.canonical_payload,
            )
        from chiplog.composition.r14_native_accounting_records import (
            NATIVE_SEALED_ACCOUNTING_V3_PUBLICATION_OPERATION,
            NATIVE_SEALED_ACCOUNTING_V3_RECORD_SCHEMA,
            sealed_accounting_v3_command,
            validate_retained_sealed_accounting_v3_publication,
        )
        from chiplog.composition.r14_native_sealed_accounting_v3_history import (
            retained_selected_native_sealed_accounting_v3,
        )

        v3_selections = []
        for _, _, raw in runtime._loop_decisions().entries():
            entry = json.loads(raw)
            if (
                entry.get("kind") != "DECIDED"
                or entry.get("operation_kind")
                != NATIVE_SEALED_ACCOUNTING_V3_PUBLICATION_OPERATION
            ):
                continue
            evidence = retained_selected_native_sealed_accounting_v3(entry)
            if (
                evidence.request.selected_response.subject_id
                == seals[0].proposal.fan_out.response_seal.response_seal_id
                and evidence.request.selected_response.revision.fingerprint
                == seals[0].proposal.fan_out.response_seal.digest()
            ):
                command = sealed_accounting_v3_command(evidence)
                record = validate_retained_sealed_accounting_v3_publication(evidence)
                if (
                    runtime._publication(entry) != command
                    or entry.get("operation_id") != command.idempotency_key
                    or entry.get("native_sealed_accounting_v3_envelope")
                    is None
                    or record.record_id != command.records[0].record_id
                ):
                    return None
                v3_selections.append(evidence)
        if len(v3_selections) > 1:
            return None
        for evidence in v3_selections:
            record = evidence.record
            raw = record.canonical_bytes()
            selection = selected.get(record.record_id)
            if selection is None or (
                selection[0].owner,
                selection[0].schema_id,
                selection[0].canonical_bytes,
            ) != (
                "agent_loop",
                NATIVE_SEALED_ACCOUNTING_V3_RECORD_SCHEMA,
                raw,
            ):
                return None
            native_sources.setdefault(record.record_id, []).append(
                RecoverySourceRecord(
                    owner="agent_loop",
                    subject=_ref(record.record_id, raw),
                    schema_id=NATIVE_SEALED_ACCOUNTING_V3_RECORD_SCHEMA,
                    canonical_record_bytes=raw,
                    selected_decision=selection[1],
                    physical_record=_ref(record.record_id, raw),
                )
            )
            known.add(record.record_id)
        if subset.authenticated_acceptances:
            from chiplog.composition.r14_call_batch import (
                LOOP_PREPARATION_SCHEMA,
                verify_call_batch,
            )

            for retained in subset.authenticated_acceptances:
                matching = [
                    decision
                    for decision in owner_journal.decisions
                    if decision.prepared.request.identity.command_id
                    == retained.loop_request.command_id
                ]
                if len(matching) != 1:
                    return None
                decision = matching[0]
                batch = decision.prepared.request
                try:
                    verify_call_batch(batch, retained)
                except TypeError, ValueError:
                    return None
                authenticated_call_publication_ids.add(batch.identity.command_id)
                authenticated_commands[f"selected/{decision.decision_id}/command/0"] = (
                    "agent_loop",
                    LOOP_PREPARATION_SCHEMA,
                    batch.loop_command.canonical_bytes,
                )
                decision_raw = next(
                    (raw for key, _, raw in inventory.owner_entries if key == decision.decision_id),
                    None,
                )
                if decision_raw is None:
                    return None
                selected_ref = _ref(batch.identity.command_id, decision_raw, decision.decision_id)
                manifest = retained.loop_proposal.complete_acceptance_manifest
                for record in batch.complete_records:
                    physical = next(
                        (row for row in inventory.records if row.record_id == record.record_id),
                        None,
                    )
                    if physical is None or (physical.owner, physical.schema, physical.raw) != (
                        record.owner,
                        record.schema_id,
                        record.canonical_bytes,
                    ):
                        return None
                    logical_subject = next(
                        (member for member in manifest if member.subject_id == record.record_id),
                        None,
                    )
                    native_sources.setdefault(record.record_id, []).append(
                        RecoverySourceRecord(
                            owner=record.owner,
                            subject=logical_subject
                            or _ref(record.record_id, record.canonical_bytes),
                            schema_id=record.schema_id,
                            canonical_record_bytes=record.canonical_bytes,
                            selected_decision=selected_ref,
                            physical_record=_ref(record.record_id, record.canonical_bytes),
                        )
                    )
                    known.add(record.record_id)
        if successor_input is not None:
            selected_ingress = _current_successor_ingress_occurrences(
                runtime, inventory, *successor_input
            )
            if selected_ingress is None:
                return None
            selected_commands, selected_records = selected_ingress
            for locator, expected in selected_commands.items():
                previous = authenticated_commands.setdefault(locator, expected)
                if previous != expected:
                    return None
        # R16 selected/physical occurrences are decoded before they can be
        # dismissed. The native source/history reader authenticates its closed
        # native members. Other registered leaves use their existing pure codecs.
        if not classify_native_occurrences(
            inventory.occurrences,
            {row.locator: row for row in inventory.decoded},
            {identity: by_id[identity] for identity in known},
            scope,
            authenticated_commands,
            selected_records if successor_input is not None else None,
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
                and (
                    publication := next(
                        row
                        for row in inventory.publications
                        if "publications/" + row.command_id == gap.locator
                    )
                ).operation
                not in operations
                and publication.command_id not in authenticated_call_publication_ids
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
        if selected_suspension is not None:
            selected_calls = cut.frontier.ordered_calls
            if len(selected_calls) != 1:
                return None
            disposition = selected_calls[0].disposition
            current_obligations = (
                (disposition.obligation,)
                if isinstance(disposition, RecoveryRequiredResult)
                else ()
            )
            if current_obligations != selected_suspension.baseline.original_obligations:
                return None
            return cut, selected_suspension
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
        from chiplog.capabilities.agent_loop.call_acceptance_preparation import (
            call_record_reference,
        )
        from chiplog.capabilities.agent_loop.direct_terminal_contracts import (
            AcceptedDirectTerminalResultV1,
        )
        from chiplog.capabilities.agent_loop.recovery_contracts import Present
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
        recovery_route_present = False
        recovery_operations = {
            ACCEPTED_OPEN_ACCOUNTING_OPERATION,
            ACCEPTED_OPEN_RECOVERY_OPERATION,
            "agent_loop.original-resolver-authority-policy.v1",
            "agent_loop.original-resolver-invocation.v1",
            "agent_loop.original-call-resolution.v1",
            "agent_loop.loop-semantic-reduction.v1",
        }
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
            if command.operation_kind in recovery_operations:
                recovery_route_present = True
                continue
            if competing_schema or any(field in entry for field in competing_evidence_fields):
                claimed = True
                return True, None
        if not claimed:
            return False, None
        if recovery_route_present or len(entries) != 1 or subset.accepted_closed_outcome is None:
            return True, None
        try:
            decision_id, raw, entry, command = entries[0]
            evidence = retained_accepted_direct_terminal(entry)
            if runtime._publication(entry) != accepted_direct_terminal_command(evidence):
                return True, None
            authenticated_history = subset.authenticated_execution_history
            if authenticated_history is None:
                return True, None
            accounting = runtime.read_selected_accepted_direct_terminal_frontier(
                authenticated_history=authenticated_history
            )
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
                subject = (
                    call_record_reference(
                        record.record_id,
                        AcceptedDirectTerminalResultV1.model_validate_json(record.canonical_bytes),
                    )
                    if record.schema_id == "chiplog.execution.accepted-direct-terminal-result.v1"
                    else CallSubjectHead(
                        subject_id=record.record_id,
                        revision=Present(head="record:" + digest, fingerprint=digest),
                    )
                )
                sources.append(
                    RecoverySourceRecord(
                        owner=record.owner,
                        subject=subject,
                        schema_id=record.schema_id,
                        canonical_record_bytes=record.canonical_bytes,
                        selected_decision=selected_decision,
                        physical_record=_ref(record.record_id, record.canonical_bytes),
                    )
                )
            # The accepted source reader independently proves the selected R16
            # outcome and ordered evidence. Require its exact outcome source to
            # be present before allowing the direct terminal into this cut.
            outcome_id = subset.accepted_closed_outcome.latest_outcome_record_id
            if not any(
                _physical_inventory_id(source) == outcome_id
                for source in subset.accepted_effect_sources
            ):
                return True, None
            return True, (call, tuple(sources))
        except OSError, RuntimeError, TypeError, ValueError, KeyError, AttributeError:
            return True, None

    @staticmethod
    def _selected_accepted_recovery_inputs(
        runtime: CommonCliExecutionRuntime,
        subset: R14NativeRecoverySourceSubset,
    ) -> (
        tuple[
            TerminalCallFrontier,
            OriginalRecoveredClosure | None,
            EvidenceReduction | None,
            tuple[RecoverySourceRecord, ...],
        ]
        | None
    ):
        """Join accepted/open, resolver, reduction, and live evidence under this cut."""
        from chiplog.capabilities.agent_loop.original_recovery_contracts import (
            ConsumableReduction,
            IndependentResolverAuthority,
        )
        from chiplog.composition import (
            r14_loop_semantic_reduction_publication as reduction_publication,
        )
        from chiplog.composition.r14_accepted_open_accounting_publication import (
            _reduction_matches_current_evidence,
            _reduction_matches_resolution,
        )
        from chiplog.composition.r14_original_call_resolution_publication import (
            _read_selected_original_call_resolution_evidence_held,
        )

        try:
            closed = subset.accepted_closed_outcome
            opened = subset.accepted_open_outcome
            selected_outcome = closed or opened
            if selected_outcome is None:
                return None
            if closed is None:
                gate = runtime._authority_gate()
                gate.require_held()
                history = subset.authenticated_execution_history
                if history is None:
                    return None
                from chiplog.composition.r14_accepted_open_accounting_publication import (
                    read_selected_accepted_open_frontier,
                )

                with gate.hold():
                    accounting = read_selected_accepted_open_frontier(
                        runtime, _authenticated_history=history
                    )
            else:
                accounting = runtime.read_selected_accepted_open_frontier()  # type: ignore[no-untyped-call]
            resolution_evidence = _read_selected_original_call_resolution_evidence_held(runtime)
            resolution = resolution_evidence.prepared if resolution_evidence is not None else None
            selected_reduction = runtime.read_selected_loop_semantic_reduction()  # type: ignore[no-untyped-call]
            if (
                accounting is None
                or len(accounting.calls) != 1
                or not isinstance(accounting.calls[0].acceptance, ConsequentialAcceptedCall)
                or not isinstance(accounting.calls[0].disposition, RecoveryRequiredResult)
            ):
                return None
            call = accounting.calls[0]
            if closed is None:
                if (
                    opened is None
                    or resolution is not None
                    or selected_reduction is not None
                    or call.original_call_id != opened.accepted.original_call_id
                    or not isinstance(call.disposition, RecoveryRequiredResult)
                    or call.disposition.recovered_outcome.outcome.kind != "ABSENT"
                ):
                    return None
                if not isinstance(call.disposition, RecoveryRequiredResult):
                    return None
                if call.disposition.recovered_outcome.outcome.kind != "ABSENT":
                    return None
                from chiplog.composition.r14_accepted_open_accounting_records import (
                    ACCEPTED_OPEN_RECOVERY_OPERATION,
                    accepted_open_recovery_command,
                    retained_accepted_open_recovery,
                )
                from chiplog.composition.r14_original_obligation_sources import (
                    accepted_open_recovery_sources,
                )

                selected = [
                    (decision_id, raw, json.loads(raw))
                    for decision_id, _, raw in runtime._loop_decisions().entries()
                    if (entry := json.loads(raw)).get("kind") == "DECIDED"
                    and entry.get("operation_kind") == ACCEPTED_OPEN_RECOVERY_OPERATION
                ]
                if len(selected) != 1:
                    return None
                decision_id, decision_raw, entry = selected[0]
                evidence = retained_accepted_open_recovery(entry)
                if (
                    runtime._publication(entry) != accepted_open_recovery_command(evidence)
                    or evidence.result.opening.original != call.disposition.obligation
                ):
                    return None
                opening_sources = accepted_open_recovery_sources(
                    evidence,
                    selected_decision_id=decision_id,
                    selected_decision_bytes=decision_raw,
                )
                return call, None, None, opening_sources
            if resolution is None or selected_reduction is None:
                return None
            reduction_entries = reduction_publication._selected_entries(runtime)
            if not reduction_entries:
                return None
            retained_reduction = reduction_publication.validate_selected_command(
                runtime, reduction_entries[-1][2], reduction_entries[-1][3]
            )  # type: ignore[no-untyped-call]
            reduction_sources = _selected_reduction_sources(runtime, reduction_entries)
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
                or not reduction_sources
                or reduction_sources[-1] != selected_reduction.source
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
            assert resolution_evidence is not None
            resolver_cut = resolution_evidence.request.cut
            authority_sources = [resolver_cut.resolver_policy]
            if resolver_cut.authority.registered_authority not in authority_sources:
                authority_sources.append(resolver_cut.authority.registered_authority)
            if isinstance(resolver_cut.authority, IndependentResolverAuthority):
                authority_sources.append(resolver_cut.authority.authenticated_invocation)
            extra_sources = (
                resolver_cut.current_obligation,
                resolver_cut.call_terminal,
                *current_request.complete_ordered_evidence,
                *current_request.complete_anchor_sources,
                current_request.reducer_policy,
                *reduction_sources,
                *authority_sources,
            )
            return call, closure, reduction, extra_sources
        except OSError, RuntimeError, TypeError, ValueError, KeyError:
            return None

    def _assemble(
        self,
        runtime: CommonCliExecutionRuntime,
        subset: R14NativeRecoverySourceSubset,
        profile_source: RecoverySourceRecord,
        profile: NativeOneCallRecoveryProfileV1
        | NativeOneCallRecoveryProfileV2
        | NativeOneCallRecoveryProfileV3
        | NativeOneCallRecoveryProfileV4
        | NativeOneCallRecoveryProfileV5,
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
            transition = "ExecutionSuspension" if row.state == "SUSPENDED" else row.event
            if transition not in profile.causal_transitions:
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
                    transition_id=transition,
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
        cut = cut.model_copy(
            update={"complete_inventory_fingerprint": native_recovery_fingerprint("cut", value)}
        )
        return cut

    @staticmethod
    def _selected_resume_for_run(runtime, run: ExecutionRunRecord, accepted_inputs):
        """Authenticate retained originals on this exact resumed ACTIVE Run."""
        from .r14_execution_resume import OPERATION, _retained, _validate_selected

        if accepted_inputs is None or len(run.original_obligations) != 1:
            return None
        call, closure, reduction, _ = accepted_inputs
        disposition = call.disposition
        if (
            not isinstance(disposition, RecoveryRequiredResult)
            or closure is None
            or reduction is None
            or not isinstance(disposition.recovered_outcome.outcome, Present)
            or run.original_obligations != (disposition.obligation,)
            or closure.original != disposition.obligation
            or closure.terminal_disposition != disposition.terminal
            or closure.recovered_outcome != disposition.recovered_outcome.outcome
            or reduction.reduction_id != closure.reduction_id
            or reduction.obligation_closure != closure.closure
            or reduction.accepted_outcome != closure.recovered_outcome
            or reduction.consumability != "CONSUMABLE"
        ):
            return None
        selected = []
        for _, _, raw in runtime._loop_decisions().entries():
            entry = json.loads(raw)
            if entry.get("kind") != "DECIDED" or entry.get("operation_kind") != OPERATION:
                continue
            evidence = _retained(entry)
            if evidence.result.run != run:
                continue
            evidence = _validate_selected(
                runtime, entry, runtime._publication(entry), check_history=False
            )
            suspension = evidence.request.original_suspension
            if (
                evidence.result.run != run
                or run.suspension_baseline != suspension.pair.baseline
                or run.original_obligations != suspension.baseline.original_obligations
                or suspension.baseline.activation_blocking_predicates
            ):
                return None
            selected.append(evidence)
        return selected[0] if len(selected) == 1 else None

    @staticmethod
    def _selected_suspension_for_run(
        runtime: CommonCliExecutionRuntime, run: ExecutionRunRecord
    ) -> SelectedExecutionSuspension | None:
        from .r14_execution_suspension import (
            OPERATION,
            _command,
            _retained,
            _validate_selected_evidence,
        )

        selected = []
        for decision_id, _, raw in runtime._loop_decisions().entries():
            entry = json.loads(raw)
            if entry.get("kind") != "DECIDED" or entry.get("operation_kind") != OPERATION:
                continue
            entry["decision_id"] = str(decision_id)
            entry["decision_bytes"] = raw
            evidence = _retained(entry)
            if evidence.result.run != run:
                continue
            _, suspension = _validate_selected_evidence(
                runtime,
                entry,
                _command(evidence),
                evidence,
                check_history=False,
            )
            selected.append(suspension)
        return selected[0] if len(selected) == 1 else None

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
        if profile_source.schema_id not in (SCHEMA_V3, SCHEMA_V5):
            raise ValueError("accepted recovery requires selected V3 or V5 native profile")
        run, turn = lineage[-1], lineage[-1].turns[0]
        artifact = subset.selected_facts.source.original_prepare.artifact
        prepared = subset.selected_facts.source.original_prepare.prepared_run
        prepare_source = next(row for row in physical_sources if row.subject == prepared)
        children: list[RecoverySourceRecord] = []
        from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
            InitializedCallRecord,
        )
        from chiplog.capabilities.agent_loop.call_acceptance_preparation import (
            call_record_reference,
        )
        from chiplog.composition.r14_fanout_contracts import INITIALIZED_SCHEMA, SEAL_SCHEMA

        seal_raw = seal.canonical_bytes()
        seal_carriers = tuple(
            source
            for source in physical_sources
            if source.owner == "agent_loop"
            and source.schema_id == SEAL_SCHEMA
            and source.canonical_record_bytes == seal_raw
        )
        if len(seal_carriers) != 1:
            raise ValueError("selected response seal lacks one exact physical carrier")
        seal_carrier = seal_carriers[0]
        seal_reference = call_record_reference(seal.response_seal_id, seal)
        children.append(
            RecoverySourceRecord(
                owner="agent_loop",
                subject=seal_reference,
                schema_id=SEAL_SCHEMA,
                canonical_record_bytes=seal_raw,
                selected_decision=seal_carrier.selected_decision,
                physical_record=seal_carrier.physical_record,
            )
        )
        for initialized_reference in seal.complete_ordered_initialized:
            initialized_carriers: list[tuple[RecoverySourceRecord, InitializedCallRecord]] = []
            for source in physical_sources:
                if source.owner != "agent_loop" or source.schema_id != INITIALIZED_SCHEMA:
                    continue
                try:
                    initialized = InitializedCallRecord.model_validate_json(
                        source.canonical_record_bytes
                    )
                except ValueError:
                    continue
                if (
                    initialized.canonical_bytes() == source.canonical_record_bytes
                    and call_record_reference(initialized.original_call_id, initialized)
                    == initialized_reference
                ):
                    initialized_carriers.append((source, initialized))
            if len(initialized_carriers) != 1:
                raise ValueError("selected initialized call lacks one exact physical carrier")
            initialized_carrier, initialized = initialized_carriers[0]
            children.append(
                RecoverySourceRecord(
                    owner="agent_loop",
                    subject=initialized_reference,
                    schema_id=INITIALIZED_SCHEMA,
                    canonical_record_bytes=initialized.canonical_bytes(),
                    selected_decision=initialized_carrier.selected_decision,
                    physical_record=initialized_carrier.physical_record,
                )
            )

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
            if closure is None and reduction is None:
                if (
                    profile_source.schema_id != SCHEMA_V5
                    or disposition.recovered_outcome.outcome.kind != "ABSENT"
                ):
                    raise ValueError("open accepted recovery requires the selected V5 profile")
                from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
                    AcceptedOpenRecoveryOpeningV1,
                )

                opening_matches = tuple(
                    source
                    for source in physical_sources
                    if source.owner == "agent_loop"
                    and source.schema_id
                    == "chiplog.execution.accepted-open-recovery-opening.v1"
                    and source.subject.subject_id == disposition.obligation.obligation_id
                    and source.subject.revision.head == disposition.obligation.obligation_head
                )
                if len(opening_matches) != 1:
                    raise ValueError("open accepted recovery opening is missing or ambiguous")
                opening_source = opening_matches[0]
                opening = AcceptedOpenRecoveryOpeningV1.model_validate_json(
                    opening_source.canonical_record_bytes
                )
                if opening.original != disposition.obligation:
                    raise ValueError("open accepted recovery opening differs from obligation")
                call_result_ref = disposition.recovered_outcome.outcome
                obligation_heads = (disposition.terminal, opening_source.subject.revision)
                reduction_heads = ()
                evidence_heads = ()
                closure_records = ()
                reduction_records = ()
                resolver_sources = ()
            elif (
                closure is None
                or reduction is None
                or disposition.recovered_outcome.outcome.kind != "PRESENT"
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
            else:
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
                    or row.schema_id
                    == "chiplog.loop.original-effect-evidence-reducer-policy.v1"
                )
        else:
            raise ValueError("accepted next-turn frontier has an unsupported terminal")
        accepted_sources = _accepted_source_preimages(
            subset.accepted_effect_sources, physical_sources
        )
        if direct_result is not None:
            from chiplog.capabilities.agent_loop.direct_terminal_contracts import (
                AcceptedDirectTerminalResultV1,
            )

            if len(subset.authenticated_acceptances) != 1:
                raise ValueError("selected external intent lacks one authenticated acceptance")
            retained_acceptance = subset.authenticated_acceptances[0]
            manifest = retained_acceptance.loop_proposal.complete_acceptance_manifest
            if len(manifest) != 3:
                raise ValueError("selected external intent manifest is incomplete")
            accepted_closed_outcome = subset.accepted_closed_outcome
            if accepted_closed_outcome is None:
                raise ValueError("selected external intent has no closed R16 outcome")
            intent_source = _selected_external_intent_preimage(
                manifest[2],
                retained_acceptance.effects_proposal,
                subset.accepted_effect_sources,
                accepted_closed_outcome.complete_ordered_dispatch_members,
            )
            if intent_source is None:
                raise ValueError("external intent lacks exact selected R16 carrier preimage")
            children.append(intent_source)

            result_sources = tuple(
                row
                for row in physical_sources
                if row.schema_id == "chiplog.execution.accepted-direct-terminal-result.v1"
                and isinstance(row.subject.revision, Present)
                and row.subject.revision.head == direct_result.result.head
            )
            if len(result_sources) != 1:
                raise ValueError("direct terminal result source is missing or ambiguous")
            direct_result_record = AcceptedDirectTerminalResultV1.model_validate_json(
                result_sources[0].canonical_record_bytes
            )
            if (
                direct_result_record.digest() != direct_result.result.fingerprint
                or not isinstance(result_sources[0].subject.revision, Present)
                or result_sources[0].subject.revision.fingerprint
                != _sha(result_sources[0].canonical_record_bytes)
            ):
                raise ValueError("direct terminal result digest/preimage differs")
            evidence_references: list[HeadMarker] = []
            for evidence in direct_result_record.classification.ordered_evidence:
                matches = tuple(
                    source
                    for source in accepted_sources
                    if source.subject.subject_id == evidence.subject_id
                    and isinstance(source.subject.revision, Present)
                    and source.subject.revision.head == evidence.head
                )
                if len(matches) != 1 or not isinstance(matches[0].subject.revision, Present):
                    raise ValueError("direct terminal evidence lacks one exact recovery source")
                evidence_references.append(matches[0].subject.revision)
            evidence_heads = tuple(evidence_references)
        else:
            direct_result_record = None
            if isinstance(disposition, RecoveryRequiredResult):
                if len(subset.authenticated_acceptances) != 1:
                    raise ValueError("selected external intent lacks one authenticated acceptance")
                retained_acceptance = subset.authenticated_acceptances[0]
                manifest = retained_acceptance.loop_proposal.complete_acceptance_manifest
                if len(manifest) != 3:
                    raise ValueError("selected external intent manifest is incomplete")
                accepted_outcome = (
                    subset.accepted_closed_outcome or subset.accepted_open_outcome
                )
                if accepted_outcome is None:
                    raise ValueError(
                        "selected recovery obligation has no authenticated R16 outcome"
                    )
                intent_source = _selected_external_intent_preimage(
                    manifest[2],
                    retained_acceptance.effects_proposal,
                    subset.accepted_effect_sources,
                    accepted_outcome.complete_ordered_dispatch_members,
                )
                if intent_source is None:
                    raise ValueError("external intent lacks exact selected R16 carrier preimage")
                children.append(intent_source)
        acceptance_manifest = acceptance.complete_acceptance_manifest
        if direct_result is not None:
            resolved_manifest: list[HeadMarker] = []
            for manifest_reference in acceptance_manifest:
                revision = _selected_source_revision(
                    manifest_reference, (*physical_sources, *children)
                )
                if revision is None:
                    raise ValueError("accepted call manifest lacks one selected source preimage")
                resolved_manifest.append(revision)
            acceptance_manifest = tuple(resolved_manifest)
        heads: dict[str, tuple[HeadMarker, ...]] = {
            "RUN": (_run_ref(run).revision,),
            "TURN": (turn_ref.revision,),
            "SEALED_RESPONSE": (reference(seal.response_seal_id, seal).revision,),
            "CALL": (
                acceptance.initialized,
                *acceptance_manifest,
                disposition.terminal,
                call_result_ref,
            ),
            "EFFECT": tuple(
                row.subject.revision
                for row in accepted_sources
                if row.schema_id.startswith("chiplog.effects.")
                and not (
                    profile_source.schema_id == SCHEMA_V5
                    and isinstance(disposition, RecoveryRequiredResult)
                    and row.schema_id != "chiplog.effects.dispatch-record.v2"
                )
            ),
            "EVIDENCE": evidence_heads,
            "AUTHORITY": (
                bindings.authority_scope,
                *(row.subject.revision for row in resolver_sources),
            ),
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
        *,
        authenticated_loop_decisions=None,
        verified_h1_prepares=None,
    ) -> bool:
        from .r14_native_recovery_historical import authenticate_native_recovery_selected

        return authenticate_native_recovery_selected(
            self,
            runtime,
            evidence,
            entry,
            authenticated_loop_decisions=authenticated_loop_decisions,
            verified_h1_prepares=verified_h1_prepares,
        )


# Preserve callback dispatch when trusted composition replaces the native method.
_NATIVE_AUTHENTICATE_SELECTED_HELD = NativeRecoveryCutSource.authenticate_selected_held
