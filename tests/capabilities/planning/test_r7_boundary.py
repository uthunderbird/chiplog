from __future__ import annotations

import pytest
from pydantic import ValidationError

from chiplog.capabilities.planning.r7_boundary import R7PlanningCreateDTO


def test_planning_dto_is_strict_frozen_and_rejects_unknown_fields() -> None:
    values = {
        "tenant_id": "tenant-1",
        "principal_id": "principal-1",
        "command_id": "command-1",
        "intention_line_id": "intention-1",
        "revision_id": "revision-1",
        "purpose": "Prepare release",
        "authority_act_id": "act-1",
        "trust_reference_bytes": b"canonical-trust-reference",
        "planning_snapshot_bytes": b'{"commands":[],"head":0,"record_ids":[]}',
    }
    request = R7PlanningCreateDTO.model_validate(values)
    assert request.model_dump() == values
    with pytest.raises(ValidationError):
        R7PlanningCreateDTO.model_validate({**values, "unknown": "rejected"})
    with pytest.raises(ValidationError):
        R7PlanningCreateDTO.model_validate({**values, "tenant_id": 1})
