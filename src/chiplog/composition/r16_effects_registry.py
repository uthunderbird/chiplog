"""Closed offline PlanEffect configuration; constructing it grants no authority.

Plan: register the exact self endpoint and implementation semantics, evaluate the
authenticated preview against the complete inventory, retain canonical evidence.
Assumptions: composition authenticates the supplied identity and inventory cut and
rechecks them under its writer gate. This pure module cannot prove provenance.
Purpose prose and payload are opaque: neither can extend this policy.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from chiplog.capabilities.effects.contracts import (
    DispatchSemanticBinding,
    EffectStoreSnapshot,
    ExactHead,
    ProviderRecipient,
)
from chiplog.composition.r16_effects import EffectPreviewBinding


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def exact_head(subject_id: str, canonical_value: bytes) -> ExactHead:
    digest = hashlib.sha256(canonical_value).hexdigest()
    return ExactHead(subject_id=subject_id, head=f"{subject_id}/{digest}", fingerprint=digest)


@dataclass(frozen=True)
class RegistryReference:
    source_id: str
    source_version: str
    head: ExactHead
    canonical_value: bytes


def _reference(name: str, value: object, version: str = "1") -> RegistryReference:
    data = _canonical(value)
    source = f"effects.hermetic.{name}"
    return RegistryReference(source, version, exact_head(source, data), data)


@dataclass(frozen=True)
class HermeticPolicyEvaluation:
    payload: bytes
    bundle_members: tuple[ExactHead, ...]
    recipient: ProviderRecipient
    semantics: DispatchSemanticBinding
    blocking_effect_heads: tuple[ExactHead, ...]
    affected_party_constraints: tuple[ExactHead, ...]
    hold_conflict_order: ExactHead
    dependencies: tuple[ExactHead, ...]
    factual_assertion_evidence: tuple[ExactHead, ...]
    verification_contradiction: tuple[ExactHead, ...]
    authority_applicability: tuple[ExactHead, ...]
    consequence_scope: ExactHead
    communication_mandate: ExactHead
    disclosure_projection: ExactHead
    channel_class: str
    registry_inputs: tuple[tuple[str, str], ...]
    references: tuple[RegistryReference, ...]


@dataclass(frozen=True)
class HermeticPlanEffectRegistry:
    """A single immutable profile, with no caller-selectable policy parameters."""

    @property
    def semantics(self) -> DispatchSemanticBinding:
        return DispatchSemanticBinding(
            normative_manifest="chiplog.hermetic-self-effect-policy.v1",
            reducer_version="chiplog.effects.domain.v1",
            transition_registry_version="chiplog.effects.application.prepare-transition.v1",
            canonicalization_fingerprint_version="chiplog.effects.sorted-json-sha256.v1",
            adapter_contract_version="chiplog.hermetic-effects.v1",
        )

    @property
    def endpoint_reference(self) -> RegistryReference:
        return _reference(
            "endpoint",
            {
                "schema": "chiplog.hermetic-effects.endpoint.v1",
                "provider": "hermetic-effects",
                "account": "hermetic-account",
                "recipient": "hermetic-principal",
                "canonical_address": "hermetic://effects/hermetic-principal",
                "adapter_contract": "chiplog.hermetic-effects.v1",
                "mode": "OFFLINE_PROFILE_NO_SEND_CAPABILITY",
            },
        )

    @property
    def credential_reference(self) -> RegistryReference:
        return _reference(
            "account-profile",
            {
                "schema": "chiplog.hermetic-effects.account-profile.v1",
                "tenant": "hermetic-tenant",
                "principal": "hermetic-principal",
                "account": "hermetic-account",
                "provider": "hermetic-effects",
                "kind": "OFFLINE_CONFIGURATION_IDENTITY_NOT_AUTHENTICATION_CREDENTIAL",
                "grants": [],
                "send_capability": False,
            },
        )

    @property
    def recipient(self) -> ProviderRecipient:
        return ProviderRecipient(
            provider="hermetic-effects",
            account="hermetic-account",
            recipient="hermetic-principal",
            endpoint=self.endpoint_reference.head,
            canonical_address=b"hermetic://effects/hermetic-principal",
            credential_binding=self.credential_reference.head,
        )

    def canonical_bytes(self) -> bytes:
        return _canonical(
            {
                "schema": "chiplog.hermetic-plan-effect-registry.v1",
                "operation": "effects.publish_plan_effect",
                "purpose": "ORDINARY_EFFECT",
                "semantics": self.semantics.model_dump(mode="json"),
                "implementations": {
                    "reducer": "chiplog.capabilities.effects.domain",
                    "transitions": "chiplog.capabilities.effects.application.prepare_transition",
                    "canonicalization": (
                        "chiplog.capabilities.effects.contracts._Frozen.canonical_bytes"
                    ),
                    "adapter": "chiplog.adapters.driven.effects_hermetic",
                },
                "endpoint": json.loads(self.endpoint_reference.canonical_value),
                "account_profile": json.loads(self.credential_reference.canonical_value),
                "policy": {
                    "identity": ["hermetic-tenant", "hermetic-principal"],
                    "scope": "explicitly adopted exact opaque payload and complete bundle to self",
                    "purpose_prose_grants": [],
                    "send_capability": False,
                    "dependencies": "no external or declarative dependencies in opaque emission",
                    "factual_claims": "no authenticated factual assertions in opaque emission",
                    "conflict_scope": (
                        "all latest tenant effects, regardless of payload or purpose prose"
                    ),
                    "blocking_states": sorted(_BLOCKING_STATES),
                    "unsupported_inventory": "REJECT",
                },
            }
        )

    @property
    def reference(self) -> ExactHead:
        return exact_head("effects.hermetic.registry", self.canonical_bytes())

    @property
    def read_reference(self) -> RegistryReference:
        return RegistryReference(
            "effects.hermetic.registry", "1", self.reference, self.canonical_bytes()
        )

    @property
    def registry_inputs(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (ref.source_id, ref.head.fingerprint)
            for ref in (
                self.read_reference,
                self.endpoint_reference,
                self.credential_reference,
            )
        )

    def evaluate(
        self,
        binding: EffectPreviewBinding,
        snapshot: EffectStoreSnapshot,
        *,
        tenant_id: str,
        principal_id: str,
    ) -> HermeticPolicyEvaluation:
        if (tenant_id, principal_id) != ("hermetic-tenant", "hermetic-principal"):
            raise ValueError("unsupported authenticated hermetic effect identity")
        if (binding.tenant, binding.principal, snapshot.tenant_id) != (
            tenant_id,
            principal_id,
            tenant_id,
        ):
            raise ValueError("preview or complete inventory belongs to another identity")
        recipient = self.recipient
        if (
            binding.provider,
            binding.account,
            binding.recipient,
            binding.canonical_address,
            binding.adapter_contract,
            binding.policy,
        ) != (
            recipient.provider,
            recipient.account,
            recipient.recipient,
            recipient.canonical_address.decode(),
            self.semantics.adapter_contract_version,
            self.semantics.normative_manifest,
        ):
            raise ValueError("unregistered recipient or policy scope")
        payload = binding.proposal.payload()
        latest = {}
        for record in snapshot.records:
            intent = record.snapshot.intent
            authority = intent.authority
            if (
                authority.tenant_id != tenant_id
                or authority.principal_id != principal_id
                or authority.actor_id != principal_id
                or authority.recipient != recipient
                or intent.semantics != self.semantics
                or intent.purpose.kind != "ORDINARY_EFFECT"
            ):
                raise ValueError("unsupported semantics or scope in complete effects inventory")
            latest[intent.intent_id] = record.snapshot
        ordered = tuple(latest[key] for key in sorted(latest))
        blockers = tuple(item.attempt for item in ordered if item.state in _BLOCKING_STATES)
        scope = {
            "preview": binding.model_dump(mode="json"),
            "tenant": tenant_id,
            "principal": principal_id,
            "recipient": recipient.model_dump(mode="json"),
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "bundle": binding.proposal.bundle_members,
            "purpose": "ORDINARY_EFFECT",
            "registry": self.reference.model_dump(mode="json"),
        }
        refs = [self.read_reference, self.endpoint_reference, self.credential_reference]

        def evidence(name: str, value: object, version: str = "1") -> ExactHead:
            ref = _reference(name, {"scope": scope, "evaluation": value}, version)
            refs.append(ref)
            return ref.head

        members = tuple(
            evidence(f"bundle-member/{n}", {"ordinal": n, "member": member})
            for n, member in enumerate(binding.proposal.bundle_members)
        )
        consequence = evidence("consequence-scope", "exact opaque self emission only")
        constraints = evidence(
            "constraints",
            {
                "affected_party": principal_id,
                "resources": [recipient.account],
                "external_dependencies": [],
                "authenticated_factual_assertions": [],
                "absence_scope": "opaque self emission only; no global absence claim",
            },
        )
        conflicts = evidence(
            "conflict-order",
            {
                "complete_inventory": {
                    "schema": "effects.inventory-reference.v1",
                    "tenant_id": snapshot.tenant_id,
                    "tenant_head": snapshot.tenant_head,
                    "ordered_records": [
                        row.record.model_dump(mode="json") for row in snapshot.records
                    ],
                    "canonical_inventory_sha256": hashlib.sha256(
                        snapshot.canonical_bytes()
                    ).hexdigest(),
                },
                "latest_order": [item.attempt.model_dump(mode="json") for item in ordered],
                "blockers": [item.model_dump(mode="json") for item in blockers],
            },
            version="2",
        )
        verification = evidence(
            "verification-scope",
            {
                "scope": "registered profile, exact preview, complete tenant inventory",
                "blocking_conflicts": [item.model_dump(mode="json") for item in blockers],
                "global_verification": False,
            },
        )
        applicability = evidence(
            "applicability",
            {
                "operation": "effects.publish_plan_effect",
                "send_authorized": False,
                "applicable": not blockers,
            },
        )
        mandate = evidence("mandate", "exact adopted opaque self emission only")
        disclosure = evidence(
            "disclosure",
            {
                "payload_sha256": hashlib.sha256(payload).hexdigest(),
                "recipient": recipient.model_dump(mode="json"),
                "extra_disclosure": False,
            },
        )
        return HermeticPolicyEvaluation(
            payload,
            members,
            recipient,
            self.semantics,
            blockers,
            (constraints,),
            conflicts,
            (),
            (),
            (verification,),
            (applicability,),
            consequence,
            mandate,
            disclosure,
            "chiplog.hermetic-effects.self.v1",
            self.registry_inputs,
            tuple(refs),
        )


_BLOCKING_STATES = frozenset(
    {
        "SEND_COMMITTED",
        "SENT",
        "OUTCOME_UNKNOWN",
        "PARTIAL",
        "PARTIAL_CONFIRMED",
    }
)
