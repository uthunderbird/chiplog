"""Closed schema routing for the inert H1 SCOPED_V3 issuance wire."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chiplog.composition import h1_completion_issuance as issuance
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_v3_recovery_historical_source import (
    H1V3RecoveryHistoricalSource,
    H1V3RecoveryHistoricalSourceError,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.composition.test_j7_scoped_recovered_grant_route import (
    _install_scoped_root,
    _recover_first_two_scoped_stages,
)
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def test_v3_dispatch_requires_matching_closed_outer_and_inner_schema() -> None:
    schema = issuance.V3_SCHEMA

    assert issuance.dispatch_h1_completion_issuance_schema(
        schema, _canonical({"schema_id": schema})
    ) == "V3"

    for outer, inner in (
        (issuance.V2_SCHEMA, schema),
        (schema, issuance.V2_SCHEMA),
        (schema, "chiplog.composition.h1-completion-issuance.v9"),
    ):
        with pytest.raises(ValueError, match="outer and inner"):
            issuance.dispatch_h1_completion_issuance_schema(
                outer, _canonical({"schema_id": inner})
            )


@pytest.mark.parametrize(
    "raw",
    (
        b'{"schema_id":"chiplog.composition.h1-completion-issuance.v3", "extra":true}',
        b'{"schema_id":"chiplog.composition.h1-completion-issuance.v3","schema_id":"chiplog.composition.h1-completion-issuance.v3"}',
        _canonical({"schema_id": issuance.V3_SCHEMA, "extra": True}),
    ),
)
def test_v3_dispatch_rejects_noncanonical_duplicate_or_extra_members(raw: bytes) -> None:
    with pytest.raises(ValueError):
        issuance.dispatch_h1_completion_issuance_schema(issuance.V3_SCHEMA, raw)


def test_v3_decoder_does_not_admit_a_schema_probe_as_issuance() -> None:
    with pytest.raises(ValueError):
        issuance.decode_h1_completion_issuance_v3(_canonical({"schema_id": issuance.V3_SCHEMA}))


@pytest.mark.asyncio
async def test_v3_completed_stage_reader_rejects_a_genuine_two_stage_prefix(tmp_path: Path) -> None:
    """A readable B prefix cannot masquerade as a four-stage V3 recovery result."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            request, locator, state = await _install_scoped_root(runtime)
            await _recover_first_two_scoped_stages(
                runtime, request=request, locator=locator, state=state
            )
            with pytest.raises(H1V3RecoveryHistoricalSourceError, match="four-stage journal"):
                H1V3RecoveryHistoricalSource(runtime).capture_completed_stages(
                    original_identity=request.identity,
                    original_fingerprint=request.original_driver_command_fingerprint(),
                    selected_seal=locator,
                )
