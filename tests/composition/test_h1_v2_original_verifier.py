"""V2 original-workspace reopening closes the same retained cut as V1."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from chiplog.adapters.driven.workspace_issuance import WorkspaceIssuanceJournal
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticOutputPolicyV1,
    HermeticOutputScopeAnchorV1,
    SelectedHermeticResourceObservationRefV1,
)
from chiplog.capabilities.evidence_journal.commands import TrustedIngress
from chiplog.capabilities.projections.r9_boundary import WorkspaceRejected
from chiplog.composition.h1_workspace_policy_v2 import (
    H1OriginalWorkspaceIssuanceV2,
    H1PreissuanceRegistrationV1,
    H1WorkspacePolicyHeadsV1,
    H1WorkspacePolicyV2,
    H1WorkspaceSourcesV2,
)
from chiplog.composition.r13_workspace import R13Workspace
from chiplog.composition.r14_execution_runtime import open_execution_runtime
from chiplog.composition.r14_h1_workspace_issuance import (
    H1WorkspaceIssuanceJournal,
    verify_h1_original_workspace,
)
from chiplog.composition.r14_h1_workspace_issuance_contracts import (
    H1PublicationRowV1,
    H1RecordRowV1,
    H1WorkspaceIssuanceRefV1,
)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _head(identity: str) -> ExactHead:
    return ExactHead(identity=identity, head=identity + "/head", fingerprint="a" * 64)


def _policy(tenant: str, principal: str, channel: str, endpoint: str) -> H1WorkspacePolicyV2:
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
    registration = H1PreissuanceRegistrationV1(
        deployment_id="deployment",
        database_id="database",
        database_genesis_digest="c" * 64,
        tenant_id=tenant,
        principal_id=principal,
        channel_id=channel,
        registration_id="registration",
        generation=0,
        custody_entry_digest="d" * 64,
        origin_recipient_id="recipient",
        conversation_id="conversation",
        visible_channels=(channel,),
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
    )
    return H1WorkspacePolicyV2(
        tenant=tenant,
        principal=principal,
        channel=channel,
        database="database",
        endpoint=endpoint,
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


async def _issued_v2(
    tmp_path: Path,
) -> AsyncIterator[
    tuple[
        H1OriginalWorkspaceIssuanceV2,
        H1WorkspaceIssuanceRefV1,
        H1WorkspaceIssuanceJournal,
        WorkspaceIssuanceJournal,
    ]
]:
    async with open_execution_runtime(tmp_path / "execution.sqlite") as runtime:
        created = await runtime.create_execution("hermetic-ingress", "run", "Plan", BudgetPolicy())
        started = await runtime.begin_execution("hermetic-ingress", "run", created.head)
        workspace = R13Workspace(runtime)
        await workspace.context(started)
        original_ref = workspace._last_h1_workspace_issuance
        assert original_ref is not None
        original = workspace.open_h1_workspace_issuance().load(original_ref)
        ingress = TrustedIngress.model_validate_json(original.sources.trusted_ingress_json)
        policy = _policy(original.tenant, ingress.principal, ingress.endpoint, ingress.endpoint)
        raw = policy.canonical_bytes()
        policy_record_id = (
            "workspace-policy:"
            + _digest(json.dumps([policy.tenant, policy.principal, policy.channel]).encode())
            + ":"
            + _digest(raw)
        )
        policy_row = H1RecordRowV1(
            tenant_id=original.tenant,
            record_id=policy_record_id,
            owner="workspace_policy",
            schema_id="chiplog.workspace.policy.v2",
            canonical_bytes_base64=base64.b64encode(raw).decode(),
            commit_sequence=original.snapshot.tenant_head + 1,
        )
        snapshot = original.snapshot.model_copy(
            update={
                "tenant_head": original.snapshot.tenant_head + 1,
                "publications": (
                    *original.snapshot.publications,
                    H1PublicationRowV1(
                        tenant_id=original.tenant,
                        operation_kind="workspace.policy.h1.v2",
                        idempotency_key=policy_record_id,
                        request_fingerprint=_digest(raw),
                        commit_sequence=original.snapshot.tenant_head + 1,
                        record_ids_json=policy_row.record_id,
                    ),
                ),
                "records": (*original.snapshot.records, policy_row),
            }
        )
        source_values = original.sources.model_dump()
        source_values.update(
            policy_record_id=policy_row.record_id,
            policy_payload_base64=base64.b64encode(raw).decode(),
            endpoint=ingress.endpoint,
            policy_owner="workspace_policy",
            policy_schema_id="chiplog.workspace.policy.v2",
            policy_payload_digest=_digest(raw),
        )
        sources = H1WorkspaceSourcesV2(**source_values)
        issued = H1OriginalWorkspaceIssuanceV2(
            **original.model_dump(exclude={"schema_id", "sources", "snapshot"}),
            snapshot=snapshot,
            sources=sources,
        )
        journal = H1WorkspaceIssuanceJournal.open(
            tmp_path / "v2-issuance", runtime._authority_gate(), original.tenant
        )
        ref = journal.append(issued)
        yield issued, ref, journal, workspace.open_dashboard_issuance()


@pytest.mark.asyncio
async def test_v2_verifier_reopens_physical_policy_and_v1_closure(
    tmp_path: Path,
) -> None:
    async for issued, ref, journal, dashboard in _issued_v2(tmp_path):
        verified = verify_h1_original_workspace(
            ref,
            issued.workspace_member_json.encode(),
            issued.proposal_context_json.encode(),
            journal,
            dashboard,
        )
        assert verified.sources.policy_payload_base64 == issued.sources.policy_payload_base64

        for ordinal, changed in enumerate(
            (
                issued.model_copy(
                    update={
                        "sources": issued.sources.model_copy(
                            update={"policy_payload_digest": "0" * 64}
                        )
                    }
                ),
                issued.model_copy(
                    update={
                        "queries": (
                            issued.queries[0].model_copy(update={"result_json": "{}"}),
                            *issued.queries[1:],
                        )
                    }
                ),
            )
        ):
            changed_journal = H1WorkspaceIssuanceJournal.open(
                tmp_path / ("v2-mutation-" + str(ordinal)), journal._gate, issued.tenant
            )
            changed_ref = changed_journal.append(changed)
            with pytest.raises(WorkspaceRejected):
                verify_h1_original_workspace(
                    changed_ref,
                    changed.workspace_member_json.encode(),
                    changed.proposal_context_json.encode(),
                    changed_journal,
                    dashboard,
                )
        with pytest.raises(WorkspaceRejected, match="selected workspace bytes"):
            verify_h1_original_workspace(
                ref, b"{}", issued.proposal_context_json.encode(), journal, dashboard
            )
