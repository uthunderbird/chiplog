"""Installed startup retains one authenticated post-seal recovery journal."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.h1_launch_enrollment import (
    H1RecoveryMountError,
    InstalledH1Launch,
    _open_installed_h1_launch,
    _provision_h1_recovery_mount,
)
from chiplog.composition.h1_postseal_recovery import H1PostSealRecoveryJournal
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._sqlite import EventAppender
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


@pytest.mark.asyncio
async def test_installed_startup_mounts_scanned_recovery_singleton_and_closes_it(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    _provision_h1_recovery_mount(slot, expected)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            private_runtime = cast(Any, runtime)
            mount = private_runtime._h1_recovery_mount
            journal = private_runtime._h1_postseal_recovery_journal

            assert type(journal) is H1PostSealRecoveryJournal
            assert journal._mount is mount
            assert journal._gate is runtime._authority_gate()
            assert journal.scan().tip is None

        assert journal._closed is True
        assert not hasattr(runtime, "_h1_recovery_mount")
        assert not hasattr(runtime, "_h1_postseal_recovery_journal")
        with pytest.raises(H1RecoveryMountError, match="closed"):
            mount.assert_current()


@pytest.mark.asyncio
async def test_recovery_role_preflight_aborts_before_event_appender_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    _provision_h1_recovery_mount(slot, expected)
    entered = False
    original_enter = EventAppender.__aenter__

    async def observe_enter(self: EventAppender) -> EventAppender:
        nonlocal entered
        entered = True
        return await original_enter(self)

    def reject_recovery_mount(self: InstalledH1Launch, gate: object) -> object:
        del self, gate
        raise H1RecoveryMountError("injected recovery preflight failure")

    monkeypatch.setattr(EventAppender, "__aenter__", observe_enter)
    monkeypatch.setattr(InstalledH1Launch, "open_enrolled_recovery_mount", reject_recovery_mount)

    with (
        _open_installed_h1_launch(slot) as launch,
        pytest.raises(H1RecoveryMountError, match="injected recovery preflight failure"),
    ):
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)):
            raise AssertionError("recovery preflight must abort installed startup")

    assert entered is False
