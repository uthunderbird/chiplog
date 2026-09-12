"""Public journal command and outbound contracts; DTOs never authenticate callers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .boundary import DisclosureEnvelope, SourceReference


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Subject(Frozen):
    kind: Literal["occurrence"]
    identity: str = Field(min_length=1)


class Payload(Frozen):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    outcome: Literal["completed", "not_completed"]

    @field_validator("date")
    @classmethod
    def valid_date(cls, value: str) -> str:
        from datetime import date

        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("noncanonical date")
        return value


class Provenance(Frozen):
    ingress_id: str
    ingress_version: int = Field(gt=0)
    utterance: str
    digest: str
    source: Literal["principal", "model", "provider"]


class Heads(Frozen):
    journal: int = Field(ge=0)
    policy: str
    credential: str
    session: str
    contour: str
    deletion: str


class Claim(Frozen):
    subject: Subject
    payload: Payload
    provenance: tuple[Provenance, ...]
    envelope: DisclosureEnvelope


class Display(Frozen):
    tenant: str
    principal: str
    candidate_id: str
    candidate_version: int = Field(gt=0)
    interpretation: str
    claim: Claim
    consequence: str
    schema_version: Literal[1] = 1
    canonicalization_version: Literal[1] = 1
    heads: Heads
    predecessor: str | None
    operation: Literal["RECORD", "CORRECT", "RETRACT"]
    digest: str
    supersedes: tuple[str, ...] = ()


class Command(Frozen):
    command_id: str
    tenant: str
    principal: str
    action: Literal["record_fact", "correct_claim", "retract_claim", "confirm_candidate"]
    claim: Claim
    heads: Heads
    predecessor: str | None = None
    display: Display | None = None
    supersedes: tuple[str, ...] = ()
    schema_version: Literal[1] = 1
    canonicalization_version: Literal[1] = 1


class JournalRecord(Frozen):
    record_id: str
    tenant: str
    principal: str
    kind: Literal["FactClaim", "ClaimDisposition", "CandidateEvidence"]
    family: str
    predecessor: str | None
    operation: Literal["RECORD", "CORRECT", "RETRACT", "CANDIDATE"]
    claim: Claim
    display: Display | None
    command_id: str
    fingerprint: str
    positive_proof: tuple[tuple[str, bool], ...] = ()
    schema_version: Literal[1] = 1
    canonicalization_version: Literal[1] = 1

    def canonical_bytes(self) -> bytes:
        """Owner defines the only accepted complete record encoding."""
        import json

        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()


Disposition = Literal[
    "COMMITTED",
    "REPLAY",
    "CONFLICT",
    "STALE",
    "DENIED",
    "INDETERMINATE",
    "NEEDS_CONFIRMATION",
    "UNRESOLVED",
]


class Outcome(Frozen):
    disposition: Disposition
    record: JournalRecord | None = None
    reason: str | None = None


class ConfirmationIngress(Frozen):
    confirmation_id: str
    binding_digest: str


class TrustedIngress(Frozen):
    tenant: str
    principal: str
    heads: Heads
    items: tuple[Provenance, ...]
    sources: tuple[SourceReference, ...]
    endpoint: str
    confirmations: tuple[ConfirmationIngress, ...] = ()


class IdentityPort(Protocol):
    def authenticate(self, peer: str) -> TrustedIngress | None: ...


class Snapshot(Frozen):
    head: int
    records: tuple[JournalRecord, ...]


class JournalPersistence(Protocol):
    def snapshot(self, tenant: str) -> Snapshot: ...
    async def publish(
        self,
        record: JournalRecord,
        expected_head: int,
        guard: Callable[[], Literal["DENIED", "STALE", "INDETERMINATE"] | None],
    ) -> Disposition: ...


class JournalPort(Protocol):
    async def execute(self, peer: str, command: Command) -> Outcome: ...
    async def prepare(self, peer: str, command: Command) -> Outcome: ...
    def lineage(self, peer: str, tenant: str, family: str) -> tuple[JournalRecord, ...]: ...


class JournalQuery(Frozen):
    tenant: str
    expected_heads: Heads
    max_rows: int = Field(gt=0, le=1000)
    after_id: str | None = None
    subject: Subject | None = None


class JournalRow(Frozen):
    record: JournalRecord
    status: Literal[
        "user reported",
        "provider observed",
        "user confirmed provider observation",
        "disputed",
        "corrected",
        "retracted",
        "unknown",
    ]
    current_positive: bool
    lineage: tuple[JournalRecord, ...]


class JournalView(Frozen):
    disposition: Literal["CURRENT", "DENIED", "STALE", "INDETERMINATE"]
    head: int
    rows: tuple[JournalRow, ...] = ()
    next_cursor: str | None = None


class JournalQueryPort(Protocol):
    def project(self, peer: str, query: JournalQuery) -> JournalView: ...
