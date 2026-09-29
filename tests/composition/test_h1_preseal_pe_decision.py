"""Mounted RED witnesses for P/E anchoring at the V2 DECIDED writer.

These tests use the installed V3 Prepare -> V2 seal route.  They deliberately
do not manufacture a P/E capture, a selected seal, or an owner mutation DTO.
The missing capture-and-bind owner is expected to make the prospective
contracts fail until it is mounted at the real decision boundary.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition import h1_preseal
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_installed_h1_runtime,
)
from chiplog.composition.common_execution_driver_contracts import SelectedExecutionReceiptV1
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_preseal_pe_anchor_records import (
    H1PresealPEAnchorRecordV1,
    decode_h1_preseal_pe_anchor_record,
)
from chiplog.composition.h1_preseal_pe_decision import H1PresealPEDecisionError
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_candidate
from chiplog.composition.h1_v2_recovery_native_source import H1V2RecoveryNativeSource
from chiplog.composition.r14_execution_complete_seal_records import (
    ExecutionCompleteSealPhysicalEnvelopeV2,
    RetainedExecutionCompleteSealV2,
    build_complete_seal_envelope,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.authority_gate import AuthorityGate, _state
from tests.support.h1_cli_execution import _admit
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


def _text(decision: dict[str, object], field: str) -> str:
    value = decision.get(field)
    if not isinstance(value, str):
        raise AssertionError(f"DECIDED entry lacks canonical {field}")
    return value


def _with_deadline(observed: Any, deadline_ns: int) -> Any:
    budget = observed.request.budget.model_copy(
        update={"absolute_deadline_ns": deadline_ns}
    )
    return replace(
        observed,
        request=observed.request.model_copy(update={"budget": budget}),
    )


async def _prepared_runtime_cut(runtime: CommonCliExecutionRuntime) -> tuple[Any, str, str]:
    request = await _admit(runtime)
    initial = await runtime.drive_input(request)
    assert isinstance(initial, SelectedExecutionReceiptV1)
    run_id = initial.stable_run_lineage_id
    runtime._execution_model._responses = (
        DeliveryCompletion(
            tenant=TENANT,
            run_id=run_id,
            turn_id=run_id + "/turn/1",
            deliveries=(ProposedDelivery(payload=(Commentary(text="preseal P/E decision"),)),),
        ).canonical_bytes(),
    )
    started = await runtime.begin_execution(
        "hermetic-ingress", run_id, initial.selected_run_head.head
    )
    captured = await runtime.capture_execution("hermetic-ingress", run_id, started.head)
    selected = select_h1_v3_prepare_for_candidate(runtime, captured, expected_head=captured.head)
    assert selected.decision_id
    return request, run_id, captured.head


def _selected_decision(
    runtime: CommonCliExecutionRuntime, operation_id: str
) -> tuple[str, bytes, dict[str, object]]:
    decision_id, _previous, raw = next(
        entry
        for entry in runtime._loop_decisions().entries()
        if json.loads(entry[2]).get("operation_id") == operation_id
    )
    decoded = json.loads(raw)
    assert isinstance(decoded, dict)
    return decision_id, raw, decoded


def _physical_publication_snapshot(database: Path) -> tuple[tuple[object, ...], tuple[object, ...]]:
    with sqlite3.connect(database) as connection:
        publications = tuple(
            connection.execute(
                "SELECT * FROM publications ORDER BY commit_sequence, rowid"
            ).fetchall()
        )
        records = tuple(
            connection.execute("SELECT * FROM records ORDER BY commit_sequence, rowid").fetchall()
        )
    return publications, records


def _assert_anchor_is_sibling_and_physical_members_stay_native(
    runtime: CommonCliExecutionRuntime,
    request: Any,
    sealed: Any,
    decision_id: str,
    raw: bytes,
    decision: dict[str, object],
) -> H1PresealPEAnchorRecordV1:
    retained = RetainedExecutionCompleteSealV2.model_validate_json(
        _text(decision, "execution_complete_seal")
    )
    envelope = ExecutionCompleteSealPhysicalEnvelopeV2.model_validate_json(
        _text(decision, "execution_complete_seal_envelope")
    )
    assert envelope == build_complete_seal_envelope(retained)
    run = retained.exchange.proposal.sealed_run
    response_seal = retained.exchange.proposal.fan_out.response_seal
    registry_digest = hashlib.sha256(
        base64.b64decode(retained.canonical_registry_base64, validate=True)
    ).hexdigest()
    # Anchoring cannot turn its residual evidence into a fourth physical record.
    assert tuple(record.record_id for record in envelope.records) == (
        run.head,
        "record:" + response_seal.digest(),
        "recovery-frontier-registry:" + run.head + ":" + registry_digest,
    )
    assert len(envelope.records) == 3

    anchor = decode_h1_preseal_pe_anchor_record(
        _text(decision, "h1_preseal_pe_anchor").encode()
    )
    native = H1V2RecoveryNativeSource(runtime).select(
        original_identity=request.identity,
        original_fingerprint=request.original_driver_command_fingerprint(),
        selected_seal=CallSubjectHead(
            subject_id=response_seal.response_seal_id,
            revision=Present(
                head="record:" + response_seal.digest(), fingerprint=response_seal.digest()
            ),
        ),
    )
    assert native.seal.decision_id == decision_id
    assert native.seal.decision_fingerprint == hashlib.sha256(raw).hexdigest()
    assert sealed.head == decision["operation_id"]
    return anchor


@pytest.mark.asyncio
async def test_installed_v3_prepare_v2_decided_entry_authenticates_pe_anchor_without_extra_member(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, run_id, captured_head = await _prepared_runtime_cut(runtime)
            sealed = await runtime.seal_execution_complete(
                "hermetic-ingress", run_id, captured_head, profile="H1_V2"
            )
            decision_id, raw, decision = _selected_decision(runtime, sealed.head)
            anchor = _assert_anchor_is_sibling_and_physical_members_stay_native(
                runtime, request, sealed, decision_id, raw, decision
            )
            assert anchor.canonical_bytes() == _text(decision, "h1_preseal_pe_anchor").encode()


@pytest.mark.asyncio
async def test_v2_preseal_owner_change_after_capture_rejects_before_decided_or_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The installed owner rejects an E worker change between capture and DECIDED."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            _request, run_id, captured_head = await _prepared_runtime_cut(runtime)
            owner = cast(Any, runtime)._h1_preseal_pe_decision_owner
            owner_type = type(owner)
            original_capture = owner_type.capture
            original_bind = owner_type.recheck_and_bind
            worker_owner = runtime._h1_installed_worker_evidence_owner
            original_lifetime = worker_owner._lifetime_id
            calls = {"capture": 0, "bind": 0}
            before_decisions = runtime._loop_decisions().entries()
            before_pending = runtime._pending()
            evidence_journal = runtime._h1_delivery_evidence_journal
            assert evidence_journal is not None
            before_evidence = evidence_journal._entries()
            before_physical = _physical_publication_snapshot(runtime._database)
            before_commitment = runtime._commitment_journal.load(runtime._tenant_id)

            async def captured_then_changed(owner: object, preflight: object) -> object:
                calls["capture"] += 1
                receipt = await original_capture(owner, preflight)
                worker_owner._lifetime_id = "changed-after-preseal-capture"
                return receipt

            def traced_bind(
                owner: object,
                receipt: object,
                preflight: object,
                command: object,
                retained_v2: object,
                owner_asof: object,
            ) -> object:
                calls["bind"] += 1
                return original_bind(owner, receipt, preflight, command, retained_v2, owner_asof)

            monkeypatch.setattr(owner_type, "capture", captured_then_changed)
            monkeypatch.setattr(owner_type, "recheck_and_bind", traced_bind)
            try:
                with pytest.raises(
                    LoopRejected, match="H1 V2 P/E preseal anchor is unproven"
                ) as rejected:
                    await runtime.seal_execution_complete(
                        "hermetic-ingress", run_id, captured_head, profile="H1_V2"
                    )
            finally:
                worker_owner._lifetime_id = original_lifetime

            assert isinstance(rejected.value.__cause__, H1PresealPEDecisionError)
            assert str(rejected.value.__cause__) == "H1 preseal P/E source changed after capture"
            assert calls == {"capture": 1, "bind": 1}
            assert runtime._loop_decisions().entries() == before_decisions
            assert runtime._pending() == before_pending
            assert evidence_journal._entries() == before_evidence
            assert _physical_publication_snapshot(runtime._database) == before_physical
            assert runtime._commitment_journal.load(runtime._tenant_id) == before_commitment


@pytest.mark.asyncio
async def test_preflight_consumption_and_decision_append_share_one_authority_gate_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one-use native receipt survives guard and is consumed only in DECIDED."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    generations = 0
    original_hold = AuthorityGate.hold
    original_recheck = h1_preseal.recheck_h1_v2_seal
    observed: dict[str, int] = {}

    @contextmanager
    def traced_hold(gate: AuthorityGate) -> Iterator[None]:
        nonlocal generations
        outermost = _state(gate.path).depth == 0
        with original_hold(gate):
            if outermost:
                generations += 1
            yield

    def traced_recheck(runtime: CommonCliExecutionRuntime, preflight: object) -> bool:
        observed["consume"] = generations
        return bool(original_recheck(runtime, cast(Any, preflight)))

    monkeypatch.setattr(AuthorityGate, "hold", traced_hold)
    monkeypatch.setattr(h1_preseal, "recheck_h1_v2_seal", traced_recheck)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            original_append = runtime._append_decision

            def traced_append(decision: dict[str, object]) -> None:
                if decision.get("kind") == "DECIDED":
                    observed["append"] = generations
                original_append(decision)

            monkeypatch.setattr(runtime, "_append_decision", traced_append)
            _request, run_id, captured_head = await _prepared_runtime_cut(runtime)
            await runtime.seal_execution_complete(
                "hermetic-ingress", run_id, captured_head, profile="H1_V2"
            )
    assert observed["consume"] == observed["append"]


@pytest.mark.asyncio
async def test_v2_capture_can_expire_its_preseal_observation_before_fresh_owner_fanout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A same-actor reauth authorizes the still-unbuilt fanout request after capture."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, run_id, captured_head = await _prepared_runtime_cut(runtime)
            original_actor = runtime._execution_actor
            owner_type = type(cast(Any, runtime)._h1_preseal_pe_decision_owner)
            original_capture = owner_type.capture
            observed: list[Any] = []
            events: list[str] = []

            async def traced_actor(peer: str) -> Any:
                current = await original_actor(peer)
                observed.append(current)
                events.append("actor:" + str(len(observed)))
                return current

            async def delayed_capture(owner: object, preflight: object) -> object:
                events.append("capture:start")
                await asyncio.sleep(5.1)
                receipt = await original_capture(owner, preflight)
                events.append("capture:end")
                return receipt

            monkeypatch.setattr(runtime, "_execution_actor", traced_actor)
            monkeypatch.setattr(owner_type, "capture", delayed_capture)
            sealed = await runtime.seal_execution_complete(
                "hermetic-ingress", run_id, captured_head, profile="H1_V2"
            )
            decision_id, raw, decision = _selected_decision(runtime, sealed.head)
            anchor = _assert_anchor_is_sibling_and_physical_members_stay_native(
                runtime, request, sealed, decision_id, raw, decision
            )
            assert anchor.canonical_bytes() == _text(decision, "h1_preseal_pe_anchor").encode()
            assert len(observed) >= 2
            assert observed[0].request.budget.absolute_deadline_ns <= time.monotonic_ns()
            assert observed[1] is not observed[0]
            assert events.index("actor:1") < events.index("capture:start")
            assert events.index("capture:end") < events.index("actor:2")


@pytest.mark.asyncio
async def test_v2_owner_fanout_rejects_its_own_expired_fresh_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh actor observation does not extend the deadline copied into its fanout call."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            _request, run_id, captured_head = await _prepared_runtime_cut(runtime)
            original_actor = runtime._execution_actor
            calls = 0

            async def expiring_refreshed_actor(peer: str) -> Any:
                nonlocal calls
                calls += 1
                observed = await original_actor(peer)
                if calls == 2:
                    return _with_deadline(observed, time.monotonic_ns() + 1)
                return observed

            monkeypatch.setattr(runtime, "_execution_actor", expiring_refreshed_actor)
            with pytest.raises(LoopRejected):
                await runtime.seal_execution_complete(
                    "hermetic-ingress", run_id, captured_head, profile="H1_V2"
                )
            assert calls >= 2
            assert runtime._pending() == ()


@pytest.mark.asyncio
async def test_v2_preseal_route_refuses_to_issue_without_installed_owner(tmp_path: Path) -> None:
    """An unmounted route denies before DECIDED rather than accepting a caller substitute."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            _request, run_id, captured_head = await _prepared_runtime_cut(runtime)
            delattr(runtime, "_h1_preseal_pe_decision_owner")
            with pytest.raises(LoopRejected, match="decision owner is unavailable"):
                await runtime.seal_execution_complete(
                    "hermetic-ingress", run_id, captured_head, profile="H1_V2"
                )
            assert runtime._pending() == ()


