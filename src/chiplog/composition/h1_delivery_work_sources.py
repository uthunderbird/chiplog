"""Authenticated V4 selected source and post-completion work-state absence read."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from chiplog.capabilities.agent_loop.completion_owner_record_contracts import (
    CompletionCanonicalMember,
    make_terminal_manifest_member,
    make_terminal_run_member,
)
from chiplog.capabilities.agent_loop.delivery_work_contracts import (
    DELIVERY_WORK_SCHEMA,
    DeliveryWorkSourceMemberV1,
    DeliveryWorkSourceV1,
    delivery_work_record_id,
    delivery_work_subject_id,
)
from chiplog.capabilities.effects.scoped_intent_record_contracts import (
    ScopedIntentCanonicalMemberV3,
    make_scoped_intent_member,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV4
from chiplog.composition.h1_inventory_owner_cut import OwnerJournalSqlRecord
from chiplog.composition.r16_effects import _read_materialized_effects_with_history
from chiplog.composition.scoped_delivery_selected_sources import ScopedDeliverySelectedSources
from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2, OwnerRecordBytes
from chiplog.platform.workspace_snapshot import read_connection

WORK_SCHEMA_PREFIX = "chiplog.delivery-work."


class H1DeliveryWorkSourceHold(ValueError):
    pass


def require_delivery_work_absent(
    rows: tuple[OwnerJournalSqlRecord, ...], reserved_record_ids: tuple[str, str]
) -> None:
    """Only this subject is reserved; any occupant of its IDs fails closed."""
    if len(set(reserved_record_ids)) != 2 or any(not value for value in reserved_record_ids):
        raise TypeError("delivery work reservation is invalid")
    if any(
        row.record_id in reserved_record_ids
        or (row.schema.startswith(WORK_SCHEMA_PREFIX) and row.schema != DELIVERY_WORK_SCHEMA)
        for row in rows
    ):
        raise H1DeliveryWorkSourceHold("delivery-work subject record is present or unknown")


@dataclass(frozen=True, slots=True)
class DeliveryWorkLocatorV1:
    intent_id: str


@dataclass(frozen=True, slots=True)
class SelectedDeliveryWorkSourceV1:
    tenant_id: str
    decision_id: str
    decision_fingerprint: str
    terminal_run: OwnerRecordBytes
    terminal_manifest: OwnerRecordBytes
    intent: OwnerRecordBytes
    subject_id: str
    reserved_record_ids: tuple[str, str]

    def owner_source(self) -> DeliveryWorkSourceV1:
        """Project only the authenticated immutable inputs into the owner DTO."""

        def member(value: OwnerRecordBytes) -> DeliveryWorkSourceMemberV1:
            return DeliveryWorkSourceMemberV1(
                owner=value.owner,
                record_kind=value.record_kind,
                record_id=value.record_id,
                schema_id=value.schema_id,
                canonical_record_bytes=value.canonical_bytes,
                fingerprint=value.fingerprint,
            )

        return DeliveryWorkSourceV1(
            tenant_id=self.tenant_id,
            intent_id=self.intent.record_id,
            selected_decision_id=self.decision_id,
            selected_decision_fingerprint=self.decision_fingerprint,
            terminal_run=member(self.terminal_run),
            terminal_manifest=member(self.terminal_manifest),
            intent=member(self.intent),
        )


class H1DeliveryWorkSources:
    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("installed runtime required")
        self.runtime = runtime
        self._selected = ScopedDeliverySelectedSources(runtime)

    @staticmethod
    def _record(
        owner: Literal["agent_loop", "effects"],
        m: CompletionCanonicalMember | ScopedIntentCanonicalMemberV3,
    ) -> OwnerRecordBytes:
        return OwnerRecordBytes(
            owner=owner,
            record_kind=m.record_kind,
            record_id=m.record_id,
            schema_id=m.schema_id,
            canonical_bytes=m.canonical_record_bytes,
            fingerprint=m.fingerprint,
        )

    def read_absent(self, locator: DeliveryWorkLocatorV1) -> SelectedDeliveryWorkSourceV1:
        if type(locator) is not DeliveryWorkLocatorV1 or not locator.intent_id:
            raise TypeError("invalid locator")
        with self.runtime._authority_gate().hold():
            selected = self._selected.read(intent_id=locator.intent_id)
            decision, issuance = selected.selected_h1.decision, selected.selected_h1.issuance
            batch = decision.prepared.request
            if (
                type(batch) is not CompleteDeliveryBatchV2
                or type(issuance) is not H1CompletionIssuanceV4
            ):
                raise H1DeliveryWorkSourceHold("V4 selection required")
            p = issuance.assembly.prepared_completion
            wanted = (
                self._record("agent_loop", make_terminal_run_member(p.run)),
                self._record("agent_loop", make_terminal_manifest_member(p.terminal_manifest)),
                self._record(
                    "effects",
                    make_scoped_intent_member(issuance.effects_result.intent_result.intent),
                ),
            )
            if any(
                sum(x.record_id == w.record_id for x in batch.complete_records) != 1
                or w not in batch.complete_records
                for w in wanted
            ):
                raise H1DeliveryWorkSourceHold("same-batch V4 members differ")
            effects_cut, owner_snapshot = _read_materialized_effects_with_history(
                self.runtime, self.runtime._owner_decisions()
            )
            if decision not in owner_snapshot.decisions or not any(
                row.commit_sequence == decision.tenant_commit_sequence and row.record == wanted[2]
                for row in effects_cut.rows
            ):
                raise H1DeliveryWorkSourceHold("selected V4 physical intent differs")
            with read_connection(self.runtime._database) as c:
                rows = tuple(
                    OwnerJournalSqlRecord(*row)
                    for row in c.execute(
                        "SELECT record_id,owner,schema_id,canonical_bytes,commit_sequence "
                        "FROM records WHERE tenant_id=? ORDER BY commit_sequence,record_id",
                        (batch.identity.tenant_id,),
                    )
                )
            subject_id = delivery_work_subject_id(batch.identity.tenant_id, wanted[2].record_id)
            reserved_record_ids = (
                delivery_work_record_id(subject_id, 0),
                delivery_work_record_id(subject_id, 1),
            )
            require_delivery_work_absent(rows, reserved_record_ids)
            return SelectedDeliveryWorkSourceV1(
                batch.identity.tenant_id,
                decision.decision_id,
                decision.decision_fingerprint,
                *wanted,
                subject_id,
                reserved_record_ids,
            )
