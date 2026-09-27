"""Physical V2 workspace-policy selection stays separate from legacy H1 V1."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import cast

import pytest

from chiplog.adapters.driven.journal_sqlite import SQLiteJournal
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticOutputPolicyV1,
    HermeticOutputScopeAnchorV1,
    SelectedHermeticResourceObservationRefV1,
)
from chiplog.capabilities.evidence_journal.commands import Heads, TrustedIngress
from chiplog.composition.h1_workspace_policy_v2 import (
    H1OriginalWorkspaceIssuanceV2,
    H1PreissuanceRegistrationV1,
    H1WorkspacePolicyHeadsV1,
    H1WorkspacePolicyV2,
    H1WorkspaceSourcesV2,
    decode_h1_original_workspace_issuance_v2,
)
from chiplog.composition.r12 import install_h1_workspace_policy_v2
from chiplog.composition.r14_h1_workspace_issuance import H1WorkspaceIssuanceJournal
from chiplog.composition.r14_h1_workspace_issuance_contracts import (
    H1CalendarOriginalReadV1,
    H1DashboardIssuanceRefV1,
    H1PublicationRowV1,
    H1QueryProofV1,
    H1RecordRowV1,
    H1WorkspaceIssuanceRefV1,
    H1WorkspaceSnapshotV1,
)
from chiplog.platform._sqlite import EventAppender, PhysicalPublicationCommand, PublicationResult
from chiplog.platform.authority_gate import AuthorityGate


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


def _v2() -> tuple[H1OriginalWorkspaceIssuanceV2, H1WorkspaceIssuanceRefV1]:
    policy = _policy()
    raw = policy.canonical_bytes()
    record_id = (
        "workspace-policy:"
        + _digest(json.dumps([policy.tenant, policy.principal, policy.channel]).encode())
        + ":"
        + _digest(raw)
    )
    record = H1RecordRowV1(
        tenant_id="tenant",
        record_id=record_id,
        owner="workspace_policy",
        schema_id="chiplog.workspace.policy.v2",
        canonical_bytes_base64=base64.b64encode(raw).decode(),
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
                idempotency_key=record.record_id,
                request_fingerprint=_digest(raw),
                commit_sequence=1,
                record_ids_json=record.record_id,
            ),
        ),
        records=(record,),
        release_fingerprint="a" * 64,
    )
    sources = H1WorkspaceSourcesV2(
        trusted_ingress_json="{}",
        conversation_bindings_json=(),
        selected_loop_decisions=(),
        planning_sources=(),
        policy_record_id=record.record_id,
        policy_payload_base64=base64.b64encode(raw).decode(),
        endpoint="endpoint",
        policy_owner="workspace_policy",
        policy_schema_id="chiplog.workspace.policy.v2",
        policy_payload_digest=_digest(raw),
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
            operation_fingerprint="b" * 64,
            recipient_fingerprint="c" * 64,
            proof_fingerprint="d" * 64,
            result_digest="e" * 64,
            cursor_token=None,
            cursor_snapshot_id=None,
            cursor_query_binding_base64=None,
            cursor_last_order_key=None,
            display_result_json="{}",
        ),
        sources=sources,
        dashboard=H1DashboardIssuanceRefV1(
            channel_id="channel", sequence=1, entry_id="f" * 64, payload_digest="0" * 64
        ),
    )
    return issuance, H1WorkspaceIssuanceRefV1(
        tenant="tenant",
        batch_id="batch",
        entry_id="1" * 64,
        payload_digest=_digest(issuance.canonical_bytes()),
    )


def test_v2_requires_exact_selected_physical_member_before_decode() -> None:
    issuance, ref = _v2()
    assert decode_h1_original_workspace_issuance_v2(issuance.canonical_bytes(), ref) == issuance
    changed = issuance.model_copy(
        update={
            "snapshot": issuance.snapshot.model_copy(
                update={
                    "records": (
                        issuance.snapshot.records[0].model_copy(
                            update={"canonical_bytes_base64": base64.b64encode(b"forged").decode()}
                        ),
                    )
                }
            )
        }
    )
    forged_ref = ref.model_copy(update={"payload_digest": _digest(changed.canonical_bytes())})
    with pytest.raises(ValueError, match="policy member bytes"):
        decode_h1_original_workspace_issuance_v2(changed.canonical_bytes(), forged_ref)


def test_h1_journal_dispatches_v2_and_rejects_unknown_schema() -> None:
    issuance, _ = _v2()
    # This deliberately uses the journal decoder only: the actual materializer
    # V2 schema admission is broker-owned and covered by its explicit RED seam.
    assert H1WorkspaceIssuanceJournal._decode(issuance.canonical_bytes()) == issuance
    with pytest.raises(ValueError, match="schema"):
        H1WorkspaceIssuanceJournal._decode(b'{"schema_id":"forged"}')


def test_v2_original_issuance_reopens_same_canonical_bytes_after_restart(tmp_path: Path) -> None:
    issuance, _ = _v2()
    database = tmp_path / "authority.sqlite"
    database.touch()
    path = tmp_path / "h1-workspace-issuance"
    first = H1WorkspaceIssuanceJournal.open(path, AuthorityGate(database), "tenant")
    reference = first.append(issuance)

    reopened = H1WorkspaceIssuanceJournal.open(path, AuthorityGate(database), "tenant")
    loaded = reopened.load(reference)
    assert type(loaded) is H1OriginalWorkspaceIssuanceV2
    assert loaded.canonical_bytes() == issuance.canonical_bytes()


async def test_v2_install_selects_one_exact_physical_policy_member() -> None:
    policy = _policy()

    class Storage:
        def snapshot(self, tenant: str) -> object:
            assert tenant == "tenant"
            return type("Snapshot", (), {"head": 0})()

    class Appender:
        command: object | None = None

        async def submit(self, command: object) -> PublicationResult:
            self.command = command
            physical = cast(PhysicalPublicationCommand, command)
            return PublicationResult("COMMITTED", 1, (physical.records[0].record_id,))

    identity = TrustedIngress(
        tenant="tenant",
        principal="principal",
        heads=Heads(
            journal=0,
            policy="policy",
            credential="credential",
            session="session",
            contour="contour",
            deletion="deletion",
        ),
        items=(),
        sources=(),
        endpoint="endpoint",
    )
    appender = Appender()
    await install_h1_workspace_policy_v2(
        cast(EventAppender, appender),
        cast(SQLiteJournal, Storage()),
        identity,
        "origin-channel",
        "database",
        0,
        policy,
    )
    command = appender.command
    assert command is not None
    record = cast(PhysicalPublicationCommand, command).records[0]
    assert (record.owner, record.schema_id) == ("workspace_policy", "chiplog.workspace.policy.v2")
    assert record.canonical_bytes == policy.canonical_bytes()
    assert record.fingerprint == _digest(policy.canonical_bytes())
    assert record.record_id.endswith(record.fingerprint)
