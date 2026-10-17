"""Pure, closed delivery-work genesis and first-claim preparation.

These records describe delivery work after a selected V4 terminal completion.
They do not send, authorize a delivery, read a clock, or write a journal.
The broker must authenticate the selected source and perform the CAS publication.
"""

from __future__ import annotations

import hashlib
from typing import Final, Literal

from pydantic import Field, model_validator

from .recovery_contracts import Digest, Identity, RecoveryDTO

DELIVERY_WORK_SCHEMA: Final = "chiplog.delivery-work.subject.v1"
DELIVERY_WORK_OWNER = "agent_loop"
DELIVERY_WORK_GENESIS_COMMAND_SCHEMA: Final = "chiplog.agent-loop.prepare-delivery-work-genesis.v1"
DELIVERY_WORK_CLAIM_COMMAND_SCHEMA: Final = "chiplog.agent-loop.prepare-delivery-work-claim.v1"


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def delivery_work_subject_id(tenant_id: str, intent_id: str) -> str:
    """A stable subject identity scoped to one selected Effects intent."""
    if not tenant_id or not intent_id:
        raise ValueError("delivery work subject needs tenant and intent")
    tenant_bytes = tenant_id.encode("utf-8")
    intent_bytes = intent_id.encode("utf-8")
    return "delivery-work:" + _digest(
        b"chiplog.delivery-work.subject.v1\0"
        + len(tenant_bytes).to_bytes(8, "big")
        + tenant_bytes
        + len(intent_bytes).to_bytes(8, "big")
        + intent_bytes
    )


def delivery_work_record_id(subject_id: str, generation: int) -> str:
    if not subject_id or generation not in (0, 1):
        raise ValueError("delivery work record generation is unsupported")
    return subject_id + ":g" + str(generation)


class DeliveryWorkSourceMemberV1(RecoveryDTO):
    owner: Identity
    record_kind: Identity
    record_id: Identity
    schema_id: Identity
    canonical_record_bytes: bytes
    fingerprint: Digest

    @model_validator(mode="after")
    def exact_fingerprint(self) -> DeliveryWorkSourceMemberV1:
        if self.fingerprint != _digest(self.canonical_record_bytes):
            raise ValueError("delivery work source fingerprint differs")
        return self


class DeliveryWorkSourceV1(RecoveryDTO):
    tenant_id: Identity
    intent_id: Identity
    selected_decision_id: Identity
    selected_decision_fingerprint: Digest
    terminal_run: DeliveryWorkSourceMemberV1
    terminal_manifest: DeliveryWorkSourceMemberV1
    intent: DeliveryWorkSourceMemberV1

    @model_validator(mode="after")
    def exact_members(self) -> DeliveryWorkSourceV1:
        if (
            self.terminal_run.owner != DELIVERY_WORK_OWNER
            or self.terminal_manifest.owner != DELIVERY_WORK_OWNER
            or self.intent.owner != "effects"
            or self.intent_id != self.intent.record_id
            or len(
                {
                    self.terminal_run.record_id,
                    self.terminal_manifest.record_id,
                    self.intent.record_id,
                }
            )
            != 3
        ):
            raise ValueError("delivery work sources have wrong owners or duplicate records")
        return self

    @property
    def subject_id(self) -> str:
        return delivery_work_subject_id(self.tenant_id, self.intent_id)


class DeliveryWorkStateV1(RecoveryDTO):
    schema_id: Literal["chiplog.delivery-work.subject.v1"] = DELIVERY_WORK_SCHEMA
    subject_id: Identity
    record_id: Identity
    generation: int = Field(ge=0, le=1)
    state: Literal["UNLEASED", "CLAIMED"]
    selected_source: DeliveryWorkSourceV1
    predecessor_head: str | None = None
    holder_id: str | None = None
    session_id: str | None = None
    lease_expires_at_ns: int | None = Field(default=None, ge=0)
    trusted_clock_ns: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def exact_transition_shape(self) -> DeliveryWorkStateV1:
        if (
            self.schema_id != DELIVERY_WORK_SCHEMA
            or self.subject_id != self.selected_source.subject_id
        ):
            raise ValueError("delivery work subject differs from selected source")
        if self.record_id != delivery_work_record_id(self.subject_id, self.generation):
            raise ValueError("delivery work record ID differs from subject generation")
        if self.generation == 0:
            if (
                self.state != "UNLEASED"
                or self.predecessor_head is not None
                or any(
                    value is not None
                    for value in (
                        self.holder_id,
                        self.session_id,
                        self.lease_expires_at_ns,
                        self.trusted_clock_ns,
                    )
                )
            ):
                raise ValueError("delivery work genesis must be unleased")
        elif (
            self.state != "CLAIMED"
            or not self.predecessor_head
            or not self.holder_id
            or not self.session_id
            or self.lease_expires_at_ns is None
            or self.trusted_clock_ns is None
            or self.lease_expires_at_ns <= self.trusted_clock_ns
        ):
            raise ValueError("delivery work claim differs from exact leased transition")
        return self

    @property
    def head(self) -> str:
        return DELIVERY_WORK_SCHEMA + ":" + _digest(self.canonical_bytes())


