from __future__ import annotations

from dataclasses import is_dataclass
from typing import is_protocol

import pytest
from pydantic import ValidationError

from chiplog.capabilities.planning.r7_boundary import R7PlanningCreateDTO
from chiplog.composition import OwnerGeneration, R7GraphBuilder, RuntimeGraphGeneration
from chiplog.platform.broker import (
    AuthorityBrokerPort,
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortFailure,
    PublicPortRejected,
    PublicPortResult,
    PublicPortSuccess,
)


def test_consumer_can_describe_r7_generation_through_public_contracts() -> None:
    assert is_protocol(AuthorityBrokerPort)
    assert is_protocol(R7GraphBuilder)
    assert OwnerGeneration(
        "planning",
        "pid:1",
        "generation-1",
        "session-1",
        ("planning",),
        ("provider",),
        ("target",),
        ("factory",),
        ("APP",),
    )
    assert is_dataclass(RuntimeGraphGeneration)


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


def test_broker_call_contract_binds_identity_budget_and_closed_failure() -> None:
    caller = BrokerSession(
        tenant_id="tenant-1",
        broker_epoch=1,
        generation_id="generation-1",
        owner_id="cli",
        session_id="session-cli",
    )
    callee = BrokerSession(
        tenant_id="tenant-1",
        broker_epoch=1,
        generation_id="generation-1",
        owner_id="planning",
        session_id="session-planning",
    )
    call = PublicPortCall(
        operation_id="planning.create_intention_line",
        request_id="request-1",
        caller=caller,
        callee=callee,
        schema_id="chiplog.planning.public.create-intention-line.v1",
        canonical_payload=b"{}",
        budget=CallBudget(
            remaining_calls=4,
            remaining_depth=2,
            absolute_deadline_ns=10_000,
            policy_version=1,
        ),
    )
    failure = PublicPortFailure(kind="STALE_GENERATION", reason="generation replaced")
    result: PublicPortResult = PublicPortRejected(
        request_id=call.request_id, responder=callee, failure=failure
    )
    assert isinstance(result, PublicPortRejected)
    assert result.failure == failure

    success: PublicPortResult = PublicPortSuccess(
        request_id=call.request_id,
        responder=callee,
        schema_id=call.schema_id,
        canonical_payload=b"result",
    )
    assert isinstance(success, PublicPortSuccess)
    assert success.disposition == "SUCCESS"


@pytest.mark.parametrize(
    "field",
    ["remaining_calls", "remaining_depth", "absolute_deadline_ns", "policy_version"],
)
def test_broker_budget_rejects_non_positive_values(field: str) -> None:
    values = {
        "remaining_calls": 1,
        "remaining_depth": 1,
        "absolute_deadline_ns": 1,
        "policy_version": 1,
    }
    values[field] = 0
    with pytest.raises(ValidationError):
        CallBudget.model_validate(values)
