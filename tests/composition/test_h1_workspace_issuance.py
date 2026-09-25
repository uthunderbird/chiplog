"""Retained H1 workspace inventory rejects incomplete physical cuts."""

import base64
import json
from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.capabilities.projections.r9_boundary import WorkspaceRejected
from chiplog.composition.r13_workspace import R13Workspace
from chiplog.composition.r14_execution_runtime import open_execution_runtime
from chiplog.composition.r14_h1_workspace_issuance import (
    _validate_dashboard,
    _validate_retained_original,
    _validate_snapshot,
    verify_h1_original_workspace,
)
from chiplog.composition.r14_h1_workspace_issuance_contracts import (
    H1OriginalWorkspaceIssuanceV1,
    H1PublicationRowV1,
    H1RecordRowV1,
    H1WorkspaceSnapshotV1,
)


def _issuance(snapshot: H1WorkspaceSnapshotV1) -> H1OriginalWorkspaceIssuanceV1:
    return H1OriginalWorkspaceIssuanceV1.model_construct(tenant="tenant", snapshot=snapshot)


def _record(record_id: str, sequence: int = 1) -> H1RecordRowV1:
    return H1RecordRowV1(
        tenant_id="tenant",
        record_id=record_id,
        owner="owner",
        schema_id="schema",
        canonical_bytes_base64=base64.b64encode(b"bytes").decode(),
        commit_sequence=sequence,
    )


def test_h1_snapshot_requires_every_record_to_belong_to_original_publication() -> None:
    snapshot = H1WorkspaceSnapshotV1(
        tenant_head=1,
        deletion_generation="generation",
        deletion_frontier=0,
        publications=(
            H1PublicationRowV1(
                tenant_id="tenant",
                operation_kind="operation",
                idempotency_key="key",
                request_fingerprint="0" * 64,
                commit_sequence=1,
                record_ids_json="published",
            ),
        ),
        records=(_record("published"), _record("hidden")),
        release_fingerprint="0" * 64,
    )

    with pytest.raises(WorkspaceRejected, match="unpublished"):
        _validate_snapshot(_issuance(snapshot))


def test_h1_snapshot_requires_contiguous_original_publication_frontier() -> None:
    snapshot = H1WorkspaceSnapshotV1(
        tenant_head=2,
        deletion_generation="generation",
        deletion_frontier=0,
        publications=(
            H1PublicationRowV1(
                tenant_id="tenant",
                operation_kind="operation",
                idempotency_key="key",
                request_fingerprint="0" * 64,
                commit_sequence=2,
                record_ids_json="record",
            ),
        ),
        records=(_record("record", 2),),
        release_fingerprint="0" * 64,
    )

    with pytest.raises(WorkspaceRejected, match="frontier"):
        _validate_snapshot(_issuance(snapshot))


async def test_h1_original_workspace_reopens_exact_native_issuance(tmp_path: Path) -> None:
    database = tmp_path / "execution.sqlite"
    async with open_execution_runtime(database) as runtime:
        created = await runtime.create_execution("hermetic-ingress", "run", "Plan", BudgetPolicy())
        started = await runtime.begin_execution("hermetic-ingress", "run", created.head)
        workspace = R13Workspace(runtime)
        member = await workspace.context(started)
        ref = workspace._last_h1_workspace_issuance
        assert ref is not None
        verified = verify_h1_original_workspace(
            ref,
            member.model_dump_json().encode(),
            member.content.encode(),
            workspace.open_h1_workspace_issuance(),
            workspace.open_dashboard_issuance(),
        )
        assert verified.proposal_context_bytes == member.content.encode()

        sorted_proposal = json.dumps(
            json.loads(member.content), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
        assert sorted_proposal != member.content.encode()
        with pytest.raises(WorkspaceRejected, match="selected workspace bytes"):
            verify_h1_original_workspace(
                ref,
                member.model_dump_json().encode(),
                sorted_proposal,
                workspace.open_h1_workspace_issuance(),
                workspace.open_dashboard_issuance(),
            )

        substituted_member = member.model_dump_json().encode() + b" "
        with pytest.raises(WorkspaceRejected, match="selected workspace bytes"):
            verify_h1_original_workspace(
                ref,
                substituted_member,
                member.content.encode(),
                workspace.open_h1_workspace_issuance(),
                workspace.open_dashboard_issuance(),
            )

        issued = workspace.open_h1_workspace_issuance().load(ref)
        changed_query = issued.queries[0].model_copy(update={"result_json": "{}"})
        with pytest.raises(WorkspaceRejected, match="query result"):
            _validate_retained_original(
                issued.model_copy(update={"queries": (changed_query, *issued.queries[1:])})
            )
        with pytest.raises(WorkspaceRejected, match="policy record"):
            _validate_retained_original(
                issued.model_copy(
                    update={
                        "sources": issued.sources.model_copy(
                            update={"policy_payload_base64": base64.b64encode(b"forged").decode()}
                        )
                    }
                )
            )
        with pytest.raises(WorkspaceRejected, match="dashboard issuance"):
            _validate_dashboard(
                issued.model_copy(
                    update={
                        "dashboard": issued.dashboard.model_copy(
                            update={"payload_digest": "0" * 64}
                        )
                    }
                ),
                workspace.open_dashboard_issuance(),
            )
