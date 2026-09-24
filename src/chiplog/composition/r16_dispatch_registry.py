"""Registered self-only offline policy and independently held resource lifecycle.

The issuer owns an actual hermetic provider and a grant restricted to that object.
No request can select a provider, synthesize its grant or choose a credential head.
The resource object must be retained independently across runtime reopen; absence
or replacement cannot reconstruct a prior grant or renew an adopted clock epoch.
"""

from __future__ import annotations

import hmac
import json
import secrets
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from chiplog.adapters.driven.effects_hermetic import (
    HermeticEffectsProvider,
    HermeticReceiptObservation,
    IssuedEffectSendTicket,
    verify_hermetic_receipt,
)
from chiplog.capabilities.effects.contracts import (
    DispatchSemanticBinding,
    ExactHead,
    ProviderRecipient,
)
from chiplog.capabilities.effects.dispatch_outcome_contracts import (
    DispatchOutcomePreparationV2,
    DispatchOutcomeRecordV2,
)
from chiplog.capabilities.effects.dispatch_v2 import canonical, digest, reference
from chiplog.platform.authority_gate import AuthorityGate

from .r14_call_dispatch_policy import policy_reference as call_policy_reference
from .r16_dispatch_custody import HistoricalDispatchCustody

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


def verify_historical_observation(
    custody: HistoricalDispatchCustody, observation: ResourceObservation
) -> bool:
    """Verify original R16 evidence without a provider, clock, or current grant state."""
    try:
        payload = canonical(
            [
                observation.grant_bytes.hex(),
                observation.credential_bytes.hex(),
                observation.endpoint_bytes.hex(),
                observation.clock_epoch,
            ]
        )
        expected = hmac.digest(
            custody.issuer_key, b"dispatch-resources.v1\x00" + payload, "sha256"
        ).hex()
        return type(observation.signature) is str and hmac.compare_digest(
            observation.signature, expected
        )
    except AttributeError, TypeError, ValueError:
        return False


def historical_recipient(
    custody: HistoricalDispatchCustody, observation: ResourceObservation
) -> ProviderRecipient:
    """Derive the original recipient after authenticating its retained observation."""
    if not verify_historical_observation(custody, observation):
        raise ValueError("historical offline recipient observation is unauthentic")
    return ProviderRecipient(
        provider="hermetic-effects",
        account="hermetic-account",
        recipient="hermetic-principal",
        endpoint=reference("hermetic-effects-endpoint", observation.endpoint_bytes),
        canonical_address=b"hermetic://effects/hermetic-principal",
        credential_binding=reference(custody.credential_id, observation.credential_bytes),
    )


