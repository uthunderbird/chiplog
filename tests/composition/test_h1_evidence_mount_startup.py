"""Installed H1 evidence validation is earlier than any runtime sidecar."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from chiplog.architecture.r7_runtime import R7_PRODUCTION_MANIFEST
from chiplog.composition.r7_planning import R7PlanningRuntime, _open_runtime
from chiplog.platform.authority_gate import AuthorityGate


@pytest.mark.asyncio
async def test_preflight_runs_before_any_runtime_constructor_and_store_setup(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime.sqlite3"
    observed: list[str] = []

    def preflight(gate: AuthorityGate) -> object:
        observed.append("preflight")
        assert gate.database == database.resolve()
        raise RuntimeError("missing enrolled evidence")

    def store_setup(*_: object) -> None:
        observed.append("store_setup")

    with pytest.raises(RuntimeError, match="missing enrolled evidence"):
        async with _open_runtime(
            database,
            tenant_id="actual-installed-tenant",
            operator_secret=b"test",
            runtime_type=R7PlanningRuntime,
            manifest=R7_PRODUCTION_MANIFEST,
            preflight=preflight,
            store_setup=store_setup,
        ):
            await asyncio.sleep(0)
    assert observed == ["preflight"]


@pytest.mark.asyncio
async def test_store_setup_receives_preflight_mount_and_exact_gate_before_appender(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "runtime.sqlite3"
    mount = object()
    observed: list[str] = []

    def preflight(gate: AuthorityGate) -> object:
        observed.append("preflight")
        return (mount, gate)

    def store_setup(store: object, gate: AuthorityGate, preflight_mount: object) -> None:
        observed.append("store_setup")
        assert store.authority_gate is gate  # type: ignore[attr-defined]
        assert preflight_mount == (mount, gate)
        raise RuntimeError("stop before writer")

    with pytest.raises(RuntimeError, match="stop before writer"):
        async with _open_runtime(
            database,
            tenant_id="actual-installed-tenant",
            operator_secret=b"test",
            runtime_type=R7PlanningRuntime,
            manifest=R7_PRODUCTION_MANIFEST,
            preflight=preflight,
            store_setup=store_setup,
        ):
            await asyncio.sleep(0)
    assert observed == ["preflight", "store_setup"]
