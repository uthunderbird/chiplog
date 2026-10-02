"""Authenticated current R16 source inventory, without recovery authority.

This collector never applies H1's zero-call scope rules. Every tenant occurrence
is retained and registered leaves decode before relevance can be considered.
The missing authenticated R17 call relevance carrier is an explicit HOLD.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from chiplog.composition.h1_inventory_effect_delivery import (
    REGISTRATIONS as EFFECT_REGISTRATIONS,
)
from chiplog.composition.h1_inventory_effect_delivery import decode_h1_effect_delivery_scope
from chiplog.composition.h1_inventory_ingress_evidence import (
    AUTHENTICATED_COMMAND_SCHEMA,
    CUSTODY_COMMAND_SCHEMA,
    decode_h1_ingress_evidence_scope,
)
from chiplog.composition.h1_inventory_ingress_evidence import (
    REGISTRATIONS as EVIDENCE_REGISTRATIONS,
)
from chiplog.composition.h1_inventory_leaf_contracts import (
    H1DecodedInventoryItem,
    H1OwnerInventoryFailure,
    H1RawInventoryItem,
)
from chiplog.composition.h1_inventory_owner_cut import (
    OWNER_PUBLICATION_OPERATIONS,
    H1OwnerCut,
    OwnerJournalSqlPublication,
    OwnerJournalSqlRecord,
    reconcile_h1_owner_cut,
)
from chiplog.composition.h1_owner_inventory import _evidence_items, _physical_item, _PhysicalRow
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.ingress_authenticated_contracts import AuthenticatedCustodyCommand
from chiplog.platform.ingress_custody_records import CustodyCommand
from chiplog.platform.owner_decision_journal import snapshot_from_captured_entries
from chiplog.platform.owner_publications import source_commands
from chiplog.platform.workspace_snapshot import read_connection

if TYPE_CHECKING:
    from chiplog.composition.r16_dispatch_runtime import R16DispatchRuntime


class R16InventoryIntegrityError(RuntimeError):
    """Unverifiable source: no partial inventory may escape the boundary."""

    status: Literal["HOLD"] = "HOLD"

    def __init__(self, tenant: str, locator: str) -> None:
        self.tenant = tenant
        self.locator = locator
        super().__init__(
            f"R16 inventory integrity: operation=collect tenant={tenant} record={locator}"
        )


@dataclass(frozen=True, slots=True)
class R16InventoryGap:
    code: str
    locator: str


@dataclass(frozen=True, slots=True)
class R16NativeInventory:
    """Complete authenticated raw capture; leaf decoding is not call authority."""

    tenant: str
    commitment: str
    owner_head: str | None
    owner_entries: tuple[tuple[str, str | None, bytes], ...]
    owner_cut: H1OwnerCut
    publications: tuple[OwnerJournalSqlPublication, ...]
    records: tuple[OwnerJournalSqlRecord, ...]
    evidence_rows: tuple[tuple[object, ...], ...]
    occurrences: tuple[H1RawInventoryItem, ...]
    decoded: tuple[H1DecodedInventoryItem, ...]
    gaps: tuple[R16InventoryGap, ...]
    status: Literal["HOLD"] = "HOLD"


def _decode(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    for registrations, decoder in (
        (EFFECT_REGISTRATIONS, decode_h1_effect_delivery_scope),
        (EVIDENCE_REGISTRATIONS, decode_h1_ingress_evidence_scope),
    ):
        if any(
            (entry.surface, entry.owner, entry.schema) == (item.surface, item.owner, item.schema)
            for entry in registrations
        ):
            return decoder(item)
    raise H1OwnerInventoryFailure.unsupported(
        family="R16_NATIVE", owner=item.owner, schema=item.schema, locator=item.locator
    )


def _physical_occurrence(row: _PhysicalRow, tenant: str) -> H1RawInventoryItem:
    item = _physical_item(row, tenant)
    # SQL retains owner/schema/bytes but no record_kind column. These closed
    # wire shapes use the owner publication's namespace; the leaf subsequently
    # checks this envelope against the fully decoded canonical record.
    if (
        row.owner == "effects"
        and row.schema
        in {
            "chiplog.effects.record.v1",
            "chiplog.effects.dispatch-record.v2",
            "chiplog.effects.dispatch-outcome-record.v2",
        }
        and item.record_kind is not None
    ):
        return replace(item, record_kind="effects." + item.record_kind)
    if (row.owner, row.schema) == (
        "broker_dispatch",
        "chiplog.broker-dispatch.send-consumption.v1",
    ):
        return replace(item, record_kind="send-consumption")
    return item


def collect_r16_native_inventory(runtime: R16DispatchRuntime) -> R16NativeInventory:
    """Enumerate one current authenticated tenant cut under its mounted gate.

    Pending decisions are retained, not projected into historical authority.
    Unsupported occurrences are explicit gaps. Corruption raises a typed HOLD
    with the root cause, rather than returning successfully decoded fragments.
    """
    tenant = runtime._tenant_id
    locator = "gate"
    try:
        with runtime._authority_gate().hold():
            locator = "database-identity"
            runtime._check_database_identity()
            locator = "owner-journal"
            journal = runtime._owner_decisions()
            owner_entries = tuple(journal._raw.entries())
            owner_snapshot = snapshot_from_captured_entries(owner_entries, tenant)
            if owner_snapshot != journal.snapshot():
                raise ValueError("captured owner prefix differs from mounted journal")
            locator = "physical-anchor"
            with read_connection(runtime._database) as connection:
                commitment = capture_authority_snapshot_commitment(connection, tenant)
                if commitment != runtime._commitment_journal.load(tenant):
                    raise ValueError("physical commitment differs from independent anchor")
                raw_publications = tuple(
                    connection.execute(
                        "SELECT operation_kind,idempotency_key,request_fingerprint,"
                        "commit_sequence,record_ids FROM publications WHERE tenant_id=? "
                        "ORDER BY commit_sequence,operation_kind,idempotency_key",
                        (tenant,),
                    )
                )
                raw_records = tuple(
                    connection.execute(
                        "SELECT record_id,owner,schema_id,canonical_bytes,commit_sequence "
                        "FROM records WHERE tenant_id=? ORDER BY commit_sequence,record_id",
                        (tenant,),
                    )
                )
                evidence = tuple(
                    connection.execute(
                        "SELECT source_id,evidence_id,fingerprint,canonical_bytes,followup_kind,"
                        "state,attempt_id,transport_version,cursor FROM evidence_inbox "
                        "WHERE tenant_id=? ORDER BY source_id,evidence_id",
                        (tenant,),
                    )
                )
            locator = "owner-physical-membership"
            publications = tuple(
                OwnerJournalSqlPublication(
                    operation, key, fingerprint, sequence, tuple(ids.split("\n"))
                )
                for operation, key, fingerprint, sequence, ids in raw_publications
            )
            records = tuple(OwnerJournalSqlRecord(*row) for row in raw_records)
            owner_cut = reconcile_h1_owner_cut(
                tenant=tenant,
                raw_entries=owner_entries,
                publications=publications,
                records=records,
                phase="PRE_SEAL",
            )
            gaps = [R16InventoryGap("R17_CALL_RELEVANCE_UNAVAILABLE", "call-scope")]
            if runtime._pending() or runtime._pending_gate_publications():
                gaps.append(R16InventoryGap("PENDING", "runtime"))
            for decision in owner_cut.decisions:
                if not decision.materialized:
                    gaps.append(R16InventoryGap("PENDING", f"selected/{decision.command_id}"))
            for publication in publications:
                if publication.operation not in OWNER_PUBLICATION_OPERATIONS:
                    gaps.append(
                        R16InventoryGap(
                            "UNSUPPORTED_PUBLICATION", f"publications/{publication.command_id}"
                        )
                    )
            occurrences: list[H1RawInventoryItem] = []
            for selected in owner_snapshot.decisions:
                batch = selected.prepared.request
                prefix = f"selected/{selected.decision_id}"
                for index, command in enumerate(source_commands(batch)):
                    command_record_id: str | None = None
                    if command.owner == "broker_ingress":
                        if command.schema_id == CUSTODY_COMMAND_SCHEMA:
                            command_record_id = CustodyCommand.model_validate_json(
                                command.canonical_bytes
                            ).command_id
                        elif command.schema_id == AUTHENTICATED_COMMAND_SCHEMA:
                            command_record_id = AuthenticatedCustodyCommand.model_validate_json(
                                command.canonical_bytes
                            ).command_id
                    occurrences.append(
                        H1RawInventoryItem(
                            "OWNER_COMMAND",
                            f"{prefix}/command/{index}",
                            tenant,
                            command.owner,
                            command.schema_id,
                            None,
                            command_record_id,
                            command.fingerprint,
                            command.canonical_bytes,
                        )
                    )
                for index, member in enumerate(batch.complete_records):
                    # PHYSICAL denotes the leaf wire shape here; the distinct
                    # selected locator preserves an unmaterialized occurrence.
                    occurrences.append(
                        H1RawInventoryItem(
                            "PHYSICAL",
                            f"{prefix}/member/{index}",
                            tenant,
                            member.owner,
                            member.schema_id,
                            member.record_kind,
                            member.record_id,
                            member.fingerprint,
                            member.canonical_bytes,
                        )
                    )
            for row in raw_records:
                occurrences.append(_physical_occurrence(_PhysicalRow(*row), tenant))
            locator = "evidence-inbox"
            occurrences.extend(_evidence_items(tenant, evidence))
            decoded: list[H1DecodedInventoryItem] = []
            for item in occurrences:
                locator = item.locator
                try:
                    decoded.append(_decode(item))
                except H1OwnerInventoryFailure as error:
                    if error.code != "UNSUPPORTED":
                        raise
                    gaps.append(R16InventoryGap("UNSUPPORTED", item.locator))
                if item.owner == "broker_ingress":
                    gaps.append(R16InventoryGap("CUSTODY_AUTHORITY_UNAVAILABLE", item.locator))
            return R16NativeInventory(
                tenant,
                commitment,
                owner_snapshot.head,
                owner_entries,
                owner_cut,
                publications,
                records,
                evidence,
                tuple(occurrences),
                tuple(decoded),
                tuple(gaps),
            )
    except (OSError, RuntimeError, TypeError, ValueError, AttributeError, sqlite3.Error) as error:
        raise R16InventoryIntegrityError(tenant, locator) from error
