from __future__ import annotations

import hashlib
import hmac

from chiplog.platform.deployment_gate import (
    CurrentEntitlement,
    DeploymentGateGeneration,
    DeploymentGateRequest,
    ExposureBounds,
)
from chiplog.platform.r8_gate import (
    canonical_entitlement,
)

KEY = b"independent-fixture-authority"

PAYLOAD = b"synthetic bounded handoff"


def entitlement() -> CurrentEntitlement:
    return CurrentEntitlement(
        generation=DeploymentGateGeneration(tenant_id="t", epoch="e", sequence=0),
        mode="EVALUATION",
        readiness_head="ready:1",
        readiness="READY",
        entitlement_head="evaluation:1",
        status="ACTIVE",
        evidence_cursors=(("evidence", "1"),),
        freshness_leases=(("evidence", 100),),
        bounds=ExposureBounds(
            capability_id="synthetic",
            cohort_id="fixture",
            purpose="test",
            cap=1,
            starts_ns=0,
            expires_ns=100,
            instrumentation=("sink-log",),
            stop_rules=("stop-on-breach",),
        ),
        live_predicates=(("scope-clear", True),),
        open_causes=(),
    )


def request(value: CurrentEntitlement, operation: str = "op") -> DeploymentGateRequest:
    return DeploymentGateRequest(
        operation_id=operation,
        surface_id="synthetic.send",
        payload_digest=hashlib.sha256(PAYLOAD).hexdigest(),
        mode=value.mode,
        generation=value.generation,
        readiness_head=value.readiness_head,
        entitlement_head=value.entitlement_head,
        evidence_cursors=value.evidence_cursors,
        freshness_leases=value.freshness_leases,
        bounds=value.bounds,
    )


def signature(value: CurrentEntitlement) -> bytes:
    return hmac.digest(KEY, canonical_entitlement(value), "sha256")
