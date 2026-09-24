"""Inert H1 local Commentary preparation contracts.

These values retain a bounded local preparation candidate.  They neither
authenticate selected sources nor authorize an external delivery, SEND, or an
effects-owner publication.  Those operations deliberately remain outside this
module's public surface.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Self

from pydantic import Field, model_validator

from chiplog.capabilities.agent_loop.delivery_contracts import AcceptedDelivery, ExactHead
from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    PreparedExecutionCompletion,
)
from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    PrepareExecutionCompletionFirstPathV2,
)
from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    H1RetainedSelectedWrapperV1,
)
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    CurrentHermeticExecutionScopeV1,
    HermeticOutputScopeAnchorV1,
    HermeticOutputScopeV1,
    ReadCurrentHermeticExecutionScopeV1,
)

from .contracts import CommandIdentity
from .dispatch_authority_contracts import Digest, DispatchObservationDTO, Identity
from .fences import Absent, WorkerFence
from .scoped_intent_contracts import PreparedDeliveryBasisV3

PROFILE: Literal["chiplog.effects.h1-local-commentary-preparation.v1"] = (
    "chiplog.effects.h1-local-commentary-preparation.v1"
)
PREPARE_SCHEMA: Literal["chiplog.effects.prepare-h1-local-commentary.v1"] = (
    "chiplog.effects.prepare-h1-local-commentary.v1"
)
INTENT_SCHEMA: Literal["chiplog.effects.h1-local-prepared-commentary-intent.v1"] = (
    "chiplog.effects.h1-local-prepared-commentary-intent.v1"
)
RESULT_SCHEMA: Literal["chiplog.effects.prepared-h1-local-commentary.v1"] = (
    "chiplog.effects.prepared-h1-local-commentary.v1"
)
_RETAINED_MAX_BYTES = 524_288


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


class H1SelectedScopeSourceV1(DispatchObservationDTO):
    """Self-consistent retained H1 scope bytes, without authentication.

    The public trust contracts do not expose a materialized-envelope decoder.
    Accordingly this contract binds each retained physical byte string to its
    declared physical anchor, and joins typed scope/current values.  A mounted
    owner must still authenticate the envelopes and current observation.
    """

    anchor: HermeticOutputScopeAnchorV1
    scope: HermeticOutputScopeV1
    selected_decision_bytes: bytes = Field(min_length=1, max_length=_RETAINED_MAX_BYTES)
    selected_record_bytes: bytes = Field(min_length=1, max_length=_RETAINED_MAX_BYTES)
    current_request: ReadCurrentHermeticExecutionScopeV1
    current_result: CurrentHermeticExecutionScopeV1

    @model_validator(mode="after")
    def exact_integrity_joins(self) -> Self:
        if (
            _sha256(self.selected_decision_bytes) != self.anchor.decision.fingerprint
            or _sha256(self.selected_record_bytes) != self.anchor.record.fingerprint
        ):
            raise ValueError("retained scope bytes differ from physical anchor")
        request = self.current_request
        result = self.current_result
        if (
            request.source_anchor != self.anchor
            or result.source_anchor != self.anchor
            or request.expected_revision != self.scope.revision
            or request.database_id != self.scope.database_id
            or request.scope_id != self.scope.scope_id
            or request.expected_worker_session_id != self.scope.worker_session_id
            or request.selected_resource_observation_ref
            != self.scope.selected_resource_observation_ref
            or request.admitted_authentication_ref != self.scope.admitted_authentication
            or request.expected_scope_ref != result.scope_ref
        ):
            raise ValueError("selected/current scope logical join differs")
        if (
            self.anchor.scope_revision != self.scope.revision
            or self.anchor.predecessor != self.scope.predecessor
            or self.anchor.selected_resource_observation_ref
            != self.scope.selected_resource_observation_ref
        ):
            raise ValueError("scope differs from physical anchor")
        return self


class PrepareH1LocalCommentaryV1(DispatchObservationDTO):
    """Inert owner request shape; no public preparation service is provided."""

    schema_id: Literal["chiplog.effects.prepare-h1-local-commentary.v1"] = PREPARE_SCHEMA
    identity: CommandIdentity
    intent_id: Identity
    expected_intent: Absent
    original_completion_request: PrepareExecutionCompletionFirstPathV2
    prepared_completion: PreparedExecutionCompletion
    selected_scope: H1SelectedScopeSourceV1
    retained_origin: H1RetainedSelectedWrapperV1
    fence: WorkerFence


class H1LocalCommentaryRouteV1(DispatchObservationDTO):
    """The broker-owned mount identity for the one local preparation call.

    This value binds a call to a live broker route; it is deliberately not an
    authority assertion that a caller may mint outside the mounted IPC route.
    """

    tenant_id: Identity
    database_id: Identity
    worker_session_id: Identity
    broker_epoch: int = Field(ge=0, le=2**64 - 1)
    runtime_generation: Identity
    broker_session_id: Identity
    owner_session_id: Identity
    request_id: Identity
    caller_owner: Literal["broker"] = "broker"
    callee_owner: Literal["effects"] = "effects"
    operation: Literal["effects.prepare_h1_local_commentary"] = (
        "effects.prepare_h1_local_commentary"
    )


class H1LocalCommentaryOwnerCallV1(DispatchObservationDTO):
    """The only public effects-owner input for H1 local preparation."""

    schema_id: Literal["chiplog.effects.h1-local-commentary-owner-call.v1"] = (
        "chiplog.effects.h1-local-commentary-owner-call.v1"
    )
    route: H1LocalCommentaryRouteV1
    request: PrepareH1LocalCommentaryV1
    request_digest: Digest

    @model_validator(mode="after")
    def exact_request(self) -> Self:
        if _sha256(self.request.canonical_bytes()) != self.request_digest:
            raise ValueError("H1 local owner request digest differs")
        if (
            self.route.tenant_id != self.request.original_completion_request.run.tenant
            or self.route.database_id != self.request.original_completion_request.source.database_id
            or self.route.worker_session_id
            != self.request.original_completion_request.run.worker_session
            or self.route.request_id != self.request.identity.command_id
        ):
            raise ValueError("H1 local owner route differs from request")
        return self


class H1LocalPreparedCommentaryIntentV1(DispatchObservationDTO):
    """Durable local receipt shape, explicitly outside external action schemas."""

    schema_id: Literal["chiplog.effects.h1-local-prepared-commentary-intent.v1"] = INTENT_SCHEMA
    kind: Literal["H1_LOCAL_PREPARED_COMMENTARY"] = "H1_LOCAL_PREPARED_COMMENTARY"
    profile: Literal["chiplog.effects.h1-local-commentary-preparation.v1"] = PROFILE
    phase: Literal["PREPARED"] = "PREPARED"
    external_delivery: Literal[False] = False
    intent_id: Identity
    tenant_id: Identity
    database_id: Identity
    principal_id: Identity
    worker_session_id: Identity
    original_run: ExactHead
    captured_attempt: ExactHead
    delivery: AcceptedDelivery
    basis: PreparedDeliveryBasisV3
    scope_anchor: HermeticOutputScopeAnchorV1
    scope_ref: ExactHead
    disclosure_policy_ref: ExactHead
    source_request_fingerprint: Digest
    fingerprint: Digest


class PreparedH1LocalCommentaryV1(DispatchObservationDTO):
    """Inert deterministic result shape; it is not an authenticated outcome."""

    schema_id: Literal["chiplog.effects.prepared-h1-local-commentary.v1"] = RESULT_SCHEMA
    disposition: Literal["PREPARED"] = "PREPARED"
    source_request_fingerprint: Digest
    intent: H1LocalPreparedCommentaryIntentV1
    complete_owner_commitment: Digest


class H1LocalCommentaryRejectedV1(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.prepared-h1-local-commentary.v1"] = RESULT_SCHEMA
    disposition: Literal["DENIED", "STALE", "UNSUPPORTED"]
    reason: Identity
