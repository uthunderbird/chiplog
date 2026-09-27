"""The A conversation source owner lives only for an installed H1 runtime."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from chiplog.composition.common_cli_execution_runtime import (
    open_common_cli_execution_runtime,
    open_installed_h1_runtime,
)
from chiplog.composition.h1_conversation_sources import (
    H1ConversationSources,
    H1ConversationSourceUnavailable,
)
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot


@pytest.mark.asyncio
async def test_installed_h1_mounts_and_revokes_its_exact_conversation_source_owner(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            source_owner = cast(H1ConversationSources, runtime._h1_conversation_source_port)

            assert type(source_owner) is H1ConversationSources
            assert source_owner._runtime is runtime
            assert runtime._h1_conversation_source_port is source_owner

        assert not hasattr(runtime, "_h1_conversation_source_port")
        assert source_owner.check_current(object()) is False
        with pytest.raises(H1ConversationSourceUnavailable, match="closed"):
            source_owner._prepare_conversation_completion_request(object())


@pytest.mark.asyncio
async def test_generic_common_runtime_never_mounts_conversation_source_owner(
    tmp_path: Path,
) -> None:
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )

    async with open_common_cli_execution_runtime(
        tmp_path / "generic.sqlite3", resources=resources
    ) as runtime:
        assert not hasattr(runtime, "_h1_conversation_source_port")
