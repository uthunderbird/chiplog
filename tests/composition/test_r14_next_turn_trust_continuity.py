"""Trust continuity fences for native next-Turn admission."""

import json
import time
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import chiplog.composition.r14_next_turn_publication as next_turn_publication
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.r14_execution_runtime import R14ExecutionRuntime
from chiplog.composition.r14_next_turn_publication import _require_trust_continuity
from chiplog.composition.r14_next_turn_records import NEXT_TURN_PUBLICATION
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.r7_trust_durability import FrozenTrustObservation


def test_next_turn_trust_continuity_requires_the_exact_observation() -> None:
    authenticated = FrozenTrustObservation(
        snapshot_bytes=b"snapshot-a",
        journal_head="trust-head-a",
        bundle_path="/db/trust",
        sources=(),
    )
    _require_trust_continuity(authenticated, authenticated, "unexpected change")

    changed_snapshot = replace(authenticated, snapshot_bytes=b"snapshot-b")
    with pytest.raises(LoopRejected, match="changed during owner exchange"):
        _require_trust_continuity(
            authenticated, changed_snapshot, "trust source changed during owner exchange"
        )

    changed_journal = replace(authenticated, journal_head="trust-head-b")
    with pytest.raises(LoopRejected, match="changed between actor authentication and source cut"):
        _require_trust_continuity(
            authenticated,
            changed_journal,
            "trust changed between actor authentication and source cut",
        )

    changed_materializer = replace(authenticated, sources=(("different-source", 1, 2),))
    with pytest.raises(LoopRejected):
        _require_trust_continuity(
            authenticated, changed_materializer, "trust materializer changed"
        )

    actor = SimpleNamespace(
        request=SimpleNamespace(callee=SimpleNamespace(generation_id="generation-a")),
        result=SimpleNamespace(
            reference_bytes=(
                b'{"tenant_id":"t","principal_id":"p","contour":"CLI",'
                b'"source_head":"s","peer_credential":"peer",'
                b'"credential_head":"credential","session_head":"session"}'
            )
        ),
    )
    rival_generation = SimpleNamespace(
        request=SimpleNamespace(callee=SimpleNamespace(generation_id="generation-b")),
        result=actor.result,
    )
    assert not R14ExecutionRuntime._same_execution_actor_identity(actor, rival_generation)


@pytest.mark.asyncio
async def test_next_turn_holds_if_trust_changes_between_authentication_and_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "next-turn-auth-cut.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(database, resources=resources) as runtime:
        authenticated = FrozenTrustObservation(b"snapshot-a", "head-a", str(database), ())
        changed = replace(authenticated, snapshot_bytes=b"snapshot-b")
        monkeypatch.setattr(
            runtime,
            "_execution_actor",
            lambda _: _resolved(SimpleNamespace(observation=authenticated)),
        )
        monkeypatch.setattr(runtime, "_check_execution_actor", lambda _: None)
        monkeypatch.setattr(runtime._trust, "capture_verified_observation", lambda: changed)
        monkeypatch.setattr(runtime, "_require_no_pending", lambda: None)

        class CutSource:
            def collect_held(self, current_runtime):
                return SimpleNamespace(tenant_id=current_runtime._tenant_id)

        monkeypatch.setattr(next_turn_publication, "source", lambda _: CutSource())
        monkeypatch.setattr(
            "chiplog.composition.r14_accepted_open_accounting_publication.hold_if_accepted_open",
            _resolved_false,
        )
        submit_called = False

        async def submit_must_not_run(_command):
            nonlocal submit_called
            submit_called = True
            raise AssertionError("changed trust must be rejected before writer admission")

        monkeypatch.setattr(runtime._appender, "submit", submit_must_not_run)
        with pytest.raises(LoopRejected, match="between next Turn actor authentication"):
            await runtime.start_next_execution_turn("run-unreached", "head-unreached")
        assert not submit_called
        assert runtime._loop_decisions().entries() == ()


