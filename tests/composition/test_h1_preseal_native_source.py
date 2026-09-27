"""Issuer-owned native facts available before an H1 V2 seal exists."""

from __future__ import annotations

import hashlib
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
    request = await _admit(runtime)
    initial = cast(Any, await runtime.drive_input(request))
    runtime._execution_model._responses = (
        DeliveryCompletion(
            tenant=TENANT,
            run_id=initial.stable_run_lineage_id,
            turn_id=initial.stable_run_lineage_id + "/turn/1",
            deliveries=(ProposedDelivery(payload=(Commentary(text="preseal native source"),)),),
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
async def test_genuine_preseal_v3_prepare_exposes_recheckable_ordered_native_occurrences(
    tmp_path: Path,
) -> None:
    """The source is usable before direct V2 sealing and accepts no seal locator."""
    from chiplog.composition.h1_preseal_native_source import H1PresealNativeSource

    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            preflight = await _genuine_preflight(runtime)
            issuer = H1PresealNativeSource(runtime)

            cut = issuer.capture(preflight)
            occurrences = issuer.replay(cut)

            assert tuple(row.member_index for row in occurrences) == tuple(range(len(occurrences)))
            assert len(occurrences) == len(
                preflight.captured_run.turns[0].attempts[0].manifest.members
            )
            assert tuple(row.member_bytes for row in occurrences) == tuple(
                member.canonical_bytes()
                for member in preflight.captured_run.turns[0].attempts[0].manifest.members
            )
            assert tuple(row.member_digest for row in occurrences) == tuple(
                hashlib.sha256(member.canonical_bytes()).hexdigest()
                for member in preflight.captured_run.turns[0].attempts[0].manifest.members
            )

            with pytest.raises(ValueError, match="issuer-owned"):
                issuer.replay(replace(cut))
            with pytest.raises(ValueError, match="runtime-issued"):
                issuer.capture(replace(preflight))


@pytest.mark.asyncio
async def test_preseal_source_rechecks_selected_prepare_and_owner_inventory_before_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A changed pre-seal read cannot be projected as future P/E evidence."""
    import chiplog.composition.h1_preseal_native_source as native_source

    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            preflight = await _genuine_preflight(runtime)
            issuer = native_source.H1PresealNativeSource(runtime)
            cut = issuer.capture(preflight)

            changed_prepare = replace(
                preflight,
                prepare=replace(
                    preflight.prepare,
                    decision_bytes=preflight.prepare.decision_bytes + b" ",
                ),
            )
            monkeypatch.setattr(
                native_source, "_read_preflight", lambda *_args, **_kwargs: changed_prepare
            )
            with pytest.raises(ValueError, match="current"):
                issuer.replay(cut)

            monkeypatch.undo()
            changed_inventory = replace(
                preflight,
                inventory=replace(preflight.inventory, commitment="0" * 64),
            )
            monkeypatch.setattr(
                native_source, "_read_preflight", lambda *_args, **_kwargs: changed_inventory
            )
            with pytest.raises(ValueError, match="current"):
                issuer.replay(cut)


@pytest.mark.asyncio
async def test_preseal_source_rejects_a_genuine_preflight_from_another_installed_runtime(
    tmp_path: Path,
) -> None:
    """A live issuer cannot acquire authority from another runtime's preflight."""
    from chiplog.composition.h1_preseal_native_source import H1PresealNativeSource

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
            preflight = await _genuine_preflight(first)
            with _open_installed_h1_launch(second_slot) as second_launch:
                async with open_installed_h1_runtime(
                    second_launch, resources=_resources(second_root)
                ) as second:
                    with pytest.raises(ValueError, match="runtime-issued"):
                        H1PresealNativeSource(second).capture(preflight)
