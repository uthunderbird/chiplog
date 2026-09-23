"""Inert dispatch observation boundary; no value here grants SEND authority.

Historical interpretation uses the original selected cut. Current observations
must be acquired independently at the writer cut. Public construction proves
neither provenance nor completeness; unavailable required sources forbid issuance.
The existing v1 intent and reducer are unchanged.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .contracts import DispatchSemanticBinding
from .contracts import ExactHead as ExactHead
from .fences import WorkerFence

Identity = Annotated[str, Field(min_length=1)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class DispatchObservationDTO(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()


class UnavailableSource(DispatchObservationDTO):
    kind: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    reason: Literal["UNIMPLEMENTED", "UNREGISTERED_VERSION", "UNVERIFIABLE", "STALE", "UNREACHABLE"]
    detail: Identity


class CapturedSource(DispatchObservationDTO):
    """Claimed capture description, not a successful authorization verdict."""

    kind: Literal["CAPTURED"] = "CAPTURED"
    source_id: Identity
    source_version: Identity
    owner_id: Identity
    reader_id: Identity
    invalidation_manifest: ExactHead
    head: ExactHead
    canonical_value: bytes = Field(min_length=1)
    clock_contract: Identity
    clock_epoch: Identity
    valid_until_ns: int = Field(ge=0)


SourceObservation = Annotated[CapturedSource | UnavailableSource, Field(discriminator="kind")]


class DispatchSourceInventory(DispatchObservationDTO):
    """Every required group is explicit, even when it has no available issuer."""

    trust: SourceObservation
    planning: SourceObservation
    semantic_registry: SourceObservation
    original_adoption: SourceObservation
    runtime_and_fence: SourceObservation
    endpoint: SourceObservation
    credential_lifecycle: SourceObservation
    deployment_entitlement: SourceObservation
    clock: SourceObservation
    effects_history: SourceObservation
    normative_conflict_generation: SourceObservation


EffectHistoryKind = Literal[
    "INTENT_ACCEPTED",
    "PLAN_EFFECT_PUBLISHED",
    "DELIVERY_PREPARED",
    "RECOVERY_INTENT_PUBLISHED",
    "DISPATCH_AUTHORIZED",
    "BEFORE_SEND_DISPOSITION",
    "SEND_COMMITTED",
    "TRANSMISSION_ALLOCATED",
    "EVIDENCE_RECORDED",
    "RECONCILED",
]


class SelectedEffectHistoryMember(DispatchObservationDTO):
    """Retain the original command/result, including historical semantic binding."""

    kind: EffectHistoryKind
    selected_decision: ExactHead
    publication_ordinal: int = Field(ge=0)
    original_selected_cut: ExactHead
    record: ExactHead
    predecessor: ExactHead | None
    intent: ExactHead
    semantics: DispatchSemanticBinding
    command_bytes: bytes = Field(min_length=1)
    record_bytes: bytes = Field(min_length=1)


class DispatchObservationCut(DispatchObservationDTO):
    tenant_id: Identity
    database_identity: ExactHead
    selected_journal_head: ExactHead
    materialization_commitment: Digest
    tenant_frontier: int = Field(ge=0)
    source_role_registry: ExactHead
    capture_id: Identity


class DispatchObservationQuery(DispatchObservationDTO):
    """A proposal to inspect an operation, never permission to perform it."""

    operation: Literal["AUTHORIZE_SEND", "COMMIT_SEND", "HOLD", "CANCEL", "SUPERSEDE"]
    command_id: Identity
    original_intent: ExactHead
    original_mandate_bytes: bytes = Field(min_length=1)
    original_acquisition_bytes: bytes = Field(min_length=1)
    expected_attempt: ExactHead
    semantics: DispatchSemanticBinding
    fence: WorkerFence
    denying_decision: ExactHead | None


class DispatchAuthorityObservation(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.dispatch-observation.v2"]
    query_fingerprint: Digest
    cut: DispatchObservationCut
    sources: DispatchSourceInventory
    complete_effect_history: tuple[SelectedEffectHistoryMember, ...]
    complete_history_fingerprint: Digest


class DispatchAuthorityObservationPort(Protocol):
    """Source acquisition only; there is deliberately no grant or send method.

    Later private issuance must authenticate the actual captured sources, verify
    complete history, operation-specific denying or send authority and the current
    writer cut. An unavailable required source cannot produce a send grant.
    """

    async def observe(self, query: DispatchObservationQuery) -> DispatchAuthorityObservation: ...
