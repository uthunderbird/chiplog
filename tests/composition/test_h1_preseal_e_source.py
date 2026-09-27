"""RED witnesses for issuer-owned E residual capture before an H1 V2 seal.

These witnesses deliberately do not use the installed E journal or the
post-seal exact-head domain.  The required preseal owner is absent today:
the mounted E owners accept only ``H1CurrentNativeMemberSourceCut``, whose
authenticated source starts at a response seal.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.composition import h1_preseal
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_installed_h1_runtime,
)
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_preseal_contracts import H1V2SealPreflight
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_cli_execution import _admit
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


async def _genuine_preflight(runtime: CommonCliExecutionRuntime) -> H1V2SealPreflight:
    """Reach the installed V3 Prepare and captured Run cut, without sealing."""
    request = await _admit(runtime)
    initial = cast(Any, await runtime.drive_input(request))
    runtime._execution_model._responses = (
        DeliveryCompletion(
            tenant=TENANT,
            run_id=initial.stable_run_lineage_id,
            turn_id=initial.stable_run_lineage_id + "/turn/1",
            deliveries=(ProposedDelivery(payload=(Commentary(text="preseal E source"),)),),
        ).canonical_bytes(),
    )
    started = await runtime.begin_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
    )
    captured = await runtime.capture_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, started.head
    )
    return h1_preseal.preflight_h1_v2_seal(runtime, captured, expected_head=captured.head)


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "the installed E owners start from a post-seal native cut and E journal; no installed "
        "preseal E owner yet captures anchor residuals from V3 Prepare plus captured Run"
    ),
)
async def test_installed_preseal_e_owner_captures_complete_codec_shaped_residuals_without_journal(
    tmp_path: Path,
) -> None:
    """The capture is seal-agnostic and has no E journal or exact-head output."""
    source_module = importlib.import_module("chiplog.composition.h1_preseal_e_source")
    source_type = source_module.H1PresealESource

    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            preflight = await _genuine_preflight(runtime)
            journal = runtime._h1_delivery_evidence_journal
            assert journal is not None
            before = journal._entries()

            source = source_type(runtime)
            capture = source.capture(preflight)
            residual = source.recheck(capture)

            members = residual.e_members
            worker = residual.e_worker
            native_members = preflight.captured_run.turns[0].attempts[0].manifest.members
            assert len(members) == len(native_members)
            assert [member["member_index"] for member in members] == list(range(len(members)))
            assert [member["member_digest"] for member in members] == [
                hashlib.sha256(member.canonical_bytes()).hexdigest()
                for member in native_members
            ]
            for member in members:
                assert set(member) == {
                    "member_index",
                    "member_digest",
                    "provenance",
                    "disclosure",
                    "narrowing",
                }
                assert "journal" not in repr(member).lower()
                assert "h1-evidence:" not in repr(member)
                assert "record:" not in repr(member)
            assert set(worker) == {
                "runtime_instance_id",
                "owner_route_generation",
                "worker_session_id",
                "owner_id",
                "run_head",
                "fence_kind",
            }
            installed_worker_owner = runtime._h1_installed_worker_evidence_owner
            assert worker["runtime_instance_id"] == installed_worker_owner._lifetime_id
            assert worker["worker_session_id"] == preflight.worker_session
            assert worker["owner_route_generation"] == preflight.worker_session.split(":")[1]
            assert worker["run_head"] == preflight.captured_run.head
            assert journal._entries() == before


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "the installed path has no one-use preseal E receipt joined to the current worker-owner "
        "session, so copied, foreign, and stale preseal captures cannot yet be denied"
    ),
)
async def test_preseal_e_capture_recheck_denies_copied_foreign_and_stale_owner_inputs(
    tmp_path: Path,
) -> None:
    """Only the issuing installed owner may recheck one current preseal capture."""
    source_module = importlib.import_module("chiplog.composition.h1_preseal_e_source")
    source_type = source_module.H1PresealESource

    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first_slot, first_expected = installed_slot(first_root)
    second_slot, second_expected = installed_slot(second_root)
    await prepare_installed_slot(first_slot, first_expected, first_root)
    await prepare_installed_slot(second_slot, second_expected, second_root)
    with _open_installed_h1_launch(first_slot) as first_launch:
        async with open_installed_h1_runtime(
            first_launch, resources=_resources(first_root)
        ) as first:
            source = source_type(first)
            preflight = await _genuine_preflight(first)
            capture = source.capture(preflight)
            with pytest.raises(TypeError, match=r"copied|owner"):
                copy.copy(capture)

            with _open_installed_h1_launch(second_slot) as second_launch:
                async with open_installed_h1_runtime(
                    second_launch, resources=_resources(second_root)
                ) as second:
                    with pytest.raises(ValueError, match=r"issuer|owner|foreign"):
                        source_type(second).recheck(capture)

            worker_owner = first._h1_installed_worker_evidence_owner
            previous_lifetime = worker_owner._lifetime_id
            worker_owner._lifetime_id = "replaced-worker-owner-instance"
            try:
                with pytest.raises(ValueError, match=r"stale|current|worker"):
                    source.recheck(capture)
            finally:
                worker_owner._lifetime_id = previous_lifetime

            with pytest.raises(ValueError, match=r"stale|current|source"):
                object.__setattr__(
                    preflight,
                    "prepare",
                    replace(
                        preflight.prepare,
                        decision_bytes=preflight.prepare.decision_bytes + b" ",
                    ),
                )
                source.recheck(capture)
