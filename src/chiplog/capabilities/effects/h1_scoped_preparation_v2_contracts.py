"""Distinct V2 wire contract for dispatch-aware H1 producer preparation."""

from __future__ import annotations

import hashlib
from typing import Literal, Self

from pydantic import model_validator

from .dispatch_authority_contracts import Digest
from .h1_scoped_preparation_contracts import (
    H1ScopedDeliveryOwnerCallV1,
    H1ScopedDeliveryRejectedV1,
    H1ScopedDeliveryRouteV1,
    PreparedH1ScopedDeliveryV1,
    PrepareH1ScopedDeliveryV1,
)


class PrepareH1ScopedDeliveryV2(PrepareH1ScopedDeliveryV1):
    schema_id: Literal["chiplog.effects.prepare-h1-scoped-delivery.v2"] = (
        "chiplog.effects.prepare-h1-scoped-delivery.v2"  # type: ignore[assignment]
    )


class H1ScopedDeliveryRouteV2(H1ScopedDeliveryRouteV1):
    operation: Literal["effects.prepare_h1_scoped_delivery_v2"] = (
        "effects.prepare_h1_scoped_delivery_v2"  # type: ignore[assignment]
    )


class H1ScopedDeliveryOwnerCallV2(H1ScopedDeliveryOwnerCallV1):
    schema_id: Literal["chiplog.effects.h1-scoped-delivery-owner-call.v2"] = (
        "chiplog.effects.h1-scoped-delivery-owner-call.v2"  # type: ignore[assignment]
    )
    route: H1ScopedDeliveryRouteV2
    request: PrepareH1ScopedDeliveryV2
    request_digest: Digest

    @model_validator(mode="after")
    def exact_request(self) -> Self:
        first = self.request.original_completion_request
        if hashlib.sha256(self.request.canonical_bytes()).hexdigest() != self.request_digest:
            raise ValueError("H1 V2 scoped owner request digest differs")
        if (
            self.route.tenant_id,
            self.route.database_id,
            self.route.worker_session_id,
            self.route.request_id,
        ) != (
            first.run.tenant,
            first.source.database_id,
            first.run.worker_session,
            self.request.identity.command_id,
        ):
            raise ValueError("H1 V2 scoped owner route differs from request")
        return self


class PreparedH1ScopedDeliveryV2(PreparedH1ScopedDeliveryV1):
    schema_id: Literal["chiplog.effects.prepared-h1-scoped-delivery.v2"] = (
        "chiplog.effects.prepared-h1-scoped-delivery.v2"  # type: ignore[assignment]
    )


class H1ScopedDeliveryRejectedV2(H1ScopedDeliveryRejectedV1):
    schema_id: Literal["chiplog.effects.prepared-h1-scoped-delivery.v2"] = (
        "chiplog.effects.prepared-h1-scoped-delivery.v2"  # type: ignore[assignment]
    )
