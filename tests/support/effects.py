"""Immutable effects fixture builders shared by algebra and owner-flow scenarios."""

import hashlib

from chiplog.capabilities.effects.contracts import (
    AuthorityBinding,
    AuthorityRead,
    DirectAuthorityAct,
    DispatchSemanticBinding,
    ExactHead,
    ExternalActionIntent,
    OrdinaryPurpose,
    ProviderRecipient,
    TransmissionAttempt,
)


def head(value: str) -> ExactHead:
    return ExactHead(subject_id=value, head=value + "-head", fingerprint=value + "-digest")


def semantics() -> DispatchSemanticBinding:
    return DispatchSemanticBinding(
        normative_manifest="VISION-dispatch",
        reducer_version="1",
        transition_registry_version="1",
        canonicalization_fingerprint_version="1",
        adapter_contract_version="fake-1",
    )


def child(ordinal: int) -> TransmissionAttempt:
    return TransmissionAttempt(
        transmission=head(f"child-{ordinal}"),
        intent=head("intent"),
        ordinal=ordinal,
        semantics=semantics(),
        dispatch_time_ns=1,
        payload_fingerprint="payload",
        recipient=ProviderRecipient(
            provider="fake",
            account="account",
            recipient="principal",
            endpoint=head("endpoint"),
            canonical_address=b"local",
            credential_binding=head("credential"),
        ),
        idempotency_fence_key="key",
        send_commit=head(f"send-{ordinal}"),
        coverage_proof=None,
    )


def intent() -> ExternalActionIntent:
    reference = head("authority")
    authority = AuthorityBinding(
        tenant_id="tenant",
        principal_id="principal",
        actor_id="principal",
        authenticated_session=reference,
        act=DirectAuthorityAct(kind="DIRECT_AUTHENTICATED_ACT", act=reference, ingress=reference),
        planning_revision=reference,
        authorization_evidence=reference,
        authority_sources=(reference,),
        affected_party_constraints=(reference,),
        hold_conflict_order=reference,
        dependencies=(reference,),
        factual_assertion_evidence=(reference,),
        verification_contradiction=(reference,),
        authority_applicability=(reference,),
        consequence_scope=reference,
        communication_mandate=reference,
        disclosure_projection=reference,
        channel_class="hermetic",
        interaction_context=reference,
        recipient=child(0).recipient,
        reads=(
            AuthorityRead(
                source_id="authority",
                source_version="1",
                head=reference,
                generation="gen",
                frontier="frontier",
                valid_until_ns=100,
                canonical_value=b"authority",
            ),
        ),
        registry_inputs=(("registry", "1"),),
        valid_until_ns=100,
    )
    return ExternalActionIntent(
        intent_id="intent",
        fingerprint="intent-digest",
        authority=authority,
        semantics=semantics(),
        payload=b"payload",
        effect_fingerprint=hashlib.sha256(b"payload").hexdigest(),
        idempotency_fence_key="key",
        inseparable_bundle_members=(head("member"),),
        purpose=OrdinaryPurpose(kind="ORDINARY_EFFECT"),
    )
