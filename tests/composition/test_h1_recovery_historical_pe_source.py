"""Mounted RED contract for historical P/E COMPLETION reconstruction.

The test deliberately seals the selected V2 prefix without retaining any
caller-shaped delivery DTO.  A future installed reader must select its own
authenticated P/E closure, and must fail closed when that closure is absent.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
from pathlib import Path
from typing import Literal

import pytest

from chiplog.composition.common_cli_execution_runtime import (
    open_installed_h1_runtime,
)
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_preseal_pe_anchor_records import (
    H1PresealPEAnchorRecordV1,
    decode_h1_preseal_pe_anchor_record,
    h1_preseal_pe_anchor_projection_identity,
)
from chiplog.composition.h1_v2_recovery_native_source import H1V2RecoveryNativeSource
from chiplog.composition.r14_execution_complete_seal_records import (
    ExecutionCompleteSealPhysicalEnvelopeV2,
    RetainedExecutionCompleteSealV2,
    build_complete_seal_envelope,
)
from tests.support.composition.h1_recovery_historical_pe_source import (
    _resources,
    _seal_v3_prepare_as_v2,
    _selected_seal_decision,
)
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot


def test_current_historical_reader_reconstructs_once_and_rejects_noncanonical_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Currentness uses one source-owned rebuild, never a synthetic receipt."""
    source_module = importlib.import_module("chiplog.composition.h1_recovery_historical_pe_source")
    source_type = source_module.H1RecoveryHistoricalPESource
    reader = object.__new__(source_type)
    calls: list[object] = []

    class Request:
        def canonical_bytes(self) -> bytes:
            return b"canonical"

    monkeypatch.setattr(source_type, "_validate_inputs", staticmethod(lambda *_: None))
    monkeypatch.setattr(
        source_type,
        "_reconstruct",
        lambda _self, *_: calls.append("reconstruct") or Request(),
    )
    monkeypatch.setattr(source_module, "decode_first_path_completion_request", lambda _: Request())

    assert (
        reader._reconstruct_current_completion_bytes(
            original_identity=object(), original_fingerprint="f", selected_seal=object()
        )
        == b"canonical"
    )
    assert calls == ["reconstruct"]

    class Noncanonical(Request):
        def canonical_bytes(self) -> bytes:
            return b"different"

    monkeypatch.setattr(
        source_module, "decode_first_path_completion_request", lambda _: Noncanonical()
    )
    with pytest.raises(source_module.H1RecoveryHistoricalPESourceIntegrityError):
        reader._reconstruct_current_completion_bytes(
            original_identity=object(), original_fingerprint="f", selected_seal=object()
        )


def _historical_projection(
    anchor: H1PresealPEAnchorRecordV1,
    *,
    selected_decision_id: str,
    role: Literal["P", "E_MEMBER", "E_WORKER"],
    member_index: int | None,
    facet: str,
    facts: object,
) -> dict[str, str]:
    """The frozen anchor-derived ExactHead, never an E journal reference."""
    identity = h1_preseal_pe_anchor_projection_identity(
        anchor,
        selected_decision_id=selected_decision_id,
        role=role,
        member_index=member_index,
    )
    payload = {
        "schema_id": "chiplog.execution.h1-historical-pe-projection.v1",
        "anchor_digest": hashlib.sha256(anchor.canonical_bytes()).hexdigest(),
        "selected_decision_id": selected_decision_id,
        "role": role,
        "member_index": member_index,
        "facet": facet,
        "facts": facts,
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "identity": identity + ":" + facet,
        "head": "record:" + fingerprint,
        "fingerprint": fingerprint,
    }


