"""R15 shared-cut history integration; actual adoption tests follow below."""

import asyncio
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

import chiplog.composition.r15_scheduler_history as history
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.adapters.driven.loop_prompts import OwnedStaticPrompts
from chiplog.adapters.driven.loop_sqlite import LoopIntegrityError, SQLiteLoopStore
from chiplog.capabilities.agent_loop.application import AgentLoop
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, EndpointSelection, LoopRejected
from chiplog.capabilities.agent_loop.scheduler_contracts import SchedulerIntervalBound
from chiplog.capabilities.agent_loop.scheduler_preparation import (
    ConfigurationPreparationRequest,
    PreparedSchedulerBatch,
    prepare_configuration,
)
from chiplog.composition.r14_runtime import open_r14_runtime
from chiplog.composition.r15_scheduler_authority import (
    SchedulerFreshSources,
    SchedulerPublicationAuthority,
)
from chiplog.composition.r15_scheduler_publication import _authenticate
from chiplog.composition.r15_scheduler_registry import (
    BoundReplacementDraft,
    ConfigurationGenesisCommand,
    PolicyAmendmentDraft,
    PublicationRejected,
    ScheduleAmendmentDraft,
    SchedulerConfigurationAdoption,
    SchedulerGenesisAdoption,
    SchedulerGenesisDraft,
    capture_generation_sources,
    command_from_draft,
    configuration_authority_head,
    configuration_authority_preimage,
    validate_fresh_adoption,
)
from chiplog.composition.r15_scheduler_runtime import open_r15_loop, open_r15_runtime
from chiplog.composition.scheduler_source_registry import (
    AdmittedSchedulerStartup,
    read_admitted_scheduler_startup,
)
from chiplog.platform._owner_publication_contracts import ExactReplayQuery, SingleOwnerBatch
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord, PublicationResult
from chiplog.platform.authority_reads import capture_authority_storage_state
from chiplog.platform.broker import PublicPortCall, PublicPortResult, PublicPortSuccess
from chiplog.platform.owner_publications import OwnerPublicationUncertain, PreparedOwnerPublication
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime
from chiplog.platform.workspace_snapshot import _CURRENT, workspace_snapshot

_COMPLETE = b'{"kind":"Complete","deliveries":[{"kind":"NonAuthoritativeText","text":"Ready"}]}'
_CONTINUE = (
    b'{"kind":"Continue","tool_calls":[{"call_id":"one","tool":"propose_planning","text":"Swim"}]}'
)


async def test_configuration_authority_is_stable_across_actual_authentication_calls(
    tmp_path: Path,
) -> None:
    async with open_r15_runtime(tmp_path / "authority.sqlite") as runtime:
        first_call = await _authenticate(runtime, "hermetic-ingress")
        first = capture_generation_sources(runtime, first_call)
        second_call = await _authenticate(runtime, "hermetic-ingress")
        second = capture_generation_sources(runtime, second_call)
        assert first_call.request.request_id != second_call.request.request_id
        assert first.evidence_bytes != second.evidence_bytes
        assert configuration_authority_preimage(first) == configuration_authority_preimage(second)
        original = configuration_authority_head(first)
        assert original == configuration_authority_head(second)
        for changed_sources in (
            replace(first, authority_epoch="changed"),
            replace(first, broker_generation="changed"),
            replace(first, runtime_graph_generation="changed"),
            replace(first, worker_session="changed"),
        ):
            assert configuration_authority_head(changed_sources) != original
        evidence = json.loads(first.evidence_bytes)
        evidence["trust_reference"]["credential_head"] = "changed-credential"
        changed = replace(first, evidence_bytes=json.dumps(evidence).encode())
        assert configuration_authority_head(changed) != original
        # Hashing source descriptions selects nothing and grants no issuer handle.
        assert runtime._owner_decisions().snapshot().decisions == ()


def _draft() -> SchedulerGenesisDraft:
    return SchedulerGenesisDraft(
        adoption_act_id="actual-act",
        schedule_id="actual-schedule",
        start_ns=0,
        period_ns=1_000_000_000,
        end_exclusive_ns="NO_END",
        prompt="Review the plan",
        policy=BudgetPolicy(),
        missed_policy="SKIP",
        interval_bound=SchedulerIntervalBound(
            max_member_count=16,
            max_manifest_bytes=65536,
            max_serialized_batch_bytes=1_048_576,
        ),
    )


