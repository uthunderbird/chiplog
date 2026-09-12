from __future__ import annotations

import json
from pathlib import Path

import pytest

from chiplog.verification.r7_inert import R7InertSchemaViolation, verify_r7_inert_schema
from tests.support.inert_owner_models import SCHEMA


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
