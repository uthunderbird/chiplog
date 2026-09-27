"""Prospective H1 V3 complete-seal selection carries a static checkpoint profile."""

import base64
import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallAuthorityObservation
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.execution_fan_out_contracts import (
    ExecutionCapturedFanOutProposal,
)
from chiplog.capabilities.agent_loop.execution_fan_out_preparation import (
    prepare_execution_captured_fan_out,
)
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.common_execution_driver_contracts import SelectedExecutionReceiptV1
from chiplog.composition.r14_execution_complete_seal_records import (
    ExecutionCompleteSealPhysicalEnvelopeV3,
    RetainedExecutionCompleteSealV3,
    build_complete_seal_envelope,
    complete_seal_physical_command,
    retained_execution_complete_seal,
)
from chiplog.composition.r14_execution_fanout_contracts import RetainedExecutionFanOutPreparation
from chiplog.composition.r14_execution_fanout_records import reference
from chiplog.composition.r14_execution_runtime import open_execution_runtime
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._sqlite import (
    CHECKPOINT_BUNDLE_PROFILE,
    PhysicalPublicationCommand,
    PublicationResult,
)
from chiplog.platform.broker import BrokerSession
from tests.support.execution_fan_out import bind_run, fixture
from tests.support.h1_cli_execution import admit_complete_script


async def _complete_evidence() -> RetainedExecutionFanOutPreparation:
    request = await fixture(complete=True)
    run = request.captured_run
    turn = run.turns[-1]
    attempt = turn.attempts[-1]
    worker = "1:0:callee"
    attempt = attempt.model_copy(
        update={
            "worker_session": worker,
            "manifest": attempt.manifest.model_copy(update={"worker_session": worker}),
        }
    )
    run = run.model_copy(
        update={
            "worker_session": worker,
            "turns": (turn.model_copy(update={"attempts": (attempt,)}),),
        }
    )
    request = bind_run(request, run)
    registry = request.tool_registry
    source = CallAuthorityObservation(
        source_id=registry.registry_id,
        family="TOOL_SCHEMA",
        source=reference(registry.registry_id, registry),
        generation="0",
        frontier="1",
        canonical_value_base64=base64.b64encode(registry.canonical_bytes()).decode(),
        observed_at_ns=1,
        valid_until_ns=20,
    )
    cut = request.request.cut.model_copy(
        update={
            "authority_registry": request.tool_registry_head,
            "sources": (source,),
            "fence": request.request.cut.fence.model_copy(update={"worker_session_id": worker}),
        }
    )
    request = request.model_copy(
        update={"request": request.request.model_copy(update={"cut": cut})}
    )
    proposal = prepare_execution_captured_fan_out(request)
    assert isinstance(proposal, ExecutionCapturedFanOutProposal)
    return RetainedExecutionFanOutPreparation(
        request=request,
        proposal=proposal,
        expected_snapshot_fingerprint="a" * 64,
        caller=BrokerSession(
            tenant_id="tenant",
            broker_epoch=1,
            generation_id="0",
            owner_id="broker",
            session_id="caller",
        ),
        callee=BrokerSession(
            tenant_id="tenant",
            broker_epoch=1,
            generation_id="0",
            owner_id="agent_loop",
            session_id="callee",
        ),
        request_id="request",
        deadline_ns=10,
    )


def _complete_response() -> bytes:
    complete = DeliveryCompletion(
        tenant="hermetic-tenant",
        run_id="run",
        turn_id="run/turn/1",
        deliveries=(ProposedDelivery(payload=(Commentary(text="Done"),)),),
    )
    return json.dumps(complete.model_dump(mode="json"), indent=2).encode() + b"\n"


