"""Registered self-only offline policy and independently held resource lifecycle.

The issuer owns an actual hermetic provider and a grant restricted to that object.
No request can select a provider, synthesize its grant or choose a credential head.
The resource object must be retained independently across runtime reopen; absence
or replacement cannot reconstruct a prior grant or renew an adopted clock epoch.
"""

from __future__ import annotations

import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Literal

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.capabilities.effects.contracts import (
    DispatchSemanticBinding,
    ExactHead,
    ProviderRecipient,
)
from chiplog.capabilities.effects.dispatch_v2 import canonical, digest, reference
from chiplog.platform.authority_gate import AuthorityGate

CLOCK = "chiplog.dispatch.monotonic.v2"
POLICY_ID = "chiplog.effects.hermetic-self-send-policy.v2"
SEMANTICS = DispatchSemanticBinding(
    normative_manifest=POLICY_ID,
    reducer_version="chiplog.effects.first-send-reducer.v2",
    transition_registry_version="chiplog.effects.vision-transitions.v2",
    canonicalization_fingerprint_version="chiplog.effects.intent.v2",
    adapter_contract_version="chiplog.hermetic-effects.v1",
)


def policy_bytes() -> bytes:
    return canonical(
        {
            "schema": POLICY_ID,
            "tenant": "hermetic-tenant",
            "principal": "hermetic-principal",
            "operations": ["PUBLISH_PLAN_EFFECT", "AUTHORIZE_SEND", "COMMIT_FIRST_SEND"],
            "provider": "hermetic-effects",
            "account": "hermetic-account",
            "recipient": "hermetic-principal",
            "address": "hermetic://effects/hermetic-principal",
            "scope": "OPAQUE_SELF_EMISSION_ONLY",
            "external_factual_assertions": [],
            "external_dependencies": [],
            "blocking_latest_states": [
                "SEND_COMMITTED",
                "SENT",
                "OUTCOME_UNKNOWN",
                "PARTIAL",
                "PARTIAL_CONFIRMED",
            ],
            "real_provider_entitlement": False,
            "maximum_horizon_ns": 60_000_000_000,
            "maximum_payload_bytes": 65536,
            "clock": CLOCK,
            "semantics": SEMANTICS.model_dump(mode="json"),
        }
    )


def policy_reference() -> ExactHead:
    return reference(POLICY_ID, policy_bytes())


@dataclass(frozen=True)
class ResourceObservation:
    grant_bytes: bytes
    credential_bytes: bytes
    endpoint_bytes: bytes
    clock_epoch: str
    signature: str


