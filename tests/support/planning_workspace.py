from __future__ import annotations

from typing import cast

from chiplog.adapters.driven.r9_planning import PlanningWorkspaceQueries
from chiplog.capabilities.planning._planning import _InMemoryPlanningRepository
from chiplog.capabilities.projections import PlanningProjectionQueries
from chiplog.capabilities.projections._planning import (
    _PlanningProjectionRebuilder,
    _PlanningReadStore,
)
from chiplog.domain_primitives import TenantId
from tests.support.workspace import IssuedContext


def empty_planning(contexts: IssuedContext) -> PlanningWorkspaceQueries:
    repository = _InMemoryPlanningRepository()
    public: PlanningProjectionQueries = _PlanningProjectionRebuilder(
        cast(_PlanningReadStore, repository)
    ).rebuild(TenantId(contexts.current.tenant_id))
    return PlanningWorkspaceQueries(public, contexts, {})
