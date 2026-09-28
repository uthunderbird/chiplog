"""H1 scoped producer wires; retained evidence is never a SEND authority grant.

The mounted owner authenticates the route and all selected authority preimages.
The historical hermetic scope alone authorizes no external delivery. Mandate and
precursor inputs are candidates to check, not caller-issued permissions.
"""

from __future__ import annotations

import hashlib
from typing import Literal, Self

from pydantic import Field, model_validator

from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    PreparedExecutionCompletion,
)
from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    PrepareExecutionCompletionFirstPathV2,
)
from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    H1RetainedSelectedWrapperV1,
)

from .contracts import CommandIdentity
from .dispatch_authority_contracts import (
    Digest,
    DispatchObservationDTO,
    DispatchSourceInventory,
    Identity,
)
from .dispatch_v2_contracts import CurrentDispatchInputsV2
from .fences import Absent, WorkerFence
from .h1_local_preparation_contracts import H1SelectedScopeSourceV1
from .scoped_intent_contracts import (
    PreparedDeliveryBasisV3,
    PreparedScopedIntentPublication,
    PrepareScopedIntentPublication,
    ScopedAuthorityRecord,
    ScopedPrecursorRequest,
    ScopedPrecursorResult,
)
from .scoped_intent_record_contracts import RetainedOriginalSourceV3


class PrepareH1ScopedDeliveryV1(DispatchObservationDTO):
    """Pinned producer input with separately authenticated authority sources.

    The command and intent identities belong to this scoped producer. Selected
    local scope is historical context, never upgraded into communication authority.
    """

    schema_id: Literal["chiplog.effects.prepare-h1-scoped-delivery.v1"] = (
        "chiplog.effects.prepare-h1-scoped-delivery.v1"
    )
    identity: CommandIdentity
    intent_id: Identity
    expected_intent: Absent
    original_completion_request: PrepareExecutionCompletionFirstPathV2
    prepared_completion: PreparedExecutionCompletion
    selected_scope: H1SelectedScopeSourceV1
    retained_origin: H1RetainedSelectedWrapperV1
    fence: WorkerFence
    precursor_request: ScopedPrecursorRequest
    preexisting_communication_authority: ScopedAuthorityRecord
    current_disclosure_authority: ScopedAuthorityRecord
    original_sources: DispatchSourceInventory
    current: CurrentDispatchInputsV2
    complete_current_origin_sources: tuple[ScopedAuthorityRecord, ...]
    retained_sources: tuple[RetainedOriginalSourceV3, ...]


class H1ScopedDeliveryRouteV1(DispatchObservationDTO):
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
    operation: Literal["effects.prepare_h1_scoped_delivery"] = "effects.prepare_h1_scoped_delivery"


class H1ScopedDeliveryOwnerCallV1(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.h1-scoped-delivery-owner-call.v1"] = (
        "chiplog.effects.h1-scoped-delivery-owner-call.v1"
    )
    route: H1ScopedDeliveryRouteV1
    request: PrepareH1ScopedDeliveryV1
    request_digest: Digest

    @model_validator(mode="after")
    def exact_request(self) -> Self:
        first = self.request.original_completion_request
        if hashlib.sha256(self.request.canonical_bytes()).hexdigest() != self.request_digest:
            raise ValueError("H1 scoped owner request digest differs")
        if (
            self.route.tenant_id != first.run.tenant
            or self.route.database_id != first.source.database_id
            or self.route.worker_session_id != first.run.worker_session
            or self.route.request_id != self.request.identity.command_id
        ):
            raise ValueError("H1 scoped owner route differs from request")
        return self


class PreparedH1ScopedDeliveryV1(DispatchObservationDTO):
    """Exact precursor and intent exchange returned by the effects owner."""

    schema_id: Literal["chiplog.effects.prepared-h1-scoped-delivery.v1"] = (
        "chiplog.effects.prepared-h1-scoped-delivery.v1"
    )
    disposition: Literal["PREPARED"] = "PREPARED"
    source_request_fingerprint: Digest
    delivery_id: str = Field(min_length=1)
    basis: PreparedDeliveryBasisV3
    precursor_request: ScopedPrecursorRequest
    precursor_result: ScopedPrecursorResult
    intent_request: PrepareScopedIntentPublication
    intent_result: PreparedScopedIntentPublication
    acquisition_bytes: bytes = Field(min_length=1)
    mandate_bytes: bytes = Field(min_length=1)
    retained_sources: tuple[RetainedOriginalSourceV3, ...]


class H1ScopedDeliveryRejectedV1(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.prepared-h1-scoped-delivery.v1"] = (
        "chiplog.effects.prepared-h1-scoped-delivery.v1"
    )
    disposition: Literal["DENIED", "STALE", "UNSUPPORTED"]
    reason: str = Field(min_length=1)
