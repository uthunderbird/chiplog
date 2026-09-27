"""Strict retained V2 original-workspace evidence, without V1 widening."""

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
from chiplog.composition.h1_workspace_policy_v2 import (
    H1OriginalWorkspaceIssuanceV2,
    H1PreissuanceRegistrationV1,
    H1WorkspacePolicyHeadsV1,
    H1WorkspacePolicyV2,
    H1WorkspaceSourcesV2,
    decode_h1_original_workspace_issuance_v2,
)
from chiplog.composition.r14_h1_workspace_issuance_contracts import (
    H1CalendarOriginalReadV1,
    H1DashboardIssuanceRefV1,
    H1OriginalWorkspaceIssuanceV1,
    H1PublicationRowV1,
    H1QueryProofV1,
    H1RecordRowV1,
    H1WorkspaceIssuanceRefV1,
    H1WorkspaceSnapshotV1,
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


def _issuance() -> tuple[H1OriginalWorkspaceIssuanceV2, H1WorkspaceIssuanceRefV1]:
    policy = _policy()
    policy_bytes = policy.canonical_bytes()
    policy_digest = _digest(policy_bytes)
    record_id = (
        "workspace-policy:"
        + _digest(json.dumps([policy.tenant, policy.principal, policy.channel]).encode())
        + ":"
        + policy_digest
    )
    record = H1RecordRowV1(
        tenant_id="tenant",
        record_id=record_id,
        owner="workspace_policy",
        schema_id="chiplog.workspace.policy.v2",
        canonical_bytes_base64=base64.b64encode(policy_bytes).decode("ascii"),
        commit_sequence=1,
    )
    snapshot = H1WorkspaceSnapshotV1(
        tenant_head=1,
        deletion_generation="generation",
        deletion_frontier=0,
        publications=(
            H1PublicationRowV1(
                tenant_id="tenant",
                operation_kind="workspace.policy.h1.v2",
                idempotency_key=record_id,
                request_fingerprint=policy_digest,
                commit_sequence=1,
                record_ids_json=record_id,
            ),
        ),
        records=(record,),
        release_fingerprint="b" * 64,
    )
    sources = H1WorkspaceSourcesV2(
        trusted_ingress_json="{}",
        conversation_bindings_json=(),
        selected_loop_decisions=(),
        planning_sources=(),
        policy_record_id=record_id,
        policy_payload_base64=base64.b64encode(policy_bytes).decode("ascii"),
        endpoint="endpoint",
        policy_owner="workspace_policy",
        policy_schema_id="chiplog.workspace.policy.v2",
        policy_payload_digest=_digest(policy_bytes),
    )
    issuance = H1OriginalWorkspaceIssuanceV2(
        tenant="tenant",
        run_id="run",
        started_run_head="run-head",
        turn_id="turn",
        worker_session="worker",
        workspace_member_json="{}",
        proposal_context_json=json.dumps({"batch": {"batch_id": "batch"}}),
        snapshot=snapshot,
        queries=(
            H1QueryProofV1(
                slot="history",
                reader_id="conversation.context_read.v1",
                request_json="{}",
                result_json="{}",
            ),
            H1QueryProofV1(
                slot="planning",
                reader_id="planning.workspace.read.v1",
                request_json="{}",
                result_json="{}",
            ),
            H1QueryProofV1(
                slot="journal",
                reader_id="journal.workspace.read.v1",
                request_json="{}",
                result_json="{}",
            ),
            H1QueryProofV1(
                slot="calendar",
                reader_id="calendar.workspace.read.v1",
                request_json="{}",
                result_json="{}",
            ),
        ),
        calendar=H1CalendarOriginalReadV1(
            provider_batch_json="{}",
            state_json="{}",
            operation_json="{}",
            result_json="{}",
            operation_fingerprint="c" * 64,
            recipient_fingerprint="d" * 64,
            proof_fingerprint="e" * 64,
            result_digest="f" * 64,
            cursor_token=None,
            cursor_snapshot_id=None,
            cursor_query_binding_base64=None,
            cursor_last_order_key=None,
            display_result_json="{}",
        ),
        sources=sources,
        dashboard=H1DashboardIssuanceRefV1(
            channel_id="channel", sequence=1, entry_id="1" * 64, payload_digest="0" * 64
        ),
    )
    ref = H1WorkspaceIssuanceRefV1(
        tenant="tenant",
        batch_id="batch",
        entry_id="1" * 64,
        payload_digest=_digest(issuance.canonical_bytes()),
    )
    return issuance, ref


def test_v2_original_issuance_reopens_exact_policy_physical_member() -> None:
    issuance, ref = _issuance()
    assert decode_h1_original_workspace_issuance_v2(issuance.canonical_bytes(), ref) == issuance


@pytest.mark.parametrize(
    "field, value, message",
    (("owner", "wrong", "policy owner"), ("schema_id", "wrong", "policy schema")),
)
def test_v2_original_issuance_rejects_wrong_policy_physical_identity(
    field: str, value: str, message: str
) -> None:
    issuance, ref = _issuance()
    row = issuance.snapshot.records[0].model_copy(update={field: value})
    changed = issuance.model_copy(
        update={"snapshot": issuance.snapshot.model_copy(update={"records": (row,)})}
    )
    changed_ref = ref.model_copy(update={"payload_digest": _digest(changed.canonical_bytes())})
    with pytest.raises(ValueError, match=message):
        decode_h1_original_workspace_issuance_v2(changed.canonical_bytes(), changed_ref)


def test_v2_original_issuance_rejects_wrong_policy_digest() -> None:
    issuance, ref = _issuance()
    changed = issuance.model_copy(
        update={"sources": issuance.sources.model_copy(update={"policy_payload_digest": "0" * 64})}
    )
    changed_ref = ref.model_copy(update={"payload_digest": _digest(changed.canonical_bytes())})
    with pytest.raises(ValueError, match="policy digest"):
        decode_h1_original_workspace_issuance_v2(changed.canonical_bytes(), changed_ref)


def test_v2_original_issuance_rejects_policy_member_substitution_and_v1_bytes() -> None:
    issuance, ref = _issuance()
    substituted = issuance.model_copy(
        update={
            "snapshot": issuance.snapshot.model_copy(
                update={"tenant_head": 0, "publications": (), "records": ()}
            )
        }
    )
    substituted_ref = ref.model_copy(
        update={"payload_digest": _digest(substituted.canonical_bytes())}
    )
    with pytest.raises(ValueError, match="policy member"):
        decode_h1_original_workspace_issuance_v2(substituted.canonical_bytes(), substituted_ref)
    v1 = H1OriginalWorkspaceIssuanceV1.model_construct(tenant="tenant")
    with pytest.raises(ValueError, match="schema"):
        decode_h1_original_workspace_issuance_v2(v1.canonical_bytes(), ref)