async def test_configuration_transitions_publish_and_replay_original_bytes_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "configuration.sqlite"
    adoptions = []
    results = []
    async with open_r15_runtime(database) as runtime:
        genesis = await runtime.preview_scheduler_genesis("hermetic-ingress", _draft())
        assert (
            await runtime.adopt_scheduler_genesis(
                "hermetic-ingress",
                SchedulerGenesisAdoption(
                    adoption_act_id=_draft().adoption_act_id,
                    command_bytes=genesis.canonical_bytes(),
                ),
            )
        ).kind == "COMMITTED"
        drafts = (
            ScheduleAmendmentDraft(
                adoption_act_id="schedule-change",
                schedule_id=_draft().schedule_id,
                start_ns=1,
                period_ns=2_000_000_000,
                end_exclusive_ns="NO_END",
                prompt="Changed recurring prompt",
                policy=BudgetPolicy(),
            ),
            PolicyAmendmentDraft(
                adoption_act_id="policy-change",
                schedule_id=_draft().schedule_id,
                missed_policy="COALESCE",
            ),
            BoundReplacementDraft(
                adoption_act_id="bound-change",
                schedule_id=_draft().schedule_id,
                interval_bound=SchedulerIntervalBound(
                    max_member_count=32,
                    max_manifest_bytes=131072,
                    max_serialized_batch_bytes=2_097_152,
                ),
            ),
        )
        for draft in drafts:
            command = await runtime.preview_scheduler_configuration("hermetic-ingress", draft)
            adoption = SchedulerConfigurationAdoption(
                adoption_act_id=draft.adoption_act_id, command_bytes=command.canonical_bytes()
            )
            result = await runtime.adopt_scheduler_configuration("hermetic-ingress", adoption)
            assert result.kind == "COMMITTED"
            assert len(result.complete_records) == 1
            assert (await runtime.adopt_scheduler_configuration("hermetic-ingress", adoption)) == (
                result.model_copy(update={"kind": "EXACT_REPLAY"})
            )
            adoptions.append(adoption)
            results.append(result)
        admitted = read_admitted_scheduler_startup(
            database, runtime._tenant_id, runtime._owner_decisions(), runtime._commitment_journal
        )
        assert isinstance(admitted, AdmittedSchedulerStartup)
        current = history.configuration_snapshot(admitted, _draft().schedule_id)
        assert current.schedule is not None and current.schedule.revision == 1
        assert current.policy is not None and current.policy.policy == "COALESCE"
        assert current.bound is not None and current.bound.generation == 1
        assert current.bound.bound.max_member_count == 32
        assert runtime._loop_snapshot().records == ()
    original_call = AuthorityBrokerRuntime.call

    async def no_preparation(
        self: AuthorityBrokerRuntime, call: PublicPortCall
    ) -> PublicPortResult:
        assert call.operation_id != "scheduler.prepare_configuration"
        return await original_call(self, call)

    monkeypatch.setattr(AuthorityBrokerRuntime, "call", no_preparation)
    async with open_r15_runtime(database) as runtime:
        anchor = runtime._commitment_journal.load(runtime._tenant_id)
        for adoption, result in zip(adoptions, results, strict=True):
            assert (await runtime.adopt_scheduler_configuration("hermetic-ingress", adoption)) == (
                result.model_copy(update={"kind": "EXACT_REPLAY"})
            )
            changed = adoption.model_copy(update={"command_bytes": b"malformed changed bytes"})
            assert (
                await runtime.adopt_scheduler_configuration("hermetic-ingress", changed)
            ).kind == "CONFLICT"
        assert runtime._commitment_journal.load(runtime._tenant_id) == anchor


@pytest.mark.parametrize("fault", ["before_commit", "after_commit"])
async def test_configuration_selected_recovery_preserves_original_bound_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: Literal["before_commit", "after_commit"]
) -> None:
    database = tmp_path / "bound-recovery.sqlite"
    async with open_r15_runtime(database) as runtime:
        genesis = await runtime.preview_scheduler_genesis("hermetic-ingress", _draft())
        assert (
            await runtime.adopt_scheduler_genesis(
                "hermetic-ingress",
                SchedulerGenesisAdoption(
                    adoption_act_id=_draft().adoption_act_id,
                    command_bytes=genesis.canonical_bytes(),
                ),
            )
        ).kind == "COMMITTED"
        command = await runtime.preview_scheduler_configuration(
            "hermetic-ingress",
            BoundReplacementDraft(
                adoption_act_id="recover-bound",
                schedule_id=_draft().schedule_id,
                interval_bound=_draft().interval_bound,
            ),
        )
        adoption = SchedulerConfigurationAdoption(
            adoption_act_id="recover-bound", command_bytes=command.canonical_bytes()
        )
        submit = runtime._appender.submit

        async def fail(command: PhysicalPublicationCommand) -> PublicationResult:
            assert command.operation_kind == "scheduler.replace_bound"
            return await submit(replace(command, fault=fault))

        with monkeypatch.context() as patch:
            patch.setattr(runtime._appender, "submit", fail)
            with pytest.raises(OwnerPublicationUncertain):
                await runtime.adopt_scheduler_configuration("hermetic-ingress", adoption)
        selected = runtime._owner_decisions().snapshot().decisions[-1]
        retained = selected.prepared.request.complete_records
        assert selected.prepared.request.operation == "scheduler.replace_bound"
    call = AuthorityBrokerRuntime.call

    async def no_preparation(
        self: AuthorityBrokerRuntime, request: PublicPortCall
    ) -> PublicPortResult:
        assert request.operation_id != "scheduler.prepare_configuration"
        return await call(self, request)

    monkeypatch.setattr(AuthorityBrokerRuntime, "call", no_preparation)
    async with open_r15_runtime(database) as runtime:
        runtime._require_no_pending()
        result = await runtime.adopt_scheduler_configuration("hermetic-ingress", adoption)
        assert result.kind == "EXACT_REPLAY"
        assert result.complete_records == retained
        assert result.decision_id == selected.decision_id
        assert runtime._owner_decisions().snapshot().decisions[-1] == selected