class HermeticDispatchResources:
    """Independent offline leaf/grant custody, never an injectable permission DTO."""

    def __init__(
        self,
        *,
        scenarios: tuple[
            Literal["CONFIRM", "PERMANENT_NO_EFFECT", "LOST_RESPONSE_AFTER_EFFECT", "MIXED"], ...
        ],
        cap: int,
    ) -> None:
        if cap < 1 or cap > len(scenarios):
            raise ValueError("offline grant cap must fit the independent provider scenario budget")
        self._key = secrets.token_bytes(32)
        self._provider = HermeticEffectsProvider(
            receipt_key=secrets.token_bytes(32), scenarios=scenarios
        )
        self._original_provider = self._provider
        self._grant_id = "hermetic-send-grant/" + secrets.token_hex(24)
        self._credential_id = "hermetic-send-credential/" + secrets.token_hex(24)
        self._epoch = "monotonic-process/" + secrets.token_hex(24)
        self._cap = cap
        self._generation = 0
        self._active = True
        self._gate: AuthorityGate | None = None

    def require_original_provider(self) -> HermeticEffectsProvider:
        if self._provider is not self._original_provider:
            raise ValueError("offline provider identity differs from original custody")
        return self._original_provider

    def bind(self, gate: AuthorityGate) -> None:
        # This binding is lifetime custody, not authority provided by gate equality.
        if self._gate is not None and self._gate != gate:
            raise ValueError("offline issuer already belongs to another authority bundle")
        self._gate = gate

    def _require_gate(self) -> AuthorityGate:
        if self._gate is None:
            raise ValueError("offline authority has no registered ordering domain")
        return self._gate

    def revoke(self) -> None:
        with self._require_gate().hold():
            self._generation += 1
            self._active = False

    def observe(self) -> ResourceObservation:
        with self._require_gate().hold():
            provider = self.require_original_provider()
            grant = canonical(
                {
                    "schema": "chiplog.hermetic.dispatch-grant.v1",
                    "grant_id": self._grant_id,
                    "version": self._generation,
                    "status": "ACTIVE" if self._active else "REVOKED",
                    "cap": self._cap,
                    "policy": policy_reference().model_dump(mode="json"),
                    "tenant": "hermetic-tenant",
                    "recipient": "hermetic-principal",
                    "provider": "hermetic-effects",
                    "clock_epoch": self._epoch,
                }
            )
            credential = canonical(
                {
                    "schema": "chiplog.hermetic.dispatch-credential.v1",
                    "credential_id": self._credential_id,
                    "version": self._generation,
                    "status": "ACTIVE" if self._active else "REVOKED",
                    "account": "hermetic-account",
                    "provider": "hermetic-effects",
                    "grant_id": self._grant_id,
                }
            )
            endpoint = canonical(
                {
                    "schema": "chiplog.hermetic.dispatch-endpoint.v1",
                    "provider": "hermetic-effects",
                    "account": "hermetic-account",
                    "recipient": "hermetic-principal",
                    "canonical_address": "hermetic://effects/hermetic-principal",
                    "adapter_contract": provider.contract_version,
                }
            )
            payload = canonical([grant.hex(), credential.hex(), endpoint.hex(), self._epoch])
            return ResourceObservation(
                grant,
                credential,
                endpoint,
                self._epoch,
                hmac.digest(self._key, b"dispatch-resources.v1\x00" + payload, "sha256").hex(),
            )

    def verify_current(self, observation: ResourceObservation) -> bool:
        with self._require_gate().hold():
            current = self.observe()
            return (
                self._active
                and current == observation
                and hmac.compare_digest(current.signature, observation.signature)
            )

    def verify_historical(self, observation: ResourceObservation) -> bool:
        payload = canonical(
            [
                observation.grant_bytes.hex(),
                observation.credential_bytes.hex(),
                observation.endpoint_bytes.hex(),
                observation.clock_epoch,
            ]
        )
        return hmac.compare_digest(
            observation.signature,
            hmac.digest(self._key, b"dispatch-resources.v1\x00" + payload, "sha256").hex(),
        )

    def recipient(self, observation: ResourceObservation) -> ProviderRecipient:
        if not self.verify_current(observation):
            raise ValueError("offline endpoint/credential/grant observation no longer current")
        return ProviderRecipient(
            provider="hermetic-effects",
            account="hermetic-account",
            recipient="hermetic-principal",
            endpoint=reference("hermetic-effects-endpoint", observation.endpoint_bytes),
            canonical_address=b"hermetic://effects/hermetic-principal",
            credential_binding=reference(self._credential_id, observation.credential_bytes),
        )

    def clock(self) -> tuple[str, int]:
        with self._require_gate().hold():
            return self._epoch, time.monotonic_ns()

    @property
    def grant_identity(self) -> str:
        return self._grant_id

    @property
    def cap(self) -> int:
        return self._cap

    def observation_digest(self, observation: ResourceObservation) -> str:
        return digest(
            canonical(
                [
                    observation.grant_bytes.hex(),
                    observation.credential_bytes.hex(),
                    observation.endpoint_bytes.hex(),
                    observation.clock_epoch,
                    observation.signature,
                ]
            )
        )
