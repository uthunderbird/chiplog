"""V2 original evidence binds its policy row to the writer's one-record publication."""

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


def _policy() -> H1WorkspacePolicyV2:
    selected = SelectedHermeticResourceObservationRefV1(
        signature_domain="dispatch-resources.v1",
        selected_initialization=_head("initialization"),
        signed_observation_fingerprint="b" * 64,
    )
    accepted = HermeticOutputPolicyV1(
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
    accepted_raw = accepted.canonical_bytes()
    accepted_digest = _digest(accepted_raw)
    registration = H1PreissuanceRegistrationV1(
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
            head="h1-disclosure-policy/" + accepted_digest,
            fingerprint=accepted_digest,
        ),
        accepted_policy_bytes_base64=base64.b64encode(accepted_raw).decode("ascii"),
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
    )
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
        registration=registration,
    )


def _ref(issuance: H1OriginalWorkspaceIssuanceV2) -> H1WorkspaceIssuanceRefV1:
    return H1WorkspaceIssuanceRefV1(
        tenant=issuance.tenant,
        batch_id="batch",
        entry_id="1" * 64,
        payload_digest=_digest(issuance.canonical_bytes()),
    )


def _queries() -> tuple[H1QueryProofV1, ...]:
    return (
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
    )


def _issuance() -> H1OriginalWorkspaceIssuanceV2:
    policy = _policy()
    raw = policy.canonical_bytes()
    digest = _digest(raw)
    record_id = (
        "workspace-policy:"
        + _digest(json.dumps([policy.tenant, policy.principal, policy.channel]).encode())
        + ":"
        + digest
    )
    record = H1RecordRowV1(
        tenant_id="tenant",
        record_id=record_id,
        owner="workspace_policy",
        schema_id="chiplog.workspace.policy.v2",
        canonical_bytes_base64=base64.b64encode(raw).decode("ascii"),
        commit_sequence=1,
    )
    return H1OriginalWorkspaceIssuanceV2(
        tenant="tenant",
        run_id="run",
        started_run_head="run-head",
        turn_id="turn",
        worker_session="worker",
        workspace_member_json="{}",
        proposal_context_json=json.dumps({"batch": {"batch_id": "batch"}}),
        snapshot=H1WorkspaceSnapshotV1(
            tenant_head=1,
            deletion_generation="generation",
            deletion_frontier=0,
            publications=(
                H1PublicationRowV1(
                    tenant_id="tenant",
                    operation_kind="workspace.policy.h1.v2",
                    idempotency_key=record_id,
                    request_fingerprint=digest,
                    commit_sequence=1,
                    record_ids_json=record_id,
                ),
            ),
            records=(record,),
            release_fingerprint="e" * 64,
        ),
        queries=_queries(),
        calendar=H1CalendarOriginalReadV1(
            provider_batch_json="{}",
            state_json="{}",
            operation_json="{}",
            result_json="{}",
            operation_fingerprint="f" * 64,
            recipient_fingerprint="0" * 64,
            proof_fingerprint="1" * 64,
            result_digest="2" * 64,
            cursor_token=None,
            cursor_snapshot_id=None,
            cursor_query_binding_base64=None,
            cursor_last_order_key=None,
            display_result_json="{}",
        ),
        sources=H1WorkspaceSourcesV2(
            trusted_ingress_json="{}",
            conversation_bindings_json=(),
            selected_loop_decisions=(),
            planning_sources=(),
            policy_record_id=record_id,
            policy_payload_base64=base64.b64encode(raw).decode("ascii"),
            endpoint="endpoint",
            policy_owner="workspace_policy",
            policy_schema_id="chiplog.workspace.policy.v2",
            policy_payload_digest=digest,
        ),
        dashboard=H1DashboardIssuanceRefV1(
            channel_id="channel", sequence=1, entry_id="3" * 64, payload_digest="4" * 64
        ),
    )


def test_v2_original_accepts_the_writer_publication_shape() -> None:
    issuance = _issuance()
    assert (
        decode_h1_original_workspace_issuance_v2(issuance.canonical_bytes(), _ref(issuance))
        == issuance
    )


@pytest.mark.parametrize(
    "mutation",
    ("wrong_operation", "extra_member", "trailing_separator", "duplicate", "sequence", "identity"),
)
def test_v2_original_rejects_rehashed_policy_publication_shape_mutants(mutation: str) -> None:
    issuance = _issuance()
    publication = issuance.snapshot.publications[0]
    record = issuance.snapshot.records[0]
    if mutation == "wrong_operation":
        snapshot = issuance.snapshot.model_copy(
            update={
                "publications": (
                    publication.model_copy(update={"operation_kind": "workspace.policy"}),
                )
            }
        )
    elif mutation == "extra_member":
        extra = H1RecordRowV1(
            tenant_id="tenant",
            record_id="extra",
            owner="other",
            schema_id="chiplog.other.v1",
            canonical_bytes_base64=base64.b64encode(b"extra").decode("ascii"),
            commit_sequence=1,
        )
        snapshot = issuance.snapshot.model_copy(
            update={
                "publications": (
                    publication.model_copy(
                        update={"record_ids_json": record.record_id + "\nextra"}
                    ),
                ),
                "records": (record, extra),
            }
        )
    elif mutation == "trailing_separator":
        snapshot = issuance.snapshot.model_copy(
            update={
                "publications": (
                    publication.model_copy(update={"record_ids_json": record.record_id + "\n"}),
                )
            }
        )
    elif mutation == "duplicate":
        duplicate = publication.model_copy(update={"commit_sequence": 2})
        snapshot = issuance.snapshot.model_copy(
            update={"tenant_head": 2, "publications": (publication, duplicate)}
        )
    elif mutation == "sequence":
        snapshot = issuance.snapshot.model_copy(
            update={"records": (record.model_copy(update={"commit_sequence": 2}),)}
        )
    else:
        forged_id = "workspace-policy:forged"
        snapshot = issuance.snapshot.model_copy(
            update={
                "publications": (
                    publication.model_copy(
                        update={"idempotency_key": forged_id, "record_ids_json": forged_id}
                    ),
                ),
                "records": (record.model_copy(update={"record_id": forged_id}),),
            }
        )
        issuance = issuance.model_copy(
            update={"sources": issuance.sources.model_copy(update={"policy_record_id": forged_id})}
        )
    changed = issuance.model_copy(update={"snapshot": snapshot})
    with pytest.raises(ValueError):
        decode_h1_original_workspace_issuance_v2(changed.canonical_bytes(), _ref(changed))
