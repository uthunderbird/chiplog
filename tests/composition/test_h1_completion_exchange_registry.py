"""Focused fail-closed checks for B's retained completion exchange registry."""

from __future__ import annotations

import base64
import inspect
import time
from typing import Any, cast

import pytest

from chiplog.capabilities.agent_loop import _execution_completion_process as completion_process
from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    PreparedExecutionCompletion,
)
from chiplog.composition.h1_completion_exchange_registry import (
    H1CompletionExchangeRegistry,
    H1CompletionExchangeRegistryViolation,
)
from chiplog.composition.h1_completion_issuance import H1CompletionOwnerExchangeV1
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess
from tests.capabilities.agent_loop.test_execution_first_path_completion_contracts import (
    request as real_first_path_request,
)


async def _exchange(*, malformed: bool = False) -> H1CompletionOwnerExchangeV1:
    request = await real_first_path_request(canonical_response=True)
    reply = completion_process.dispatch(
        completion_process.FIRST_PATH_OPERATION, request.canonical_bytes()
    )
    raw = base64.b64decode(cast(str, reply["payload"]))
    result = PreparedExecutionCompletion.model_validate_json(raw)
    assert result.canonical_bytes() == raw
    callee = BrokerSession(
        tenant_id="tenant",
        broker_epoch=1,
        generation_id="generation",
        owner_id="agent_loop",
        session_id="agent-loop-session",
    )
    sent_at_ns = time.monotonic_ns()
    sent = PublicPortCall(
        operation_id="agent_loop.prepare_first_path_completion",
        request_id="h1-completion:registry",
        caller=BrokerSession(
            tenant_id="tenant",
            broker_epoch=1,
            generation_id="generation",
            owner_id="broker",
            session_id="broker:generation",
        ),
        callee=callee,
        schema_id=request.schema_id,
        canonical_payload=request.canonical_bytes(),
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            policy_version=1,
            absolute_deadline_ns=sent_at_ns + 1_000_000_000,
        ),
    )
    returned = PublicPortSuccess(
        request_id=sent.request_id,
        responder=callee,
        schema_id="chiplog.agent-loop.prepared-execution-completion-result.v1",
        canonical_payload=b"{}" if malformed else raw,
    )
    return H1CompletionOwnerExchangeV1(
        role="completion",
        sent=sent,
        returned=returned,
        sent_at_ns=sent_at_ns,
        returned_at_ns=sent_at_ns + 1,
    )


@pytest.mark.asyncio
async def test_registry_accepts_only_a_canonical_correlated_completion_wire() -> None:
    request = await real_first_path_request(canonical_response=True)
    exchange = await _exchange()

    H1CompletionExchangeRegistry._require_success(exchange, request)


@pytest.mark.asyncio
async def test_registry_rejects_a_noncanonical_completion_reply_before_registration() -> None:
    request = await real_first_path_request(canonical_response=True)
    exchange = await _exchange(malformed=True)

    with pytest.raises(H1CompletionExchangeRegistryViolation, match="malformed"):
        H1CompletionExchangeRegistry._require_success(exchange, request)


def test_registry_registration_accepts_only_the_exact_b_session_not_dtos_or_callbacks() -> None:
    signature = inspect.signature(H1CompletionExchangeRegistry._register_actual_success)

    assert tuple(signature.parameters) == ("self", "session")
    registry = object.__new__(H1CompletionExchangeRegistry)
    registry._closed = False
    with pytest.raises(H1CompletionExchangeRegistryViolation, match="session is foreign"):
        registry._register_actual_success(object())


def test_registry_revocation_clears_records_and_denies_future_use() -> None:
    registry = object.__new__(H1CompletionExchangeRegistry)
    registry._records = cast(Any, [object()])
    registry._closed = False

    registry._revoke_all()

    assert registry._records == []
    with pytest.raises(H1CompletionExchangeRegistryViolation, match="revoked"):
        registry._register_actual_success(object())
    with pytest.raises(H1CompletionExchangeRegistryViolation, match="revoked"):
        registry._replay_completion_exchange(object(), object())