async def test_configuration_stale_policy_head_selects_nothing_and_cross_operation_act_conflicts(
    tmp_path: Path,
) -> None:
    async with open_r15_runtime(tmp_path / "configuration-cas.sqlite") as runtime:
        genesis = await runtime.preview_scheduler_genesis("hermetic-ingress", _draft())
        assert (
            await runtime.adopt_scheduler_genesis(
                "hermetic-ingress",
                SchedulerGenesisAdoption(
                    adoption_act_id=_draft().adoption_act_id,
                    command_bytes=genesis.canonical_bytes(),
                ),
            )
        ).kind == "COMMITTED"
        first_draft = PolicyAmendmentDraft(
            adoption_act_id="winner", schedule_id=_draft().schedule_id, missed_policy="COALESCE"
        )
        first = await runtime.preview_scheduler_configuration("hermetic-ingress", first_draft)
        stale = await runtime.preview_scheduler_configuration(
            "hermetic-ingress",
            first_draft.model_copy(
                update={"adoption_act_id": "stale", "missed_policy": "MATERIALIZE_EACH"}
            ),
        )
        assert (
            await runtime.adopt_scheduler_configuration(
                "hermetic-ingress",
                SchedulerConfigurationAdoption(
                    adoption_act_id="winner", command_bytes=first.canonical_bytes()
                ),
            )
        ).kind == "COMMITTED"
        before = runtime._owner_decisions().snapshot()
        with pytest.raises(LoopRejected):
            await runtime.adopt_scheduler_configuration(
                "hermetic-ingress",
                SchedulerConfigurationAdoption(
                    adoption_act_id="stale", command_bytes=stale.canonical_bytes()
                ),
            )
        other = await runtime.preview_scheduler_configuration(
            "hermetic-ingress",
            BoundReplacementDraft(
                adoption_act_id="winner",
                schedule_id=_draft().schedule_id,
                interval_bound=_draft().interval_bound,
            ),
        )
        result = await runtime.adopt_scheduler_configuration(
            "hermetic-ingress",
            SchedulerConfigurationAdoption(
                adoption_act_id="winner", command_bytes=other.canonical_bytes()
            ),
        )
        assert result.kind == "CONFLICT"
        assert runtime._owner_decisions().snapshot() == before


