"""Public R7 broker contracts; implementations arrive after the contract freeze."""

from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

PositiveInt = Annotated[int, Field(gt=0)]


class _StrictBrokerDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class BrokerSession(_StrictBrokerDTO):
    tenant_id: str
    broker_epoch: PositiveInt
    generation_id: str
    owner_id: str
    session_id: str


class CallBudget(_StrictBrokerDTO):
    remaining_calls: PositiveInt
    remaining_depth: PositiveInt
    absolute_deadline_ns: PositiveInt
    policy_version: PositiveInt


class PublicPortCall(_StrictBrokerDTO):
    operation_id: str
    request_id: str
    caller: BrokerSession
    callee: BrokerSession
    schema_id: str
    canonical_payload: bytes
    budget: CallBudget
    held_resources: tuple[str, ...] = ()


class PublicPortFailure(_StrictBrokerDTO):
    kind: Literal[
        "BUDGET_EXHAUSTED",
        "DEADLINE_EXCEEDED",
        "OWNER_DRAINING",
        "STALE_GENERATION",
        "STALE_SESSION",
        "UNAVAILABLE",
        "PROTOCOL_REJECTED",
    ]
    reason: str


class PublicPortSuccess(_StrictBrokerDTO):
    disposition: Literal["SUCCESS"] = "SUCCESS"
    request_id: str
    responder: BrokerSession
    schema_id: str
    canonical_payload: bytes


class PublicPortRejected(_StrictBrokerDTO):
    disposition: Literal["REJECTED"] = "REJECTED"
    request_id: str
    responder: BrokerSession
    failure: PublicPortFailure


type PublicPortResult = PublicPortSuccess | PublicPortRejected


class AuthorityBrokerPort(Protocol):
    async def call(self, request: PublicPortCall) -> PublicPortResult: ...


__all__ = [
    "AuthorityBrokerPort",
    "BrokerSession",
    "CallBudget",
    "PublicPortCall",
    "PublicPortFailure",
    "PublicPortRejected",
    "PublicPortResult",
    "PublicPortSuccess",
]