@pytest.mark.asyncio
async def test_next_turn_holds_if_trust_changes_during_owner_exchange(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "next-turn-cut-owner.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody-owner"
    )
    async with open_common_cli_execution_runtime(database, resources=resources) as runtime:
        authenticated = FrozenTrustObservation(b"snapshot-a", "head-a", str(database), ())
        changed = replace(authenticated, snapshot_bytes=b"snapshot-b")
        actor = SimpleNamespace(observation=authenticated)
        refreshed = SimpleNamespace(observation=changed)
        run = ExecutionRunRecord.model_construct(
            run_id="run-owner-race",
            head="run-head",
            worker_session="historical-worker-session",
            turns=(SimpleNamespace(ordinal=1, response_seal=object()),),
        )
        cut = SimpleNamespace(
            tenant_id=runtime._tenant_id,
            complete_ordered_run_lineage=(run,),
            materialization_commitment=runtime._commitment_journal.load(runtime._tenant_id),
            tenant_commit_sequence=0,
        )
        monkeypatch.setattr(runtime, "_execution_actor", lambda _: _resolved(actor))
        monkeypatch.setattr(runtime, "_check_execution_actor", lambda _: None)
        monkeypatch.setattr(runtime, "_require_no_pending", lambda: None)
        monkeypatch.setattr(runtime, "_same_execution_actor_identity", lambda _old, _new: True)
        monkeypatch.setattr(runtime._trust, "capture_verified_observation", lambda: authenticated)
        monkeypatch.setattr(
            "chiplog.composition.r14_accepted_open_accounting_publication.hold_if_accepted_open",
            _resolved_false,
        )
        monkeypatch.setattr(
            next_turn_publication, "source", lambda _: SimpleNamespace(collect_held=lambda __: cut)
        )
        monkeypatch.setattr(
            next_turn_publication, "_selected_pair_held", lambda *_args: (None, None)
        )
        monkeypatch.setattr(next_turn_publication, "_turn_ref", lambda _turn: None)
        monkeypatch.setattr(
            next_turn_publication,
            "PrepareNextExecutionTurn",
            lambda **values: SimpleNamespace(**values),
        )

        async def exchange(*_args):
            return SimpleNamespace(), SimpleNamespace(), SimpleNamespace()

        async def refresh(*_args):
            return refreshed, None

        monkeypatch.setattr(next_turn_publication, "_exchange_next_turn", exchange)
        monkeypatch.setattr(next_turn_publication, "issue_execution_recovery_actor_v1", refresh)
        submit_called = False

        async def submit_must_not_run(_command):
            nonlocal submit_called
            submit_called = True
            raise AssertionError("changed trust must be rejected before writer admission")

        monkeypatch.setattr(runtime._appender, "submit", submit_must_not_run)
        with pytest.raises(
            LoopRejected, match="trust source changed during next Turn owner exchange"
        ):
            await runtime.start_next_execution_turn(run.run_id, run.head)
        assert not submit_called
        assert runtime._loop_decisions().entries() == ()