async def test_actual_genesis_atomic_publication_and_historical_replay_after_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "genesis.sqlite"
    async with open_r15_runtime(database) as runtime:
        command = await runtime.preview_scheduler_genesis("hermetic-ingress", _draft())
        adoption = SchedulerGenesisAdoption(
            adoption_act_id=_draft().adoption_act_id, command_bytes=command.canonical_bytes()
        )
        result = await runtime.adopt_scheduler_genesis("hermetic-ingress", adoption)
        assert result.kind == "COMMITTED"
        assert len(result.complete_records) == 3
        assert tuple(row.schema_id for row in result.complete_records) == (
            "chiplog.scheduler.schedule-definition.v1",
            "chiplog.scheduler.missed-policy.v1",
            "chiplog.scheduler.interval-bound.v1",
        )
        assert runtime._loop_snapshot().records == ()
        admitted = read_admitted_scheduler_startup(
            database, runtime._tenant_id, runtime._owner_decisions(), runtime._commitment_journal
        )
        assert isinstance(admitted, AdmittedSchedulerStartup)
        snapshot = history.configuration_snapshot(admitted, _draft().schedule_id)
        assert snapshot.schedule is not None
        assert snapshot.policy is not None
        assert snapshot.bound is not None
        assert snapshot.schedule.definition == command.definition
        assert snapshot.policy.policy == command.policy
        assert snapshot.bound.bound == command.bound
        assert snapshot.active_hold.kind == "ABSENT"
        with pytest.raises(ValueError, match="one complete registered schedule"):
            history.configuration_snapshot(admitted, "unregistered-schedule")
        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute(
                "SELECT record_id FROM main.records WHERE owner='agent_loop' ORDER BY record_id"
            ).fetchall() == sorted((row.record_id,) for row in result.complete_records)
            assert connection.execute(
                "SELECT operation_kind,commit_sequence,record_ids FROM main.publications "
                "WHERE operation_kind='scheduler.genesis'"
            ).fetchall() == [
                (
                    "scheduler.genesis",
                    result.tenant_commit_sequence,
                    "\n".join(row.record_id for row in result.complete_records),
                )
            ]
        replay = await runtime.adopt_scheduler_genesis("hermetic-ingress", adoption)
        assert replay == result.model_copy(update={"kind": "EXACT_REPLAY"})

    async with open_r15_loop(database, responses=(_CONTINUE, _COMPLETE)) as loop:
        created = await loop.create("after-genesis", "Hello", BudgetPolicy())
        active = await loop.activate("after-genesis", created.head)
        await loop.step("after-genesis", active.head)
        display = await loop.display("after-genesis/turn/1/proposal/one")
        receipt = await loop.adopt(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )
        assert receipt.purpose == "Swim"
        assert (
            await loop.adopt(
                "hermetic-ingress",
                display.display_id,
                display.display_digest,
                display.adoption_act_id,
            )
            == receipt
        )
        complete = await loop.step("after-genesis", loop.record("after-genesis").head)
        assert complete.state == "SUCCEEDED"
        assert loop.record("after-genesis").planning_receipts == (receipt,)

    async with open_r15_runtime(database) as reopened:
        assert reopened.current_worker() != command.definition.run_inputs.worker_session
        before = reopened._loop_snapshot()
        call = reopened._supervisor.runtime().call

        async def forbid_preparation(request: PublicPortCall) -> PublicPortResult:
            assert request.operation_id != "scheduler.prepare_configuration"
            return await call(request)

        monkeypatch.setattr(reopened._supervisor.runtime(), "call", forbid_preparation)
        replay = await reopened.adopt_scheduler_genesis("hermetic-ingress", adoption)
        assert replay == result.model_copy(update={"kind": "EXACT_REPLAY"})
        assert reopened._loop_snapshot() == before
        changed = command.model_copy(update={"schedule_id": "changed-subject"})
        rejected = await reopened.adopt_scheduler_genesis(
            "hermetic-ingress",
            SchedulerGenesisAdoption(
                adoption_act_id=adoption.adoption_act_id, command_bytes=changed.canonical_bytes()
            ),
        )
        assert rejected.kind == "CONFLICT"
        assert reopened._loop_snapshot() == before
        assert reopened._proposal(display.proposal_id)[1] == "Swim"


@pytest.mark.parametrize("fault", ["before_commit", "after_commit"])
async def test_selected_genesis_recovers_original_bytes_after_restart_without_owner_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: Literal["before_commit", "after_commit"]
) -> None:
    database = tmp_path / "recovery.sqlite"
    async with open_r15_runtime(database) as runtime:
        command = await runtime.preview_scheduler_genesis("hermetic-ingress", _draft())
        adoption = SchedulerGenesisAdoption(
            adoption_act_id=_draft().adoption_act_id, command_bytes=command.canonical_bytes()
        )
        submit = runtime._appender.submit

        async def fail(command: PhysicalPublicationCommand) -> PublicationResult:
            assert command.operation_kind == "scheduler.genesis"
            return await submit(replace(command, fault=fault))

        with monkeypatch.context() as patch:
            patch.setattr(runtime._appender, "submit", fail)
            with pytest.raises(OwnerPublicationUncertain):
                await runtime.adopt_scheduler_genesis("hermetic-ingress", adoption)
        (selected,) = runtime._owner_decisions().snapshot().decisions
        assert runtime._owner_decisions().snapshot().materialized_command_ids == frozenset()
        retained = selected.prepared.request.complete_records

    call = AuthorityBrokerRuntime.call

    async def no_current_preparation(
        self: AuthorityBrokerRuntime, request: PublicPortCall
    ) -> PublicPortResult:
        assert request.operation_id != "scheduler.prepare_configuration"
        return await call(self, request)

    monkeypatch.setattr(AuthorityBrokerRuntime, "call", no_current_preparation)
    async with open_r15_runtime(database) as reopened:
        reopened._require_no_pending()
        replay = await reopened.adopt_scheduler_genesis("hermetic-ingress", adoption)
        assert replay.kind == "EXACT_REPLAY"
        assert replay.complete_records == retained
        assert replay.decision_id == selected.decision_id
        assert reopened._owner_decisions().snapshot().decisions == (selected,)
        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM main.publications WHERE operation_kind='scheduler.genesis'"
            ).fetchone() == (1,)


