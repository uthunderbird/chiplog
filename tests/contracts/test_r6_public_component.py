"""R6 V4/V7 component tests through public R4/R5 and composition ports only."""

from __future__ import annotations

import asyncio

from chiplog.capabilities.deployment_trust import (
    AuthenticationRequest,
    TrustDecision,
    TrustReference,
)
from chiplog.capabilities.planning import (
    CreateIntentionLine,
    InvocationContext,
    PlanningOutcome,
)
from chiplog.composition import R6CreateRequest, build_r6_component
from chiplog.domain_primitives import PrincipalId, RecordId, TenantId

TENANT = TenantId("tenant-1")
PRINCIPAL = PrincipalId("principal-1")
PEER = "uid:501"


class Authenticator:
    def __init__(self) -> None:
        self.requests: list[AuthenticationRequest] = []

    def authenticate(self, request: AuthenticationRequest) -> TrustDecision:
        self.requests.append(request)
        if request.peer_credential != PEER:
            return TrustDecision("STALE", None, "peer credential changed")
        return TrustDecision(
            "VALID",
            TrustReference(
                TENANT,
                PRINCIPAL,
                "CLI",
                "credential-head",
                "session-head",
                "local",
                "trust-head",
                "materialization-head",
                1,
                PEER,
            ),
            None,
        )


class Planning:
    def __init__(self) -> None:
        self.calls: list[tuple[InvocationContext, CreateIntentionLine]] = []

    def execute(self, context: InvocationContext, command: CreateIntentionLine) -> PlanningOutcome:
        self.calls.append((context, command))
        return PlanningOutcome("COMMITTED", None, None)


def _request(peer: str = PEER) -> R6CreateRequest:
    return R6CreateRequest(
        AuthenticationRequest("CLI", "credential-1", "session-1", "", None, peer),
        "tenant-1",
        "principal-1",
        CreateIntentionLine(
            RecordId(TENANT, "command-1"),
            RecordId(TENANT, "intention-1"),
            RecordId(TENANT, "revision-1"),
            "Prepare release",
            "act-1",
        ),
    )


def test_public_r6_component_passes_exact_authenticated_context_to_public_planning_port() -> None:
    authenticator = Authenticator()
    planning = Planning()
    component = build_r6_component(authenticator, planning, lambda: PEER)
    assert asyncio.run(component.create(_request())).disposition == "COMMITTED"
    assert len(authenticator.requests) == 1
    assert len(planning.calls) == 1
    context, _ = planning.calls[0]
    assert context.trust_reference.peer_credential == PEER
    assert context.permission_scope.value == "planning.create_intention_line"


def test_peer_credential_substitution_denies_before_public_planning_port() -> None:
    authenticator = Authenticator()
    planning = Planning()
    component = build_r6_component(authenticator, planning, lambda: PEER)
    result = asyncio.run(component.create(_request("uid:attacker")))
    assert result.disposition == "DENIED"
    assert planning.calls == []
