"""Signed execution boundary; private signing keys never enter the runtime."""

from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.deployment_trust.operator_policy_command_verifier import (
    _verify_ed25519_signature,
)
from chiplog.composition.r15_lease_acquire import EXECUTION_SERVICE
from chiplog.composition.r15_scheduler_registry import _canonical, _digest
from chiplog.composition.r15_tick_contracts import TickDTO

DOMAIN = b"chiplog.r15.scheduler-execution.acquire.v2\x00"


class LeaseExecutionAuthority(TickDTO):
    schema_id: Literal["chiplog.r15.lease-execution-authority.v2"] = (
        "chiplog.r15.lease-execution-authority.v2"
    )
    principal_id: Literal["r15-hermetic-execution-principal-v1"] = (
        "r15-hermetic-execution-principal-v1"
    )
    service_identity: Literal["r15-hermetic-scheduler-execution-v1"] = EXECUTION_SERVICE
    session_id: Literal["r15-hermetic-execution-session-v1"] = "r15-hermetic-execution-session-v1"
    public_key: bytes = Field(min_length=32, max_length=32)

    def canonical_bytes(self) -> bytes:
        return _canonical(self.model_dump(mode="json"))

    def record(self, policy: Present) -> dict[str, object]:
        return {"authority": self.model_dump(mode="json"), "policy": policy.model_dump(mode="json")}


@dataclass(frozen=True)
class LeaseExecutionSession:
    """Inert retained challenge, never an execution capability."""

    service_identity: str
    session_id: str
    policy: Present
    broker_epoch: int
    generation: str
    authority: LeaseExecutionAuthority
    nonce: str
    expires_monotonic_ns: int

    def intent(self, root_id: str, command_id: str, duration_ns: int) -> dict[str, object]:
        return {
            "schema": "chiplog.r15.lease-acquire-intent.v2",
            "operation": "scheduler.acquire",
            "tenant": "hermetic-tenant",
            "principal": self.authority.principal_id,
            "root_id": root_id,
            "command_id": command_id,
            "duration_ns": duration_ns,
            "service": self.service_identity,
            "session": self.session_id,
            "policy": self.policy.model_dump(mode="json"),
            "authority": _digest(self.authority.canonical_bytes()),
        }

    def signed_message(
        self, root_id: str, command_id: str, duration_ns: int = 60_000_000_000
    ) -> bytes:
        return DOMAIN + _canonical(
            {
                "intent": self.intent(root_id, command_id, duration_ns),
                "challenge": {
                    "nonce": self.nonce,
                    "broker_epoch": self.broker_epoch,
                    "generation": self.generation,
                    "expires_monotonic_ns": self.expires_monotonic_ns,
                },
            }
        )


def verify_execution(authority: LeaseExecutionAuthority, message: bytes, signature: bytes) -> None:
    if not message.startswith(DOMAIN) or len(signature) != 64:
        raise LoopRejected("independent execution signature required")
    try:
        _verify_ed25519_signature(authority.public_key, signature, message)
    except Exception as error:
        raise LoopRejected("independent execution signature invalid") from error
