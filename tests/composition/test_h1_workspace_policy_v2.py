"""Strict inert H1 V2 workspace-policy evidence."""

from __future__ import annotations

import base64
import hashlib

import pytest

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticOutputPolicyV1,
    HermeticOutputScopeAnchorV1,
    SelectedHermeticResourceObservationRefV1,
)
from chiplog.composition.h1_workspace_policy_v2 import (
    H1ConversationRegistrationV1,
    H1PreissuanceRegistrationV1,
    H1WorkspacePolicyHeadsV1,
    H1WorkspacePolicyV2,
    decode_h1_workspace_policy_v2,
)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _head(identity: str) -> ExactHead:
    return ExactHead(identity=identity, head=identity + "/head", fingerprint="a" * 64)


def _registration() -> H1PreissuanceRegistrationV1:
    selected = SelectedHermeticResourceObservationRefV1(
        signature_domain="dispatch-resources.v1",
        selected_initialization=_head("initialization"),
        signed_observation_fingerprint="b" * 64,
    )
    policy = HermeticOutputPolicyV1(
        endpoint_ref=_head("endpoint"),
        selected_resource_observation_ref=selected,
        selection="ORIGIN_EXACT",
        ingress_class="AUTHENTICATED_R17_CLI",
        payload_class="NonAuthoritativeText",
        purpose="H1_LOCAL_COMMENTARY",
        external_delivery=False,
        attempt_ordinal=0,
        call_count=0,
    )
    policy_bytes = policy.canonical_bytes()
    policy_digest = _digest(policy_bytes)
    policy_head = ExactHead(
        identity="h1-disclosure-policy",
        head="h1-disclosure-policy/" + policy_digest,
        fingerprint=policy_digest,
    )
    anchor = HermeticOutputScopeAnchorV1(
        owner_id="deployment_trust",
        decision=_head("decision"),
        record_ordinal=0,
        record_type_id="chiplog.deployment_trust.hermetic_output_scope",
        schema_id="chiplog.deployment_trust.record.v1",
        record=_head("scope-record"),
        scope_revision=0,
        predecessor=None,
        selected_resource_observation_ref=selected,
    )
    return H1PreissuanceRegistrationV1(
        deployment_id="deployment",
        database_id="database",
        database_genesis_digest="c" * 64,
        tenant_id="tenant",
        principal_id="principal",
        channel_id="origin-channel",
        registration_id="registration",
        generation=0,
        custody_entry_digest="d" * 64,
        origin_recipient_id="recipient",
        conversation_id="conversation",
        visible_channels=("origin-channel", "visible-channel"),
        accepted_policy_selector="H1_OWNER_ISSUED_ORIGIN_EXACT_V1",
        accepted_policy=policy_head,
        accepted_policy_bytes_base64=base64.b64encode(policy_bytes).decode("ascii"),
        output_scope_anchor=anchor,
        output_scope_ref=_head("scope"),
        selected_resource_observation_ref=selected,
        admitted_authentication_ref=_head("authentication"),
    )


def _policy() -> H1WorkspacePolicyV2:
    return H1WorkspacePolicyV2(
        tenant="tenant",
        principal="principal",
        channel="origin-channel",
        database="database",
        endpoint="endpoint",
        heads=H1WorkspacePolicyHeadsV1(
            policy="policy",
            credential="credential",
            session="session",
            contour="contour",
            deletion="deletion",
        ),
        sources=(),
        registration=_registration(),
    )


def test_v2_workspace_policy_is_canonical_and_binds_a_distinct_delivery_policy() -> None:
    policy = _policy()

    assert decode_h1_workspace_policy_v2(policy.canonical_bytes()) == policy
    assert policy.registration.accepted_policy.identity != "workspace-policy"
    assert H1ConversationRegistrationV1.model_fields["accepted_policy"].annotation is ExactHead


@pytest.mark.parametrize(
    "update",
    (
        {"visible_channels": ("visible-channel", "origin-channel")},
        {"visible_channels": ("origin-channel", "origin-channel")},
        {"visible_channels": ("visible-channel",)},
    ),
)
def test_registration_rejects_noncanonical_or_invisible_origin_channel(
    update: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="visible channels"):
        _registration().model_copy(update=update).model_validate(
            _registration().model_copy(update=update).model_dump()
        )


def test_policy_rejects_a_workspace_policy_substituted_as_accepted_policy() -> None:
    registration = _registration().model_copy(
        update={
            "accepted_policy": ExactHead(
                identity="workspace-policy", head="workspace-policy/head", fingerprint="e" * 64
            )
        }
    )
    with pytest.raises(ValueError, match="accepted policy"):
        H1WorkspacePolicyV2.model_validate(
            _policy().model_copy(update={"registration": registration}).model_dump()
        )


def test_policy_decoder_rejects_noncanonical_or_wrong_schema_bytes() -> None:
    policy = _policy()
    with pytest.raises(ValueError, match="canonical"):
        decode_h1_workspace_policy_v2(policy.canonical_bytes() + b" ")
    with pytest.raises(ValueError, match="schema"):
        decode_h1_workspace_policy_v2(
            policy.model_copy(update={"schema_id": "chiplog.workspace.policy.v1"}).canonical_bytes()
        )