async def test_second_replay_authentication_denial_cannot_recover_pending_genesis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "denied-pending-replay.sqlite"
    async with open_r15_runtime(database) as runtime:
        command = await runtime.preview_scheduler_genesis("hermetic-ingress", _draft())
        adoption = SchedulerGenesisAdoption(
            adoption_act_id=_draft().adoption_act_id, command_bytes=command.canonical_bytes()
        )
        submit = runtime._appender.submit

        async def fail_before_sql(command: PhysicalPublicationCommand) -> PublicationResult:
            assert command.operation_kind == "scheduler.genesis"
            return await submit(replace(command, fault="before_commit"))

        with monkeypatch.context() as patch:
            patch.setattr(runtime._appender, "submit", fail_before_sql)
            with pytest.raises(OwnerPublicationUncertain):
                await runtime.adopt_scheduler_genesis("hermetic-ingress", adoption)
        (selected,) = runtime._owner_decisions().snapshot().decisions
        assert runtime._owner_decisions().snapshot().materialized_command_ids == frozenset()
        authentication = SchedulerPublicationAuthority.authenticate_replay
        authentications: list[ExactReplayQuery] = []
        recovery_submits: list[PhysicalPublicationCommand] = []

        def deny_second(
            self: SchedulerPublicationAuthority, query: ExactReplayQuery
        ) -> PublicationRejected | None:
            assert authentication(self, query) is None
            authentications.append(query)
            if len(authentications) == 2:
                return PublicationRejected(
                    kind="DENIED",
                    tenant_id=query.identity.tenant_id,
                    command_id=query.identity.command_id,
                    reason="current invocation expired between explicit and coordinator checks",
                )
            return None

        async def observe_recovery(command: PhysicalPublicationCommand) -> PublicationResult:
            recovery_submits.append(command)
            return await submit(command)

        call = runtime._supervisor.runtime().call

        async def no_new_preparation(request: PublicPortCall) -> PublicPortResult:
            assert request.operation_id != "scheduler.prepare_configuration"
            return await call(request)

        monkeypatch.setattr(runtime._supervisor.runtime(), "call", no_new_preparation)
        with monkeypatch.context() as patch:
            patch.setattr(SchedulerPublicationAuthority, "authenticate_replay", deny_second)
            patch.setattr(runtime._appender, "submit", observe_recovery)
            denied = await runtime.adopt_scheduler_genesis("hermetic-ingress", adoption)
        assert len(authentications) == 2
        assert recovery_submits == [], "a later replay denial must never submit recovery"
        assert denied.kind == "DENIED"
        assert runtime._owner_decisions().snapshot().materialized_command_ids == frozenset()
        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM main.publications WHERE operation_kind='scheduler.genesis'"
            ).fetchone() == (0,)

        # Current authorized retry still recovers the original selected decision.
        recovered = await runtime.adopt_scheduler_genesis("hermetic-ingress", adoption)
        assert recovered.kind == "EXACT_REPLAY"
        assert recovered.complete_records == selected.prepared.request.complete_records
        assert recovered.decision_id == selected.decision_id
        assert runtime._owner_decisions().snapshot().decisions == (selected,)
        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM main.publications WHERE operation_kind='scheduler.genesis'"
            ).fetchone() == (1,)


async def test_copied_issuer_handles_and_unregistered_input_do_not_grant_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    issued: list[tuple[SchedulerPublicationAuthority, PreparedOwnerPublication]] = []
    register = SchedulerPublicationAuthority.register_prepared

    def remember(
        self: SchedulerPublicationAuthority,
        request: SingleOwnerBatch,
        sources: SchedulerFreshSources,
    ) -> PreparedOwnerPublication:
        prepared = register(self, request, sources)
        issued.append((self, prepared))
        return prepared

    monkeypatch.setattr(SchedulerPublicationAuthority, "register_prepared", remember)
    async with open_r15_runtime(tmp_path / "handles.sqlite") as runtime:
        with pytest.raises(LoopRejected, match="registered CLI"):
            await runtime.preview_scheduler_genesis("foreign-peer", _draft())
        with pytest.raises(LoopRejected):
            await runtime.adopt_scheduler_genesis(
                "hermetic-ingress",
                SchedulerGenesisAdoption(adoption_act_id="malformed", command_bytes=b"\xff"),
            )
        assert runtime._owner_decisions().snapshot().decisions == ()
        command = await runtime.preview_scheduler_genesis("hermetic-ingress", _draft())
        result = await runtime.adopt_scheduler_genesis(
            "hermetic-ingress",
            SchedulerGenesisAdoption(
                adoption_act_id=_draft().adoption_act_id, command_bytes=command.canonical_bytes()
            ),
        )
        assert result.kind == "COMMITTED"
        ((authority, prepared),) = issued
        assert authority.check_prepared(replace(prepared)) == "DENIED"
        copied = await authority.prepare(prepared.request.model_copy())
        assert isinstance(copied, PublicationRejected) and copied.kind == "DENIED"
        assert isinstance(prepared.request, SingleOwnerBatch)
        request = prepared.request
        denied = authority.authenticate_replay(
            ExactReplayQuery(
                identity=request.identity,
                operation=request.operation,
                current_invocation=request.authentication.invocation.model_copy(),
                original_commands=(request.command,),
            )
        )
        assert denied is not None and denied.kind == "DENIED"


