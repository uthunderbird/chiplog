from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from chiplog.capabilities.planning.r7_boundary import R7PlanningCreateDTO
from chiplog.platform.r7_planning_boundary import BrokerPlanningCreateDTO
from chiplog.verification.r7_inert import R7InertSchemaViolation, verify_r7_inert_schema

ROOT = Path(__file__).parents[2]
SCHEMA = ROOT / "src/chiplog/inert_shared/r7-planning-v1.json"

VALUES = {
    "tenant_id": "tenant-1",
    "principal_id": "principal-1",
    "command_id": "command-1",
    "intention_line_id": "intention-1",
    "revision_id": "revision-1",
    "purpose": "Prepare release",
    "authority_act_id": "act-1",
    "trust_reference_bytes": b'{"head":"trust-1"}',
    "planning_snapshot_bytes": b'{"commands":[],"head":0,"record_ids":[]}',
}


def test_inert_schema_is_closed_and_owner_models_encode_identically() -> None:
    assert verify_r7_inert_schema(SCHEMA)[-1] == ("planning_snapshot_bytes", "bytes")
    planning = R7PlanningCreateDTO.model_validate(VALUES)
    broker = BrokerPlanningCreateDTO.model_validate(VALUES)
    assert planning.canonical_bytes() == broker.canonical_bytes()


def test_owner_models_are_distinct_and_strict() -> None:
    assert R7PlanningCreateDTO.__module__ != BrokerPlanningCreateDTO.__module__
    with pytest.raises(ValidationError):
        BrokerPlanningCreateDTO.model_validate({**VALUES, "tenant_id": 1})
    with pytest.raises(ValidationError):
        BrokerPlanningCreateDTO.model_validate({**VALUES, "callback": "forbidden"})


@pytest.mark.parametrize("mutation", ["extra", "owner", "type", "order"])
def test_inert_schema_mutants_are_rejected(tmp_path: Path, mutation: str) -> None:
    value = json.loads(SCHEMA.read_text())
    if mutation == "extra":
        value["validator"] = "planning.validate"
    elif mutation == "owner":
        value["owner"] = "broker"
    elif mutation == "type":
        value["fields"][0][1] = "callable"
    else:
        value["fields"] = list(reversed(value["fields"]))
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps(value))
    with pytest.raises(R7InertSchemaViolation):
        verify_r7_inert_schema(candidate)
