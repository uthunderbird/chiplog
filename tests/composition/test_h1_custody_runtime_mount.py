"""Installed H1 runtime binds the custody mutation gate to its authenticated database."""

from __future__ import annotations

from pathlib import Path

import pytest

from chiplog.composition.common_cli_execution_runtime import (
    open_common_cli_execution_runtime,
)
from chiplog.composition.h1_registration_custody import (
    H1RegistrationCustody,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


@pytest.mark.asyncio
async def test_uninstalled_runtime_does_not_bind_registration_custody_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[tuple[object, object, object, object]] = []
    original_bind = H1RegistrationCustody.bind_authority_gate

    def capture_bind(
        self: H1RegistrationCustody,
        gate: object,
        database_path: object,
        database_identity: object,
    ) -> None:
        observed.append((self, gate, database_path, database_identity))
        original_bind(self, gate, database_path, database_identity)

    monkeypatch.setattr(H1RegistrationCustody, "bind_authority_gate", capture_bind)

    async with open_common_cli_execution_runtime(
        tmp_path / "ordinary.sqlite3", resources=_resources(tmp_path)
    ) as runtime:
        assert runtime._appender._materializer._owner_publication_resolver is None

    assert observed == []
