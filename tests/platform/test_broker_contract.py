from __future__ import annotations

from typing import is_protocol

import pytest
from pydantic import ValidationError

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


def test_broker_port_is_structural_protocol() -> None:
    assert is_protocol(AuthorityBrokerPort)


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
