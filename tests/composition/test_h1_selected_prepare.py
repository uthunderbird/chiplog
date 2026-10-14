"""Contracts at the private selected-H1-Prepare boundary."""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_transition_contracts import PrepareExecutionRequest
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition import h1_selected_prepare as selected_prepare
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.h1_preseal_contracts import H1SelectedPrepare, H1SelectedSeal
from chiplog.composition.h1_selected_prepare import (
    H1SelectedPostSealPrepare,
    _decode_v3_prepare,
    _require_candidate,
    select_h1_v3_prepare_for_candidate,
    select_h1_v3_prepare_for_seal,
)
from chiplog.composition.r14_execution_complete_seal_records import RetainedExecutionCompleteSealV2
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_cli_execution import admit_complete_script, advance


@pytest.mark.parametrize("case", ["second-turn", "missing", "duplicate", "bad-earlier"])
def test_prepare_selection_is_turn_qualified_but_validates_the_whole_prefix(monkeypatch, case):
    workspace = SimpleNamespace(
        producer="projections",
        surface="workspace",
        content="context",
        model_dump_json=lambda: "{}",
    )
    started = SimpleNamespace(head="started")
    runs = [("started", b"started", object(), started, object())]
    retained_by_raw = {}
    for ordinal in (1, 2):
        turn_id = f"turn-{ordinal}"
        run = SimpleNamespace(head=f"prepared-{ordinal}")
        command = SimpleNamespace(expected_head=ordinal)
        retained = SimpleNamespace(
            request=PrepareExecutionRequest.model_construct(
                manifest=SimpleNamespace(turn_id=turn_id, members=(workspace,))
            ),
            proposal=SimpleNamespace(run=run),
            workspace_issuance=turn_id,
            command=command,
            canonical_bytes=lambda: b"retained",
        )
        raw = turn_id.encode()
        retained_by_raw[raw] = retained
        runs.append((turn_id, raw, command, run, object()))
    if case == "duplicate":
        runs.append(runs[-1])
    verified = []

    def verify(request, *_args):
        verified.append(request.manifest.turn_id)
        if case == "bad-earlier" and request.manifest.turn_id == "turn-1":
            raise ValueError("bad earlier Prepare")

    monkeypatch.setattr(selected_prepare, "_decode_v3_prepare", retained_by_raw.get)
    monkeypatch.setattr(selected_prepare, "transition_command", lambda value: value.command)
    issuance = SimpleNamespace(started_run_head="started", snapshot=SimpleNamespace(tenant_head=1))
    monkeypatch.setattr(
        selected_prepare,
        "R13Workspace",
        lambda _runtime: SimpleNamespace(
            open_h1_workspace_issuance=lambda: SimpleNamespace(load=lambda _ref: issuance)
        ),
    )
    runtime = SimpleNamespace(_verify_h1_prepare_issuance=verify)
    turn_id = "absent" if case == "missing" else "turn-2"
    if case == "second-turn":
        result = selected_prepare._select_prepare_from_runs(runtime, tuple(runs), turn_id=turn_id)
        assert result.retained is retained_by_raw[b"turn-2"]
        assert verified == ["turn-1", "turn-2"]
    else:
        reason = "bad earlier Prepare" if case == "bad-earlier" else "unique V3 Prepare"
        with pytest.raises(ValueError, match=reason):
            selected_prepare._select_prepare_from_runs(runtime, tuple(runs), turn_id=turn_id)


def test_selected_prepare_carrier_has_the_frozen_evidence_fields() -> None:
    assert tuple(field.name for field in fields(H1SelectedPrepare)) == (
        "retained",
        "retained_bytes",
        "decision_id",
        "decision_bytes",
        "publication",
        "physical_run",
        "started_run",
        "source_tenant_sequence",
        "workspace_member_bytes",
        "proposal_context_bytes",
        "issuance_ref",
    )


def test_selected_seal_carrier_keeps_an_authenticated_historical_cut_boundary() -> None:
    assert tuple(field.name for field in fields(H1SelectedSeal)) == (
        "command",
        "decision_id",
        "decision_bytes",
        "commit_sequence",
    )


def test_postseal_prepare_carrier_keeps_only_reselected_evidence() -> None:
    assert tuple(field.name for field in fields(H1SelectedPostSealPrepare)) == (
        "captured_run",
        "sealed_run",
        "prepare",
        "seal",
    )


def test_declared_malformed_selected_v3_prepare_is_not_silently_skipped() -> None:
    raw = (
        b'{"kind":"DECIDED","execution_transition":"{\\"kind\\":'
        b'\\"R14_SELECTED_EXECUTION_TRANSITION_V3\\"}"}'
    )

    with pytest.raises(ValueError, match="selected V3 Prepare is malformed"):
        _decode_v3_prepare(raw)


@pytest.mark.asyncio
async def test_unsealed_native_complete_response_is_a_valid_preseal_candidate() -> None:
    from tests.support.execution_fan_out import fixture

    captured = (await fixture(complete=True, canonical_response=True)).captured_run

    assert captured.turns[0].initialized_calls is None
    _require_candidate(captured, captured.head)


@pytest.mark.asyncio
async def test_continue_with_tool_calls_is_not_a_valid_preseal_candidate() -> None:
    from tests.support.execution_fan_out import fixture

    captured = (await fixture()).captured_run

    with pytest.raises(ValueError, match="Complete zero-call response"):
        _require_candidate(captured, captured.head)


@pytest.mark.asyncio
async def test_postseal_selector_reopens_real_selected_v2_seal_and_rejects_tampered_locator(
    tmp_path: Path,
) -> None:
    """The lower reader derives its predecessor and Prepare from the V2 seal itself."""
    database = tmp_path / "selected-postseal.sqlite"
    custody = tmp_path / "dispatch-custody"
    request, complete = await admit_complete_script(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = await runtime.drive_input(request)
        committed = await runtime.advance_execution(advance(cast(Any, initial), request))
        assert committed.kind == "SELECTED_EXECUTION_RECEIPT_V1"
        retained = RetainedExecutionCompleteSealV2.model_validate_json(
            next(
                entry["execution_complete_seal"]
                for _, _, raw in runtime._loop_decisions().entries()
                if "execution_complete_seal" in (entry := json.loads(raw))
            )
        )
        seal = retained.exchange.proposal.fan_out.response_seal
        locator = CallSubjectHead(
            subject_id=seal.response_seal_id,
            revision=Present(head="record:" + seal.digest(), fingerprint=seal.digest()),
        )

        selected = select_h1_v3_prepare_for_seal(runtime, selected_seal=locator)

        assert selected.sealed_run == retained.exchange.proposal.sealed_run
        assert selected.captured_run.head == selected.sealed_run.predecessor
        assert selected.prepare.retained.proposal.run.run_id == selected.captured_run.run_id
        with pytest.raises(ValueError, match="does not end at captured Run"):
            select_h1_v3_prepare_for_candidate(
                runtime, selected.captured_run, expected_head=selected.captured_run.head
            )
        with pytest.raises(ValueError, match="unique selected response seal"):
            select_h1_v3_prepare_for_seal(
                runtime,
                selected_seal=locator.model_copy(
                    update={"revision": Present(head=locator.revision.head, fingerprint="0" * 64)}
                ),
            )
