"""Closed interpretation for adopted executable calls; no resource authority.

Bundle heads identify original opaque labels of one inseparable payload. They
neither resolve external entities nor confer independently usable permissions.
Legacy PlanEffect policy bytes and derivations are deliberately independent.
"""

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallDispatchSemantics,
    CallSubjectHead,
)
from chiplog.capabilities.agent_loop.execution_contracts import SelfEffectArguments
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.effects.contracts import DispatchSemanticBinding, ExactHead
from chiplog.capabilities.effects.dispatch_v2 import canonical, reference

POLICY_ID = "chiplog.effects.hermetic-adopted-call-policy.v1"
CLOCK = "chiplog.dispatch.monotonic.v2"
MAX_PAYLOAD_BYTES = 65536
MAX_BUNDLE_MEMBERS = 32
MAX_LABEL_BYTES = 4096
MAX_HORIZON_NS = 60_000_000_000
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
            "operations": ["ACCEPT_INITIALIZED_CALL", "AUTHORIZE_SEND", "COMMIT_FIRST_SEND"],
            "provider": "hermetic-effects",
            "account": "hermetic-account",
            "recipient": "hermetic-principal",
            "address": "hermetic://effects/hermetic-principal",
            "scope": "OPAQUE_SELF_EMISSION_ONLY",
            "external_factual_assertions": [],
            "external_dependencies": [],
            "real_provider_entitlement": False,
            "maximum_horizon_ns": MAX_HORIZON_NS,
            "maximum_payload_bytes": MAX_PAYLOAD_BYTES,
            "maximum_bundle_members": MAX_BUNDLE_MEMBERS,
            "maximum_label_utf8_bytes": MAX_LABEL_BYTES,
            "bundle_meaning": "ORDERED_OPAQUE_LABELS_OF_SINGLE_INSEPARABLE_PAYLOAD",
            "bundle_member_schema": "chiplog.call.bundle-member.v1",
            "clock": CLOCK,
            "semantics": SEMANTICS.model_dump(mode="json"),
        }
    )


def policy_reference() -> ExactHead:
    return reference(POLICY_ID, policy_bytes())


def bundle_references(
    original_call_id: str, arguments: SelfEffectArguments
) -> tuple[ExactHead, ...]:
    """Preserve all labels, their order and Unicode bytes under original call identity."""
    arguments = SelfEffectArguments.model_validate_json(arguments.model_dump_json())
    labels = arguments.bundle_members
    if (
        not original_call_id
        or len(arguments.payload) > MAX_PAYLOAD_BYTES
        or not 1 <= len(labels) <= MAX_BUNDLE_MEMBERS
        or len(set(labels)) != len(labels)
        or any(not label or len(label.encode("utf-8")) > MAX_LABEL_BYTES for label in labels)
    ):
        raise ValueError("call payload or complete ordered bundle exceeds closed policy")
    return tuple(
        reference(
            original_call_id + "/bundle/" + str(ordinal),
            canonical(
                {
                    "schema_id": "chiplog.call.bundle-member.v1",
                    "original_call_id": original_call_id,
                    "ordinal": ordinal,
                    "label": label,
                }
            ),
        )
        for ordinal, label in enumerate(labels)
    )


def loop_dispatch_semantics() -> CallDispatchSemantics:
    mapping = {
        "normative_manifest": SEMANTICS.normative_manifest,
        "reducer": SEMANTICS.reducer_version,
        "transition_registry": SEMANTICS.transition_registry_version,
        "canonicalization": SEMANTICS.canonicalization_fingerprint_version,
        "adapter_contract": SEMANTICS.adapter_contract_version,
    }
    values: dict[str, CallSubjectHead] = {}
    for name, version in mapping.items():
        head = reference(
            "call-effects:semantic-component:" + name + ":v2",
            canonical(
                {
                    "schema_id": "chiplog.call-effects.semantic-component.v2",
                    "component": name,
                    "version": version,
                }
            ),
        )
        values[name] = CallSubjectHead(
            subject_id=head.subject_id,
            revision=Present(head=head.head, fingerprint=head.fingerprint),
        )
    return CallDispatchSemantics.model_validate(values)
