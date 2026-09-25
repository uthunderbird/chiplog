"""Pure, closed carriers shared by the H1 inventory decoder leaves.

These values retain one authenticated enumeration occurrence at a time.  They
do not read storage, authenticate input, filter scope, or decide whether an
occurrence is globally empty.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from chiplog.capabilities.projections.workspace_boundary import SourceReference

type Surface = Literal[
    "PHYSICAL",
    "OWNER_COMMAND",
    "LOOP_ENTRY",
    "EVIDENCE_INBOX",
    "CUSTODY_ENTRY",
    "WORKSPACE_SOURCE",
]
type ScopeNamespace = Literal[
    "run",
    "turn",
    "call",
    "intent",
    "mandate",
    "obligation",
    "reduction",
    "evidence",
    "source",
    "record",
    "effect",
    "dispatch",
    "delivery",
    "outcome",
    "proposal",
    "workspace",
    "planning",
]
type H1InventoryFamily = Literal[
    "RUN",
    "TURN",
    "CALL",
    "INTENT",
    "MANDATE",
    "OBLIGATION",
    "REDUCTION",
    "EVIDENCE",
    "SOURCE",
    "RECORD",
    "EFFECT",
    "DISPATCH",
    "DELIVERY",
    "OUTCOME",
    "PROPOSAL",
    "WORKSPACE",
    "PLANNING",
]
type ScopeRelationName = Literal[
    "RUN_TURN",
    "TURN_CALL",
    "CALL_INTENT",
    "INTENT_MANDATE",
    "MANDATE_OBLIGATION",
    "OBLIGATION_REDUCTION",
    "EVIDENCE_SOURCE",
    "RECORD_SOURCE",
    "RECORD_RUN",
    "RECORD_TURN",
    "EFFECT_INTENT",
    "DISPATCH_EFFECT",
    "DELIVERY_DISPATCH",
    "OUTCOME_DELIVERY",
    "PROPOSAL_INTENT",
    "WORKSPACE_SOURCE",
    "PLANNING_SOURCE",
]
type FailureCode = Literal["UNSUPPORTED", "INCOMPLETE", "CORRUPT", "NONEMPTY"]

SURFACES: tuple[Surface, ...] = (
    "PHYSICAL",
    "OWNER_COMMAND",
    "LOOP_ENTRY",
    "EVIDENCE_INBOX",
    "CUSTODY_ENTRY",
    "WORKSPACE_SOURCE",
)
H1_SCOPE_NAMESPACES: tuple[ScopeNamespace, ...] = (
    "run",
    "turn",
    "call",
    "intent",
    "mandate",
    "obligation",
    "reduction",
    "evidence",
    "source",
    "record",
    "effect",
    "dispatch",
    "delivery",
    "outcome",
    "proposal",
    "workspace",
    "planning",
)
H1_INVENTORY_FAMILIES: tuple[H1InventoryFamily, ...] = (
    "RUN",
    "TURN",
    "CALL",
    "INTENT",
    "MANDATE",
    "OBLIGATION",
    "REDUCTION",
    "EVIDENCE",
    "SOURCE",
    "RECORD",
    "EFFECT",
    "DISPATCH",
    "DELIVERY",
    "OUTCOME",
    "PROPOSAL",
    "WORKSPACE",
    "PLANNING",
)
H1_SCOPE_RELATIONS: Mapping[ScopeRelationName, tuple[ScopeNamespace, ScopeNamespace]] = (
    MappingProxyType(
        {
            "RUN_TURN": ("run", "turn"),
            "TURN_CALL": ("turn", "call"),
            "CALL_INTENT": ("call", "intent"),
            "INTENT_MANDATE": ("intent", "mandate"),
            "MANDATE_OBLIGATION": ("mandate", "obligation"),
            "OBLIGATION_REDUCTION": ("obligation", "reduction"),
            "EVIDENCE_SOURCE": ("evidence", "source"),
            "RECORD_SOURCE": ("record", "source"),
            "RECORD_RUN": ("record", "run"),
            "RECORD_TURN": ("record", "turn"),
            "EFFECT_INTENT": ("effect", "intent"),
            "DISPATCH_EFFECT": ("dispatch", "effect"),
            "DELIVERY_DISPATCH": ("delivery", "dispatch"),
            "OUTCOME_DELIVERY": ("outcome", "delivery"),
            "PROPOSAL_INTENT": ("proposal", "intent"),
            "WORKSPACE_SOURCE": ("workspace", "source"),
            "PLANNING_SOURCE": ("planning", "source"),
        }
    )
)
H1_EVIDENCE_INBOX_METADATA_DOMAIN = "chiplog.h1.evidence-inbox-metadata.v1"
H1_EVIDENCE_INBOX_METADATA_FIELDS = (
    "domain",
    "followup_kind",
    "state",
    "attempt_id",
    "transport_version",
    "cursor",
    "fingerprint",
    "source_id",
    "evidence_id",
)


def _required_text(value: object, name: str) -> None:
    if type(value) is not str or not value:
        raise ValueError(f"{name} must be a non-empty str")


def _validate_evidence_inbox_metadata(metadata_bytes: bytes) -> None:
    """Accept only B's exact compact encoding of the typed inbox row metadata."""
    try:
        metadata = json.loads(metadata_bytes)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("metadata_bytes must be canonical evidence inbox metadata") from error
    if type(metadata) is not dict or frozenset(metadata) != frozenset(
        H1_EVIDENCE_INBOX_METADATA_FIELDS
    ):
        raise ValueError("metadata_bytes has an unexpected evidence inbox field set")
    if metadata["domain"] != H1_EVIDENCE_INBOX_METADATA_DOMAIN:
        raise ValueError("metadata_bytes has an unknown evidence inbox domain")
    required_strings = ("followup_kind", "state", "fingerprint", "source_id", "evidence_id")
    if any(type(metadata[name]) is not str or not metadata[name] for name in required_strings):
        raise ValueError("metadata_bytes required values must be non-empty strings")
    optional_strings = ("attempt_id", "transport_version", "cursor")
    if any(
        metadata[name] is not None and type(metadata[name]) is not str for name in optional_strings
    ):
        raise ValueError("metadata_bytes optional values must be strings or null")
    canonical = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    if metadata_bytes != canonical:
        raise ValueError("metadata_bytes must use compact sorted-key canonical JSON")


