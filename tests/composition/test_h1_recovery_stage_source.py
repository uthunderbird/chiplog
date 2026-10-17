"""RED contracts for the historical H1 COMPLETION-stage source boundary."""

from __future__ import annotations

import contextlib
import dataclasses
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import chiplog.composition.h1_recovery_stage_source as stage_module
from chiplog.composition.common_cli_execution_runtime import (
    open_installed_h1_runtime,
)
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_recovery_stage_source import (
    H1RecoveryStageSource,
    H1RecoveryStageSourceError,
)
from chiplog.composition.h1_v2_recovery_native_source import (
    H1V2RecoveryNativeCut,
    H1V2RecoveryNativeSource,
)
from tests.support.composition.h1_recovery_stage_source import (
    _admit_other,
    _resources,
    _v2_seal_with_historical_v3_prepare,
)
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot


def test_require_current_holds_one_cut_and_reconstructs_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native, one-shot P/E rebuild, and retained P replay share one gate hold."""
    events: list[str] = []

    class Gate:
        @contextlib.contextmanager
        def hold(self) -> Iterator[None]:
            events.append("enter")
            try:
                yield
            finally:
                events.append("exit")

    native = dataclasses.make_dataclass(
        "Native", ["original_identity", "original_fingerprint", "selected_seal"]
    )(object(), "f" * 64, object())
    context = object.__new__(stage_module._IssuedRecoveryStages)
    source = object.__new__(H1RecoveryStageSource)
    port = SimpleNamespace(
        _replay_historical_recovery_sources=lambda cap: events.append("p-replay") or cap
    )
    source._runtime = SimpleNamespace(
        _authority_gate=lambda: Gate(), _h1_preissuance_registration_source_port=port
    )
    source._recovery_contexts = {
        id(context): stage_module._RecoveryStageState(
            context=context,
            root=object(),
            native=native,
            completion_input=b"expected",
            p_source_cap="cap",
        )
    }
    monkeypatch.setattr(source, "replay_completion_native", lambda _: events.append("native"))
    calls: list[object] = []

    class Reader:
        def __init__(self, _runtime: object) -> None:
            pass

        def _reconstruct_current_completion_bytes(self, **_kwargs: object) -> bytes:
            calls.append("reconstruct")
            events.append("reconstruct")
            return b"expected"

    monkeypatch.setattr(stage_module, "H1RecoveryHistoricalPESource", Reader)
    source._require_current(context)

    assert events == ["enter", "native", "reconstruct", "p-replay", "exit"]
    assert calls == ["reconstruct"]

    class ChangedReader(Reader):
        def _reconstruct_current_completion_bytes(self, **_kwargs: object) -> bytes:
            events.append("reconstruct-changed")
            return b"changed"

    monkeypatch.setattr(stage_module, "H1RecoveryHistoricalPESource", ChangedReader)
    events.clear()
    with pytest.raises(H1RecoveryStageSourceError, match="completion input differs"):
        source._require_current(context)
    assert events == ["enter", "native", "reconstruct-changed", "exit"]

    monkeypatch.setattr(stage_module, "H1RecoveryHistoricalPESource", Reader)
    events.clear()

    def changed_capability(_cap: object) -> None:
        events.append("p-replay-changed")
        raise ValueError("changed retained capability")

    port._replay_historical_recovery_sources = changed_capability
    with pytest.raises(H1RecoveryStageSourceError, match="historical P source differs"):
        source._require_current(context)
    assert events == ["enter", "native", "reconstruct", "p-replay-changed", "exit"]


@pytest.mark.asyncio
async def test_stage_source_reopens_v3_prepare_and_v2_seal_as_one_issued_native_cut(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, locator, selected_prepare = await _v2_seal_with_historical_v3_prepare(runtime)

            reader = H1RecoveryStageSource(runtime)
            issued = reader.issue_completion_native(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )
            native = reader.replay_completion_native(issued)

            assert native.selected_response_seal == locator
            assert native.run.head == issued.native_cut.source.complete_ordered_run_lineage[-1].head
            assert selected_prepare.decision_id

            # A dataclass copy has no issuer identity and cannot be used as a
            # recovery capability even when every visible field is identical.
            with pytest.raises(H1RecoveryStageSourceError, match="issuer-owned"):
                reader.replay_completion_native(dataclasses.replace(issued))


@pytest.mark.asyncio
async def test_stage_source_replays_selected_prefix_after_later_unrelated_publication(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, locator, _prepare = await _v2_seal_with_historical_v3_prepare(runtime)
            reader = H1RecoveryStageSource(runtime)
            issued = reader.issue_completion_native(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )
            before = reader.replay_completion_native(issued)

        async with open_installed_h1_runtime(
            launch, resources=_resources(tmp_path, "-later")
        ) as runtime:
            await runtime.drive_input(await _admit_other(runtime))
            reader = H1RecoveryStageSource(runtime)
            replayed = reader.issue_completion_native(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )

            assert reader.replay_completion_native(replayed) == before


@pytest.mark.asyncio
async def test_stage_source_rejects_a_changed_replayed_native_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, locator, _prepare = await _v2_seal_with_historical_v3_prepare(runtime)
            reader = H1RecoveryStageSource(runtime)
            issued = reader.issue_completion_native(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )
            select = H1V2RecoveryNativeSource.select

            def changed_select(
                self: H1V2RecoveryNativeSource, **kwargs: Any
            ) -> H1V2RecoveryNativeCut:
                selected = select(self, **kwargs)
                return dataclasses.replace(
                    selected,
                    seal=dataclasses.replace(selected.seal, raw_bytes=b'{"changed":true}'),
                )

            monkeypatch.setattr(H1V2RecoveryNativeSource, "select", changed_select)

            with pytest.raises(H1RecoveryStageSourceError, match="selected native source"):
                reader.replay_completion_native(issued)


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "P owns the historical delivery/fence reconstruction and exposes only a current, "
        "opaque receipt; no installed historical P bridge exists"
    ),
)
async def test_stage_source_derives_canonical_completion_input_from_historical_native_and_p(
    tmp_path: Path,
) -> None:
    """The eventual seam must return a canonical request without caller DTO input."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, locator, _prepare = await _v2_seal_with_historical_v3_prepare(runtime)
            reader = H1RecoveryStageSource(runtime)
            issued = reader.issue_completion_native(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )

            completion = reader.reconstruct_completion_input(issued)

            facts = reader.replay_completion_native(issued)
            assert completion.kind == "PREPARE_EXECUTION_COMPLETION_FIRST_PATH_V2"
            assert completion.run == facts.run
            assert completion.selected_attempt == facts.selected_attempt
            assert completion.exact_captured_response == facts.exact_captured_response
            assert completion.delivery.captured_response == facts.exact_captured_response
            assert completion.fence.run_head == facts.run.head
