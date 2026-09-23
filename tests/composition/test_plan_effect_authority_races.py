"""Real publication races and private issuance mechanics, not hostile-Python isolation."""

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop.application import AgentLoop
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected, ProposalDisplay
from chiplog.composition.r14 import open_r14_loop
from chiplog.composition.r14_runtime import R14PlanningRuntime
from chiplog.composition.r16_effects import R16EffectsProducer
from chiplog.composition.r16_effects_authority import R16PlanEffectAuthority
from chiplog.platform._owner_publication_contracts import (
    BrokerPublicationResult,
    JournalSelectedPublication,
    PlanEffectBatch,
    PublicationRejected,
    RegisteredPublication,
)
from chiplog.platform._sqlite import PhysicalPublicationCommand, PublicationResult
from chiplog.platform.authority_gate import AuthorityGateError
from chiplog.platform.broker import PublicPortCall, PublicPortResult
from chiplog.platform.owner_publications import (
    BrokerPublicationCoordinator,
    PreparedOwnerPublication,
)
from tests.composition.test_effects_authenticated_cut import _response


async def _display(loop: AgentLoop) -> tuple[R14PlanningRuntime, ProposalDisplay]:
    created = await loop.create("r", "Propose my action", BudgetPolicy())
    active = await loop.activate("r", created.head)
    await loop.step("r", active.head)
    runtime = loop._planning
    assert isinstance(runtime, R14PlanningRuntime)
    display = await R16EffectsProducer(runtime).display_effect("r/turn/1/proposal/effect")
    return runtime, display


def _assert_no_plan_effect(runtime: R14PlanningRuntime) -> None:
    assert all(
        decision.prepared.request.operation != "effects.publish_plan_effect"
        for decision in runtime._owner_decisions().snapshot().decisions
    )
    with sqlite3.connect(runtime._database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM records WHERE owner IN ('planning', 'effects')"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM publications WHERE operation_kind='effects.publish_plan_effect'"
        ).fetchone() == (0,)


@pytest.mark.parametrize("mutate", (False, True))
async def test_real_effects_ipc_unlocked_and_run_mutation_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate: bool
) -> None:
    async with open_r14_loop(tmp_path / "ipc.sqlite", responses=(_response(),)) as loop:
        runtime, display = await _display(loop)
        broker = runtime._supervisor.runtime()
        original = broker.call
        observed: list[tuple[PublicPortCall, PublicPortResult]] = []

        async def call(request: PublicPortCall) -> PublicPortResult:
            if request.operation_id == "effects.prepare_transition":
                with pytest.raises(AuthorityGateError):
                    runtime._authority_gate().require_held()
            response = await original(request)
            if request.operation_id == "effects.prepare_transition":
                observed.append((request, response))
                if mutate:
                    await loop.create("interleaving", "Actual canonical Run", BudgetPolicy())
            return response

        monkeypatch.setattr(broker, "call", call)
        if mutate:
            with pytest.raises(LoopRejected, match="changed"):
                await runtime.publish_effect(
                    "hermetic-ingress",
                    display.display_id,
                    display.display_digest,
                    display.adoption_act_id,
                )
            _assert_no_plan_effect(runtime)
        else:
            result = await runtime.publish_effect(
                "hermetic-ingress",
                display.display_id,
                display.display_digest,
                display.adoption_act_id,
            )
            assert isinstance(result, JournalSelectedPublication)
            assert result.kind == "COMMITTED"
            assert {record.owner for record in result.complete_records} == {"planning", "effects"}
        assert len(observed) == 1
        assert observed[0][1].request_id == observed[0][0].request_id


