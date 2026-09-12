from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import ValidationError

from chiplog.capabilities.calendar_observations import boundary as calendar
from chiplog.capabilities.evidence_journal import boundary as journal
from chiplog.capabilities.projections import workspace_boundary as workspace

OWNERS = (workspace, journal, calendar)
SCHEMA = Path(__file__).parents[2] / "src/chiplog/inert_shared/r9-r11-workspace-v1.json"


def test_public_consumer_schemas_match_inert_contract() -> None:
    frozen = json.loads(SCHEMA.read_text())
    for owner in OWNERS:
        for name, schema in frozen["models"].items():
            model = getattr(owner, name)
            assert model.model_json_schema() == schema
            assert model.model_config["frozen"]
            assert model.model_config["extra"] == "forbid"
        assert inspect.iscoroutinefunction(owner.WorkspaceQueryPort.read)


def test_owner_local_context_round_trip_preserves_every_binding() -> None:
    original = workspace.WorkspaceReadContext(
        tenant_id="tenant",
        database_instance_id="db",
        principal_id="principal",
        principal_contour_head="contour",
        channel_id="cli",
        endpoint_binding_head="endpoint",
        broker_epoch=1,
        owner_id="calendar_observations",
        generation_id="generation",
        session_id="session",
        snapshot_id="snapshot",
        snapshot_frontier=2,
        verified_snapshot_bytes=b"\x00\xffexact-owner-bytes",
        invalidator_registry_digest="registry",
        policy_head="policy",
        deletion_fence_head="fence",
        observed_at_ns=3,
    )
    wire = original.model_dump_json()
    for owner in OWNERS:
        parsed = owner.WorkspaceReadContext.model_validate_json(wire)
        assert parsed.model_dump_json() == wire
        with pytest.raises(ValidationError):
            parsed.session_id = "replacement"
        for field in workspace.WorkspaceReadContext.model_fields:
            missing = json.loads(wire)
            del missing[field]
            with pytest.raises(ValidationError):
                owner.WorkspaceReadContext.model_validate_json(json.dumps(missing))
        unknown = json.loads(wire) | {"authenticated": True}
        with pytest.raises(ValidationError):
            owner.WorkspaceReadContext.model_validate_json(json.dumps(unknown))


@pytest.mark.parametrize("owner", OWNERS)
def test_explicit_bottom_is_required_and_unknown_label_rejects(owner: ModuleType) -> None:
    model = owner.DisclosureLabel
    good = {
        "lattice_version": "chiplog.disclosure.v1",
        "value": "UNRESTRICTED",
        "allowed_endpoints": [],
    }
    assert model.model_validate_json(json.dumps(good)).value == "UNRESTRICTED"
    for bad in ({}, good | {"value": "MODEL_APPROVED"}, good | {"lattice_version": "vNext"}):
        with pytest.raises(ValidationError):
            model.model_validate_json(json.dumps(bad))