async def test_h1_v3_retained_and_physical_carriers_round_trip_with_static_profile() -> None:
    evidence = await _complete_evidence()

    retained = retained_execution_complete_seal(evidence, profile="H1_V3")
    assert type(retained) is RetainedExecutionCompleteSealV3
    assert retained.profile == "H1_FULL_AUTHORITY_CHECKPOINT_V1"
    restored = RetainedExecutionCompleteSealV3.model_validate_json(retained.canonical_bytes())
    assert restored.canonical_bytes() == retained.canonical_bytes()

    envelope = build_complete_seal_envelope(restored)
    assert type(envelope) is ExecutionCompleteSealPhysicalEnvelopeV3
    assert envelope.profile == "H1_FULL_AUTHORITY_CHECKPOINT_V1"
    command = complete_seal_physical_command(envelope)
    physical = envelope.canonical_bytes()
    assert b"H1_FULL_AUTHORITY_CHECKPOINT_V1" in physical
    assert b"blob_sha256" not in physical
    assert b"authority_surface_digest" not in physical
    assert all("checkpoint" not in record.record_id for record in command.records)


async def test_h1_v3_uses_v2_frontier_without_changing_v2_bytes() -> None:
    evidence = await _complete_evidence()

    before = retained_execution_complete_seal(evidence, profile="H1_V2").canonical_bytes()
    v3 = retained_execution_complete_seal(evidence, profile="H1_V3")
    after = retained_execution_complete_seal(evidence, profile="H1_V2").canonical_bytes()

    assert before == after
    v2_registry = complete_seal_physical_command(
        build_complete_seal_envelope(retained_execution_complete_seal(evidence, profile="H1_V2"))
    ).records[-1]
    v3_registry = complete_seal_physical_command(build_complete_seal_envelope(v3)).records[-1]
    assert v3_registry.canonical_bytes == v2_registry.canonical_bytes
    assert v3_registry.fingerprint == hashlib.sha256(v3_registry.canonical_bytes).hexdigest()


async def test_h1_v3_rejects_plain_r14_before_checkpoint_or_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "plain-r14.sqlite"
    async with open_execution_runtime(database, responses=(_complete_response(),)) as runtime:
        activated: list[None] = []
        original_activate = runtime.activate_checkpoint_bundle

        def activate() -> None:
            activated.append(None)
            original_activate()

        monkeypatch.setattr(runtime, "activate_checkpoint_bundle", activate)
        created = await runtime.create_execution("hermetic-ingress", "run", "Plan", BudgetPolicy())
        started = await runtime.begin_execution("hermetic-ingress", "run", created.head)
        captured = await runtime.capture_execution("hermetic-ingress", "run", started.head)
        with pytest.raises(LoopRejected, match="workspace original verification is unavailable"):
            await runtime.seal_execution_complete(
                "hermetic-ingress", "run", captured.head, profile="H1_V3"
            )
        assert runtime._pending() == ()
        assert activated == []
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM publications WHERE operation_kind=?",
                ("agent_loop.execution-complete-seal.v1",),
            ).fetchone() == (0,)


async def test_h1_v3_stages_before_decision_and_binds_selected_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "v3.sqlite"
    custody = tmp_path / "dispatch-custody"
    request, complete = await admit_complete_script(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = await runtime.drive_input(request)
        assert isinstance(initial, SelectedExecutionReceiptV1)
        started = await runtime.begin_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, started.head
        )
        ordering: list[str] = []
        original_activate = runtime.activate_checkpoint_bundle
        original_stage = runtime._h1_checkpoint_staged
        original_append = runtime._append_decision

        def activate() -> None:
            ordering.append("activate")
            original_activate()

        def staged() -> None:
            ordering.append("stage")
            original_stage()

        def append(decision: dict[str, object]) -> None:
            if decision.get("kind") == "DECIDED":
                ordering.append("decision")
            original_append(decision)

        monkeypatch.setattr(runtime, "activate_checkpoint_bundle", activate)
        monkeypatch.setattr(runtime, "_h1_checkpoint_staged", staged)
        monkeypatch.setattr(runtime, "_append_decision", append)
        sealed = await runtime.seal_execution_complete(
            "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V3"
        )
        assert ordering == ["activate", "stage", "decision"]
        decisions = [json.loads(raw) for _, _, raw in runtime._loop_decisions().entries()]
        selected = [
            decision
            for decision in decisions
            if decision.get("kind") == "DECIDED" and decision.get("operation_id") == sealed.head
        ]
        assert len(selected) == 1
        assert [
            decision
            for decision in decisions
            if decision.get("kind") == "MATERIALIZED"
            and decision.get("operation_id") == sealed.head
        ]
        checkpoint = selected[0]["h1_historical_checkpoint"]
        assert isinstance(checkpoint, dict)
        assert checkpoint["tenant_id"] == runtime._tenant_id
        assert checkpoint["operation_id"] == sealed.head
        assert checkpoint["commit_sequence"] == selected[0]["expected_head"] + 1
        assert checkpoint["resulting"] == selected[0]["resulting"]
        assert checkpoint["reference"]["blob_sha256"] == selected[0]["resulting"]


