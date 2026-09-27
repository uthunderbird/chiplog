"""Exact physical V2 workspace-policy inventory leaf contracts."""

from __future__ import annotations

import base64
import hashlib
import json

import pytest

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticOutputPolicyV1,
    HermeticOutputScopeAnchorV1,
    SelectedHermeticResourceObservationRefV1,
)
from chiplog.capabilities.projections.workspace_boundary import (
    DisclosureLabel,
    SourceReference,
)
from chiplog.composition.h1_inventory_leaf_contracts import (
    H1OwnerInventoryFailure,
    H1RawInventoryItem,
)
from chiplog.composition.h1_inventory_workspace_planning import (
    REGISTRATIONS,
    decode_h1_workspace_planning_scope,
)
from chiplog.composition.h1_workspace_policy_v2 import (
    H1PreissuanceRegistrationV1,
    H1WorkspacePolicyHeadsV1,
    H1WorkspacePolicyV2,
)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _head(identity: str) -> ExactHead:
    return ExactHead(identity=identity, head=identity + "/head", fingerprint="a" * 64)


def _source() -> SourceReference:
    return SourceReference(
        tenant_id="tenant",
        owner="ingress",
        record_id="source-1",
        record_version="revision:content",
        content_digest="content",
        label_head="label",
        label=DisclosureLabel(
            lattice_version="chiplog.disclosure.v1",
            value="ENDPOINT_RESTRICTED",
            allowed_endpoints=("origin-channel",),
        ),
    )


def _policy() -> H1WorkspacePolicyV2:
    selected = SelectedHermeticResourceObservationRefV1(
        signature_domain="dispatch-resources.v1",
        selected_initialization=_head("initialization"),
        signed_observation_fingerprint="b" * 64,
    )
    output = HermeticOutputPolicyV1(
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
    output_raw = output.canonical_bytes()
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
        sources=(_source(),),
        registration=H1PreissuanceRegistrationV1(
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
            visible_channels=("origin-channel",),
            accepted_policy_selector="H1_OWNER_ISSUED_ORIGIN_EXACT_V1",
            accepted_policy=ExactHead(
                identity="h1-disclosure-policy",
                head="h1-disclosure-policy/" + _digest(output_raw),
                fingerprint=_digest(output_raw),
            ),
            accepted_policy_bytes_base64=base64.b64encode(output_raw).decode(),
            output_scope_anchor=HermeticOutputScopeAnchorV1(
                owner_id="deployment_trust",
                decision=_head("decision"),
                record_ordinal=0,
                record_type_id="chiplog.deployment_trust.hermetic_output_scope",
                schema_id="chiplog.deployment_trust.record.v1",
                record=_head("scope-record"),
                scope_revision=0,
                predecessor=None,
                selected_resource_observation_ref=selected,
            ),
            output_scope_ref=_head("scope"),
            selected_resource_observation_ref=selected,
            admitted_authentication_ref=_head("authentication"),
        ),
    )


def _item(
    *,
    owner: str = "workspace_policy",
    schema: str = "chiplog.workspace.policy.v2",
    tenant: str = "tenant",
    raw: bytes | None = None,
    record_id: str | None = None,
) -> H1RawInventoryItem:
    payload = _policy().canonical_bytes() if raw is None else raw
    identity = hashlib.sha256(
        json.dumps(["tenant", "principal", "origin-channel"]).encode()
    ).hexdigest()
    return H1RawInventoryItem(
        "PHYSICAL",
        "records/" + (record_id or "v2-policy"),
        tenant,
        owner,
        schema,
        None,
        record_id or "workspace-policy:" + identity + ":" + _digest(payload),
        _digest(payload),
        payload,
    )


def test_v2_policy_registration_and_exact_physical_sources_are_decoded() -> None:
    item = _item()

    decoded = decode_h1_workspace_planning_scope(item)

    assert ("PHYSICAL", "workspace_policy", "chiplog.workspace.policy.v2", None) in {
        (row.surface, row.owner, row.schema, row.record_kind) for row in REGISTRATIONS
    }
    assert {(key.namespace, key.identity) for key in decoded.identities} == {
        ("record", item.record_id),
        ("source", "ingress:source-1"),
    }
    assert decoded.source_refs == (_source(),)
    relation_kinds = {
        (edge.relation, edge.subject.namespace, edge.target.namespace) for edge in decoded.relations
    }
    assert relation_kinds == {("RECORD_SOURCE", "record", "source")}
    assert decoded.families == ("RECORD", "SOURCE")


@pytest.mark.parametrize(
    "item",
    (
        _item(owner="wrong"),
        _item(schema="chiplog.workspace.policy.v1"),
        _item(tenant="other-tenant"),
        _item(
            raw=_policy()
            .model_copy(update={"sources": (_source().model_copy(update={"tenant_id": "other"}),)})
            .canonical_bytes()
        ),
        _item(raw=_policy().canonical_bytes() + b" "),
        _item(record_id="workspace-policy:forged"),
    ),
)
def test_v2_policy_wrong_outer_pair_bytes_tenant_or_id_refuses(item: H1RawInventoryItem) -> None:
    with pytest.raises(H1OwnerInventoryFailure):
        decode_h1_workspace_planning_scope(item)


def test_v1_workspace_planning_leaf_behavior_remains_registered() -> None:
    assert (
        "WORKSPACE_SOURCE",
        "workspace_issuance",
        "chiplog.execution.h1-original-workspace-issuance.v1",
        None,
    ) in {(row.surface, row.owner, row.schema, row.record_kind) for row in REGISTRATIONS}
