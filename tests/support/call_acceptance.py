"""Untrusted public mandate fixture; no authentication, selection or source proof."""

import hashlib

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.effects.dispatch_v2_contracts import DispatchMandateV2
from chiplog.composition.r14_call_acceptance_port import CallAcceptanceTarget


def preview_inputs() -> tuple[CallAcceptanceTarget, DispatchMandateV2]:
    def head(subject: str) -> dict[str, str]:
        return {"subject_id": subject, "head": subject + "/head", "fingerprint": "a" * 64}

    target = CallAcceptanceTarget(
        original_call_id="original-call",
        initialized=CallSubjectHead(
            subject_id="original-call",
            revision=Present(head="original-call/head", fingerprint="a" * 64),
        ),
        current_run=CallSubjectHead(
            subject_id="current-run",
            revision=Present(head="current-run/head", fingerprint="b" * 64),
        ),
    )
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
            "tool_policy": head("tool-policy"),
            "sealed_arguments": b"original arguments",
        },
        "channel_class": "hermetic",
        "recipient": {
            "provider": "hermetic-effects",
            "account": "account",
            "recipient": "principal",
            "endpoint": head("endpoint"),
            "canonical_address": b"hermetic://self",
            "credential_binding": head("credential"),
        },
        "payload": b"\xff\x00payload",
        "effect_fingerprint": hashlib.sha256(b"\xff\x00payload").hexdigest(),
        "bundle_members": (head("member"),),
        "idempotency_fence_key": "one-effect",
        "horizon": {
            "clock_contract": "clock.v1",
            "clock_epoch": "epoch",
            "not_before_ns": 1,
            "expires_at_ns": 20,
            "continuity_policy": head("continuity"),
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
        value[name] = head(name)
    for name in (
        "authority_sources",
        "affected_party_constraints",
        "dependencies",
        "factual_assertion_evidence",
        "verification_contradiction",
        "authority_applicability",
    ):
        value[name] = (head(name),)
    return target, DispatchMandateV2.model_validate(value)
