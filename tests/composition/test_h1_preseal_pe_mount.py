"""The H1 preseal P/E decision owner is installed only for an enrolled runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from chiplog.composition.common_cli_execution_runtime import (
    open_common_cli_execution_runtime,
    open_installed_h1_runtime,
)
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_preseal_pe_decision import (
    H1PresealPEDecisionError,
    H1PresealPEDecisionOwner,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


@pytest.mark.asyncio
async def test_installed_runtime_mounts_one_preseal_pe_owner_and_revokes_it_on_restart(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            mounted = cast(Any, runtime)
            first = mounted._h1_preseal_pe_decision_owner

            assert type(first) is H1PresealPEDecisionOwner
            assert first._runtime is runtime
            assert first._native_source is mounted._h1_preseal_native_source
            assert first._p_owner is mounted._h1_preissuance_registration_source_port
            assert first._member_owner is mounted._h1_pre_request_member_evidence
            assert first._worker_owner is mounted._h1_installed_worker_evidence_owner

        assert not hasattr(mounted, "_h1_preseal_pe_decision_owner")
        with pytest.raises(H1PresealPEDecisionError, match="closed"):
            await first.capture(cast(Any, object()))
        # Revoke remains idempotent after the context manager revoked the owner.
        first.revoke()

        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as reopened:
            second = cast(Any, reopened)._h1_preseal_pe_decision_owner
            assert type(second) is H1PresealPEDecisionOwner
            assert second is not first

        assert not hasattr(cast(Any, reopened), "_h1_preseal_pe_decision_owner")


@pytest.mark.asyncio
async def test_common_runtime_never_mounts_preseal_pe_decision_owner(tmp_path: Path) -> None:
    async with open_common_cli_execution_runtime(
        tmp_path / "generic.sqlite3", resources=_resources(tmp_path)
    ) as runtime:
        assert not hasattr(runtime, "_h1_preseal_pe_decision_owner")
