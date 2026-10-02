"""The V22 runtime mounts and revokes the inert semantic-registry reader."""

from __future__ import annotations

from pathlib import Path

import pytest

from chiplog.composition.common_cli_execution_runtime import open_installed_h1_scoped_v22_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_scoped_authorization_clock_reader import (
    H1ScopedAuthorizationClockReaderV1,
)
from chiplog.composition.h1_scoped_authorization_delivery_work_reader import (
    H1ScopedAuthorizationDeliveryWorkReaderV1,
)
from chiplog.composition.h1_scoped_authorization_semantic_registry_reader import (
    H1ScopedAuthorizationSemanticRegistryReaderV1,
)
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot
from tests.support.h1_scoped_first_b import scoped_resources, write_scoped_pins


@pytest.mark.asyncio
async def test_v22_mounts_and_closes_semantic_registry_reader(tmp_path: Path) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    write_scoped_pins(slot.database_path)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_v22_runtime(
            launch, resources=scoped_resources(tmp_path)
        ) as runtime:
            reader = runtime._h1_scoped_authorization_semantic_registry_reader_v1
            joined = runtime._h1_scoped_authorization_delivery_work_reader_v1
            clock = runtime._h1_scoped_authorization_clock_reader_v1

            assert type(reader) is H1ScopedAuthorizationSemanticRegistryReaderV1
            assert reader._runtime is runtime
            assert type(joined) is H1ScopedAuthorizationDeliveryWorkReaderV1
            assert reader._joined is joined
            assert type(clock) is H1ScopedAuthorizationClockReaderV1
            assert reader._clock is clock

        assert reader._closed is True
        assert not hasattr(runtime, "_h1_scoped_authorization_semantic_registry_reader_v1")