@pytest.mark.parametrize("mutation", ("storage", "owner_generation", "credential", "drain"))
async def test_writer_guard_rechecks_independent_sources_before_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    async with open_r14_loop(tmp_path / "writer.sqlite", responses=(_response(),)) as loop:
        runtime, display = await _display(loop)
        original = runtime._appender.submit
        mutated = False

        async def submit(command: PhysicalPublicationCommand) -> PublicationResult:
            nonlocal mutated
            if command.operation_kind == "effects.publish_plan_effect" and not mutated:
                mutated = True
                if mutation == "storage":
                    # Workspace admission rejects instance dispatch overrides. Restore
                    # canonical dispatch during the independent canonical mutation.
                    monkeypatch.delattr(runtime._appender, "submit")
                    try:
                        await loop.create("writer-race", "Actual canonical storage", BudgetPolicy())
                    finally:
                        monkeypatch.setattr(runtime._appender, "submit", submit)
                else:
                    with runtime._authority_gate().hold():
                        ledger = runtime._read_ledger
                        state = ledger.current_state(runtime._tenant_id)
                        if mutation == "owner_generation":
                            ledger.invalidate_owner_generation(
                                runtime._tenant_id, state.fingerprint(), "next-generation"
                            )
                        elif mutation == "credential":
                            ledger.invalidate_credential_session(
                                runtime._tenant_id, state.fingerprint(), "next-credential"
                            )
                        else:
                            ledger.start_owner_drain(runtime._tenant_id, state.fingerprint())
            return await original(command)

        monkeypatch.setattr(runtime._appender, "submit", submit)
        result = await runtime.publish_effect(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )
        assert mutated
        assert isinstance(result, PublicationRejected), result
        assert result.kind in {"STALE", "CONFLICT", "DENIED"}
        _assert_no_plan_effect(runtime)


@pytest.mark.parametrize(
    "mutation", ("invocation_id", "invocation_fingerprint", "records", "batch")
)
async def test_changed_actual_issued_batch_has_no_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    async with open_r14_loop(tmp_path / "batch.sqlite", responses=(_response(),)) as loop:
        runtime, display = await _display(loop)
        original = BrokerPublicationCoordinator.commit
        intercepted: list[PlanEffectBatch] = []

        async def commit(
            coordinator: BrokerPublicationCoordinator, request: RegisteredPublication
        ) -> BrokerPublicationResult:
            assert isinstance(request, PlanEffectBatch)
            intercepted.append(request)
            if mutation.startswith("invocation"):
                proof = request.authentication.invocation.model_copy(
                    update={
                        "issuance_id"
                        if mutation == "invocation_id"
                        else "issuance_fingerprint": "0" * 64
                    }
                )
                changed = request.model_copy(
                    update={
                        "authentication": request.authentication.model_copy(
                            update={"invocation": proof}
                        )
                    }
                )
            elif mutation == "records":
                changed = request.model_copy(
                    update={"complete_records": request.complete_records[:-1]}
                )
            else:
                changed = request.model_copy(update={"complete_batch_fingerprint": "0" * 64})
            return await original(coordinator, changed)

        monkeypatch.setattr(BrokerPublicationCoordinator, "commit", commit)
        result = await runtime.publish_effect(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )
        assert len(intercepted) == 1
        assert isinstance(result, PublicationRejected), result
        assert result.kind == "DENIED"
        _assert_no_plan_effect(runtime)


async def test_changed_actual_prepared_issuance_is_denied_by_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_r14_loop(tmp_path / "issuance.sqlite", responses=(_response(),)) as loop:
        runtime, display = await _display(loop)
        original = R16PlanEffectAuthority.prepare
        issued: list[PreparedOwnerPublication] = []

        async def prepare(
            authority: R16PlanEffectAuthority, request: RegisteredPublication
        ) -> PreparedOwnerPublication | PublicationRejected:
            actual = await original(authority, request)
            assert isinstance(actual, PreparedOwnerPublication)
            issued.append(actual)
            return replace(actual, issuance_id="0" * 64)

        monkeypatch.setattr(R16PlanEffectAuthority, "prepare", prepare)
        result = await runtime.publish_effect(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )
        assert len(issued) == 1
        assert isinstance(result, PublicationRejected), result
        assert result.kind == "DENIED"
        _assert_no_plan_effect(runtime)