@pytest.mark.asyncio
async def test_unchanged_trust_reaches_next_turn_writer_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "next-turn-trust-stable.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody-stable"
    )
    async with open_common_cli_execution_runtime(database, resources=resources) as runtime:
        authenticated = FrozenTrustObservation(b"snapshot", "trust-head", str(database), ())
        actor = SimpleNamespace(observation=authenticated)
        run = ExecutionRunRecord.model_construct(
            run_id="run-stable",
            head="run-head",
            worker_session="historical-worker-session",
            turns=(SimpleNamespace(ordinal=1, response_seal=object()),),
        )
        cut = SimpleNamespace(
            tenant_id=runtime._tenant_id,
            complete_ordered_run_lineage=(run,),
            materialization_commitment=runtime._commitment_journal.load(runtime._tenant_id),
            tenant_commit_sequence=0,
        )
        journal_rows: list[tuple[str, str | None, bytes]] = []

        @dataclass
        class FakeCommand:
            idempotency_key: str = "next-turn-stable"
            operation_kind: str = NEXT_TURN_PUBLICATION
            expected_head: int = 0
            request_fingerprint: str = "fingerprint"
            records: tuple[object, ...] = ()
            admission_guard: object = None
            decision_guard: object = None

        class Evidence:
            def canonical_bytes(self) -> bytes:
                return b"{}"

        evidence = Evidence()

        class Journal:
            def entries(self):
                return tuple(journal_rows)

        monkeypatch.setattr(runtime, "_execution_actor", lambda _: _resolved(actor))
        monkeypatch.setattr(runtime, "_check_execution_actor", lambda _: None)
        monkeypatch.setattr(runtime, "_require_no_pending", lambda: None)
        monkeypatch.setattr(runtime, "_same_execution_actor_identity", lambda _old, _new: True)
        current_worker = "current-worker-generation"
        monkeypatch.setattr(runtime, "current_worker", lambda: current_worker)
        monkeypatch.setattr(runtime, "_append_decision", lambda entry: journal_rows.append(
            (entry["operation_id"], None, json.dumps(entry, sort_keys=True).encode())
        ))
        monkeypatch.setattr(runtime, "_loop_decisions", lambda: Journal())
        monkeypatch.setattr(runtime._trust, "capture_verified_observation", lambda: authenticated)
        monkeypatch.setattr(
            "chiplog.composition.r14_accepted_open_accounting_publication.hold_if_accepted_open",
            _resolved_false,
        )
        monkeypatch.setattr(
            next_turn_publication, "source", lambda _: SimpleNamespace(collect_held=lambda __: cut)
        )
        monkeypatch.setattr(
            next_turn_publication, "_selected_pair_held", lambda *_args: (None, None)
        )
        monkeypatch.setattr(next_turn_publication, "_turn_ref", lambda _turn: None)
        prepared_requests: list[SimpleNamespace] = []

        def prepare_next_turn(**values):
            request = SimpleNamespace(**values)
            prepared_requests.append(request)
            return request

        monkeypatch.setattr(next_turn_publication, "PrepareNextExecutionTurn", prepare_next_turn)
        monkeypatch.setattr(
            next_turn_publication,
            "_exchange_next_turn",
            lambda *_args: _resolved((None, None, None)),
        )
        refreshed = SimpleNamespace(observation=authenticated)
        actor_budget = SimpleNamespace(
            request=SimpleNamespace(
                budget=SimpleNamespace(absolute_deadline_ns=time.monotonic_ns() + 30_000_000_000)
            )
        )
        monkeypatch.setattr(
            next_turn_publication,
            "issue_execution_recovery_actor_v1",
            lambda *_args: _resolved((refreshed, actor_budget)),
        )
        monkeypatch.setattr(
            next_turn_publication,
            "RetainedNextTurnPublication",
            lambda **_: evidence,
        )
        monkeypatch.setattr(next_turn_publication, "next_turn_command", lambda _: FakeCommand())
        monkeypatch.setattr(next_turn_publication, "retained_next_turn", lambda _: evidence)

        async def submit(command):
            assert command.admission_guard() is None
            command.decision_guard("resulting-commitment")
            return SimpleNamespace(disposition="COMMITTED")

        monkeypatch.setattr(runtime._appender, "submit", submit)
        monkeypatch.setattr(next_turn_publication, "_finish", lambda *_args: _resolved(run))
        assert await runtime.start_next_execution_turn(run.run_id, run.head) is run
        assert prepared_requests[0].fence.worker_session_id == runtime.current_worker()
        assert prepared_requests[0].fence.worker_session_id != run.worker_session
        current_generation = runtime._supervisor.runtime().session("agent_loop").generation_id
        assert prepared_requests[0].fence.runtime_generation == current_generation
        assert len(journal_rows) == 1


async def _resolved(value: object) -> object:
    return value


async def _resolved_false(*_args: object) -> bool:
    return False
