"""Mounted P receipt for a complete H1 delivery observation."""

from __future__ import annotations

import base64
import copy
from pathlib import Path

import pytest

from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_preissuance_registration import H1PreissuanceSourceViolation
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.composition.test_h1_completion_scope_source import _original_and_native
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot


@pytest.mark.asyncio
async def test_installed_p_receipt_replays_exact_observation_and_consumes_once(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            original, native = await _original_and_native(runtime)
            port = runtime._h1_preissuance_registration_source_port
            member_owner = runtime._h1_pre_request_member_evidence
            worker_owner = runtime._h1_installed_worker_evidence_owner
            worker_issuer = runtime._h1_pre_request_worker_evidence
            assert port is not None
            assert member_owner is not None
            assert worker_owner is not None
            assert worker_issuer is not None

            scope = await port._capture_completion_scope(native._capture, native, original)
            member_receipt = member_owner._issue_members(native, scope)
            worker_cap = worker_owner._capture_for_pre_request(native)
            worker_receipt = worker_issuer._issue_worker(native, worker_cap)
            receipt = port._prepare_delivery_inputs(
                native._capture, native, scope, member_receipt, worker_receipt
            )

            observation, fence = port._replay_delivery_inputs(receipt, native._capture)
            run = native._native.source.complete_ordered_run_lineage[-1]
            captured = native._native.source.complete_ordered_run_lineage[-2]
            assert observation.run.identity == run.run_id
            assert observation.run.head == run.head
            assert observation.captured_response == base64.b64decode(
                captured.turns[0].attempts[0].response_base64 or "", validate=True
            )
            assert observation.recipients == (run.origin.recipient,)
            assert observation.policy == port._replay_completion_scope(scope, native).policy_ref
            assert len(observation.history) == len(native._occurrences)
            assert [entry.label for entry in observation.history] == [
                entry.original_label for entry in native._occurrences
            ]
            assert fence.run_id == run.run_id
            assert fence.run_head == run.head
            assert fence.worker_session_id == run.worker_session
            assert observation.worker_fence == worker_issuer._replay_worker(worker_receipt)[2]
            with pytest.raises(TypeError, match="cannot be copied"):
                copy.copy(receipt)

            assert port._consume_delivery_inputs(receipt, native._capture) == (observation, fence)
            with pytest.raises(H1PreissuanceSourceViolation, match="already consumed"):
                port._consume_delivery_inputs(receipt, native._capture)


@pytest.mark.asyncio
async def test_p_receipt_refuses_forged_owner_receipts_before_issuing(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            original, native = await _original_and_native(runtime)
            port = runtime._h1_preissuance_registration_source_port
            assert port is not None
            scope = await port._capture_completion_scope(native._capture, native, original)

            with pytest.raises(H1PreissuanceSourceViolation, match="receipts are not owner-issued"):
                port._prepare_delivery_inputs(
                    native._capture,
                    native,
                    scope,
                    object(),
                    object(),
                )
