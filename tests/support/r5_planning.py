from __future__ import annotations

from chiplog.capabilities.planning import (
    CreateIntentionLine,
    InvocationContext,
    PlanningTrustDecision,
    PlanningTrustReference,
)
from chiplog.domain_primitives import PermissionScope, PrincipalId, RecordId, TenantId

TENANT = TenantId("tenant-1")

PRINCIPAL = PrincipalId("principal-1")


class TrustFake:
    def __init__(self, disposition: str = "VALID") -> None:
        self.disposition = disposition
        self.calls: list[tuple[PlanningTrustReference, str, RecordId]] = []
        self.before_return: object | None = None

    def revalidate(
        self, reference: PlanningTrustReference, operation: str, subject_id: RecordId
    ) -> PlanningTrustDecision:
        self.calls.append((reference, operation, subject_id))
        if callable(self.before_return):
            self.before_return()
        return PlanningTrustDecision(self.disposition, None)  # type: ignore[arg-type]


def context(
    *, tenant: TenantId = TENANT, sequence: int = 0, contour: str = "CLI"
) -> InvocationContext:
    reference = PlanningTrustReference(
        tenant,
        PRINCIPAL,
        contour,
        "credential-head",
        "session-head",
        "source-head",
        "trust-head",
        "materialization-head",
        sequence,
        "peer-credential",
    )
    return InvocationContext(
        tenant, PRINCIPAL, PermissionScope("planning.create_intention_line"), reference
    )


def command(
    *,
    tenant: TenantId = TENANT,
    command_value: str = "command-1",
    line_value: str = "line-1",
    revision_value: str = "revision-1",
    purpose: str = "Ship the first trustworthy slice",
) -> CreateIntentionLine:
    return CreateIntentionLine(
        RecordId(tenant, command_value),
        RecordId(tenant, line_value),
        RecordId(tenant, revision_value),
        purpose,
        "principal-act-1",
    )
