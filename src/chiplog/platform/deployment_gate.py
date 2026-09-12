"""Public operation-gate contracts; entitlement issuance is outside this port."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class DeploymentGateGeneration(_Frozen):
    tenant_id: str
    epoch: str
    sequence: int = Field(ge=0)


class ExposureBounds(_Frozen):
    capability_id: str
    cohort_id: str
    purpose: str
    cap: int = Field(gt=0)
    starts_ns: int = Field(ge=0)
    expires_ns: int = Field(gt=0)
    instrumentation: tuple[str, ...]
    stop_rules: tuple[str, ...]


class DeploymentGateRequest(_Frozen):
    operation_id: str
    surface_id: str
    payload_digest: str
    mode: Literal["EVALUATION", "PRODUCTION"]
    generation: DeploymentGateGeneration
    readiness_head: str
    entitlement_head: str
    evidence_cursors: tuple[tuple[str, str], ...]
    freshness_leases: tuple[tuple[str, int], ...]
    bounds: ExposureBounds


class CurrentEntitlement(_Frozen):
    """A view read from an independent authority, never authority by construction."""

    generation: DeploymentGateGeneration
    mode: Literal["EVALUATION", "PRODUCTION"]
    readiness_head: str
    readiness: Literal["READY", "HOLD"]
    entitlement_head: str
    status: Literal["ACTIVE", "HOLD", "REVOKED", "SUPERSEDED", "BREACHED"]
    evidence_cursors: tuple[tuple[str, str], ...]
    freshness_leases: tuple[tuple[str, int], ...]
    bounds: ExposureBounds
    live_predicates: tuple[tuple[str, bool], ...]
    open_causes: tuple[str, ...]


class DeploymentGateResult(_Frozen):
    disposition: Literal["PERMIT_EXACT_EVALUATION", "PERMIT_EXACT_PRODUCTION", "HOLD"]
    request_digest: str
    reason: str


class DeploymentGatePort(Protocol):
    def check(self, request: DeploymentGateRequest) -> DeploymentGateResult:
        """A check is not a transferable handoff permission; revalidate at use."""
        ...


__all__ = [
    "CurrentEntitlement",
    "DeploymentGateGeneration",
    "DeploymentGatePort",
    "DeploymentGateRequest",
    "DeploymentGateResult",
    "ExposureBounds",
]
