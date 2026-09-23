"""Retained PRE_AUTH custody transitions; constructing these values grants no authority."""

import hashlib
import json
from typing import Literal

from pydantic import Field

from chiplog.platform._ingress_contracts import (
    Head,
    IngressDTO,
    ReceiptToken,
    RetainedRetry,
    SourceClass,
    UnknownEndpoint,
)
from chiplog.platform._ingress_domain import (
    CustodySnapshot,
    IngressConflict,
    IngressHold,
    TokenEntry,
    allocate_token,
    publish_custody,
    stage_raw,
)
from chiplog.platform.broker import BrokerSession


def canonical(value: IngressDTO) -> bytes:
    return json.dumps(
        value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def reference(identity: str, raw: bytes) -> Head:
    value = digest(raw)
    return Head(identity=identity, head=identity + "/" + value, fingerprint=value)


class CustodyProfile(IngressDTO):
    schema_id: Literal["chiplog.ingress.retained-profile.v1"] = (
        "chiplog.ingress.retained-profile.v1"
    )
    tenant_id: str = Field(min_length=1)
    database_id: str = Field(min_length=1)
    source_identity: str = Field(min_length=1)
    source_class: SourceClass
    transport_version: str = Field(min_length=1)
    maximum_items: int = Field(gt=0, le=2**64 - 1)
    maximum_total_bytes: int = Field(gt=0, le=2**64 - 1)
    maximum_item_bytes: int = Field(gt=0, le=2**64 - 1)

    def head(self) -> Head:
        return reference("ingress.source-profile", canonical(self))

    def epoch(self) -> Head:
        return reference("ingress.admission-epoch", canonical(self))

    def empty(self) -> CustodySnapshot:
        epoch = self.epoch()
        return CustodySnapshot(epoch.identity, epoch.head, 0, "OPEN")


class RetentionClaim(IngressDTO):
    """Historical evidence only; the live adapter must independently verify it."""

    slot_id: str = Field(min_length=1)
    raw_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=0, le=2**64 - 1)
    proof: Head
    observation_bytes: bytes = Field(min_length=1)


class CustodyCommand(IngressDTO):
    schema_id: Literal["chiplog.ingress.retained-command.v1"] = (
        "chiplog.ingress.retained-command.v1"
    )
    operation: Literal[
        "ingress.allocate_receipt_token",
        "ingress.stage_raw_bytes",
        "ingress.publish_custody_successor",
    ]
    command_id: str = Field(min_length=1)
    profile: CustodyProfile
    predecessor: Head | None
    token: ReceiptToken
    retention: RetentionClaim
    raw_bytes: bytes | None
    broker_session: BrokerSession
    read_state_bytes: bytes = Field(min_length=1)
    tenant_frontier: int = Field(ge=0, le=2**64 - 1)


