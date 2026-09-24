"""Shared exact dispatch mandate, ticket and model-response builders."""

import base64
import hashlib
import json

from chiplog.adapters.driven.effects_hermetic import IssuedEffectSendTicket
from chiplog.composition.r16_effects import HermeticEffectProposal


def head(subject: str = "subject") -> dict[str, str]:
    return {"subject_id": subject, "head": "head", "fingerprint": "a" * 64}


def mandate_wire() -> dict[str, object]:
    value: dict[str, object] = {
        "schema_id": "chiplog.effects.dispatch-mandate.v2",
        "mandate_id": "mandate",
        "tenant_id": "tenant",
        "principal_id": "principal",
        "actor_id": "principal",
        "origin": {
            "kind": "INITIALIZED_CONSEQUENTIAL_CALL",
            "original_run_id": "original-run",
            "original_call_id": "original-call",
            "initialization_run_head": head("original-run"),
            "initialized_head": head("original-call"),
            "tool_name": "effects.emit_adopted_hermetic_v2",
            "tool_schema": "tool.v2",
            "tool_policy": head(),
            "sealed_arguments": b"\xff\x00",
        },
        "channel_class": "hermetic",
        "recipient": {
            "provider": "hermetic-effects",
            "account": "account",
            "recipient": "principal",
            "endpoint": head(),
            "canonical_address": b"hermetic://self",
            "credential_binding": head(),
        },
        "payload": b"\xff\x00payload",
        "effect_fingerprint": "b" * 64,
        "bundle_members": (head(),),
        "idempotency_fence_key": "one-effect",
        "horizon": {
            "clock_contract": "clock.v1",
            "clock_epoch": "epoch",
            "not_before_ns": 1,
            "expires_at_ns": 20,
            "continuity_policy": head(),
        },
        "semantics": {
            "normative_manifest": "manifest.v2",
            "reducer_version": "reducer.v2",
            "transition_registry_version": "transitions.v2",
            "canonicalization_fingerprint_version": "canonical.v2",
            "adapter_contract_version": "adapter.v2",
        },
    }
    for name in (
        "operation_profile",
        "planning_revision",
        "preexisting_authority_basis",
        "normative_conflict_generation",
        "consequence_scope",
        "communication_mandate",
        "disclosure_projection",
        "interaction_context",
    ):
        value[name] = head()
    for name in (
        "authority_sources",
        "affected_party_constraints",
        "dependencies",
        "factual_assertion_evidence",
        "verification_contradiction",
        "authority_applicability",
    ):
        value[name] = (head(),)
    return value


def _ticket() -> IssuedEffectSendTicket:
    return IssuedEffectSendTicket(
        tenant_id="tenant",
        issued_operation_id="issued",
        journal_decision_id="decision",
        journal_decision_fingerprint="a" * 64,
        transmission_id="child0",
        transmission_fingerprint="b" * 64,
        request_ordinal=0,
        intent_id="intent",
        intent_fingerprint="c" * 64,
        provider="hermetic-effects",
        account="hermetic-account",
        recipient="hermetic-principal",
        canonical_address=b"hermetic://effects/hermetic-principal",
        endpoint_head="endpoint",
        credential_binding_head="credential",
        adapter_contract_version="chiplog.hermetic-effects.v1",
        payload=b"\xff\x00x",
        payload_fingerprint=hashlib.sha256(b"\xff\x00x").hexdigest(),
        idempotency_key="key",
        bundle_members=("effect",),
    )


def _response() -> bytes:
    proposal = HermeticEffectProposal(
        schema_id="chiplog.hermetic-effect-proposal.v1",
        purpose="My action",
        payload_base64=base64.b64encode(b"exact payload").decode(),
        bundle_members=("self-action",),
    )
    return json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": "effect",
                    "tool": "propose_intent",
                    "text": proposal.canonical_bytes().decode(),
                }
            ],
        }
    ).encode()