@dataclass(frozen=True, slots=True)
class H1RawInventoryItem:
    """One raw enumerated occurrence; equal bytes never merge occurrences."""

    surface: Surface
    locator: str
    tenant: str
    owner: str
    schema: str
    record_kind: str | None
    record_id: str | None
    fingerprint: str | None
    raw: bytes
    metadata_bytes: bytes | None = None

    def __post_init__(self) -> None:
        if self.surface not in SURFACES:
            raise ValueError(f"unknown surface: {self.surface!r}")
        for name in ("locator", "tenant", "owner", "schema"):
            _required_text(getattr(self, name), name)
        for name in ("record_kind", "record_id", "fingerprint"):
            value = getattr(self, name)
            if value is not None:
                _required_text(value, name)
        if type(self.raw) is not bytes:
            raise ValueError("raw must be exact bytes")
        if self.metadata_bytes is not None:
            if type(self.metadata_bytes) is not bytes:
                raise ValueError("metadata_bytes must be exact bytes")
            if self.surface != "EVIDENCE_INBOX":
                raise ValueError("metadata_bytes is reserved for EVIDENCE_INBOX")
            _validate_evidence_inbox_metadata(self.metadata_bytes)


@dataclass(frozen=True, slots=True)
class H1ScopeKey:
    tenant: str
    namespace: ScopeNamespace
    identity: str
    head: str | None

    def __post_init__(self) -> None:
        _required_text(self.tenant, "tenant")
        if self.namespace not in H1_SCOPE_NAMESPACES:
            raise ValueError(f"unknown namespace: {self.namespace!r}")
        _required_text(self.identity, "identity")
        if self.head is not None:
            _required_text(self.head, "head")