def subject_id(profile: CustodyProfile, slot_id: str, operation: str) -> str:
    raw = json.dumps(
        [profile.tenant_id, profile.database_id, profile.source_identity, slot_id],
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return operation + ":" + digest(raw)


def allocation_fingerprint(
    profile: CustodyProfile, claim: RetentionClaim, maximum_bytes: int
) -> str:
    return digest(
        canonical(profile) + b"\x00" + canonical(claim) + b"\x00" + str(maximum_bytes).encode()
    )


class CustodyEntry(IngressDTO):
    token: ReceiptToken
    mode: Literal["RETAINED_SOURCE"] = "RETAINED_SOURCE"
    retention_proof: Head
    state_head: Head
    staged_bytes: bytes | None
    staged_digest: str | None
    custody: RetainedRetry | None

    @classmethod
    def from_entry(cls, entry: TokenEntry) -> CustodyEntry:
        if entry.mode != "RETAINED_SOURCE" or entry.retention_proof is None:
            raise IngressHold("unregistered retention mechanism")
        if entry.custody is not None and not isinstance(entry.custody, RetainedRetry):
            raise IngressHold(
                "PRE_AUTH route cannot authenticate, admit, reject or release evidence"
            )
        return cls(
            token=entry.token,
            retention_proof=entry.retention_proof,
            state_head=entry.state_head,
            staged_bytes=entry.staged_bytes,
            staged_digest=entry.staged_digest,
            custody=entry.custody,
        )


class CustodyRecord(IngressDTO):
    schema_id: Literal["chiplog.ingress.custody-record.v1"] = "chiplog.ingress.custody-record.v1"
    command: CustodyCommand
    resulting_entry: CustodyEntry

    def head(self) -> Head:
        return reference(self.command.command_id + "/record", canonical(self))


def prepare_custody(
    command: CustodyCommand,
    previous: Head | None,
    snapshot: CustodySnapshot,
) -> tuple[CustodyRecord, CustodySnapshot]:
    """Reproduce exact historical output from the complete preceding custody state."""
    profile, token, claim = command.profile, command.token, command.retention
    if command.predecessor != previous:
        raise IngressConflict("custody command omits or substitutes the complete predecessor")
    if (
        (snapshot.epoch_id, snapshot.epoch_head, snapshot.fence, snapshot.state)
        != (profile.epoch().identity, profile.epoch().head, 0, "OPEN")
        or not isinstance(token.source.endpoint_account_binding, UnknownEndpoint)
        or command.command_id != subject_id(profile, claim.slot_id, command.operation)
        or token.token_id != subject_id(profile, claim.slot_id, "ingress.receipt")
        or token.command_fingerprint != allocation_fingerprint(profile, claim, token.maximum_bytes)
        or claim.proof.identity != claim.slot_id
        or token.source.tenant_id != profile.tenant_id
        or token.source.database_id != profile.database_id
        or token.source.source_identity != profile.source_identity
        or token.source.source_class != profile.source_class
        or token.source.manifest_row != profile.head()
        or token.source.transport_version != profile.transport_version
        or token.source.admission_epoch != profile.epoch()
        or token.source.admission_fence != 0
        or token.receive_slot != claim.slot_id
        or token.maximum_bytes > profile.maximum_item_bytes
        or claim.byte_count > token.maximum_bytes
    ):
        raise IngressHold("retained source, scope, epoch or registered limit differs")
    if command.operation == "ingress.allocate_receipt_token":
        if command.raw_bytes is not None or token.predecessor is not None:
            raise IngressConflict("allocation cannot stage bytes or claim another predecessor")
        if any(entry.token.token_id == token.token_id for entry in snapshot.entries):
            raise IngressConflict("selected allocation requires exact publication replay")
        if (
            len(snapshot.entries) >= profile.maximum_items
            or sum(entry.token.maximum_bytes for entry in snapshot.entries) + token.maximum_bytes
            > profile.maximum_total_bytes
        ):
            raise IngressHold("registered retained custody reserve exhausted")
        result = allocate_token(snapshot, token, "RETAINED_SOURCE", claim.proof)
    else:
        entries = tuple(
            entry for entry in snapshot.entries if entry.token.token_id == token.token_id
        )
        if (
            len(entries) != 1
            or entries[0].token != token
            or entries[0].retention_proof != claim.proof
        ):
            raise IngressConflict("source, token or retention proof differs from allocation")
        entry = entries[0]
        if command.operation == "ingress.stage_raw_bytes":
            raw = command.raw_bytes
            if raw is None or len(raw) != claim.byte_count or digest(raw) != claim.raw_digest:
                raise IngressConflict("raw bytes differ from independently retained item")
            if entry.staged_bytes is not None:
                raise IngressConflict("selected staging requires exact publication replay")
            result = stage_raw(snapshot, token.token_id, raw)
        else:
            if command.raw_bytes is not None or entry.custody is not None:
                raise IngressConflict("selected retry requires exact publication replay")
            result = publish_custody(
                snapshot,
                token.token_id,
                RetainedRetry(
                    token=entry.state_head,
                    continuing_retention_proof=claim.proof,
                ),
            )
    changed = next(entry for entry in result.entries if entry.token.token_id == token.token_id)
    return CustodyRecord(command=command, resulting_entry=CustodyEntry.from_entry(changed)), result