@pytest.mark.asyncio
async def test_v2_seal_does_not_currently_persist_an_authenticated_pe_closure(
    tmp_path: Path,
) -> None:
    """The durable sibling anchor is authenticated; the E journal remains empty."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            original, selected_seal = await _seal_v3_prepare_as_v2(runtime)
            decision_id, raw, decision = _selected_seal_decision(runtime, selected_seal)
            native = H1V2RecoveryNativeSource(runtime).select(
                original_identity=original.identity,
                original_fingerprint=original.original_driver_command_fingerprint(),
                selected_seal=selected_seal,
            )
            assert native.seal.decision_id == decision_id
            assert native.seal.decision_fingerprint == hashlib.sha256(raw).hexdigest()
            anchor_bytes = decision["h1_preseal_pe_anchor"]
            assert isinstance(anchor_bytes, str)
            assert decode_h1_preseal_pe_anchor_record(anchor_bytes.encode()).canonical_bytes() == (
                anchor_bytes.encode()
            )
            retained = RetainedExecutionCompleteSealV2.model_validate_json(
                decision["execution_complete_seal"]
            )
            envelope = ExecutionCompleteSealPhysicalEnvelopeV2.model_validate_json(
                decision["execution_complete_seal_envelope"]
            )
            assert envelope == build_complete_seal_envelope(retained)
            assert len(envelope.records) == 3
            journal = runtime._h1_delivery_evidence_journal
            assert journal is not None
            assert journal._entries() == ()


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "no installed historical P/E source reopens the authenticated anchor into a complete "
        "historical completion request"
    ),
)
async def test_historical_pe_source_reconstructs_exact_request_after_restart(
    tmp_path: Path,
) -> None:
    """The historical reader, not a ROOT or current worker, supplies the complete request."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            original, selected_seal = await _seal_v3_prepare_as_v2(runtime)
            decision_id, _raw, decision = _selected_seal_decision(runtime, selected_seal)
            retained_anchor = decision["h1_preseal_pe_anchor"]
            assert isinstance(retained_anchor, str)
            anchor = decode_h1_preseal_pe_anchor_record(retained_anchor.encode())
            assert runtime._h1_postseal_recovery_journal.scan().states_by_root == ()

        source_module = importlib.import_module(
            "chiplog.composition.h1_recovery_historical_pe_source"
        )
        source_type = source_module.H1RecoveryHistoricalPESource
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as reopened:
            assert reopened._h1_postseal_recovery_journal.scan().states_by_root == ()
            source = source_type(reopened)
            issued = source.issue_completion_projection(
                original_identity=original.identity,
                original_fingerprint=original.original_driver_command_fingerprint(),
                selected_seal=selected_seal,
            )
            request = source.reconstruct_completion_input(issued)
            assert request.run.run_id == request.fence.run_id
            assert request.run.head == request.fence.run_head
            assert request.delivery.captured_response == request.exact_captured_response
            assert request.delivery.worker_fence.fingerprint
            anchor_data = anchor.as_dict()
            members = anchor_data["e_members"]
            assert isinstance(members, list)
            assert len(request.delivery.history) == len(members)
            for index, member in enumerate(members):
                assert isinstance(member, dict)
                envelope = request.delivery.history[index]
                assert envelope.provenance.model_dump(mode="json") == _historical_projection(
                    anchor,
                    selected_decision_id=decision_id,
                    role="E_MEMBER",
                    member_index=index,
                    facet="provenance",
                    facts=member["provenance"],
                )
                assert envelope.disclosure.model_dump(mode="json") == _historical_projection(
                    anchor,
                    selected_decision_id=decision_id,
                    role="E_MEMBER",
                    member_index=index,
                    facet="disclosure",
                    facts=member["disclosure"],
                )
                assert len(envelope.narrowing) == 1
                assert envelope.narrowing[0].model_dump(mode="json") == _historical_projection(
                    anchor,
                    selected_decision_id=decision_id,
                    role="E_MEMBER",
                    member_index=index,
                    facet="narrowing",
                    facts=member["narrowing"],
                )
            assert request.delivery.worker_fence.model_dump(mode="json") == _historical_projection(
                anchor,
                selected_decision_id=decision_id,
                role="E_WORKER",
                member_index=None,
                facet="worker",
                facts=anchor_data["e_worker"],
            )
            canonical = request.canonical_bytes()

        async with open_installed_h1_runtime(
            launch, resources=_resources(tmp_path, "-clean")
        ) as clean:
            source = source_type(clean)
            issued = source.issue_completion_projection(
                original_identity=original.identity,
                original_fingerprint=original.original_driver_command_fingerprint(),
                selected_seal=selected_seal,
            )
            assert source.reconstruct_completion_input(issued).canonical_bytes() == canonical


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="no installed historical P/E source issues runtime-owned recovery receipts",
)
async def test_historical_pe_source_rejects_a_copied_issued_receipt(tmp_path: Path) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            original, selected_seal = await _seal_v3_prepare_as_v2(runtime)
        source_module = importlib.import_module(
            "chiplog.composition.h1_recovery_historical_pe_source"
        )
        source_type = source_module.H1RecoveryHistoricalPESource
        error_type = source_module.H1RecoveryHistoricalPESourceError
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as reopened:
            source = source_type(reopened)
            issued = source.issue_completion_projection(
                original_identity=original.identity,
                original_fingerprint=original.original_driver_command_fingerprint(),
                selected_seal=selected_seal,
            )
            with pytest.raises(error_type, match="issuer-owned"):
                source.reconstruct_completion_input(copy.copy(issued))


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="no installed historical P/E source binds recovery receipts to one runtime",
)
async def test_historical_pe_source_rejects_a_receipt_from_a_closed_runtime(tmp_path: Path) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            original, selected_seal = await _seal_v3_prepare_as_v2(runtime)
        source_module = importlib.import_module(
            "chiplog.composition.h1_recovery_historical_pe_source"
        )
        source_type = source_module.H1RecoveryHistoricalPESource
        error_type = source_module.H1RecoveryHistoricalPESourceError
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as issuer:
            source = source_type(issuer)
            issued = source.issue_completion_projection(
                original_identity=original.identity,
                original_fingerprint=original.original_driver_command_fingerprint(),
                selected_seal=selected_seal,
            )

        async with open_installed_h1_runtime(
            launch, resources=_resources(tmp_path, "-later")
        ) as later:
            later_source = source_type(later)
            with pytest.raises(error_type, match="issuer-owned"):
                later_source.reconstruct_completion_input(issued)


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="no installed historical P/E source reconstructs a selected anchored request",
)
async def test_historical_pe_source_ignores_a_later_unrelated_v2_publication(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            original, selected_seal = await _seal_v3_prepare_as_v2(runtime)
        source_module = importlib.import_module(
            "chiplog.composition.h1_recovery_historical_pe_source"
        )
        source_type = source_module.H1RecoveryHistoricalPESource
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as initial:
            source = source_type(initial)
            issued = source.issue_completion_projection(
                original_identity=original.identity,
                original_fingerprint=original.original_driver_command_fingerprint(),
                selected_seal=selected_seal,
            )
            canonical = source.reconstruct_completion_input(issued).canonical_bytes()

        async with open_installed_h1_runtime(
            launch, resources=_resources(tmp_path, "-later")
        ) as later:
            unrelated, _unrelated_seal = await _seal_v3_prepare_as_v2(later)
            assert unrelated.identity != original.identity
            later_source = source_type(later)
            replayed = later_source.issue_completion_projection(
                original_identity=original.identity,
                original_fingerprint=original.original_driver_command_fingerprint(),
                selected_seal=selected_seal,
            )
            assert (
                later_source.reconstruct_completion_input(replayed).canonical_bytes() == canonical
            )


__all__ = []