async def test_competing_genesis_adoptions_select_only_one_atomic_triple(tmp_path: Path) -> None:
    database = tmp_path / "race.sqlite"
    async with open_r15_runtime(database) as runtime:
        first = await runtime.preview_scheduler_genesis("hermetic-ingress", _draft())
        second_draft = _draft().model_copy(
            update={"adoption_act_id": "second-act", "schedule_id": "second-schedule"}
        )
        second = await runtime.preview_scheduler_genesis("hermetic-ingress", second_draft)
        outcomes = await asyncio.gather(
            runtime.adopt_scheduler_genesis(
                "hermetic-ingress",
                SchedulerGenesisAdoption(
                    adoption_act_id=_draft().adoption_act_id, command_bytes=first.canonical_bytes()
                ),
            ),
            runtime.adopt_scheduler_genesis(
                "hermetic-ingress",
                SchedulerGenesisAdoption(
                    adoption_act_id=second_draft.adoption_act_id,
                    command_bytes=second.canonical_bytes(),
                ),
            ),
            return_exceptions=True,
        )
        winners = [
            result
            for result in outcomes
            if not isinstance(result, BaseException | PublicationRejected)
        ]
        assert len(winners) == 1 and winners[0].kind == "COMMITTED"
        losers = [result for result in outcomes if result not in winners]
        assert len(losers) == 1
        assert isinstance(losers[0], LoopRejected | PublicationRejected)
        (selected,) = runtime._owner_decisions().snapshot().decisions
        assert selected.prepared.request.complete_records == winners[0].complete_records
        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM main.records WHERE schema_id LIKE 'chiplog.scheduler.%'"
            ).fetchone() == (3,)


async def test_fresh_adoption_cannot_publish_after_actual_authentication_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_r15_runtime(tmp_path / "expired.sqlite") as runtime:
        command = await runtime.preview_scheduler_genesis("hermetic-ingress", _draft())
        call = runtime._supervisor.runtime().call

        async def delay(request: PublicPortCall) -> PublicPortResult:
            result = await call(request)
            if request.operation_id == "scheduler.prepare_configuration":
                # The returned output is genuine. Delay it beyond the real five-second
                # authentication budget; no synthetic clock/proof replaces that budget.
                await asyncio.sleep(5.1)
            return result

        monkeypatch.setattr(runtime._supervisor.runtime(), "call", delay)
        with pytest.raises(LoopRejected, match="authentication"):
            await runtime.adopt_scheduler_genesis(
                "hermetic-ingress",
                SchedulerGenesisAdoption(
                    adoption_act_id=_draft().adoption_act_id,
                    command_bytes=command.canonical_bytes(),
                ),
            )
        assert runtime._owner_decisions().snapshot().decisions == ()
        assert runtime._loop_snapshot().records == ()


@pytest.mark.parametrize("mutation", ["generation", "reordered_output", "substituted_output"])
async def test_changed_sources_or_owner_output_cannot_select_genesis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    database = tmp_path / "changed.sqlite"
    async with open_r15_runtime(database) as runtime:
        command = await runtime.preview_scheduler_genesis("hermetic-ingress", _draft())
        adoption = SchedulerGenesisAdoption(
            adoption_act_id=_draft().adoption_act_id, command_bytes=command.canonical_bytes()
        )
        call = runtime._supervisor.runtime().call

        async def alter(request: PublicPortCall) -> PublicPortResult:
            result = await call(request)
            if request.operation_id != "scheduler.prepare_configuration":
                return result
            assert isinstance(result, PublicPortSuccess)
            if mutation == "generation":
                runtime._start_generation()
                return result
            output = PreparedSchedulerBatch.model_validate_json(result.canonical_payload)
            if mutation == "reordered_output":
                output = output.model_copy(update={"records": tuple(reversed(output.records))})
            else:
                original = ConfigurationPreparationRequest.model_validate_json(
                    request.canonical_payload
                )
                changed = json.loads(original.command_bytes)
                changed["definition"]["run_inputs"]["prompt"] = "Substituted owner prompt"
                changed_bytes = ConfigurationGenesisCommand.model_validate(
                    changed
                ).canonical_bytes()
                substituted = original.model_copy(
                    update={
                        "command_bytes": changed_bytes,
                        "snapshot": original.snapshot.model_copy(
                            update={
                                "cut": original.snapshot.cut.model_copy(
                                    update={
                                        "authorized_command_fingerprint": hashlib.sha256(
                                            changed_bytes
                                        ).hexdigest()
                                    }
                                )
                            }
                        ),
                    }
                )
                output = prepare_configuration(substituted).model_copy(
                    update={
                        "source_request_fingerprint": output.source_request_fingerprint,
                        "cut": output.cut,
                    }
                )
            return result.model_copy(update={"canonical_payload": output.canonical_bytes()})

        monkeypatch.setattr(runtime._supervisor.runtime(), "call", alter)
        with pytest.raises(LoopRejected):
            await runtime.adopt_scheduler_genesis("hermetic-ingress", adoption)
        assert runtime._owner_decisions().snapshot().decisions == ()
        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM main.records WHERE schema_id LIKE 'chiplog.scheduler.%'"
            ).fetchone() == (0,)


