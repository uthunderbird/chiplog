"""Public deployment-trust contracts.

Only inert values and structural ports cross this owner boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from chiplog.domain_primitives import PrincipalId, RecordId, TenantId


@dataclass(frozen=True)
class AuthenticationRequest:
    contour: str
    credential_id: str
    session_id: str
    source_id: str
    transport_witness_id: str | None
    peer_credential: str


@dataclass(frozen=True)
class TrustReference:
    tenant_id: TenantId
    principal_id: PrincipalId
    contour: str
    credential_head: str
    session_head: str
    source_head: str
    trust_head: str
    materialization_head: str
    freshness_sequence: int
    peer_credential: str


@dataclass(frozen=True)
class TrustDecision:
    disposition: Literal["VALID", "DENIED", "STALE", "INDETERMINATE"]
    reference: TrustReference | None
    reason: str | None


@dataclass(frozen=True)
class TrustReferenceRevalidation:
    reference: TrustReference
    operation: str
    subject_id: RecordId


class TenantDecisionJournalPort(Protocol):
    def append(self, decision: bytes, predecessor: str | None) -> str: ...

    def entries(self) -> tuple[tuple[str, str | None, bytes], ...]: ...

    def observation(self, decision_id: str) -> Literal["DECIDED", "NO_DECISION", "AMBIGUOUS"]: ...


class TrustMaterializationPort(Protocol):
    def materialize(self, decision_id: str, records: tuple[bytes, ...]) -> str: ...

    def materialized(self, decision_id: str) -> bool: ...

    def records(self) -> tuple[bytes, ...]: ...


class TrustRevalidator(Protocol):
    def revalidate(self, request: TrustReferenceRevalidation) -> TrustDecision: ...


class TrustAuthenticator(Protocol):
    def authenticate(self, request: AuthenticationRequest) -> TrustDecision: ...


__all__ = [
    "AuthenticationRequest",
    "TenantDecisionJournalPort",
    "TrustAuthenticator",
    "TrustDecision",
    "TrustMaterializationPort",
    "TrustReference",
    "TrustReferenceRevalidation",
    "TrustRevalidator",
]
