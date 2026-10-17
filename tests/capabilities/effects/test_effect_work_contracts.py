"""Public consumers distinguish real origins and observed work from authority."""

import pytest
from pydantic import ValidationError

from chiplog.capabilities.effects.effect_work_contracts import (
    CompensationEffectRequest,
    EffectWorkDiscovery,
    EffectWorkPort,
    EffectWorkScan,
    EffectWorkStep,
    PendingEffectSend,
    PlanEffectRequest,
    UnresolvedEffectClaim,
)


def test_discovery_roundtrips_exact_work_without_granting_send() -> None:
    ref = {"subject_id": "intent", "head": "head", "fingerprint": "digest"}
    fields = dict(tenant_id="t", principal_id="p", stream_id="stream", intent=ref, send=ref)
    pending = PendingEffectSend.model_validate(fields)
    claimed = UnresolvedEffectClaim.model_validate({**fields, "claim": ref})
    value = EffectWorkDiscovery(snapshot_head="snapshot", items=(pending, claimed), has_more=False)
    assert EffectWorkDiscovery.model_validate_json(value.canonical_bytes()) == value
    with pytest.raises(ValidationError):
        PendingEffectSend.model_validate({**fields, "allow_send": True})
    with pytest.raises(ValidationError):
        UnresolvedEffectClaim.model_validate(fields)
    with pytest.raises(ValidationError):
        EffectWorkScan(tenant_id="t", principal_id="p", limit=0)


def test_non_tool_origins_require_explicit_planning_and_original_evidence_bindings() -> None:
    plan_fields = PlanEffectRequest.model_fields
    compensation_fields = CompensationEffectRequest.model_fields
    assert plan_fields["planning_command_schema"].is_required()
    assert plan_fields["planning_command_bytes"].is_required()
    assert plan_fields["expected_planning_revision"].is_required()
    assert compensation_fields["original_evidence_head"].is_required()
    assert compensation_fields["original_stream_id"].is_required()
    for fields in (plan_fields, compensation_fields):
        assert fields["adopted_preview"].is_required()
        assert fields["authority_act_id"].is_required()
        assert "initialized_head" not in fields
        assert "original_call_id" not in fields


def test_plan_request_keeps_owner_bytes_and_compensation_requires_original_inventory() -> None:
    ref = {"subject_id": "subject", "head": "head", "fingerprint": "digest"}
    common = dict(
        tenant_id="t",
        principal_id="p",
        intent_id="new-intent",
        adopted_preview=ref,
        authority_act_id="fresh-act",
        payload=b"\x00\xff",
        bundle_members=("member",),
        target=dict(
            provider="fake",
            account="a",
            recipient="r",
            endpoint=ref,
            canonical_address=b"r",
            credential_binding=ref,
        ),
        expires_at_ns=1,
    )
    plan = PlanEffectRequest.model_validate(
        {
            **common,
            "planning_command_schema": "planning.create.v1",
            "planning_command_bytes": b"\x00\xff",
            "expected_planning_revision": None,
        }
    )
    assert PlanEffectRequest.model_validate_json(plan.canonical_bytes()) == plan
    compensation = CompensationEffectRequest.model_validate(
        {
            **common,
            "original_intent": ref,
            "original_stream_id": "original",
            "original_evidence_head": ref,
        }
    )
    assert (
        CompensationEffectRequest.model_validate_json(compensation.canonical_bytes())
        == compensation
    )
    values = compensation.model_dump()
    del values["original_evidence_head"]
    with pytest.raises(ValidationError):
        CompensationEffectRequest.model_validate(values)


async def consume_one(port: EffectWorkPort) -> EffectWorkStep | None:
    discovery = await port.discover_effect_work(
        EffectWorkScan(tenant_id="t", principal_id="p", limit=1)
    )
    return await port.step_effect_work(discovery.items[0]) if discovery.items else None