@dataclass(frozen=True, slots=True)
class H1ScopeRelation:
    relation: ScopeRelationName
    subject: H1ScopeKey
    target: H1ScopeKey

    def __post_init__(self) -> None:
        endpoints = H1_SCOPE_RELATIONS.get(self.relation)
        if endpoints is None:
            raise ValueError(f"unknown relation: {self.relation!r}")
        if type(self.subject) is not H1ScopeKey or type(self.target) is not H1ScopeKey:
            raise ValueError("relation endpoints must be H1ScopeKey values")
        if self.subject.tenant != self.target.tenant:
            raise ValueError("relation endpoints must have the same tenant")
        if (self.subject.namespace, self.target.namespace) != endpoints:
            raise ValueError(f"relation endpoints do not match {self.relation}")


@dataclass(frozen=True, slots=True)
class H1DecodedInventoryItem:
    locator: str
    identities: tuple[H1ScopeKey, ...]
    relations: tuple[H1ScopeRelation, ...]
    source_refs: tuple[SourceReference, ...]
    families: tuple[H1InventoryFamily, ...]

    def __post_init__(self) -> None:
        _required_text(self.locator, "locator")
        if type(self.identities) is not tuple or any(
            type(item) is not H1ScopeKey for item in self.identities
        ):
            raise ValueError("identities must be a tuple of H1ScopeKey values")
        if type(self.relations) is not tuple or any(
            type(item) is not H1ScopeRelation for item in self.relations
        ):
            raise ValueError("relations must be a tuple of H1ScopeRelation values")
        if type(self.source_refs) is not tuple or any(
            type(item) is not SourceReference for item in self.source_refs
        ):
            raise ValueError("source_refs must be a tuple of workspace SourceReference values")
        if type(self.families) is not tuple or any(
            family not in H1_INVENTORY_FAMILIES for family in self.families
        ):
            raise ValueError("unknown family")


@dataclass(frozen=True, slots=True)
class H1LeafRegistration:
    surface: Surface
    owner: str
    schema: str
    record_kind: str | None
    decoder_version: str

    def __post_init__(self) -> None:
        if self.surface not in SURFACES:
            raise ValueError(f"unknown surface: {self.surface!r}")
        for name in ("owner", "schema", "decoder_version"):
            _required_text(getattr(self, name), name)
        if self.record_kind is not None:
            _required_text(self.record_kind, "record_kind")


class H1OwnerInventoryFailure(RuntimeError):
    """A complete owner-negative witness could not be established."""

    def __init__(
        self, code: FailureCode, *, family: str, owner: str, schema: str, locator: str
    ) -> None:
        self.code = code
        self.family = family
        self.owner = owner
        self.schema = schema
        self.locator = locator
        super().__init__(f"{code}: family={family} owner={owner} schema={schema} locator={locator}")

    @classmethod
    def unsupported(
        cls, *, family: str, owner: str, schema: str, locator: str
    ) -> H1OwnerInventoryFailure:
        return cls("UNSUPPORTED", family=family, owner=owner, schema=schema, locator=locator)

    @classmethod
    def incomplete(cls, *, family: str, locator: str) -> H1OwnerInventoryFailure:
        return cls("INCOMPLETE", family=family, owner="", schema="", locator=locator)

    @classmethod
    def corrupt(
        cls, *, family: str, owner: str, schema: str, locator: str
    ) -> H1OwnerInventoryFailure:
        return cls("CORRUPT", family=family, owner=owner, schema=schema, locator=locator)

    @classmethod
    def nonempty(
        cls, *, family: str, owner: str, schema: str, locator: str
    ) -> H1OwnerInventoryFailure:
        return cls("NONEMPTY", family=family, owner=owner, schema=schema, locator=locator)


__all__ = [
    "H1_EVIDENCE_INBOX_METADATA_DOMAIN",
    "H1_EVIDENCE_INBOX_METADATA_FIELDS",
    "H1_INVENTORY_FAMILIES",
    "H1_SCOPE_NAMESPACES",
    "H1_SCOPE_RELATIONS",
    "SURFACES",
    "FailureCode",
    "H1DecodedInventoryItem",
    "H1InventoryFamily",
    "H1LeafRegistration",
    "H1OwnerInventoryFailure",
    "H1RawInventoryItem",
    "H1ScopeKey",
    "H1ScopeRelation",
    "ScopeNamespace",
    "ScopeRelationName",
    "Surface",
]
