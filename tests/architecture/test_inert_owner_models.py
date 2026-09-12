from __future__ import annotations

from chiplog.capabilities.planning.r7_boundary import R7PlanningCreateDTO
from chiplog.platform.r7_planning_boundary import BrokerPlanningCreateDTO
from chiplog.verification.r7_inert import verify_r7_inert_schema
from tests.support.inert_owner_models import SCHEMA, VALUES


def test_inert_schema_is_closed_and_owner_models_encode_identically() -> None:
    assert verify_r7_inert_schema(SCHEMA)[-1] == ("planning_snapshot_bytes", "bytes")
    planning = R7PlanningCreateDTO.model_validate(VALUES)
    broker = BrokerPlanningCreateDTO.model_validate(VALUES)
    assert planning.canonical_bytes() == broker.canonical_bytes()
