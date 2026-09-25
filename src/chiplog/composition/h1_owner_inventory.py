"""Broker-private, fail-closed inventory for the H1 zero-call seal.

This reader intentionally distinguishes *admitted* SQL pairs from pairs for
which this module can extract a scope.  The former is a writer constraint; the
latter is required before absence can be evidence.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from chiplog.capabilities.agent_loop.call_acceptance_contracts import SealedResponseRecord
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
    RECOVERY_FRONTIER_REGISTRY_SCHEMA,
    execution_h1_zero_call_frontier_registry_v2,
)
from chiplog.capabilities.projections.workspace_boundary import SourceReference
from chiplog.composition.h1_inventory_leaf_contracts import (
    H1_EVIDENCE_INBOX_METADATA_DOMAIN,
    H1DecodedInventoryItem,
    H1OwnerInventoryFailure,
    H1RawInventoryItem,
    H1ScopeKey,
)
from chiplog.composition.h1_inventory_workspace_planning import (
    decode_h1_workspace_planning_scope,
)
from chiplog.composition.h1_preseal_contracts import (
    H1InventoryReceipt,
    H1Scope,
    H1SelectedPrepare,
    H1SelectedSeal,
)
from chiplog.composition.r14_execution_complete_seal_records import (
    EXECUTION_COMPLETE_SEAL_OPERATION,
)
from chiplog.composition.r14_execution_fanout_contracts import (
    EXECUTION_FANOUT_OPERATION,
    EXECUTION_RUN_SCHEMA,
)
from chiplog.composition.r14_execution_inbox_records import (
    EXECUTION_INBOX_INITIALIZATION_OPERATION,
)
from chiplog.composition.r14_fanout_contracts import FANOUT_OPERATION, SEAL_SCHEMA
from chiplog.composition.r14_h1_workspace_issuance_contracts import H1VerifiedWorkspaceClosure
from chiplog.platform._sqlite import PhysicalRecord
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.owner_decision_journal import OwnerJournalSnapshot
from chiplog.platform.workspace_snapshot import read_connection

if TYPE_CHECKING:
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime

Phase = Literal["PRE_SEAL", "POST_SEAL", "HISTORICAL"]
_Pair = tuple[str, str]

# These are the only non-owner physical publication operations that a native
# H1 zero-call history may classify before its per-record leaves run.  This is
# deliberately a closed list: an unlisted operation cannot be made harmless
# by being absent from the owner journal.
_H1_KNOWN_NON_OWNER_OPERATIONS = frozenset(
    {
        EXECUTION_INBOX_INITIALIZATION_OPERATION,
        FANOUT_OPERATION,
        EXECUTION_FANOUT_OPERATION,
        EXECUTION_COMPLETE_SEAL_OPERATION,
    }
)


@dataclass(frozen=True, slots=True)
class _PhysicalRow:
    record_id: str
    owner: str
    schema: str
    raw: bytes
    sequence: int


def h1_owner_decoder_registry() -> dict[_Pair, str]:
    """Closed scope-extractor registry, separate from the SQL writer registry."""
    from chiplog.composition.h1_inventory_baseline import REGISTRATIONS as baseline_registrations
    from chiplog.composition.h1_inventory_call import REGISTRATIONS as call_registrations
    from chiplog.composition.h1_inventory_effect_delivery import (
        REGISTRATIONS as effect_registrations,
    )
    from chiplog.composition.h1_inventory_ingress_evidence import (
        REGISTRATIONS as ingress_registrations,
    )
    from chiplog.composition.h1_inventory_workspace_planning import (
        REGISTRATIONS as workspace_registrations,
    )

    result = {
        ("agent_loop", EXECUTION_RUN_SCHEMA): "native-run-v2",
        ("agent_loop", SEAL_SCHEMA): "native-response-seal-v1",
        ("agent_loop", RECOVERY_FRONTIER_REGISTRY_SCHEMA): "h1-frontier-registry-v2",
    }
    leaf_groups = (
        ("baseline", baseline_registrations),
        ("call", call_registrations),
        ("effect", effect_registrations),
        ("ingress", ingress_registrations),
        ("workspace", workspace_registrations),
    )
    registered_by: dict[_Pair, str] = {}
    for leaf_name, registrations in leaf_groups:
        for registration in registrations:
            if registration.surface != "PHYSICAL":
                continue
            pair = (registration.owner, registration.schema)
            prior = registered_by.setdefault(pair, leaf_name)
            if prior != leaf_name:
                raise RuntimeError(f"H1 physical decoder pair has competing leaves: {pair!r}")
            result.setdefault(pair, registration.decoder_version)
    return result


def unsupported_runtime_record_pairs(runtime_type: type[object]) -> frozenset[_Pair]:
    """Expose the deliberate completeness gap for integration and tests."""
    contracts = cast(dict[str, str], getattr(runtime_type, "_record_contracts", {}))
    variants = cast(tuple[_Pair, ...], getattr(runtime_type, "_record_schema_variants", ()))
    admitted = {*contracts.items(), *variants}
    return frozenset(admitted - set(h1_owner_decoder_registry()))


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _encoded_rows(rows: tuple[tuple[object, ...], ...]) -> bytes:
    """Preserve SQL scalar types and BLOB bytes in a deterministic receipt field."""

    def scalar(value: object) -> object:
        if isinstance(value, bytes):
            return {"type": "bytes", "base64": base64.b64encode(value).decode("ascii")}
        if value is None:
            return {"type": "null"}
        if type(value) is str:
            return {"type": "str", "value": value}
        if type(value) is int:
            return {"type": "int", "value": value}
        raise TypeError(f"unsupported SQL scalar {type(value).__name__}")

    return _canonical_json([[scalar(value) for value in row] for row in rows])


def _evidence_items(
    tenant: str, rows: tuple[tuple[object, ...], ...]
) -> tuple[H1RawInventoryItem, ...]:
    """Retain every evidence column before its leaf decides scope relevance."""
    items: list[H1RawInventoryItem] = []
    for index, row in enumerate(rows):
        if len(row) != 9:
            raise H1OwnerInventoryFailure.corrupt(
                family="EVIDENCE", owner="evidence_inbox", schema="row", locator=str(index)
            )
        source_id, evidence_id, fingerprint, raw, followup, state, attempt, version, cursor = row
        required_fields = (source_id, evidence_id, fingerprint, followup, state)
        optional_fields = (attempt, version, cursor)
        if (
            not all(type(value) is str and value for value in required_fields)
            or any(value is not None and type(value) is not str for value in optional_fields)
            or type(raw) is not bytes
        ):
            raise H1OwnerInventoryFailure.corrupt(
                family="EVIDENCE", owner="evidence_inbox", schema="row", locator=str(index)
            )
        source = cast(str, source_id)
        evidence_identity = cast(str, evidence_id)
        fingerprint_text = cast(str, fingerprint)
        metadata = _canonical_json(
            {
                "domain": H1_EVIDENCE_INBOX_METADATA_DOMAIN,
                "source_id": source,
                "evidence_id": evidence_identity,
                "fingerprint": fingerprint_text,
                "followup_kind": followup,
                "state": state,
                "attempt_id": attempt,
                "transport_version": version,
                "cursor": cursor,
            }
        )
        items.append(
            H1RawInventoryItem(
                "EVIDENCE_INBOX",
                f"evidence_inbox/{source}/{evidence_identity}",
                tenant,
                "evidence_inbox",
                "chiplog.evidence-inbox.row.v1",
                "evidence.inbox",
                evidence_identity,
                fingerprint_text,
                raw,
                metadata,
            )
        )
    return tuple(items)


def _decode_evidence_items(items: tuple[H1RawInventoryItem, ...], scope: H1Scope) -> None:
    """Decode every inbox row before declaring the inbox family unsupported."""
    from chiplog.composition.h1_inventory_ingress_evidence import decode_h1_ingress_evidence_scope

    source_ids = {source.record_id for source in scope.reachable_source_refs}
    for item in items:
        decoded = decode_h1_ingress_evidence_scope(item)
        if any(
            identity.namespace == "source" and identity.identity in source_ids
            for identity in decoded.identities
        ):
            raise H1OwnerInventoryFailure.nonempty(
                family="EVIDENCE", owner=item.owner, schema=item.schema, locator=item.locator
            )
        # A source identity absent from the verified workspace closure is not
        # enough to prove irrelevance for effect/mandate paths.
        raise H1OwnerInventoryFailure.unsupported(
            family="EVIDENCE", owner=item.owner, schema=item.schema, locator=item.locator
        )


def _reconcile_current_owner_cut(
    tenant: str,
    raw_entries: tuple[tuple[str, str | None, bytes], ...],
    publications: tuple[tuple[object, ...], ...],
    records: tuple[_PhysicalRow, ...],
    phase: Phase,
    selected_input_decisions: tuple[Any, ...] = (),
) -> None:
    """Authenticate all current owner batches before using an empty snapshot."""
    from chiplog.composition.h1_inventory_owner_cut import (
        H1OwnerCutFailure,
        OwnerJournalSqlPublication,
        OwnerJournalSqlRecord,
        reconcile_h1_owner_cut,
    )

    try:
        publication_rows = tuple(
            OwnerJournalSqlPublication(
                cast(str, operation),
                cast(str, command_id),
                cast(str, fingerprint),
                cast(int, sequence),
                tuple(cast(str, record_ids).split("\n")),
            )
            for operation, command_id, fingerprint, sequence, record_ids in publications
        )
        record_rows = tuple(
            OwnerJournalSqlRecord(row.record_id, row.owner, row.schema, row.raw, row.sequence)
            for row in records
        )
        cut = reconcile_h1_owner_cut(
            tenant=tenant,
            raw_entries=raw_entries,
            publications=publication_rows,
            records=record_rows,
            phase=phase,
        )
    except H1OwnerCutFailure as error:
        if error.code == "INCOMPLETE":
            raise H1OwnerInventoryFailure.incomplete(
                family="OWNER", locator=error.locator
            ) from error
        if error.code == "UNSUPPORTED":
            raise H1OwnerInventoryFailure.unsupported(
                family="OWNER",
                owner="owner-journal",
                schema="chiplog.owner-decision.v1",
                locator=error.locator,
            ) from error
        raise H1OwnerInventoryFailure.corrupt(
            family="OWNER",
            owner="owner-journal",
            schema="chiplog.owner-decision.v1",
            locator=error.locator,
        ) from error
    from chiplog.platform.owner_publications import source_commands

    try:
        expected = {
            (
                item.decision_id,
                item.decision_fingerprint,
                item.prepared.request.identity.command_id,
                item.tenant_commit_sequence,
                tuple(
                    command.canonical_bytes for command in source_commands(item.prepared.request)
                ),
                tuple(record.canonical_bytes for record in item.prepared.request.complete_records),
            )
            for item in selected_input_decisions
        }
    except (AttributeError, TypeError, ValueError) as error:
        raise H1OwnerInventoryFailure.corrupt(
            family="SELECTED_INPUT", owner="", schema="owner-journal", locator="decisions"
        ) from error
    actual = {
        (
            item.decision_id,
            item.decision_fingerprint,
            item.command_id,
            item.tenant_commit_sequence,
            item.source_command_bytes,
            item.member_bytes,
        )
        for item in cut.decisions
    }
    if actual != expected:
        raise H1OwnerInventoryFailure.unsupported(
            family="OWNER",
            owner="owner-journal",
            schema="chiplog.owner-decision.v1",
            locator="decisions/" + repr(sorted(item[2] for item in actual ^ expected)),
        )


def _reconcile_historical_owner_cut(
    tenant: str,
    owner_journal: object,
    selected_seal: H1SelectedSeal,
    publications: tuple[tuple[object, ...], ...],
    records: tuple[_PhysicalRow, ...],
) -> tuple[OwnerJournalSnapshot, bytes]:
    """Use the seal's retained V2 owner locator, never today's owner tail."""
    from chiplog.composition.h1_inventory_owner_cut import (
        H1OwnerCutFailure,
        OwnerJournalSqlPublication,
        OwnerJournalSqlRecord,
        _parse_h1_owner_asof,
        reconcile_h1_historical_owner_cut,
    )

    try:
        publication_rows = tuple(
            OwnerJournalSqlPublication(
                cast(str, operation),
                cast(str, command_id),
                cast(str, fingerprint),
                cast(int, sequence),
                tuple(cast(str, record_ids).split("\n")),
            )
            for operation, command_id, fingerprint, sequence, record_ids in publications
        )
        record_rows = tuple(
            OwnerJournalSqlRecord(row.record_id, row.owner, row.schema, row.raw, row.sequence)
            for row in records
        )
        cut = reconcile_h1_historical_owner_cut(
            tenant=tenant,
            owner_journal=cast(Any, owner_journal),
            selected_seal=selected_seal,
            publications=publication_rows,
            records=record_rows,
            known_non_owner_operations=_H1_KNOWN_NON_OWNER_OPERATIONS,
        )
        locator = _parse_h1_owner_asof(selected_seal, tenant)
        snapshot = cast(Any, owner_journal).snapshot_at(locator.owner_head)
    except H1OwnerCutFailure as error:
        if error.code == "INCOMPLETE":
            raise H1OwnerInventoryFailure.incomplete(
                family="HISTORICAL_OWNER", locator=error.locator
            ) from error
        if error.code == "UNSUPPORTED":
            raise H1OwnerInventoryFailure.unsupported(
                family="HISTORICAL_OWNER",
                owner="owner-journal",
                schema="chiplog.owner-decision.v1",
                locator=error.locator,
            ) from error
        raise H1OwnerInventoryFailure.corrupt(
            family="HISTORICAL_OWNER",
            owner="owner-journal",
            schema="chiplog.owner-decision.v1",
            locator=error.locator,
        ) from error
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise H1OwnerInventoryFailure.corrupt(
            family="HISTORICAL_OWNER",
            owner="owner-journal",
            schema="chiplog.owner-decision.v1",
            locator="prefix",
        ) from error
    prefix_bytes = _canonical_json(
        {
            "owner_head": locator.owner_head,
            "materialized_command_ids": sorted(cut.materialized_command_ids),
            "decisions": [
                {
                    "decision_id": decision.decision_id,
                    "decision_fingerprint": decision.decision_fingerprint,
                    "command_id": decision.command_id,
                    "tenant_commit_sequence": decision.tenant_commit_sequence,
                }
                for decision in cut.decisions
            ],
        }
    )
    if not isinstance(snapshot, OwnerJournalSnapshot):
        raise H1OwnerInventoryFailure.corrupt(
            family="HISTORICAL_OWNER",
            owner="owner-journal",
            schema="chiplog.owner-decision.v1",
            locator="prefix-snapshot",
        )
    return snapshot, prefix_bytes


def _workspace_inventory_item(
    runtime: object, closure: H1VerifiedWorkspaceClosure
) -> H1RawInventoryItem:
    """Reopen the authenticated issuance journal; never rebuild it from closure fields."""
    from chiplog.composition.r13_workspace import R13Workspace

    try:
        issued = (
            R13Workspace(cast(Any, runtime)).open_h1_workspace_issuance().load(closure.issuance)
        )
        raw = issued.canonical_bytes()
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise H1OwnerInventoryFailure.corrupt(
            family="WORKSPACE",
            owner="workspace_issuance",
            schema="chiplog.execution.h1-original-workspace-issuance.v1",
            locator=closure.issuance.entry_id,
        ) from error
    if hashlib.sha256(raw).hexdigest() != closure.issuance.payload_digest:
        raise H1OwnerInventoryFailure.corrupt(
            family="WORKSPACE",
            owner="workspace_issuance",
            schema="chiplog.execution.h1-original-workspace-issuance.v1",
            locator=closure.issuance.entry_id,
        )
    return H1RawInventoryItem(
        "WORKSPACE_SOURCE",
        "workspace-issuance/" + closure.issuance.entry_id,
        closure.issuance.tenant,
        "workspace_issuance",
        "chiplog.execution.h1-original-workspace-issuance.v1",
        None,
        closure.issuance.entry_id,
        closure.issuance.payload_digest,
        raw,
    )


def _scope(
    captured: ExecutionRunRecord, selected: H1SelectedPrepare, workspace: H1VerifiedWorkspaceClosure
) -> H1Scope:
    if (
        type(captured) is not ExecutionRunRecord
        or type(selected.started_run) is not ExecutionRunRecord
    ):
        raise H1OwnerInventoryFailure.corrupt(
            family="RUN", owner="agent_loop", schema=EXECUTION_RUN_SCHEMA, locator="captured"
        )
    if (
        selected.started_run.tenant != captured.tenant
        or selected.started_run.principal != captured.principal
        or selected.started_run.run_id != captured.run_id
        or workspace.issuance != selected.issuance_ref
    ):
        raise H1OwnerInventoryFailure.corrupt(
            family="RUN",
            owner="agent_loop",
            schema=EXECUTION_RUN_SCHEMA,
            locator="selected-prepare",
        )
    if not captured.turns:
        raise H1OwnerInventoryFailure.corrupt(
            family="TURN", owner="agent_loop", schema=EXECUTION_RUN_SCHEMA, locator="captured"
        )
    heads: set[str] = set()
    for run in (selected.started_run, captured):
        current: ExecutionRunRecord | None = run
        while current is not None:
            if current.head in heads:
                break
            heads.add(current.head)
            # Predecessors are authenticated identifiers from decoded native Run
            # rows.  The later physical scan requires every same-run row to be in
            # this selected ancestry; it does not infer membership from run_id.
            predecessor = current.predecessor
            current = None
            if predecessor is not None:
                heads.add(predecessor)
    refs: list[SourceReference] = []
    try:
        for planning in workspace.sources.planning_sources:
            refs.extend(
                SourceReference.model_validate_json(raw) for raw in planning.source_reference_json
            )
    except (TypeError, ValueError) as error:
        raise H1OwnerInventoryFailure.corrupt(
            family="WORKSPACE_SOURCE",
            owner="projections",
            schema="SourceReference",
            locator="workspace",
        ) from error
    unique = {item.model_dump_json(): item for item in refs}
    return H1Scope(
        tenant=captured.tenant,
        principal=captured.principal,
        run_id=captured.run_id,
        turn_id=captured.turns[-1].turn_id,
        run_heads=tuple(sorted(heads)),
        reachable_source_refs=tuple(unique[key] for key in sorted(unique)),
    )


def _reauthenticate_selected_prepare(
    runtime: CommonCliExecutionRuntime,
    captured: ExecutionRunRecord,
    selected: H1SelectedPrepare,
    workspace: H1VerifiedWorkspaceClosure,
    phase: Phase,
    selected_seal: H1SelectedSeal | None,
) -> None:
    """Reopen the selected raw V3/SQL witness; caller DTO equality is not proof."""
    from chiplog.composition.h1_selected_prepare import (
        reopen_selected_h1_workspace,
        select_h1_v3_prepare_for_candidate,
        select_h1_v3_prepare_for_seal,
    )

    try:
        fresh_seal: H1SelectedSeal | None = None
        if phase == "PRE_SEAL":
            refreshed = select_h1_v3_prepare_for_candidate(
                runtime, captured, expected_head=captured.head
            )
        else:
            if selected_seal is None:
                raise ValueError("selected seal absent")
            seals = tuple(
                item
                for item in selected_seal.command.records
                if item.owner == "agent_loop" and item.schema_id == SEAL_SCHEMA
            )
            if len(seals) != 1:
                raise ValueError("selected seal member differs")
            seal = SealedResponseRecord.model_validate_json(seals[0].canonical_bytes)
            if (
                seal.canonical_bytes() != seals[0].canonical_bytes
                or seals[0].record_id != "record:" + seal.digest()
            ):
                raise ValueError("selected seal member differs")
            from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
            from chiplog.capabilities.agent_loop.recovery_contracts import Present

            locator = CallSubjectHead(
                subject_id=seal.response_seal_id,
                revision=Present(
                    head=seals[0].record_id,
                    fingerprint=hashlib.sha256(seals[0].canonical_bytes).hexdigest(),
                ),
            )
            postseal = select_h1_v3_prepare_for_seal(
                runtime, selected_seal=locator, historical=phase == "HISTORICAL"
            )
            if postseal.captured_run != captured:
                raise ValueError("selected sealed captured Run differs")
            refreshed, fresh_seal = postseal.prepare, postseal.seal
        reopened = reopen_selected_h1_workspace(runtime, refreshed)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise H1OwnerInventoryFailure.corrupt(
            family="SELECTED_PREPARE",
            owner="agent_loop",
            schema=EXECUTION_RUN_SCHEMA,
            locator=captured.head,
        ) from error
    if (
        refreshed != selected
        or reopened != workspace
        or (phase != "PRE_SEAL" and fresh_seal != selected_seal)
    ):
        raise H1OwnerInventoryFailure.corrupt(
            family="SELECTED_PREPARE",
            owner="agent_loop",
            schema=EXECUTION_RUN_SCHEMA,
            locator=captured.head,
        )


def _merge_workspace_sources(scope: H1Scope, item: H1RawInventoryItem) -> H1Scope:
    """Only the workspace leaf can add independently authenticated source refs."""
    decoded = decode_h1_workspace_planning_scope(item)
    known = {value.model_dump_json(): value for value in scope.reachable_source_refs}
    known.update({value.model_dump_json(): value for value in decoded.source_refs})
    return H1Scope(
        scope.tenant,
        scope.principal,
        scope.run_id,
        scope.turn_id,
        scope.run_heads,
        tuple(known[key] for key in sorted(known)),
    )


def _physical_item(row: _PhysicalRow, tenant: str) -> H1RawInventoryItem:
    """Build a raw occurrence without treating its JSON hints as trusted decoding."""
    kind: str | None = None
    try:
        body = json.loads(row.raw)
        if isinstance(body, dict):
            candidate = body.get("kind")
            if isinstance(candidate, str):
                kind = candidate
            record_type = body.get("record_type")
            if isinstance(record_type, dict) and all(
                isinstance(record_type.get(name), str) for name in ("namespace", "name")
            ):
                kind = record_type["namespace"] + "." + record_type["name"]
    except TypeError, ValueError, json.JSONDecodeError:
        # The selected leaf receives the exact malformed bytes and classifies it.
        pass
    return H1RawInventoryItem(
        "PHYSICAL",
        "records/" + row.record_id,
        tenant,
        row.owner,
        row.schema,
        kind,
        row.record_id,
        hashlib.sha256(row.raw).hexdigest(),
        row.raw,
    )


def _decode_leaf_row(row: _PhysicalRow, scope: H1Scope) -> H1DecodedInventoryItem:
    """Canonical-decode a registered physical member before global graph analysis."""
    item = _physical_item(row, scope.tenant)
    if (row.owner, row.schema) not in h1_owner_decoder_registry():
        raise H1OwnerInventoryFailure.unsupported(
            family="UNMAPPED", owner=row.owner, schema=row.schema, locator=row.record_id
        )
    from chiplog.composition.h1_inventory_baseline import (
        REGISTRATIONS as baseline_registrations,
    )
    from chiplog.composition.h1_inventory_baseline import decode_h1_baseline_scope
    from chiplog.composition.h1_inventory_call import REGISTRATIONS as call_registrations
    from chiplog.composition.h1_inventory_call import decode_h1_call_scope
    from chiplog.composition.h1_inventory_effect_delivery import (
        REGISTRATIONS as effect_registrations,
    )
    from chiplog.composition.h1_inventory_effect_delivery import decode_h1_effect_delivery_scope
    from chiplog.composition.h1_inventory_ingress_evidence import (
        REGISTRATIONS as ingress_registrations,
    )
    from chiplog.composition.h1_inventory_ingress_evidence import decode_h1_ingress_evidence_scope
    from chiplog.composition.h1_inventory_workspace_planning import (
        REGISTRATIONS as workspace_registrations,
    )

    decoder: Callable[[H1RawInventoryItem], H1DecodedInventoryItem] | None = None
    for registrations, candidate in (
        (baseline_registrations, decode_h1_baseline_scope),
        (call_registrations, decode_h1_call_scope),
        (effect_registrations, decode_h1_effect_delivery_scope),
        (ingress_registrations, decode_h1_ingress_evidence_scope),
        (workspace_registrations, decode_h1_workspace_planning_scope),
    ):
        if any(
            registration.surface == "PHYSICAL"
            and (registration.owner, registration.schema) == (row.owner, row.schema)
            for registration in registrations
        ):
            if decoder is not None:
                raise RuntimeError(f"H1 physical decoder pair has competing leaves: {row.owner!r}")
            decoder = candidate
    if decoder is None:
        raise H1OwnerInventoryFailure.unsupported(
            family="UNMAPPED", owner=row.owner, schema=row.schema, locator=row.record_id
        )
    return decoder(item)


def _classify_leaf_scope(
    decoded_rows: tuple[tuple[_PhysicalRow, H1DecodedInventoryItem], ...],
    scope: H1Scope,
    selected_input_ids: frozenset[str] = frozenset(),
) -> None:
    """Traverse all decoded typed components from exact native/workspace roots.

    Leaves authenticate an occurrence's canonical bytes.  Only this function
    decides whether a completely decoded component joins the selected H1
    closure.  A bare source string cannot join a workspace SourceReference.
    """
    adjacency: dict[H1ScopeKey, set[H1ScopeKey]] = {}
    all_keys: set[H1ScopeKey] = set()
    selected_sources = {source.model_dump_json() for source in scope.reachable_source_refs}
    source_keys: dict[H1ScopeKey, set[str]] = {}
    for row, decoded in decoded_rows:
        identities = set(decoded.identities)
        for identity in identities:
            if identity.tenant != scope.tenant:
                raise H1OwnerInventoryFailure.corrupt(
                    family="LEAF_SCOPE", owner=row.owner, schema=row.schema, locator=row.record_id
                )
            all_keys.add(identity)
            adjacency.setdefault(identity, set())
        for relation in decoded.relations:
            if relation.subject not in identities or relation.target not in identities:
                raise H1OwnerInventoryFailure.corrupt(
                    family="LEAF_SCOPE", owner=row.owner, schema=row.schema, locator=row.record_id
                )
            adjacency[relation.subject].add(relation.target)
            adjacency[relation.target].add(relation.subject)
        for source in decoded.source_refs:
            if source.tenant_id != scope.tenant:
                raise H1OwnerInventoryFailure.corrupt(
                    family="SOURCE", owner=row.owner, schema=row.schema, locator=row.record_id
                )
            key = H1ScopeKey(
                scope.tenant, "source", source.owner + ":" + source.record_id, source.record_version
            )
            source_keys.setdefault(key, set()).add(source.model_dump_json())
    roots = {
        H1ScopeKey(scope.tenant, "run", scope.run_id, None),
        H1ScopeKey(scope.tenant, "turn", scope.turn_id, None),
    }
    for key, references in source_keys.items():
        if references & selected_sources:
            # Same owner/id/version with a different digest or disclosure label
            # would be an unauthenticated source splice.
            if references - selected_sources:
                raise H1OwnerInventoryFailure.unsupported(
                    family="SOURCE", owner="", schema="SourceReference", locator=key.identity
                )
            roots.add(key)
    reached: set[H1ScopeKey] = set()
    pending = list(roots)
    while pending:
        current = pending.pop()
        if current in reached:
            continue
        reached.add(current)
        pending.extend(adjacency.get(current, ()))
    for row, decoded in decoded_rows:
        if (
            any(identity in reached for identity in decoded.identities)
            and decoded.families
            and row.record_id not in selected_input_ids
        ):
            raise H1OwnerInventoryFailure.nonempty(
                family="LEAF_SCOPE", owner=row.owner, schema=row.schema, locator=row.record_id
            )


def _selected_boundary(
    runtime: object, entries: tuple[tuple[str, str | None, bytes], ...], selected: H1SelectedSeal
) -> tuple[int, int]:
    """Authenticate the historical command before using its sequence as a cut."""
    publication = getattr(runtime, "_publication", None)
    matches: list[int] = []
    decided: set[str] = set()
    materialized: set[str] = set()
    if not callable(publication):
        raise H1OwnerInventoryFailure.unsupported(
            family="HISTORICAL_LOOP", owner="agent_loop", schema="journal", locator="runtime"
        )
    for index, (_, _, raw) in enumerate(entries):
        try:
            entry = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise H1OwnerInventoryFailure.corrupt(
                family="LOOP", owner="agent_loop", schema="journal", locator=f"loop/{index}"
            ) from error
        if not isinstance(entry, dict) or entry.get("version") != 1:
            raise H1OwnerInventoryFailure.corrupt(
                family="LOOP", owner="agent_loop", schema="journal", locator=f"loop/{index}"
            )
        if entry.get("kind") == "MATERIALIZED":
            operation_id = entry.get("operation_id")
            if (
                type(operation_id) is not str
                or not operation_id
                or operation_id not in decided
                or operation_id in materialized
                or _canonical_json(entry) != raw
            ):
                raise H1OwnerInventoryFailure.corrupt(
                    family="LOOP", owner="agent_loop", schema="journal", locator=f"loop/{index}"
                )
            materialized.add(operation_id)
            continue
        if entry.get("kind") != "DECIDED":
            raise H1OwnerInventoryFailure.corrupt(
                family="LOOP", owner="agent_loop", schema="journal", locator=f"loop/{index}"
            )
        try:
            candidate = publication(entry)
        except (KeyError, TypeError, ValueError) as error:
            raise H1OwnerInventoryFailure.corrupt(
                family="LOOP", owner="agent_loop", schema="journal", locator=f"loop/{index}"
            ) from error
        if (
            entry.get("operation_id") != candidate.idempotency_key
            or candidate.idempotency_key in decided
        ):
            raise H1OwnerInventoryFailure.corrupt(
                family="LOOP", owner="agent_loop", schema="journal", locator=f"loop/{index}"
            )
        decided.add(candidate.idempotency_key)
        if candidate == selected.command:
            matches.append(index)
    if (
        len(matches) != 1
        or entries[matches[0]][0] != selected.decision_id
        or entries[matches[0]][2] != selected.decision_bytes
    ):
        raise H1OwnerInventoryFailure.corrupt(
            family="HISTORICAL_LOOP",
            owner="agent_loop",
            schema="publication",
            locator=selected.command.idempotency_key,
        )
    if (
        selected.command.operation_kind != EXECUTION_COMPLETE_SEAL_OPERATION
        or selected.commit_sequence != selected.command.expected_head + 1
    ):
        raise H1OwnerInventoryFailure.corrupt(
            family="HISTORICAL_LOOP",
            owner="agent_loop",
            schema="publication",
            locator="not-complete-seal",
        )
    return matches[0], selected.commit_sequence


def _selected_input_roles(
    runtime: CommonCliExecutionRuntime,
    selected: H1SelectedPrepare,
    captured: ExecutionRunRecord,
    publications: tuple[tuple[object, ...], ...],
    rows: tuple[_PhysicalRow, ...],
) -> tuple[frozenset[str], bytes, tuple[Any, ...]]:
    """Admit only H0/R17 members independently replayed from retained evidence."""
    from chiplog.composition.h1_selected_input_role import read_h1_selected_input_role

    try:
        witness = read_h1_selected_input_role(runtime, selected, captured)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise H1OwnerInventoryFailure.corrupt(
            family="SELECTED_INPUT", owner="", schema="h0-r17", locator="witness"
        ) from error
    ids: set[str] = set()
    for occurrence in witness.occurrences:
        matching_rows = tuple(row for row in rows if row.record_id == occurrence.record_id)
        expected_publication = (
            occurrence.operation,
            occurrence.command_id,
            occurrence.command_fingerprint,
            occurrence.commit_sequence,
            occurrence.record_id,
        )
        if (
            len(matching_rows) != 1
            or (
                matching_rows[0].owner,
                matching_rows[0].schema,
                matching_rows[0].raw,
                matching_rows[0].sequence,
            )
            != (
                occurrence.owner,
                occurrence.schema,
                occurrence.canonical_bytes,
                occurrence.commit_sequence,
            )
            or publications.count(expected_publication) != 1
        ):
            raise H1OwnerInventoryFailure.corrupt(
                family="SELECTED_INPUT",
                owner=occurrence.owner,
                schema=occurrence.schema,
                locator=occurrence.record_id,
            )
        ids.add(occurrence.record_id)
    receipt_bytes = _canonical_json(
        {
            "fingerprint": witness.fingerprint,
            "initialization_decision_id": witness.initialization_decision_id,
            "occurrences": [
                (item.record_id, item.command_id, item.commit_sequence, item.reason)
                for item in witness.occurrences
            ],
        }
    )
    return (
        frozenset(ids),
        receipt_bytes,
        tuple(witness.inbound_owner_decisions),
    )


def _validate_publication_membership(
    publications: tuple[tuple[object, ...], ...], rows: tuple[_PhysicalRow, ...], tenant: str
) -> None:
    records_by_sequence: dict[int, set[str]] = {}
    for physical_row in rows:
        records_by_sequence.setdefault(physical_row.sequence, set()).add(physical_row.record_id)
    publication_sequences: set[int] = set()
    for index, publication_row in enumerate(publications):
        if len(publication_row) != 5:
            raise H1OwnerInventoryFailure.corrupt(
                family="PHYSICAL", owner="", schema="publications", locator=f"publications/{index}"
            )
        operation, key, fingerprint, sequence, record_ids = publication_row
        if (
            not all(type(value) is str for value in (operation, key, fingerprint, record_ids))
            or type(sequence) is not int
        ):
            raise H1OwnerInventoryFailure.corrupt(
                family="PHYSICAL", owner="", schema="publications", locator=f"publications/{index}"
            )
        ids = tuple(cast(str, record_ids).split("\n"))
        if not ids or any(not value for value in ids) or len(ids) != len(set(ids)):
            raise H1OwnerInventoryFailure.corrupt(
                family="PHYSICAL",
                owner="",
                schema="publications",
                locator=f"publications/{index}/record_ids",
            )
        publication_sequence = sequence
        publication_sequences.add(publication_sequence)
        if set(ids) != records_by_sequence.get(publication_sequence, set()):
            raise H1OwnerInventoryFailure.corrupt(
                family="PHYSICAL",
                owner="",
                schema="records",
                locator=f"publication/{operation}/{key}",
            )
    orphan_sequences = set(records_by_sequence) - publication_sequences
    if orphan_sequences:
        raise H1OwnerInventoryFailure.corrupt(
            family="PHYSICAL",
            owner="",
            schema="records",
            locator=f"orphan-sequences/{sorted(orphan_sequences)}",
        )


def _verify_selected_publication(
    selected: H1SelectedSeal, publications: tuple[tuple[object, ...], ...]
) -> None:
    """Bind the caller-independent selected loop command to its SQL row."""
    command = selected.command
    expected = (
        command.operation_kind,
        command.idempotency_key,
        command.request_fingerprint,
        selected.commit_sequence,
        "\n".join(record.record_id for record in command.records),
    )
    if publications.count(expected) != 1:
        raise H1OwnerInventoryFailure.corrupt(
            family="SELECTED_SEAL",
            owner="agent_loop",
            schema="publication",
            locator=command.idempotency_key,
        )


def _post_seal_delta(
    scope: H1Scope, selected: H1SelectedSeal
) -> tuple[H1Scope, dict[str, PhysicalRecord]]:
    """Allow only the authenticated final Run, response seal, and V2 registry."""
    records = selected.command.records
    expected_pairs = {
        ("agent_loop", EXECUTION_RUN_SCHEMA),
        ("agent_loop", SEAL_SCHEMA),
        ("agent_loop", RECOVERY_FRONTIER_REGISTRY_SCHEMA),
    }
    if len(records) != 3 or {(item.owner, item.schema_id) for item in records} != expected_pairs:
        raise H1OwnerInventoryFailure.corrupt(
            family="SELECTED_SEAL",
            owner="agent_loop",
            schema="publication",
            locator=selected.command.idempotency_key,
        )
    run_record = next(item for item in records if item.schema_id == EXECUTION_RUN_SCHEMA)
    seal_record = next(item for item in records if item.schema_id == SEAL_SCHEMA)
    registry_record = next(
        item for item in records if item.schema_id == RECOVERY_FRONTIER_REGISTRY_SCHEMA
    )
    try:
        run = ExecutionRunRecord.model_validate_json(run_record.canonical_bytes)
        seal = SealedResponseRecord.model_validate_json(seal_record.canonical_bytes)
    except ValueError as error:
        raise H1OwnerInventoryFailure.corrupt(
            family="SELECTED_SEAL",
            owner="agent_loop",
            schema="native",
            locator=selected.command.idempotency_key,
        ) from error
    registry = execution_h1_zero_call_frontier_registry_v2().canonical_bytes()
    if (
        run.canonical_bytes() != run_record.canonical_bytes
        or run_record.record_id != run.head
        or run.tenant != scope.tenant
        or run.principal != scope.principal
        or run.run_id != scope.run_id
        or run.predecessor not in scope.run_heads
        or seal.canonical_bytes() != seal_record.canonical_bytes
        or seal_record.record_id != "record:" + seal.digest()
        or seal.original_run_id != scope.run_id
        or seal.original_turn_id != scope.turn_id
        or registry_record.canonical_bytes != registry
        or registry_record.record_id
        != "recovery-frontier-registry:" + run.head + ":" + hashlib.sha256(registry).hexdigest()
    ):
        raise H1OwnerInventoryFailure.corrupt(
            family="SELECTED_SEAL",
            owner="agent_loop",
            schema="native",
            locator=selected.command.idempotency_key,
        )
    extended = H1Scope(
        scope.tenant,
        scope.principal,
        scope.run_id,
        scope.turn_id,
        tuple(sorted((*scope.run_heads, run.head))),
        scope.reachable_source_refs,
    )
    return extended, {item.record_id: item for item in records}


def _decode_native_row(
    row: _PhysicalRow, scope: H1Scope, permitted_delta: dict[str, PhysicalRecord] | None = None
) -> H1DecodedInventoryItem | None:
    """Prove relevance or irrelevance for the three native zero-call schemas."""
    permitted = None if permitted_delta is None else permitted_delta.get(row.record_id)
    if permitted is not None:
        if (
            row.owner != permitted.owner
            or row.schema != permitted.schema_id
            or row.raw != permitted.canonical_bytes
        ):
            raise H1OwnerInventoryFailure.corrupt(
                family="SELECTED_SEAL", owner=row.owner, schema=row.schema, locator=row.record_id
            )
        return None
    pair = (row.owner, row.schema)
    if pair == ("agent_loop", EXECUTION_RUN_SCHEMA):
        try:
            run = ExecutionRunRecord.model_validate_json(row.raw)
        except ValueError as error:
            raise H1OwnerInventoryFailure.corrupt(
                family="RUN", owner=row.owner, schema=row.schema, locator=row.record_id
            ) from error
        if run.canonical_bytes() != row.raw or row.record_id != run.head:
            raise H1OwnerInventoryFailure.corrupt(
                family="RUN", owner=row.owner, schema=row.schema, locator=row.record_id
            )
        if run.run_id == scope.run_id and run.head not in scope.run_heads:
            raise H1OwnerInventoryFailure.nonempty(
                family="RUN", owner=row.owner, schema=row.schema, locator=row.record_id
            )
        return None
    if pair == ("agent_loop", SEAL_SCHEMA):
        try:
            seal = SealedResponseRecord.model_validate_json(row.raw)
        except ValueError as error:
            raise H1OwnerInventoryFailure.corrupt(
                family="SEALED_RESPONSE", owner=row.owner, schema=row.schema, locator=row.record_id
            ) from error
        if seal.canonical_bytes() != row.raw or row.record_id != "record:" + seal.digest():
            raise H1OwnerInventoryFailure.corrupt(
                family="SEALED_RESPONSE", owner=row.owner, schema=row.schema, locator=row.record_id
            )
        if seal.original_run_id == scope.run_id:
            raise H1OwnerInventoryFailure.nonempty(
                family="SEALED_RESPONSE", owner=row.owner, schema=row.schema, locator=row.record_id
            )
        return None
    if pair == ("agent_loop", RECOVERY_FRONTIER_REGISTRY_SCHEMA):
        expected = execution_h1_zero_call_frontier_registry_v2().canonical_bytes()
        if row.raw != expected:
            raise H1OwnerInventoryFailure.corrupt(
                family="REGISTRY", owner=row.owner, schema=row.schema, locator=row.record_id
            )
        if any(
            row.record_id
            == "recovery-frontier-registry:" + head + ":" + hashlib.sha256(expected).hexdigest()
            for head in scope.run_heads
        ):
            raise H1OwnerInventoryFailure.nonempty(
                family="REGISTRY", owner=row.owner, schema=row.schema, locator=row.record_id
            )
        return None
    if (row.owner, row.schema) in h1_owner_decoder_registry():
        return _decode_leaf_row(row, scope)
    raise H1OwnerInventoryFailure.unsupported(
        family="UNMAPPED", owner=row.owner, schema=row.schema, locator=row.record_id
    )


def _complete_run_ancestry(scope: H1Scope, rows: tuple[_PhysicalRow, ...]) -> H1Scope:
    """Resolve the complete selected native Run predecessor chain from raw rows."""
    decoded: dict[str, ExecutionRunRecord] = {}
    for row in rows:
        if (row.owner, row.schema) != ("agent_loop", EXECUTION_RUN_SCHEMA):
            continue
        try:
            run = ExecutionRunRecord.model_validate_json(row.raw)
        except ValueError as error:
            raise H1OwnerInventoryFailure.corrupt(
                family="RUN", owner=row.owner, schema=row.schema, locator=row.record_id
            ) from error
        if run.canonical_bytes() != row.raw or row.record_id != run.head or run.head in decoded:
            raise H1OwnerInventoryFailure.corrupt(
                family="RUN", owner=row.owner, schema=row.schema, locator=row.record_id
            )
        decoded[run.head] = run
    heads = set(scope.run_heads)
    pending = list(heads)
    while pending:
        head = pending.pop()
        ancestor = decoded.get(head)
        if ancestor is None:
            raise H1OwnerInventoryFailure.corrupt(
                family="RUN", owner="agent_loop", schema=EXECUTION_RUN_SCHEMA, locator=head
            )
        predecessor = ancestor.predecessor
        if predecessor is not None and predecessor not in heads:
            heads.add(predecessor)
            pending.append(predecessor)
    return H1Scope(
        scope.tenant,
        scope.principal,
        scope.run_id,
        scope.turn_id,
        tuple(sorted(heads)),
        scope.reachable_source_refs,
    )


def read_h1_scoped_owner_inventory(
    runtime: CommonCliExecutionRuntime,
    *,
    captured: ExecutionRunRecord,
    selected_prepare: H1SelectedPrepare,
    workspace: H1VerifiedWorkspaceClosure,
    phase: Phase,
    selected_seal: H1SelectedSeal | None = None,
) -> H1InventoryReceipt:
    """Read every tenant physical family before making any owner-negative claim.

    The current registry supports only native Run/seal/frontier rows.  Every
    other admitted pair is reported as ``UNSUPPORTED`` with its physical
    locator, so this function cannot manufacture success from an empty
    workspace, an empty known-schema query, or a DTO-only decoder registry.
    """
    if phase not in ("PRE_SEAL", "POST_SEAL", "HISTORICAL"):
        raise ValueError("unknown H1 inventory phase")
    if (phase == "PRE_SEAL" and selected_seal is not None) or (
        phase != "PRE_SEAL" and type(selected_seal) is not H1SelectedSeal
    ):
        raise ValueError("H1 inventory phase has an invalid selected seal argument")
    scope = _scope(captured, selected_prepare, workspace)
    gate_factory = getattr(runtime, "_authority_gate", None)
    if not callable(gate_factory):
        raise H1OwnerInventoryFailure.unsupported(
            family="GATE", owner="", schema="", locator="runtime"
        )
    gate = gate_factory()
    if not callable(getattr(gate, "hold", None)):
        raise H1OwnerInventoryFailure.unsupported(
            family="GATE", owner="", schema="", locator="hold"
        )

    with gate.hold():
        try:
            runtime._check_database_identity()
            loop_entries = tuple(runtime._loop_decisions().entries())
            owner_journal = runtime._owner_decisions()
            owner_snapshot = owner_journal.snapshot()
            owner_entries = tuple(owner_journal._raw.entries())
            _reauthenticate_selected_prepare(
                runtime, captured, selected_prepare, workspace, phase, selected_seal
            )
            scope = _merge_workspace_sources(scope, _workspace_inventory_item(runtime, workspace))
        except H1OwnerInventoryFailure:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise H1OwnerInventoryFailure.corrupt(
                family="JOURNAL", owner="", schema="", locator="runtime"
            ) from error
        if any(
            not isinstance(item[0], str) or not isinstance(item[2], bytes) for item in loop_entries
        ):
            raise H1OwnerInventoryFailure.corrupt(
                family="LOOP", owner="agent_loop", schema="journal", locator="entries"
            )
        if any(
            not isinstance(item[0], str) or not isinstance(item[2], bytes) for item in owner_entries
        ):
            raise H1OwnerInventoryFailure.corrupt(
                family="OWNER", owner="owner-journal", schema="journal", locator="entries"
            )
        loop_cut = len(loop_entries)
        boundary: int | None = None
        permitted_delta: dict[str, PhysicalRecord] | None = None
        if phase != "PRE_SEAL":
            assert selected_seal is not None
            selected_index, selected_sequence = _selected_boundary(
                runtime, loop_entries, selected_seal
            )
            scope, permitted_delta = _post_seal_delta(scope, selected_seal)
            if phase == "HISTORICAL":
                loop_cut, boundary = selected_index + 1, selected_sequence
        if runtime._pending_owners() or runtime._pending() or runtime._pending_gate_publications():
            raise H1OwnerInventoryFailure.incomplete(family="PENDING", locator="runtime")

        with read_connection(runtime._database) as connection:
            commitment = capture_authority_snapshot_commitment(connection, scope.tenant)
            anchored = runtime._commitment_journal.load(scope.tenant)
            if commitment != anchored:
                raise H1OwnerInventoryFailure.corrupt(
                    family="ANCHOR", owner="", schema="", locator="commitment"
                )
            head_row = connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (scope.tenant,)
            ).fetchone()
            tenant_sequence = 0 if head_row is None else int(head_row[0])
            limit = "" if boundary is None else " AND commit_sequence <= ?"
            params: tuple[object, ...] = (
                (scope.tenant,) if boundary is None else (scope.tenant, boundary)
            )
            publications = tuple(
                connection.execute(
                    "SELECT operation_kind,idempotency_key,request_fingerprint,"
                    "commit_sequence,record_ids "
                    "FROM publications WHERE tenant_id=?"
                    + limit
                    + " ORDER BY commit_sequence,operation_kind,idempotency_key",
                    params,
                )
            )
            records_raw = tuple(
                connection.execute(
                    "SELECT record_id,owner,schema_id,canonical_bytes,commit_sequence FROM records "
                    "WHERE tenant_id=?" + limit + " ORDER BY commit_sequence,record_id",
                    params,
                )
            )
            evidence = tuple(
                connection.execute(
                    "SELECT source_id,evidence_id,fingerprint,canonical_bytes,followup_kind,"
                    "state,attempt_id,transport_version,cursor "
                    "FROM evidence_inbox WHERE tenant_id=? ORDER BY source_id,evidence_id",
                    (scope.tenant,),
                )
            )
        owner_prefix_bytes = b""
        selected_input_bytes = b""
        selected_input_ids: frozenset[str] = frozenset()
        selected_input_decisions: tuple[Any, ...] = ()
        try:
            rows = tuple(
                _PhysicalRow(str(a), str(b), str(c), bytes(d), int(e))
                for a, b, c, d, e in records_raw
            )
            if phase == "HISTORICAL":
                assert selected_seal is not None
                owner_snapshot, owner_prefix_bytes = _reconcile_historical_owner_cut(
                    scope.tenant, owner_journal, selected_seal, publications, rows
                )
            if phase in ("PRE_SEAL", "POST_SEAL"):
                (
                    selected_input_ids,
                    selected_input_bytes,
                    selected_input_decisions,
                ) = _selected_input_roles(runtime, selected_prepare, captured, publications, rows)
            _validate_publication_membership(publications, rows, scope.tenant)
            scope = _complete_run_ancestry(scope, rows)
            if selected_seal is not None:
                _verify_selected_publication(selected_seal, publications)
            decoded_rows: list[tuple[_PhysicalRow, H1DecodedInventoryItem]] = []
            for row in rows:
                decoded = _decode_native_row(row, scope, permitted_delta)
                if decoded is not None:
                    decoded_rows.append((row, decoded))
            _classify_leaf_scope(tuple(decoded_rows), scope, selected_input_ids)
        except H1OwnerInventoryFailure:
            raise
        except (TypeError, ValueError) as error:
            raise H1OwnerInventoryFailure.corrupt(
                family="PHYSICAL", owner="", schema="", locator="rows"
            ) from error
        if evidence:
            _decode_evidence_items(_evidence_items(scope.tenant, evidence), scope)
        if phase != "HISTORICAL":
            _reconcile_current_owner_cut(
                scope.tenant,
                owner_entries,
                publications,
                rows,
                phase,
                selected_input_decisions,
            )
        physical_bytes = _encoded_rows(tuple(tuple(row) for row in publications) + records_raw)
        evidence_bytes = _encoded_rows(evidence)
        pending_bytes = _canonical_json({"owner": [], "loop": [], "gate": []})
        source_bytes = _canonical_json(
            [item.model_dump(mode="json") for item in scope.reachable_source_refs]
        )
        registry_bytes = _canonical_json(
            sorted(
                (owner, schema, name)
                for (owner, schema), name in h1_owner_decoder_registry().items()
            )
        )
        registry_fingerprint = hashlib.sha256(registry_bytes).hexdigest()
        identity = runtime._database_identity
        preimage = _canonical_json(
            {
                "domain": "chiplog.h1.preseal-inventory.v1",
                "scope": scope.__dict__
                if hasattr(scope, "__dict__")
                else {
                    "tenant": scope.tenant,
                    "principal": scope.principal,
                    "run_id": scope.run_id,
                    "turn_id": scope.turn_id,
                    "run_heads": scope.run_heads,
                    "reachable_source_refs": [
                        x.model_dump(mode="json") for x in scope.reachable_source_refs
                    ],
                },
                "database_identity": identity,
                "tenant_sequence": tenant_sequence,
                "commitment": commitment,
                "loop_entries": base64.b64encode(_encoded_rows(loop_entries[:loop_cut])).decode(),
                "owner_head": owner_snapshot.head,
                "owner_prefix": base64.b64encode(owner_prefix_bytes).decode(),
                "selected_input": base64.b64encode(selected_input_bytes).decode(),
                "physical": base64.b64encode(physical_bytes).decode(),
                "evidence": base64.b64encode(evidence_bytes).decode(),
                "pending": base64.b64encode(pending_bytes).decode(),
                "sources": base64.b64encode(source_bytes).decode(),
                "registry": registry_fingerprint,
            }
        )
        return H1InventoryReceipt(
            scope,
            identity,
            tenant_sequence,
            commitment,
            loop_entries[:loop_cut],
            owner_snapshot,
            physical_bytes,
            evidence_bytes,
            pending_bytes,
            source_bytes,
            registry_fingerprint,
            hashlib.sha256(preimage).hexdigest(),
        )


__all__ = [
    "H1OwnerInventoryFailure",
    "h1_owner_decoder_registry",
    "read_h1_scoped_owner_inventory",
    "unsupported_runtime_record_pairs",
]