@pytest.mark.parametrize("mutation", ["missing_record", "extra_publication"])
async def test_materialized_marker_cannot_hide_corrupt_genesis_on_replay_or_restart(
    tmp_path: Path, mutation: str
) -> None:
    database = tmp_path / "corrupt.sqlite"
    async with open_r15_runtime(database) as runtime:
        command = await runtime.preview_scheduler_genesis("hermetic-ingress", _draft())
        adoption = SchedulerGenesisAdoption(
            adoption_act_id=_draft().adoption_act_id, command_bytes=command.canonical_bytes()
        )
        result = await runtime.adopt_scheduler_genesis("hermetic-ingress", adoption)
        assert result.kind == "COMMITTED"
        with closing(sqlite3.connect(database)) as connection, connection:
            if mutation == "missing_record":
                connection.execute(
                    "DELETE FROM main.records WHERE record_id=?",
                    (result.complete_records[-1].record_id,),
                )
            else:
                connection.execute(
                    "INSERT INTO main.publications VALUES (?,?,?,?,?,?)",
                    (
                        runtime._tenant_id,
                        "scheduler.genesis",
                        "orphan",
                        "f" * 64,
                        result.tenant_commit_sequence,
                        result.complete_records[0].record_id,
                    ),
                )
        runtime._commitment_journal.commit(
            runtime._tenant_id, capture_authority_storage_state(database)[0]
        )
        replay = await runtime.adopt_scheduler_genesis("hermetic-ingress", adoption)
        assert replay.kind == "INTEGRITY_FAULT"
    with pytest.raises(LoopIntegrityError):
        async with open_r15_runtime(database):
            pytest.fail("corrupt materialized scheduler history was exposed")


async def test_genesis_bindings_capture_actual_authentication_and_runtime_generations(
    tmp_path: Path,
) -> None:
    async with open_r14_runtime(tmp_path / "sources.sqlite") as runtime:
        observed = await runtime._observed_trust_call(
            "AUTHENTICATE",
            {
                "contour": "CLI",
                "credential_id": "hermetic-credential",
                "peer_credential": f"uid:{os.getuid()}",
                "session_id": "hermetic-session",
            },
        )
        sources = capture_generation_sources(runtime, observed)
        evidence = json.loads(sources.evidence_bytes)
        assert sources.agent_session == runtime._supervisor.runtime().session("agent_loop")
        assert sources.worker_session == runtime.current_worker()
        assert evidence["authentication"]["request"]["request_id"] == observed.request.request_id
        assert (
            evidence["runtime_graph_preimage"]["generation_id"]
            == sources.agent_session.generation_id
        )
        assert evidence["trust_reference"]["principal_id"] == "hermetic-principal"
        assert capture_generation_sources(runtime, observed) == sources
        value = _draft()
        command = command_from_draft(value, sources)
        adoption = SchedulerGenesisAdoption(
            adoption_act_id=value.adoption_act_id, command_bytes=command.canonical_bytes()
        )
        assert validate_fresh_adoption(adoption, sources) == command
        assert command.definition.run_inputs.worker_session == sources.worker_session
        assert command.definition.run_inputs.authority_epoch == sources.authority_epoch
        changed = command_from_draft(value.model_copy(update={"schedule_id": "changed"}), sources)
        assert changed.identity.command_id == command.identity.command_id
        assert changed.canonical_bytes() != command.canonical_bytes()

        for field, replacement in (
            ("principal", "another-principal"),
            ("worker_session", "invented-worker"),
            ("authority_epoch", "invented-epoch"),
            ("contour_head", "invented-contour"),
        ):
            raw = json.loads(command.canonical_bytes())
            raw["definition"]["run_inputs"][field] = replacement
            forged = ConfigurationGenesisCommand.model_validate(raw)
            with pytest.raises(LoopRejected, match="current command bindings"):
                validate_fresh_adoption(
                    SchedulerGenesisAdoption(
                        adoption_act_id=value.adoption_act_id,
                        command_bytes=forged.canonical_bytes(),
                    ),
                    sources,
                )
        runtime._start_generation()
        with pytest.raises(LoopRejected, match="authentication"):
            capture_generation_sources(runtime, observed)