async def test_h1_v3_crash_after_stage_leaves_orphan_without_decided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "v3-stage-crash.sqlite"
    custody = tmp_path / "dispatch-custody"
    request, complete = await admit_complete_script(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = await runtime.drive_input(request)
        assert isinstance(initial, SelectedExecutionReceiptV1)
        started = await runtime.begin_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, started.head
        )

        def crash_after_stage() -> None:
            raise RuntimeError("stage-only fault")

        monkeypatch.setattr(runtime, "_h1_checkpoint_staged", crash_after_stage)
        with pytest.raises(RuntimeError, match="stage-only fault"):
            await runtime.seal_execution_complete(
                "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V3"
            )
        assert runtime._pending() == ()
        checkpoint_root = database.with_suffix(database.suffix + ".authority-checkpoints")
        checkpoint_objects = checkpoint_root / "objects"
        assert tuple(checkpoint_objects.rglob("*"))
        with sqlite3.connect(database) as connection:
            assert connection.execute("PRAGMA user_version").fetchone() == (
                CHECKPOINT_BUNDLE_PROFILE,
            )
            assert connection.execute(
                "SELECT count(*) FROM publications WHERE operation_kind=?",
                ("agent_loop.execution-complete-seal.v1",),
            ).fetchone() == (0,)


async def test_h1_v3_crash_after_decided_before_commit_keeps_selected_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "v3-precommit-crash.sqlite"
    custody = tmp_path / "dispatch-custody"
    request, complete = await admit_complete_script(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = await runtime.drive_input(request)
        assert isinstance(initial, SelectedExecutionReceiptV1)
        started = await runtime.begin_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, started.head
        )
        submit = runtime._appender.submit

        async def crash_before_commit(command: PhysicalPublicationCommand) -> PublicationResult:
            return await submit(replace(command, fault="before_commit"))

        monkeypatch.setattr(runtime._appender, "submit", crash_before_commit)
        with pytest.raises(RuntimeError, match="before commit"):
            await runtime.seal_execution_complete(
                "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V3"
            )
        pending = runtime._pending()
        assert len(pending) == 1
        checkpoint = pending[0]["h1_historical_checkpoint"]
        assert isinstance(checkpoint, dict)
        assert checkpoint["reference"]["blob_sha256"] == pending[0]["resulting"]
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM publications WHERE operation_kind=?",
                ("agent_loop.execution-complete-seal.v1",),
            ).fetchone() == (0,)


async def test_h1_v2_complete_seal_leaves_checkpoint_bundle_unmarked(tmp_path: Path) -> None:
    database = tmp_path / "v2-unmarked.sqlite"
    custody = tmp_path / "dispatch-custody"
    request, complete = await admit_complete_script(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = await runtime.drive_input(request)
        assert isinstance(initial, SelectedExecutionReceiptV1)
        started = await runtime.begin_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, started.head
        )
        sealed = await runtime.seal_execution_complete(
            "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V2"
        )
        selected = [
            json.loads(raw)
            for _, _, raw in runtime._loop_decisions().entries()
            if json.loads(raw).get("kind") == "DECIDED"
            and json.loads(raw).get("operation_id") == sealed.head
        ]
        assert len(selected) == 1
        assert "h1_historical_checkpoint" not in selected[0]
        with sqlite3.connect(database) as connection:
            assert connection.execute("PRAGMA user_version").fetchone() == (0,)
