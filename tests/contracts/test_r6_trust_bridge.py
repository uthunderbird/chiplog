"""R6 V4/V7 contract: composition crosses the frozen public R4/R5 seam."""

from __future__ import annotations

from dataclasses import replace

import pytest

from chiplog.capabilities.deployment_trust import (
    TrustDecision,
    TrustReference,
    TrustReferenceRevalidation,
    TrustRevalidator,
)
from chiplog.capabilities.planning import PlanningTrustReference, TrustRevalidationPort
from chiplog.composition.r6 import R4PlanningTrustBridge
from chiplog.domain_primitives import PrincipalId, RecordId, TenantId

TENANT = TenantId("tenant-1")
PRINCIPAL = PrincipalId("principal-1")
SUBJECT = RecordId(TENANT, "intention-1")


def _reference() -> PlanningTrustReference:
    return PlanningTrustReference(
        TENANT,
        PRINCIPAL,
        "CLI",
        "credential-head",
        "session-head",
        "local",
        "trust-head",
        "materialization-head",
        7,
        "peer-credential",
    )


class ExactR4Trust:
    """Structural public R4 port: only the exact immutable reference is valid."""

    def __init__(self, reference: PlanningTrustReference) -> None:
        self._reference = TrustReference(
            reference.tenant_id,
            reference.principal_id,
            reference.contour,
            reference.credential_head,
            reference.session_head,
            reference.source_head,
            reference.trust_head,
            reference.materialization_head,
            reference.freshness_sequence,
            reference.peer_credential,
        )
        self.requests: list[TrustReferenceRevalidation] = []

    def revalidate(self, request: TrustReferenceRevalidation) -> TrustDecision:
        self.requests.append(request)
        if request.operation != "CREATE_INTENTION_LINE" or request.subject_id != SUBJECT:
            return TrustDecision("DENIED", None, "operation or allocation subject changed")
        if request.reference != self._reference:
            return TrustDecision("STALE", None, "immutable trust reference changed")
        return TrustDecision("VALID", self._reference, None)


def _bridge() -> tuple[TrustRevalidationPort, ExactR4Trust, PlanningTrustReference]:
    reference = _reference()
    r4: TrustRevalidator = ExactR4Trust(reference)
    return R4PlanningTrustBridge(r4, lambda _: None), r4, reference  # type: ignore[return-value]


def test_public_r4_to_r5_bridge_revalidates_exact_reference_operation_and_subject() -> None:
    bridge, r4, reference = _bridge()
    assert bridge.revalidate(reference, "CREATE_INTENTION_LINE", SUBJECT).disposition == "VALID"
    assert r4.requests == [
        TrustReferenceRevalidation(
            TrustReference(
                TENANT,
                PRINCIPAL,
                "CLI",
                "credential-head",
                "session-head",
                "local",
                "trust-head",
                "materialization-head",
                7,
                "peer-credential",
            ),
            "CREATE_INTENTION_LINE",
            SUBJECT,
        )
    ]


@pytest.mark.parametrize(
    "mutated",
    [
        lambda value: replace(value, tenant_id=TenantId("tenant-2")),
        lambda value: replace(value, principal_id=PrincipalId("principal-2")),
        lambda value: replace(value, contour="TELEGRAM"),
        lambda value: replace(value, credential_head="credential-other"),
        lambda value: replace(value, session_head="session-other"),
        lambda value: replace(value, source_head="source-other"),
        lambda value: replace(value, trust_head="trust-other"),
        lambda value: replace(value, materialization_head="materialization-other"),
        lambda value: replace(value, freshness_sequence=8),
        lambda value: replace(value, peer_credential="peer-other"),
    ],
)
def test_each_trust_reference_substitution_is_stale(mutated: object) -> None:
    bridge, _, reference = _bridge()
    assert callable(mutated)
    changed = mutated(reference)
    assert bridge.revalidate(changed, "CREATE_INTENTION_LINE", SUBJECT).disposition == "STALE"


@pytest.mark.parametrize(
    ("operation", "subject"),
    [
        ("DELETE_INTENTION_LINE", SUBJECT),
        ("CREATE_INTENTION_LINE", RecordId(TENANT, "other-intention")),
    ],
)
def test_operation_or_payload_carried_subject_substitution_denies(
    operation: str, subject: RecordId
) -> None:
    bridge, _, reference = _bridge()
    assert bridge.revalidate(reference, operation, subject).disposition == "DENIED"
