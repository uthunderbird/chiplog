"""Static self-only denial policy; evaluation grants no runtime authority.

Composition authenticates identity, captures history independently, and rechecks
the writer cut. This registry binds those inputs without renewing the original
SEND lease or modifying the original PlanEffect semantic profile.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.effects.contracts import (
    AdoptedAuthorityAct,
    DispatchSemanticBinding,
    EffectStoreSnapshot,
    ExactHead,
    ExternalActionIntent,
)
from chiplog.capabilities.effects.denial_contracts import (
    CancelDecision,
    DenyingDecision,
    HoldDecision,
    SupersedeDecision,
)
from chiplog.capabilities.effects.dispatch_authority_contracts import DispatchObservationDTO
from chiplog.composition.r16_effects_registry import HermeticPlanEffectRegistry, exact_head


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class DenialIngress(DispatchObservationDTO):
    schema_id: Literal["chiplog.hermetic-denial-ingress.v1"]
    act_id: str = Field(min_length=1)
    intent_id: str = Field(min_length=1)
    expected_attempt: ExactHead
    disposition: Literal["HELD_BEFORE_SEND", "CANCELLED_BEFORE_SEND", "SUPERSEDED_BEFORE_SEND"]
    successor: ExactHead | None


def denial_command_id(tenant: str, principal: str, act_id: str) -> str:
    """Reserve one identity per authenticated act across targets and dispositions."""
    digest = hashlib.sha256(
        _canonical(["chiplog.hermetic-denial-command.v1", tenant, principal, act_id])
    ).hexdigest()
    return "effects.hermetic.denial/" + digest


def _intent_reference(intent: ExternalActionIntent) -> ExactHead:
    return ExactHead(
        subject_id=intent.intent_id,
        head=intent.intent_id + "/" + intent.fingerprint,
        fingerprint=intent.fingerprint,
    )


@dataclass(frozen=True)
class HermeticDenialRegistry:
    """Closed deny-only compatibility registration for the original self profile."""

    @property
    def semantics(self) -> DispatchSemanticBinding:
        return HermeticPlanEffectRegistry().semantics

    def canonical_bytes(self) -> bytes:
        original = HermeticPlanEffectRegistry()
        return _canonical(
            {
                "schema": "chiplog.hermetic-denial-registry.v1",
                "operation": "effects.before_send",
                "command_schema": "chiplog.effects.before-send-command.v2",
                "evaluator": "chiplog.capabilities.effects.denial.prepare_denial",
                "compatible_original_semantics": self.semantics.model_dump(mode="json"),
                "original_registry": original.reference.model_dump(mode="json"),
                "recipient": original.recipient.model_dump(mode="json"),
                "policy": {
                    "identity": ["hermetic-tenant", "hermetic-principal"],
                    "operation_right": "effects.before_send:self",
                    "scope": "own exact original self intent and exact latest attempt",
                    "dispositions": [
                        "HELD_BEFORE_SEND",
                        "CANCELLED_BEFORE_SEND",
                        "SUPERSEDED_BEFORE_SEND",
                    ],
                    "successor": "distinct exact independently captured adopted self intent",
                    "purpose_prose_grants": [],
                    "send_capability": False,
                    "original_send_lease_required": False,
                    "runtime_authentication": False,
                    "unsupported_scope": "REJECT",
                },
            }
        )

    @property
    def reference(self) -> ExactHead:
        return exact_head("effects.hermetic.denial-registry", self.canonical_bytes())

    def evaluate(
        self,
        ingress: DenialIngress,
        expected: EffectStoreSnapshot,
        tenant: str,
        principal: str,
    ) -> DenyingDecision:
        if (tenant, principal) != ("hermetic-tenant", "hermetic-principal"):
            raise LoopRejected("unsupported authenticated denial identity")
        if expected.tenant_id != tenant:
            raise LoopRejected("denial history belongs to another tenant")
        latest = {row.snapshot.intent.intent_id: row.snapshot for row in expected.records}
        target = latest.get(ingress.intent_id)
        if target is None or target.attempt != ingress.expected_attempt:
            raise LoopRejected("denial target is absent or exact latest attempt differs")

        def require_scope(intent: ExternalActionIntent) -> None:
            authority = intent.authority
            if (
                authority.tenant_id != tenant
                or authority.principal_id != principal
                or authority.actor_id != principal
                or intent.semantics != self.semantics
                or authority.recipient != HermeticPlanEffectRegistry().recipient
            ):
                raise LoopRejected("denial subject has unsupported identity, semantics or endpoint")

        require_scope(target.intent)
        successor_ref = None
        adoption = None
        if ingress.disposition == "SUPERSEDED_BEFORE_SEND":
            if ingress.successor is None or ingress.successor.subject_id == ingress.intent_id:
                raise LoopRejected("supersession requires a distinct exact successor")
            successor = latest.get(ingress.successor.subject_id)
            if successor is None or _intent_reference(successor.intent) != ingress.successor:
                raise LoopRejected(
                    "supersession successor is absent or exact immutable head differs"
                )
            require_scope(successor.intent)
            act = successor.intent.authority.act
            if not isinstance(act, AdoptedAuthorityAct):
                raise LoopRejected("supersession requires an independently adopted successor")
            successor_ref, adoption = ingress.successor, act.adoption
        elif ingress.successor is not None:
            raise LoopRejected("hold and cancellation cannot name a successor")

        evidence = exact_head(
            "effects.hermetic.denial-decision",
            _canonical(
                {
                    "ingress": ingress.model_dump(mode="json"),
                    "target": _intent_reference(target.intent).model_dump(mode="json"),
                    "registry": self.reference.model_dump(mode="json"),
                    "successor": (successor_ref.model_dump(mode="json") if successor_ref else None),
                    "successor_adoption": adoption.model_dump(mode="json") if adoption else None,
                }
            ),
        )
        if ingress.disposition == "HELD_BEFORE_SEND":
            return HoldDecision(evidence=evidence)
        if ingress.disposition == "CANCELLED_BEFORE_SEND":
            return CancelDecision(
                evidence=evidence,
                direct_act=exact_head("effects.hermetic.denial-act", ingress.canonical_bytes()),
            )
        if successor_ref is None or adoption is None:
            raise LoopRejected("supersession lacks exact successor adoption")
        return SupersedeDecision(
            evidence=evidence, successor_intent=successor_ref, successor_adoption=adoption
        )
