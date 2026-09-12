from __future__ import annotations

import hashlib
from typing import cast

from chiplog.adapters.driven.r9_planning import PlanningWorkspaceQueries
from chiplog.capabilities.planning import (
    CreateIntentionLine,
    InvocationContext,
    PlanningTrustDecision,
    PlanningTrustReference,
)
from chiplog.capabilities.planning._planning import _InMemoryPlanningRepository, _PlanningUseCase
from chiplog.capabilities.projections import PlanningProjectionQueries
from chiplog.capabilities.projections._planning import (
    _PlanningProjectionRebuilder,
    _PlanningReadStore,
)
from chiplog.capabilities.projections.disclosure import label
from chiplog.capabilities.projections.workspace_boundary import SourceReference
from chiplog.domain_primitives import PermissionScope, PrincipalId, RecordId, TenantId
from tests.support.workspace import IssuedContext, request


class Trust:
    def revalidate(
        self, reference: PlanningTrustReference, operation: str, subject_id: RecordId
    ) -> PlanningTrustDecision:
        return PlanningTrustDecision("VALID", None)


async def test_existing_public_planning_query_builds_non_authoritative_workspace() -> None:
    # Historical owner fixture setup; the R9 consumer below uses only the public query port.
    tenant = TenantId("tenant")
    principal = PrincipalId("principal")
    reference = PlanningTrustReference(
        tenant,
        principal,
        "CLI",
        "credential",
        "session",
        "source",
        "trust",
        "materialization",
        0,
        "peer",
    )
    repository = _InMemoryPlanningRepository()
    owner = _PlanningUseCase(repository, Trust())
    result = owner.execute(
        InvocationContext(
            tenant, principal, PermissionScope("planning.create_intention_line"), reference
        ),
        CreateIntentionLine(
            RecordId(tenant, "command"),
            RecordId(tenant, "line"),
            RecordId(tenant, "revision"),
            "Ship R9",
            "act",
        ),
    )
    assert result.disposition == "COMMITTED"
    public: PlanningProjectionQueries = _PlanningProjectionRebuilder(
        cast(_PlanningReadStore, repository)
    ).rebuild(tenant)
    publication = repository.committed_publications(tenant)[0]
    sources = tuple(
        sorted(
            (
                SourceReference(
                    tenant_id="tenant",
                    owner="planning",
                    record_id=record.record_id.value,
                    record_version="1",
                    content_digest=hashlib.sha256(record.canonical_bytes).hexdigest(),
                    label_head="label-head",
                    label=label("UNRESTRICTED"),
                )
                for record in publication.records
            ),
            key=lambda source: (source.owner, source.record_id, source.record_version),
        )
    )
    contexts = IssuedContext()
    bridge = PlanningWorkspaceQueries(public, contexts, {"line": sources})
    before = repository.committed_publications(tenant)
    output = await bridge.read(request().model_copy(update={"query": "PLANNING_VIEW"}))
    assert output.disposition == "LAGGING"
    assert b"Ship R9" in output.rows[0].canonical_payload
    assert output.rows[0].envelope.sources == sources
    assert repository.committed_publications(tenant) == before