async def test_shared_history_reads_selected_runs_and_original_companion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "history.sqlite"
    model = HermeticModel((_COMPLETE,))
    async with open_r14_runtime(database, model=model) as runtime:
        model.session = runtime
        store = SQLiteLoopStore(
            database, runtime._appender, runtime._tenant_id, "r6", authority=runtime
        )
        loop = AgentLoop(
            store,
            model,
            OwnedStaticPrompts(),
            tenant=runtime._tenant_id,
            principal="hermetic-principal",
            origin=EndpointSelection(
                kind="ORIGIN_EXACT",
                ingress_binding_head="hermetic-ingress-v1",
                endpoint_head="hermetic-endpoint-v1",
                endpoint_id="hermetic-local",
                provider="hermetic-local",
                recipient="hermetic-principal",
                canonical_address="local://hermetic-principal",
                credential_binding_head="hermetic-v1",
            ),
            contour_head="hermetic-contour-v1",
            policy_head="hermetic-policy-v1",
            worker_session="hermetic-session",
            planning=runtime,
            session=runtime,
        )
        created = await loop.create("run", "Hello", BudgetPolicy())
        active = await loop.activate("run", created.head)
        complete = await loop.step("run", active.head)
        assert complete.state == "SUCCEEDED"
        expected = store.snapshot()

        def forbidden(*args: object) -> None:
            raise AssertionError("historical read regenerated a current companion")

        monkeypatch.setattr(runtime, "companions", forbidden)
        assert history.read_loop_snapshot(runtime) == expected
        with workspace_snapshot(database):
            assert history.read_loop_snapshot(runtime) == expected
    async with open_r14_runtime(database) as reopened:
        assert history.read_loop_snapshot(reopened) == expected


async def test_shared_history_establishes_one_ambient_scheduler_cut(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = read_admitted_scheduler_startup
    connections: list[sqlite3.Connection] = []

    def observed(*args: object, **kwargs: object) -> object:
        current = _CURRENT.get()
        assert current is not None and current.connection.in_transaction
        connections.append(current.connection)
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(history, "read_admitted_scheduler_startup", observed)
    database = tmp_path / "cut.sqlite"
    async with open_r14_runtime(database) as runtime:
        assert history.read_loop_snapshot(runtime).records == ()
        with workspace_snapshot(database) as cut:
            assert history.read_loop_snapshot(runtime).records == ()
            assert connections[-1] is cut.connection
        assert len(connections) == 2


@pytest.mark.parametrize("operation", ["agent_loop.unknown", "scheduler.unknown"])
async def test_shared_history_rejects_anchored_orphan_publication(
    tmp_path: Path,
    operation: str,
) -> None:
    database = tmp_path / "orphan.sqlite"
    async with open_r14_runtime(database) as runtime:
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute(
                "INSERT INTO main.publications "
                "(tenant_id,operation_kind,idempotency_key,request_fingerprint,"
                "commit_sequence,record_ids) "
                "VALUES (?,?,?,?,?,?)",
                (runtime._tenant_id, operation, "orphan", "f" * 64, 0, "missing"),
            )
        # Re-anchoring isolates membership verification from the outer AMR check.
        runtime._commitment_journal.commit(
            runtime._tenant_id, capture_authority_storage_state(database)[0]
        )
        with pytest.raises(LoopIntegrityError, match="operation=snapshot"):
            history.read_loop_snapshot(runtime)


@pytest.mark.parametrize("mutation", ["extra_publication", "extra_record", "changed_bytes"])
def test_selected_readback_rejects_whole_publication_contamination(mutation: str) -> None:
    payload = b'{"selected":true}'
    digest = hashlib.sha256(payload).hexdigest()
    command = PhysicalPublicationCommand(
        tenant_id="tenant",
        operation_kind="agent_loop",
        idempotency_key="run-head",
        request_fingerprint=digest,
        expected_head=0,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=(PhysicalRecord("run-head", "agent_loop", "schema", payload, digest),),
    )
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(
            "CREATE TABLE records "
            "(tenant_id,record_id,owner,schema_id,canonical_bytes,commit_sequence);"
            "CREATE TABLE publications "
            "(tenant_id,operation_kind,idempotency_key,request_fingerprint,commit_sequence,record_ids);"
        )
        connection.execute("BEGIN")
        assert history.inspect_publication(connection, command, 1) == "ABSENT"
        connection.execute(
            "INSERT INTO records VALUES (?,?,?,?,?,?)",
            ("tenant", "run-head", "agent_loop", "schema", payload, 1),
        )
        assert history.inspect_publication(connection, command, 1) == "CONFLICT"
        connection.execute(
            "INSERT INTO publications VALUES (?,?,?,?,?,?)",
            ("tenant", "agent_loop", "run-head", digest, 1, "run-head"),
        )
        assert history.inspect_publication(connection, command, 1) == "COMPLETE"
        if mutation == "extra_publication":
            connection.execute(
                "INSERT INTO publications VALUES (?,?,?,?,?,?)",
                ("tenant", "other", "extra", digest, 1, "run-head"),
            )
        elif mutation == "extra_record":
            connection.execute(
                "INSERT INTO records VALUES (?,?,?,?,?,?)",
                ("tenant", "extra", "conversation", "schema", payload, 1),
            )
        else:
            connection.execute("UPDATE records SET canonical_bytes=?", (b"changed",))
        assert history.inspect_publication(connection, command, 1) == "CONFLICT"
