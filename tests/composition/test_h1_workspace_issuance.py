"""Retained H1 workspace inventory rejects incomplete physical cuts."""

import base64
import json
from pathlib import Path

import pytest

from chiplog.adapters.driven.r9_fence import CONVERSATION_OWNER
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.capabilities.projections.r9_boundary import WorkspaceRejected
from chiplog.composition.r13_workspace import R13Workspace
from chiplog.composition.r14_execution_runtime import open_execution_runtime
from chiplog.composition.r14_h1_workspace_issuance import (
    H1WorkspaceIssuanceJournal,
    _validate_dashboard,
    _validate_retained_original,
    _validate_snapshot,
    _verify_loaded_h1_original_workspace,
    verify_h1_original_workspace,
)
from chiplog.composition.r14_h1_workspace_issuance_contracts import (
    H1OriginalWorkspaceIssuanceV1,
    H1PublicationRowV1,
    H1RecordRowV1,
    H1RetainedDecisionV1,
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


async def test_h1_original_workspace_reopens_exact_native_issuance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "execution.sqlite"
    async with open_execution_runtime(database) as runtime:
        created = await runtime.create_execution("hermetic-ingress", "run", "Plan", BudgetPolicy())
        started = await runtime.begin_execution("hermetic-ingress", "run", created.head)
        workspace = R13Workspace(runtime)
        member = await workspace.context(started)
        ref = workspace._last_h1_workspace_issuance
        assert ref is not None
        decoded: list[bytes] = []
        original_decode = H1WorkspaceIssuanceJournal._decode

        def count_decode(payload: bytes):
            decoded.append(payload)
            return original_decode(payload)

        monkeypatch.setattr(H1WorkspaceIssuanceJournal, "_decode", staticmethod(count_decode))
        journal = workspace.open_h1_workspace_issuance()
        decoded.clear()
        issued = journal.load(ref)
        assert len(decoded) == 1
        raw_entries = journal._journal.entries()
        monkeypatch.setattr(
            journal._journal,
            "entries",
            lambda: tuple(entry for entry in raw_entries),
        )
        decoded.clear()
        journal.load(ref)
        assert len(decoded) == len(raw_entries)
        loaded_closure = _verify_loaded_h1_original_workspace(
            ref,
            member.model_dump_json().encode(),
            member.content.encode(),
            issued,
            workspace.open_dashboard_issuance(),
        )
        assert loaded_closure.proposal_context_bytes == member.content.encode()
        with pytest.raises(WorkspaceRejected, match="reference differs"):
            _verify_loaded_h1_original_workspace(
                ref.model_copy(update={"payload_digest": "0" * 64}),
                member.model_dump_json().encode(),
                member.content.encode(),
                issued,
                workspace.open_dashboard_issuance(),
            )
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
        conversation_ids = {
            row.record_id for row in issued.snapshot.records if row.owner == CONVERSATION_OWNER
        }
        assert conversation_ids
        assert len(issued.sources.selected_loop_decisions) == len(conversation_ids)
        wide_decisions = tuple(
            H1RetainedDecisionV1(
                entry_id=entry_id,
                predecessor=predecessor,
                payload_base64=base64.b64encode(payload).decode("ascii"),
            )
            for entry_id, predecessor, payload in runtime._loop_decisions().entries()
            if json.loads(payload).get("kind") == "DECIDED"
        )
        _validate_retained_original(
            issued.model_copy(
                update={
                    "sources": issued.sources.model_copy(
                        update={"selected_loop_decisions": wide_decisions}
                    )
                }
            )
        )
        with pytest.raises(WorkspaceRejected, match="decision proof differs"):
            _validate_retained_original(
                issued.model_copy(
                    update={
                        "sources": issued.sources.model_copy(
                            update={"selected_loop_decisions": ()}
                        )
                    }
                )
            )
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
