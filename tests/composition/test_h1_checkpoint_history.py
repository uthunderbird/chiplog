"""Immutable row projection used by the H1 V3 historical read cut."""

import base64
import json
from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.common_execution_driver_contracts import SelectedExecutionReceiptV1
from chiplog.composition.h1_first_path_sources import H1FirstPathSources
from chiplog.composition.h1_verified_snapshot_rows import H1VerifiedSnapshotRows
from chiplog.composition.r14_execution_complete_seal_records import (
    RetainedExecutionCompleteSealV3,
)
from chiplog.composition.r14_loop_history import _read_call_history
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.authority_checkpoint import (
    AuthorityCheckpointRefV1,
    AuthoritySnapshotTableV1,
    VerifiedAuthoritySnapshot,
)
from tests.support.h1_cli_execution import admit_complete_script


def _snapshot() -> VerifiedAuthoritySnapshot:
    ref = AuthorityCheckpointRefV1(
        format="authority-preimage-v1",
        commitment_algorithm="authority-json-v1",
        authority_surface_digest="a" * 64,
        blob_sha256="b" * 64,
        byte_length=1,
    )
    raw = base64.b64encode(b"selected").decode()
    return VerifiedAuthoritySnapshot(
        ref=ref,
        preimage=b"x",
        tables=(
            AuthoritySnapshotTableV1(
                table="deletion_fences",
                columns=("tenant_id", "generation", "frontier"),
                rows=(("tenant", "r6", 0),),
            ),
            AuthoritySnapshotTableV1(
                table="evidence_inbox",
                columns=(
                    "tenant_id",
                    "source_id",
                    "evidence_id",
                    "fingerprint",
                    "canonical_bytes",
                    "followup_kind",
                    "state",
                    "attempt_id",
                    "transport_version",
                    "cursor",
                ),
                rows=(
                    (
                        "tenant",
                        "source",
                        "evidence",
                        "f",
                        {"base64": raw},
                        "follow",
                        "NEW",
                        None,
                        None,
                        None,
                    ),
                ),
            ),
            AuthoritySnapshotTableV1(
                table="publications",
                columns=(
                    "tenant_id",
                    "operation_kind",
                    "idempotency_key",
                    "request_fingerprint",
                    "commit_sequence",
                    "record_ids",
                ),
                rows=(("tenant", "agent_loop", "selected", "fingerprint", 3, "record"),),
            ),
            AuthoritySnapshotTableV1(
                table="records",
                columns=(
                    "tenant_id",
                    "record_id",
                    "owner",
                    "schema_id",
                    "canonical_bytes",
                    "commit_sequence",
                ),
                rows=(("tenant", "record", "agent_loop", "schema", {"base64": raw}, 3),),
            ),
            AuthoritySnapshotTableV1(
                table="tenant_heads", columns=("tenant_id", "head"), rows=(("tenant", 3),)
            ),
        ),
    )


def test_verified_rows_keep_selected_bytes_after_live_authority_would_change() -> None:
    rows = H1VerifiedSnapshotRows.from_verified(_snapshot())

    assert rows.commitment == "b" * 64
    assert rows.fence("tenant") == ("r6", 0)
    assert rows.tenant_head("tenant") == 3
    assert rows.publications("tenant") == (("agent_loop", "selected", "fingerprint", 3, "record"),)
    assert rows.records("tenant") == (("record", "agent_loop", "schema", b"selected", 3),)
    assert rows.evidence("tenant") == (
        ("source", "evidence", "f", b"selected", "follow", "NEW", None, None, None),
    )


def test_verified_rows_require_exact_selected_publication_membership() -> None:
    rows = H1VerifiedSnapshotRows.from_verified(_snapshot())
    command = PhysicalPublicationCommand(
        tenant_id="tenant",
        operation_kind="agent_loop",
        idempotency_key="selected",
        request_fingerprint="fingerprint",
        expected_head=2,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=(PhysicalRecord("record", "agent_loop", "schema", b"selected", "ignored"),),
    )

    assert rows.physical_member(command, command.records[0]).canonical_bytes == b"selected"


def test_verified_rows_admit_an_exact_ancestor_under_the_selected_head() -> None:
    snapshot = _snapshot()
    publications = snapshot.tables[2].model_copy(
        update={
            "rows": (
                ("tenant", "agent_loop", "ancestor", "ancestor-fingerprint", 1, "ancestor-record"),
                *snapshot.tables[2].rows,
            )
        }
    )
    records = snapshot.tables[3].model_copy(
        update={
            "rows": (
                ("tenant", "ancestor-record", "agent_loop", "schema", {"base64": "YQ=="}, 1),
                *snapshot.tables[3].rows,
            )
        }
    )
    rows = H1VerifiedSnapshotRows.from_verified(
        snapshot.model_copy(
            update={
                "tables": (
                    snapshot.tables[0],
                    snapshot.tables[1],
                    publications,
                    records,
                    snapshot.tables[4],
                )
            }
        )
    )
    ancestor = PhysicalPublicationCommand(
        tenant_id="tenant",
        operation_kind="agent_loop",
        idempotency_key="ancestor",
        request_fingerprint="ancestor-fingerprint",
        expected_head=0,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=(PhysicalRecord("ancestor-record", "agent_loop", "schema", b"a", "ignored"),),
    )

    assert rows.physical_member(ancestor, ancestor.records[0]).record_id == "ancestor-record"


@pytest.mark.asyncio
async def test_v3_historical_raw_cut_survives_later_valid_same_tenant_publication(
    tmp_path: Path,
) -> None:
    database = tmp_path / "historical-v3.sqlite"
    custody = tmp_path / "dispatch-custody"
    request, complete = await admit_complete_script(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = await runtime.drive_input(request)
        assert isinstance(initial, SelectedExecutionReceiptV1)
        started = await runtime.begin_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, started.head
        )
        sealed = await runtime.seal_execution_complete(
            "hermetic-ingress",
            initial.stable_run_lineage_id,
            captured.head,
            profile="H1_V3",
        )
        decision = next(
            json.loads(raw)
            for _, _, raw in runtime._loop_decisions().entries()
            if json.loads(raw).get("kind") == "DECIDED"
            and json.loads(raw).get("operation_id") == sealed.head
        )
        retained = RetainedExecutionCompleteSealV3.model_validate_json(
            decision["execution_complete_seal"]
        )
        seal = retained.exchange.proposal.fan_out.response_seal
        locator = CallSubjectHead(
            subject_id=seal.response_seal_id,
            revision=Present(head="record:" + seal.digest(), fingerprint=seal.digest()),
        )

        await runtime.create_execution("hermetic-ingress", "later-run", "Plan", BudgetPolicy())
        selected_loop, *_rest = _read_call_history(runtime, selected_only=True)

        reader = H1FirstPathSources(runtime)
        raw = reader._read_selected_cut(
            original_identity=request.identity,
            original_fingerprint=request.original_driver_command_fingerprint(),
            selected_seal=locator,
            historical=True,
        )
        replay = reader._read_v2_source(raw, historical=True)

    assert raw.commitment == decision["resulting"]
    assert replay.materialization_commitment == decision["resulting"]
    assert selected_loop.tenant_head > int(decision["expected_head"])
