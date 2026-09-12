from __future__ import annotations

import pytest
from pydantic import ValidationError

from chiplog.capabilities.planning.r7_boundary import R7PlanningCreateDTO
from chiplog.platform.r7_planning_boundary import BrokerPlanningCreateDTO
from tests.support.inert_owner_models import VALUES


def test_owner_models_are_distinct_and_strict() -> None:
    assert R7PlanningCreateDTO.__module__ != BrokerPlanningCreateDTO.__module__
    with pytest.raises(ValidationError):
        BrokerPlanningCreateDTO.model_validate({**VALUES, "tenant_id": 1})
    with pytest.raises(ValidationError):
        BrokerPlanningCreateDTO.model_validate({**VALUES, "callback": "forbidden"})