class HermeticDispatchResources:
    """Independent offline leaf/grant custody, never an injectable permission DTO."""

    def __init__(
        self,
        *,
        scenarios: tuple[
            Literal["CONFIRM", "PERMANENT_NO_EFFECT", "LOST_RESPONSE_AFTER_EFFECT", "MIXED"], ...
        ],
        cap: int,
        custody_path: Path | None = None,
    ) -> None:
        if cap < 1 or cap > len(scenarios):
            raise ValueError("offline grant cap must fit the independent provider scenario budget")
        from chiplog.composition.r16_dispatch_custody import load_or_create

        retained = None if custody_path is None else load_or_create(custody_path, scenarios, cap)
        self._custody_path = custody_path
        self._key = (
            secrets.token_bytes(32)
            if retained is None
            else bytes.fromhex(str(retained["issuer_key"]))
        )
        self._receipt_key = (
            secrets.token_bytes(32)
            if retained is None
            else bytes.fromhex(str(retained["receipt_key"]))
        )
        self._provider = HermeticEffectsProvider(
            receipt_key=self._receipt_key,
            scenarios=scenarios,
            journal_path=None if custody_path is None else custody_path.with_suffix(".transfers"),
        )
        self._original_provider = self._provider
        self._grant_id = (
            "hermetic-send-grant/" + secrets.token_hex(24)
            if retained is None
            else str(retained["grant_id"])
        )
        self._credential_id = (
            "hermetic-send-credential/" + secrets.token_hex(24)
            if retained is None
            else str(retained["credential_id"])
        )
        self._revocation_path = (
            None if custody_path is None else custody_path.with_suffix(".revoked")
        )
        self._epoch = "monotonic-process/" + secrets.token_hex(24)
        self._cap = cap
        self._generation = 0
        self._active = True
        self._gate: AuthorityGate | None = None

    def verify_receipt(
        self, ticket: IssuedEffectSendTicket, raw: bytes
    ) -> HermeticReceiptObservation:
        self.require_original_provider()
        return verify_hermetic_receipt(ticket, raw, registered_receipt_key=self._receipt_key)

    def seal_outcome(self, raw: bytes) -> str:
        self.require_original_provider()
        return hmac.digest(self._key, b"dispatch-outcome.v2\x00" + raw, "sha256").hex()

    def seal_boundary_outcome(
        self, request: DispatchOutcomePreparationV2, record: DispatchOutcomeRecordV2
    ) -> str:
        command = request.command
        if (
            command.operation != "APPEND_EVIDENCE"
            or command.evidence is None
            or command.evidence.observation != "BOUNDARY_CROSSED"
        ):
            raise ValueError("boundary seal requires original SEND uncertainty")
        # This signs a broker observation, never a provider fact or send permit.
        # Admission still verifies the original selected SEND and exact owner result.
        raw = canonical([request.model_dump(mode="json"), record.model_dump(mode="json")])
        return hmac.digest(self._key, b"dispatch-outcome.v2\x00" + raw, "sha256").hex()

    def verify_outcome(self, raw: bytes, signature: str) -> bool:
        expected = hmac.digest(self._key, b"dispatch-outcome.v2\x00" + raw, "sha256").hex()
        return hmac.compare_digest(expected, signature)

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
            if self._revocation_path is not None:
                import os

                with ExitStack() as resources:
                    descriptor = os.open(self._revocation_path, os.O_CREAT | os.O_WRONLY, 0o600)
                    resources.callback(os.close, descriptor)
                    os.fsync(descriptor)
                with ExitStack() as resources:
                    directory = os.open(self._revocation_path.parent, os.O_RDONLY)
                    resources.callback(os.close, directory)
                    os.fsync(directory)

    def observe(self) -> ResourceObservation:
        """Original PlanEffect observation; its canonical bytes retain their meaning."""
        return self._observe(for_call=False)

    def observe_call(self) -> ResourceObservation:
        """Fixed initialized-call grant from the same independent resource custody."""
        return self._observe(for_call=True)

    def _observe(self, *, for_call: bool) -> ResourceObservation:
        with self._require_gate().hold():
            if self._revocation_path is not None:
                try:
                    self._revocation_path.lstat()
                except FileNotFoundError:
                    pass
                else:
                    # Revocation is shared by every live holder of this custody.
                    # Once observed, disappearance cannot reactivate this holder.
                    self._active = False
            provider = self.require_original_provider()
            grant_id = self.grant_identities[1] if for_call else self._grant_id
            policy = call_policy_reference() if for_call else policy_reference()
            grant = canonical(
                {
                    "schema": "chiplog.hermetic.dispatch-grant.v1",
                    "grant_id": grant_id,
                    "version": self._generation,
                    "status": "ACTIVE" if self._active else "REVOKED",
                    "cap": self._cap,
                    "policy": policy.model_dump(mode="json"),
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
                    "grant_id": grant_id,
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
            try:
                grant = json.loads(observation.grant_bytes)
            except ValueError, TypeError:
                return False
            if not isinstance(grant, dict):
                return False
            if grant.get("policy") == policy_reference().model_dump(mode="json"):
                current = self.observe()
            elif grant.get("policy") == call_policy_reference().model_dump(mode="json"):
                current = self.observe_call()
            else:
                return False
            return (
                self._active
                and current == observation
                and hmac.compare_digest(current.signature, observation.signature)
            )

    def verify_historical(self, observation: ResourceObservation) -> bool:
        return verify_historical_observation(
            HistoricalDispatchCustody(
                issuer_key=self._key,
                credential_id=self._credential_id,
                grant_id=self._grant_id,
                cap=self._cap,
            ),
            observation,
        )

    def recipient(self, observation: ResourceObservation) -> ProviderRecipient:
        if not self.verify_current(observation):
            raise ValueError("offline endpoint/credential/grant observation no longer current")
        return self.historical_recipient(observation)

    def historical_recipient(self, observation: ResourceObservation) -> ProviderRecipient:
        """Read an originally signed recipient without renewing its current authority."""
        return historical_recipient(
            HistoricalDispatchCustody(
                issuer_key=self._key,
                credential_id=self._credential_id,
                grant_id=self._grant_id,
                cap=self._cap,
            ),
            observation,
        )

    def clock(self) -> tuple[str, int]:
        with self._require_gate().hold():
            return self._epoch, time.monotonic_ns()

    @property
    def grant_identity(self) -> str:
        return self._grant_id

    @property
    def grant_identities(self) -> tuple[str, str]:
        """Exactly these grants share the same cap; consumers must count both."""
        return self._grant_id, self._grant_id + "/initialized-call.v1"

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
