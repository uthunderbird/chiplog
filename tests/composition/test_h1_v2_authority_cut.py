"""Mounted RED witnesses for the versioned V2 post-write authority cut.

The witnesses use the installed V3 Prepare -> V2 seal path.  They do not
manufacture a checkpoint, an authority snapshot, or a recovery capability:
each positive assertion starts from the authenticated native DECIDED entry
and its independently reconstructed physical V2 command.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_installed_h1_runtime,
)
from chiplog.composition.common_execution_driver_contracts import (
    DriveInputRequestV1,
    SelectedExecutionReceiptV1,
)
from chiplog.composition.h1_first_path_sources import H1FirstPathSources
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_candidate
from chiplog.composition.h1_v2_authority_cut import resolve_h1_v2_authority_cut
from chiplog.composition.r14_execution_complete_seal_records import (
    ExecutionCompleteSealPhysicalEnvelopeV2,
    RetainedExecutionCompleteSealV2,
    build_complete_seal_envelope,
    complete_seal_physical_command,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._sqlite import PhysicalPublicationCommand
from chiplog.platform.authority_checkpoint import (
    AuthorityCheckpointRefV1,
    AuthorityCheckpointStore,
    AuthorityCheckpointVerificationError,
)
from tests.support.h1_cli_execution import _admit
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot

_CUT_FIELDS = {
    "kind",
    "version",
    "tenant_id",
    "operation_kind",
    "operation_id",
    "request_fingerprint",
    "expected_head",
    "commit_sequence",
    "selected_response_seal",
    "envelope_sha256",
    "database_binding",
    "predecessor",
    "resulting",
    "reference",
}


def _resources(tmp_path: Path, suffix: str = "") -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / f"dispatch-custody{suffix}"
    )


async def _seal_v3_prepare_as_v2(
    runtime: CommonCliExecutionRuntime,
) -> tuple[DriveInputRequestV1, CallSubjectHead, str]:
    """Create the only authority source used by these witnesses."""
    original = await _admit(runtime)
    initial = await runtime.drive_input(original)
    assert isinstance(initial, SelectedExecutionReceiptV1)
    run_id = initial.stable_run_lineage_id
    runtime._execution_model._responses = (
        DeliveryCompletion(
            tenant=TENANT,
            run_id=run_id,
            turn_id=run_id + "/turn/1",
            deliveries=(ProposedDelivery(payload=(Commentary(text="authority cut"),)),),
        ).canonical_bytes(),
    )
    started = await runtime.begin_execution(
        "hermetic-ingress", run_id, initial.selected_run_head.head
    )
    captured = await runtime.capture_execution("hermetic-ingress", run_id, started.head)
    selected_prepare = select_h1_v3_prepare_for_candidate(
        runtime, captured, expected_head=captured.head
    )
    assert selected_prepare.decision_id
    sealed = await runtime.seal_execution_complete(
        "hermetic-ingress", run_id, captured.head, profile="H1_V2"
    )
    decision = _selected_decision(runtime, sealed.head)
    retained = RetainedExecutionCompleteSealV2.model_validate_json(
        _text(decision[2], "execution_complete_seal")
    )
    response_seal = retained.exchange.proposal.fan_out.response_seal
    return (
        original,
        CallSubjectHead(
            subject_id=response_seal.response_seal_id,
            revision=Present(
                head="record:" + response_seal.digest(), fingerprint=response_seal.digest()
            ),
        ),
        sealed.head,
    )


def _selected_decision(
    runtime: CommonCliExecutionRuntime, operation_id: str
) -> tuple[str, bytes, dict[str, object]]:
    decision_id, _previous, raw = next(
        entry
        for entry in runtime._loop_decisions().entries()
        if json.loads(entry[2]).get("kind") == "DECIDED"
        and json.loads(entry[2]).get("operation_id") == operation_id
    )
    decoded = json.loads(raw)
    assert isinstance(decoded, dict)
    return decision_id, raw, decoded


def _text(entry: dict[str, object], field: str) -> str:
    value = entry.get(field)
    if not isinstance(value, str):
        raise AssertionError(f"selected DECIDED entry lacks canonical {field}")
    return value


def _cut(entry: dict[str, object]) -> dict[str, object]:
    value = entry.get("h1_v2_authority_cut_v1")
    if not isinstance(value, dict):
        raise AssertionError("selected V2 DECIDED entry lacks authority-cut sibling")
    return value


def _assert_authority_cut(
    runtime: CommonCliExecutionRuntime,
    selected_seal: CallSubjectHead,
    decision_id: str,
    raw: bytes,
    entry: dict[str, object],
) -> tuple[dict[str, object], bytes]:
    """Use native bytes and the blob store as independent, non-current oracles."""
    cut = _cut(entry)
    assert set(cut) == _CUT_FIELDS
    assert cut["kind"] == "H1_V2_AUTHORITY_CUT_V1"
    assert cut["version"] == 1

    retained = RetainedExecutionCompleteSealV2.model_validate_json(
        _text(entry, "execution_complete_seal")
    )
    envelope = ExecutionCompleteSealPhysicalEnvelopeV2.model_validate_json(
        _text(entry, "execution_complete_seal_envelope")
    )
    command = complete_seal_physical_command(envelope)
    assert envelope == build_complete_seal_envelope(retained)
    assert len(command.records) == 3
    assert cut["tenant_id"] == command.tenant_id
    assert cut["operation_kind"] == command.operation_kind
    assert cut["operation_id"] == command.idempotency_key
    assert cut["request_fingerprint"] == command.request_fingerprint
    assert cut["expected_head"] == command.expected_head
    assert cut["commit_sequence"] == command.expected_head + 1
    assert cut["envelope_sha256"] == hashlib.sha256(envelope.canonical_bytes()).hexdigest()
    assert cut["selected_response_seal"] == selected_seal.model_dump(mode="json")
    assert cut["predecessor"] == entry["predecessor"]
    assert cut["resulting"] == entry["resulting"]
    assert len(decision_id) == 64

    database = Path(runtime._database).resolve(strict=True)
    stat = database.stat()
    assert cut["database_binding"] == {
        "canonical_path": str(database),
        "st_dev": stat.st_dev,
        "st_ino": stat.st_ino,
    }
    reference = AuthorityCheckpointRefV1.model_validate(cut["reference"])
    assert reference.blob_sha256 == cut["resulting"]
    snapshot = runtime._h1_checkpoint_store().resolve_verified(
        reference,
        expected_resulting=str(cut["resulting"]),
        expected_surface_digest=reference.authority_surface_digest,
    )
    assert snapshot.preimage
    assert hashlib.sha256(snapshot.preimage).hexdigest() == reference.blob_sha256
    return cut, snapshot.preimage


@pytest.mark.asyncio
async def test_v3_prepare_to_v2_seal_binds_one_versioned_postimage_without_fourth_member(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            _original, selected_seal, seal_operation = await _seal_v3_prepare_as_v2(runtime)
            decision_id, raw, entry = _selected_decision(runtime, seal_operation)
            cut, _postimage = _assert_authority_cut(runtime, selected_seal, decision_id, raw, entry)
            assert cut["resulting"] == entry["resulting"]


def test_typed_v2_authority_cut_descriptor_rejects_missing_extension() -> None:
    """No enrolled old V2 fixture exists; do not manufacture one by stripping a journal.

    The production resolver receives an authenticated DECIDED mapping before
    it dispatches this strict descriptor.  This unit witness fixes the legacy
    boundary: an absent extension is not a version-zero authority cut.
    """
    with pytest.raises(ValueError, match="H1 V2 authority cut cannot be verified"):
        resolve_h1_v2_authority_cut(
            {},
            cast(AuthorityCheckpointStore, object()),
            ("unused", 0, 0),
            cast(PhysicalPublicationCommand, object()),
        )


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "historical V2 reader has no authority-cut dispatch to its selected immutable post-image"
    ),
)
async def test_historical_v2_full_cut_is_byte_identical_after_restart_and_later_v2_publication(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            original, selected_seal, seal_operation = await _seal_v3_prepare_as_v2(runtime)
            decision_id, _raw, entry = _selected_decision(runtime, seal_operation)
            _cut(entry)
            before = (
                H1FirstPathSources(runtime)
                ._read_v2_source(
                    H1FirstPathSources(runtime)._read_selected_cut(
                        original_identity=original.identity,
                        original_fingerprint=original.original_driver_command_fingerprint(),
                        selected_seal=selected_seal,
                        historical=True,
                    ),
                    historical=True,
                )
                .canonical_bytes()
            )
            descriptor_bytes = json.dumps(
                _cut(entry), sort_keys=True, separators=(",", ":")
            ).encode()
            assert len(decision_id) == 64

        async with open_installed_h1_runtime(
            launch, resources=_resources(tmp_path, "-later")
        ) as later:
            await _seal_v3_prepare_as_v2(later)
            replay = (
                H1FirstPathSources(later)
                ._read_v2_source(
                    H1FirstPathSources(later)._read_selected_cut(
                        original_identity=original.identity,
                        original_fingerprint=original.original_driver_command_fingerprint(),
                        selected_seal=selected_seal,
                        historical=True,
                    ),
                    historical=True,
                )
                .canonical_bytes()
            )
            _id, _raw, original_entry = _selected_decision(later, str(_cut(entry)["operation_id"]))
            assert (
                json.dumps(_cut(original_entry), sort_keys=True, separators=(",", ":")).encode()
                == descriptor_bytes
            )
            assert replay == before


@pytest.mark.asyncio
async def test_selected_authority_cut_blob_tamper_fails_closed_before_historical_reconstruction(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            original, selected_seal, seal_operation = await _seal_v3_prepare_as_v2(runtime)
            decision_id, raw, entry = _selected_decision(runtime, seal_operation)
            cut, _postimage = _assert_authority_cut(runtime, selected_seal, decision_id, raw, entry)
            reference = AuthorityCheckpointRefV1.model_validate(cut["reference"])
            store = runtime._h1_checkpoint_store()
            blob = store._blob_path(reference.blob_sha256)
            blob.write_bytes(b"tampered selected post-image")
            with pytest.raises(AuthorityCheckpointVerificationError):
                store.resolve_verified(
                    reference,
                    expected_resulting=reference.blob_sha256,
                    expected_surface_digest=reference.authority_surface_digest,
                )
            with pytest.raises(ValueError):
                H1FirstPathSources(runtime)._read_selected_cut(
                    original_identity=original.identity,
                    original_fingerprint=original.original_driver_command_fingerprint(),
                    selected_seal=selected_seal,
                    historical=True,
                )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate",
    (
        lambda entry: entry["h1_v2_authority_cut_v1"].__setitem__("resulting", "0" * 64),
        lambda entry: entry["h1_owner_asof"].__setitem__("owner_head", "0" * 64),
    ),
    ids=("descriptor", "owner-asof"),
)
async def test_authenticated_v2_authority_cut_or_owner_asof_tamper_holds_on_reopen(
    tmp_path: Path, mutate: Callable[[dict[str, object]], None]
) -> None:
    """Tamper the true journal body; this is never a substituted journal view."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            _original, _selected_seal, seal_operation = await _seal_v3_prepare_as_v2(runtime)
            _decision_id, raw, entry = _selected_decision(runtime, seal_operation)
            _cut(entry)
            changed = json.loads(raw)
            assert isinstance(changed, dict)
            mutate(changed)
            changed_raw = json.dumps(
                changed, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
            journal_path = Path(runtime._loop_decisions()._path)

        body = await asyncio.to_thread(journal_path.read_bytes)
        original_hex, changed_hex = raw.hex().encode(), changed_raw.hex().encode()
        assert body.count(original_hex) == 1
        await asyncio.to_thread(journal_path.write_bytes, body.replace(original_hex, changed_hex))
        with pytest.raises(RuntimeError, match="journal"):
            async with open_installed_h1_runtime(
                launch, resources=_resources(tmp_path, "-tampered")
            ):
                pass


@pytest.mark.asyncio
async def test_pending_decided_v2_reconciles_exact_selected_cut_without_restaging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    staged: list[bytes] = []
    original_stage = AuthorityCheckpointRefV1
    del original_stage  # The future writer owns staging; this test observes only durable outputs.
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request = await _admit(runtime)
            initial = await runtime.drive_input(request)
            assert isinstance(initial, SelectedExecutionReceiptV1)
            run_id = initial.stable_run_lineage_id
            runtime._execution_model._responses = (
                DeliveryCompletion(
                    tenant=TENANT,
                    run_id=run_id,
                    turn_id=run_id + "/turn/1",
                    deliveries=(
                        ProposedDelivery(payload=(Commentary(text="pending authority cut"),)),
                    ),
                ).canonical_bytes(),
            )
            started = await runtime.begin_execution(
                "hermetic-ingress", run_id, initial.selected_run_head.head
            )
            captured = await runtime.capture_execution("hermetic-ingress", run_id, started.head)
            select_h1_v3_prepare_for_candidate(runtime, captured, expected_head=captured.head)
            submit = runtime._appender.submit

            async def crash_before_commit(command: PhysicalPublicationCommand) -> object:
                return await submit(replace(command, fault="before_commit"))

            monkeypatch.setattr(runtime._appender, "submit", crash_before_commit)
            with pytest.raises(RuntimeError, match="before commit"):
                await runtime.seal_execution_complete(
                    "hermetic-ingress", run_id, captured.head, profile="H1_V2"
                )
            pending = runtime._pending()
            assert len(pending) == 1
            assert pending[0]["operation_kind"] == "agent_loop.execution-complete-seal.v1"
            operation_id = pending[0]["operation_id"]
            assert isinstance(operation_id, str)
            decision_id, raw, entry = _selected_decision(runtime, operation_id)
            descriptor = json.dumps(_cut(entry), sort_keys=True, separators=(",", ":")).encode()
            reference = AuthorityCheckpointRefV1.model_validate(_cut(entry)["reference"])
            staged.append(reference.blob_sha256.encode())

        async with open_installed_h1_runtime(
            launch, resources=_resources(tmp_path, "-restart")
        ) as reopened:
            replay_id, replay_raw, replay = _selected_decision(reopened, operation_id)
            assert replay_id == decision_id
            assert replay_raw == raw
            assert (
                json.dumps(_cut(replay), sort_keys=True, separators=(",", ":")).encode()
                == descriptor
            )
            assert (
                AuthorityCheckpointRefV1.model_validate(
                    _cut(replay)["reference"]
                ).blob_sha256.encode()
                in staged
            )
            assert reopened._pending() == ()
            with sqlite3.connect(reopened._database) as connection:
                assert connection.execute(
                    "SELECT count(*) FROM publications WHERE tenant_id=? AND operation_kind=?",
                    (TENANT, "agent_loop.execution-complete-seal.v1"),
                ).fetchone() == (1,)


__all__ = []
