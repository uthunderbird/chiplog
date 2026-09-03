"""Non-importing verifier for the closed R7 inert shared-data language."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

ALLOWED_TOP_LEVEL = {"additionalProperties", "fields", "owner", "schema_id"}
ALLOWED_TYPES = {"bytes", "str"}
PLANNING_SCHEMA_ID = "chiplog.planning.public.create-intention-line.v1"
PLANNING_FIELDS = (
    ("tenant_id", "str"),
    ("principal_id", "str"),
    ("command_id", "str"),
    ("intention_line_id", "str"),
    ("revision_id", "str"),
    ("purpose", "str"),
    ("authority_act_id", "str"),
    ("trust_reference_bytes", "bytes"),
    ("planning_snapshot_bytes", "bytes"),
)


class R7InertSchemaViolation(ValueError):
    pass


def verify_r7_inert_schema(path: Path) -> tuple[tuple[str, str], ...]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as error:
        raise R7InertSchemaViolation("R7 inert schema is unavailable or malformed") from error
    if not isinstance(value, dict) or set(value) != ALLOWED_TOP_LEVEL:
        raise R7InertSchemaViolation("R7 inert schema top-level exact-set mismatch")
    if value["additionalProperties"] is not False:
        raise R7InertSchemaViolation("R7 inert schema must reject additional properties")
    if value["owner"] != "planning" or value["schema_id"] != PLANNING_SCHEMA_ID:
        raise R7InertSchemaViolation("R7 inert schema identity or owner substitution")
    raw_fields = value["fields"]
    if not isinstance(raw_fields, list) or any(
        not isinstance(item, list)
        or len(item) != 2
        or not all(isinstance(member, str) for member in item)
        for item in raw_fields
    ):
        raise R7InertSchemaViolation("R7 inert field declaration is not closed data")
    fields = tuple((cast(str, item[0]), cast(str, item[1])) for item in raw_fields)
    if any(field_type not in ALLOWED_TYPES for _, field_type in fields):
        raise R7InertSchemaViolation("R7 inert schema contains executable or unknown type")
    if fields != PLANNING_FIELDS:
        raise R7InertSchemaViolation("R7 inert field exact-set or canonical-order mismatch")
    return fields


__all__ = ["R7InertSchemaViolation", "verify_r7_inert_schema"]