@pytest.mark.asyncio
async def test_h1_v3_checkpoint_route_never_captures_or_serializes_v2_pe_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The anchor owner is unavailable to the H1_V3 checkpoint-only seal route."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            owner = cast(Any, runtime)._h1_preseal_pe_decision_owner

            async def forbidden_capture(*_args: object) -> object:
                pytest.fail("H1_V3 checkpoint route captured a V2 P/E anchor")

            monkeypatch.setattr(owner, "capture", forbidden_capture)
            _request, run_id, captured_head = await _prepared_runtime_cut(runtime)
            sealed = await runtime.seal_execution_complete(
                "hermetic-ingress", run_id, captured_head, profile="H1_V3"
            )
            _decision_id, _raw, decision = _selected_decision(runtime, sealed.head)
            assert "h1_preseal_pe_anchor" not in decision


@pytest.mark.asyncio
async def test_crash_after_decided_before_sqlite_materialization_replays_same_anchor_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            _request, run_id, captured_head = await _prepared_runtime_cut(runtime)
            owner_type = type(cast(Any, runtime)._h1_preseal_pe_decision_owner)
            original_capture = owner_type.capture
            original_bind = owner_type.recheck_and_bind
            calls = {"capture": 0, "bind": 0}

            async def traced_capture(owner: object, preflight: object) -> object:
                calls["capture"] += 1
                return await original_capture(owner, preflight)

            def traced_bind(
                owner: object,
                receipt: object,
                preflight: object,
                command: object,
                retained_v2: object,
                owner_asof: object,
            ) -> object:
                calls["bind"] += 1
                return original_bind(owner, receipt, preflight, command, retained_v2, owner_asof)

            monkeypatch.setattr(owner_type, "capture", traced_capture)
            monkeypatch.setattr(owner_type, "recheck_and_bind", traced_bind)
            original_append = runtime._append_decision
            operation_ids: list[str] = []

            def crash_after_decision(value: object) -> None:
                original_append(value)
                if isinstance(value, dict) and value.get("kind") == "DECIDED":
                    operation_id = value.get("operation_id")
                    assert isinstance(operation_id, str)
                    operation_ids.append(operation_id)
                    raise RuntimeError("injected crash after DECIDED")

            monkeypatch.setattr(runtime, "_append_decision", crash_after_decision)
            with pytest.raises(RuntimeError, match="injected crash after DECIDED"):
                await runtime.seal_execution_complete(
                    "hermetic-ingress", run_id, captured_head, profile="H1_V2"
                )
            assert len(operation_ids) == 1
            decision_id, raw, decision = _selected_decision(runtime, operation_ids[0])
            anchor_bytes = _text(decision, "h1_preseal_pe_anchor").encode()
            assert (
                decode_h1_preseal_pe_anchor_record(anchor_bytes).canonical_bytes() == anchor_bytes
            )
            selected = runtime._publication(runtime._pending()[0])
            database = runtime._database
            assert calls == {"capture": 1, "bind": 1}

        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as reopened:
            reopened_id, reopened_raw, reopened_decision = _selected_decision(
                reopened, operation_ids[0]
            )
            assert reopened_id == decision_id
            assert reopened_raw == raw
            assert _text(reopened_decision, "h1_preseal_pe_anchor").encode() == anchor_bytes
            assert reopened._pending() == ()
            assert calls == {"capture": 1, "bind": 1}
        with sqlite3.connect(database) as connection:
            physical = connection.execute(
                "SELECT record_id, canonical_bytes FROM records "
                "WHERE commit_sequence=? ORDER BY rowid",
                (selected.expected_head + 1,),
            ).fetchall()
        assert physical == [(row.record_id, row.canonical_bytes) for row in selected.records]
