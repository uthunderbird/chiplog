"""Retained wire checks only; these fixtures authenticate no runtime source."""

import pytest
from pydantic import ValidationError

from chiplog.capabilities.agent_loop.execution_fan_out_contracts import (
    ExecutionCapturedFanOutProposal,
)
from chiplog.capabilities.agent_loop.execution_fan_out_preparation import (
    prepare_execution_captured_fan_out,
)
from chiplog.composition.r14_execution_fanout_contracts import RetainedExecutionFanOutPreparation
from chiplog.composition.r14_fanout_contracts import RetainedFanOutPreparation
from chiplog.platform.broker import BrokerSession
from tests.support.execution_fan_out import fixture


@pytest.fixture
async def retained() -> RetainedExecutionFanOutPreparation:
    request = await fixture()
    proposal = prepare_execution_captured_fan_out(request)
    assert isinstance(proposal, ExecutionCapturedFanOutProposal)
    return RetainedExecutionFanOutPreparation(
        request=request,
        proposal=proposal,
        expected_snapshot_fingerprint="a" * 64,
        caller=BrokerSession(
            tenant_id="tenant",
            broker_epoch=1,
            generation_id="0",
            owner_id="broker",
            session_id="caller",
        ),
        callee=BrokerSession(
            tenant_id="tenant",
            broker_epoch=1,
            generation_id="0",
            owner_id="agent_loop",
            session_id="callee",
        ),
        request_id="request",
        deadline_ns=10,
    )


def test_retained_wire_preserves_exact_capture_and_owner_run(
    retained: RetainedExecutionFanOutPreparation,
) -> None:
    restored = RetainedExecutionFanOutPreparation.model_validate_json(retained.canonical_bytes())
    assert restored.request.canonical_bytes() == retained.request.canonical_bytes()
    assert restored.proposal.canonical_bytes() == retained.proposal.canonical_bytes()
    assert (
        restored.proposal.sealed_run.canonical_bytes()
        == retained.proposal.sealed_run.canonical_bytes()
    )
    with pytest.raises(ValidationError):
        RetainedFanOutPreparation.model_validate_json(retained.canonical_bytes())


@pytest.mark.parametrize("field", ["accepted_run", "companions", "authorized", "prepared_batch"])
def test_retained_wire_rejects_caller_replacement_or_authority(
    retained: RetainedExecutionFanOutPreparation, field: str
) -> None:
    wire = retained.model_dump(mode="json")
    wire[field] = {}
    with pytest.raises(ValidationError):
        RetainedExecutionFanOutPreparation.model_validate(wire)