class PrepareDeliveryWorkGenesisV1(RecoveryDTO):
    """The sole owner command that derives an unleased delivery-work genesis."""

    schema_id: Literal["chiplog.agent-loop.prepare-delivery-work-genesis.v1"] = (
        DELIVERY_WORK_GENESIS_COMMAND_SCHEMA
    )
    selected_decision_id: Identity
    selected_decision_fingerprint: Digest
    selected_source: DeliveryWorkSourceV1

    @model_validator(mode="after")
    def selected_source_is_exact(self) -> PrepareDeliveryWorkGenesisV1:
        if (
            self.selected_decision_id != self.selected_source.selected_decision_id
            or self.selected_decision_fingerprint
            != self.selected_source.selected_decision_fingerprint
        ):
            raise ValueError("delivery work genesis command selected decision differs")
        return self


class DeliveryWorkClaimProjectionV1(RecoveryDTO):
    """Authority-free g0-to-g1 shape; lease facts arrive through broker binding."""

    selected_source: DeliveryWorkSourceV1
    predecessor: DeliveryWorkStateV1
    predecessor_head: Identity
    subject_id: Identity
    record_id: Identity

    @model_validator(mode="after")
    def exact_first_claim_shape(self) -> DeliveryWorkClaimProjectionV1:
        if (
            self.predecessor.selected_source != self.selected_source
            or self.predecessor_head != self.predecessor.head
            or self.subject_id != self.selected_source.subject_id
            or self.record_id != delivery_work_record_id(self.subject_id, 1)
            or self.predecessor.subject_id != self.subject_id
            or self.predecessor.generation != 0
            or self.predecessor.state != "UNLEASED"
        ):
            raise ValueError("delivery work claim projection differs from exact unleased genesis")
        return self


class PrepareDeliveryWorkClaimV1(RecoveryDTO):
    """Versioned owner command with no claimant, session, clock, or lease authority."""

    schema_id: Literal["chiplog.agent-loop.prepare-delivery-work-claim.v1"] = (
        DELIVERY_WORK_CLAIM_COMMAND_SCHEMA
    )
    projection: DeliveryWorkClaimProjectionV1


def prepare_delivery_work_genesis(source: DeliveryWorkSourceV1) -> DeliveryWorkStateV1:
    """Prepare the only generation-zero state from exact selected V4 source bytes."""
    source = DeliveryWorkSourceV1.model_validate_json(source.canonical_bytes())
    subject_id = source.subject_id
    return DeliveryWorkStateV1(
        subject_id=subject_id,
        record_id=delivery_work_record_id(subject_id, 0),
        generation=0,
        state="UNLEASED",
        selected_source=source,
    )


def prepare_delivery_work_claim_projection(
    source: DeliveryWorkSourceV1, predecessor: DeliveryWorkStateV1
) -> DeliveryWorkClaimProjectionV1:
    """Project the immutable first-claim inputs without inventing lease authority."""
    source = DeliveryWorkSourceV1.model_validate_json(source.canonical_bytes())
    predecessor = DeliveryWorkStateV1.model_validate_json(predecessor.canonical_bytes())
    return DeliveryWorkClaimProjectionV1(
        selected_source=source,
        predecessor=predecessor,
        predecessor_head=predecessor.head,
        subject_id=source.subject_id,
        record_id=delivery_work_record_id(source.subject_id, 1),
    )


def prepare_delivery_work_claim(
    source: DeliveryWorkSourceV1,
    predecessor: DeliveryWorkStateV1,
    *,
    holder_id: str,
    session_id: str,
    lease_expires_at_ns: int,
    trusted_clock_ns: int,
) -> DeliveryWorkStateV1:
    """Prepare exactly one first claim; publication still owns current-head CAS."""
    source = DeliveryWorkSourceV1.model_validate_json(source.canonical_bytes())
    predecessor = DeliveryWorkStateV1.model_validate_json(predecessor.canonical_bytes())
    if (
        predecessor.selected_source != source
        or predecessor.subject_id != source.subject_id
        or predecessor.generation != 0
        or predecessor.state != "UNLEASED"
    ):
        raise ValueError("delivery work claim predecessor differs from selected genesis")
    return DeliveryWorkStateV1(
        subject_id=source.subject_id,
        record_id=delivery_work_record_id(source.subject_id, 1),
        generation=1,
        state="CLAIMED",
        selected_source=source,
        predecessor_head=predecessor.head,
        holder_id=holder_id,
        session_id=session_id,
        lease_expires_at_ns=lease_expires_at_ns,
        trusted_clock_ns=trusted_clock_ns,
    )
