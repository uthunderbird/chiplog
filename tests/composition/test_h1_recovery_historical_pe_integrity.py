"""Mounted RED integrity witnesses for the retained H1 preseal P/E anchor.

Each case starts from an installed, owner-issued direct V3 Prepare -> V2 seal.
The test only alters the historical journal view seen by the reader; it never
supplies a P/E delivery, member, policy, fence, or worker DTO.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_installed_h1_runtime,
)
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_preseal_pe_anchor_records import (
    decode_h1_preseal_pe_anchor_record,
)
from tests.composition.test_h1_recovery_historical_pe_source import (
    _resources,
    _seal_v3_prepare_as_v2,
    _selected_seal_decision,
)
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot


def _tampered_journal(
    runtime: CommonCliExecutionRuntime,
    selected_seal: CallSubjectHead,
    alter: Callable[[dict[str, object]], None],
) -> object:
    """Return an in-memory journal view with one canonical DECIDED replacement."""
    decision_id, raw, decision = _selected_seal_decision(runtime, selected_seal)
    entries = list(runtime._loop_decisions().entries())
    alter(decision)
    replacement = json.dumps(
        decision, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()
    index = next(index for index, entry in enumerate(entries) if entry[0] == decision_id)
    entries[index] = (decision_id, entries[index][1], replacement)

    class _TamperedJournal:
        def entries(self) -> tuple[tuple[str, str | None, bytes], ...]:
            return tuple(entries)

    assert raw != replacement
    return _TamperedJournal()


def _historical_source_module() -> Any:
    return importlib.import_module("chiplog.composition.h1_recovery_historical_pe_source")


async def _opened_seal(tmp_path: Path) -> Path:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    return slot


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "the installed historical P/E reader has not yet classified legacy V2 seals as unsupported"
    ),
)
async def test_historical_pe_source_rejects_a_legacy_v2_seal_without_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot = await _opened_seal(tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            original, selected_seal = await _seal_v3_prepare_as_v2(runtime)

            def remove_anchor(decision: dict[str, object]) -> None:
                del decision["h1_preseal_pe_anchor"]

            journal = _tampered_journal(runtime, selected_seal, remove_anchor)
            monkeypatch.setattr(runtime, "_loop_decisions", lambda: journal)
            module = _historical_source_module()
            source = module.H1RecoveryHistoricalPESource(runtime)
            with pytest.raises(
                module.H1RecoveryHistoricalPESourceError,
                match=r"anchor|legacy|unsupported",
            ):
                source.issue_completion_projection(
                    original_identity=original.identity,
                    original_fingerprint=original.original_driver_command_fingerprint(),
                    selected_seal=selected_seal,
                )


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="the installed historical P/E reader has not yet rejected noncanonical retained anchors",
)
async def test_historical_pe_source_rejects_a_present_noncanonical_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot = await _opened_seal(tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            original, selected_seal = await _seal_v3_prepare_as_v2(runtime)

            def make_noncanonical(decision: dict[str, object]) -> None:
                raw = decision["h1_preseal_pe_anchor"]
                assert isinstance(raw, str)
                decision["h1_preseal_pe_anchor"] = raw.replace('","', '", "')

            journal = _tampered_journal(runtime, selected_seal, make_noncanonical)
            monkeypatch.setattr(runtime, "_loop_decisions", lambda: journal)
            module = _historical_source_module()
            source = module.H1RecoveryHistoricalPESource(runtime)
            with pytest.raises(
                module.H1RecoveryHistoricalPESourceError,
                match=r"anchor|canonical|integrity",
            ):
                source.issue_completion_projection(
                    original_identity=original.identity,
                    original_fingerprint=original.original_driver_command_fingerprint(),
                    selected_seal=selected_seal,
                )


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "the installed historical P/E reader has not yet joined a canonical anchor's member "
        "provenance to the selected V3/Run history"
    ),
)
async def test_historical_pe_source_rejects_a_canonical_anchor_with_wrong_member_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot = await _opened_seal(tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            original, selected_seal = await _seal_v3_prepare_as_v2(runtime)

            def make_provenance_wrong(decision: dict[str, object]) -> None:
                raw = decision["h1_preseal_pe_anchor"]
                assert isinstance(raw, str)
                anchor = decode_h1_preseal_pe_anchor_record(raw.encode()).as_dict()
                members = anchor["e_members"]
                assert isinstance(members, list) and members
                member = members[0]
                assert isinstance(member, dict)
                provenance = member["provenance"]
                assert isinstance(provenance, dict)
                locator = provenance["source_locator"]
                assert isinstance(locator, dict)
                locator["artifact_digest"] = "0" * 64
                member_bytes = json.dumps(
                    members, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                ).encode()
                anchor["binding"]["member_vector_digest"] = hashlib.sha256(member_bytes).hexdigest()
                decision["h1_preseal_pe_anchor"] = json.dumps(
                    anchor, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                )

            journal = _tampered_journal(runtime, selected_seal, make_provenance_wrong)
            monkeypatch.setattr(runtime, "_loop_decisions", lambda: journal)
            module = _historical_source_module()
            source = module.H1RecoveryHistoricalPESource(runtime)
            with pytest.raises(
                module.H1RecoveryHistoricalPESourceError,
                match=r"anchor|provenance|selected",
            ):
                source.issue_completion_projection(
                    original_identity=original.identity,
                    original_fingerprint=original.original_driver_command_fingerprint(),
                    selected_seal=selected_seal,
                )
