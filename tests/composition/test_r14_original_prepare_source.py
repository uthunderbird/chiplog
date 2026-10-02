"""Mounted evidence tests for the read-only original Prepare projection."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

import chiplog.composition.r14_original_prepare_source as original_source
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_common_cli_execution_runtime,
)
from chiplog.composition.common_execution_driver_contracts import SelectedExecutionReceiptV1
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_seal
from chiplog.composition.r13_workspace import R13Workspace
from chiplog.composition.r14_execution_complete_seal_records import RetainedExecutionCompleteSealV3
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_cli_execution import admit_complete_script


@asynccontextmanager
async def _selected_v3_prepare(
    tmp_path: Path,
) -> AsyncIterator[tuple[CommonCliExecutionRuntime, CallSubjectHead]]:
    database = tmp_path / "original-prepare.sqlite"
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
        await runtime.seal_execution_complete(
            "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V3"
        )
        decision = next(
            json.loads(raw)
            for _, _, raw in runtime._loop_decisions().entries()
            if "execution_complete_seal" in json.loads(raw)
            and "V3" in json.loads(raw)["execution_complete_seal"]
        )
        retained = RetainedExecutionCompleteSealV3.model_validate_json(
            decision["execution_complete_seal"]
        )
        seal = retained.exchange.proposal.fan_out.response_seal
        yield (
            cast(CommonCliExecutionRuntime, runtime),
            CallSubjectHead(
                subject_id=seal.response_seal_id,
                revision=Present(head="record:" + seal.digest(), fingerprint=seal.digest()),
            ),
        )


@pytest.mark.asyncio
async def test_reopens_exact_original_prepare_after_later_publication(tmp_path: Path) -> None:
    async with _selected_v3_prepare(tmp_path) as (runtime, seal):
        source = original_source.R14OriginalNativePrepareSource(runtime)
        projection = source.collect(seal)
        assert projection is not None
        selected = select_h1_v3_prepare_for_seal(runtime, selected_seal=seal, historical=True)
        assert (
            selected.prepare.physical_run.record_id == selected.prepare.retained.proposal.run.head
        )
        assert projection.retained_prepare_bytes == selected.prepare.retained_bytes
        assert projection.database_identity == runtime._database_identity
        assert projection.prepared_run_bytes == selected.prepare.physical_run.canonical_bytes
        issuance = (
            R13Workspace(runtime).open_h1_workspace_issuance().load(selected.prepare.issuance_ref)
        )
        assert projection.workspace_issuance_bytes == issuance.canonical_bytes()
        await runtime.create_execution("hermetic-ingress", "later-run", "Later", BudgetPolicy())
        replay = source.collect(seal)
        assert replay == projection
        assert replay.rendered_prompt_bytes == replay.artifact.rendered.encode()
        assert replay.artifact_bytes == replay.artifact.canonical_bytes()
        assert replay.generated_schema_bytes == replay.artifact.response_schema_json.encode()
        assert replay.ordered_tool_spec_bytes == tuple(
            tool.canonical_bytes() for tool in replay.artifact.tools
        )
        assert replay.policy_bytes
        rendered_digest = hashlib.sha256(replay.rendered_prompt_bytes).hexdigest()
        assert rendered_digest != replay.artifact.content_hash


@pytest.mark.asyncio
async def test_holds_for_substituted_seal_or_manifest_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _selected_v3_prepare(tmp_path) as (runtime, seal):
        source = original_source.R14OriginalNativePrepareSource(runtime)
        substituted_seal = seal.model_copy(
            update={"revision": Present(head=seal.revision.head, fingerprint="0" * 64)}
        )
        assert source.collect(substituted_seal) is None
        selected = select_h1_v3_prepare_for_seal(runtime, selected_seal=seal, historical=True)
        attempt = selected.captured_run.turns[0].attempts[0]
        manifest = attempt.manifest.model_copy(
            update={"members": tuple(reversed(attempt.manifest.members))}
        )
        turn = selected.captured_run.turns[0].model_copy(
            update={"attempts": (attempt.model_copy(update={"manifest": manifest}),)}
        )
        captured_run = selected.captured_run.model_copy(update={"turns": (turn,)})
        substituted = replace(selected, captured_run=captured_run)
        monkeypatch.setattr(
            original_source,
            "select_h1_v3_prepare_for_seal",
            lambda *_a, **_k: substituted,
        )
        assert source.collect(seal) is None
