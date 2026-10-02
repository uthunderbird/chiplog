"""Actual R15 mandate issuance through authenticated owner and physical writer."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

import chiplog.composition.r15_mandate_publication as mandate_publication
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.scheduler_configuration import policy_head, schedule_head
from chiplog.capabilities.agent_loop.scheduler_contracts import SchedulerIntervalBound
from chiplog.composition.r15_mandate_contracts import MandateIssuanceAdoption
from chiplog.composition.r15_mandate_scope_registry import (
    EXECUTION_SERVICE_IDENTITY,
    registered_scope_sources,
)
from chiplog.composition.r15_scheduler_registry import (
    SchedulerGenesisAdoption,
    SchedulerGenesisDraft,
)
from chiplog.composition.r15_scheduler_runtime import open_r15_runtime
from chiplog.composition.scheduler_source_registry import read_admitted_scheduler_startup
from chiplog.platform.owner_publications import OwnerPublicationUncertain


def _draft() -> SchedulerGenesisDraft:
    return SchedulerGenesisDraft(
        adoption_act_id="mandate-test-genesis",
        schedule_id="mandate-test-schedule",
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


async def _genesis(runtime: object) -> None:
    command = await runtime.preview_scheduler_genesis("hermetic-ingress", _draft())
    result = await runtime.adopt_scheduler_genesis(
        "hermetic-ingress",
        SchedulerGenesisAdoption(
            adoption_act_id=_draft().adoption_act_id,
            command_bytes=command.canonical_bytes(),
        ),
    )
    assert result.kind == "COMMITTED"


def _head(subject: str, record: str, fingerprint: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=subject, revision=Present(head=record, fingerprint=fingerprint)
    )


async def _adoption(runtime: object, *, act: str = "issue-once") -> MandateIssuanceAdoption:
    startup = read_admitted_scheduler_startup(
        runtime._database,
        runtime._tenant_id,
        runtime._owner_decisions(),
        runtime._commitment_journal,
    )
    schedule = startup.index.schedules[0]
    policy = startup.index.policies[0]
    bound = startup.index.bounds[0][1]
    schedule_ref, policy_ref = schedule_head(schedule), policy_head(policy)
    registered = registered_scope_sources()
    scope = {
        "tenant_id": "hermetic-tenant",
        "mandate_id": "mandate-test-id",
        "service_identity": EXECUTION_SERVICE_IDENTITY,
        "beneficiary_principal": "hermetic-principal",
        "schedule_head": _head(
            schedule_ref.schedule_id, schedule_ref.head, schedule_ref.fingerprint
        ).model_dump(mode="json"),
        "missed_policy_head": _head(
            policy_ref.policy_id, policy_ref.head, policy_ref.fingerprint
        ).model_dump(mode="json"),
        "bound_head": _head(
            schedule_ref.schedule_id,
            bound.head_id,
            hashlib.sha256(bound.canonical_bytes()).hexdigest(),
        ).model_dump(mode="json"),
        "prompt_template_fingerprint": hashlib.sha256(
            schedule.definition.run_inputs.prompt.encode()
        ).hexdigest(),
        "run_budget_policy": schedule.definition.run_inputs.policy.model_dump(mode="json"),
        "permitted_tools": [],
        "origin": registered.origin.model_dump(mode="json"),
        "delivery_scope": registered.delivery.model_dump(mode="json"),
        "recipient_scope": registered.recipient.model_dump(mode="json"),
        "disclosure_scope": registered.disclosure.model_dump(mode="json"),
        "operations": ["scheduler.execute_interval"],
        "issuance_generation": 0,
        "horizon": {
            "coordinate_codec": "chiplog.scheduler.unix-ns.v1",
            "first_eligible_due_coordinate": "0",
            "last_eligible_due_coordinate": "0",
            "expires_at_unix_ns": runtime._tick_clock.observe().unix_ns + 100_000_000_000,
            "max_cycles": 1,
            "max_runs": 1,
            "max_consequential_calls": 0,
        },
    }
    preview = await runtime.preview_scheduler_mandate_clock(
        "hermetic-ingress", act, json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()
    )
    return MandateIssuanceAdoption(
        adoption_act_id=act,
        canonical_scope_bytes=preview.canonical_scope_bytes,
        canonical_policy_bytes=registered.administrative_policy_bytes,
    )


async def test_mandate_issuance_commits_pair_replays_and_survives_restart(tmp_path: Path) -> None:
    database = tmp_path / "mandate.sqlite"
    async with open_r15_runtime(database) as runtime:
        await _genesis(runtime)
        adoption = await _adoption(runtime)
        first = await runtime.adopt_scheduler_mandate("hermetic-ingress", adoption)
        assert first.kind == "COMMITTED"
        replay = await runtime.adopt_scheduler_mandate("hermetic-ingress", adoption)
        assert replay.kind == "EXACT_REPLAY"
        startup = read_admitted_scheduler_startup(
            runtime._database,
            runtime._tenant_id,
            runtime._owner_decisions(),
            runtime._commitment_journal,
        )
        assert len(startup.index.mandates) == 1
        assert startup.index.mandates[0].budget.generation == 0
    async with open_r15_runtime(database) as restarted:
        startup = read_admitted_scheduler_startup(
            restarted._database,
            restarted._tenant_id,
            restarted._owner_decisions(),
            restarted._commitment_journal,
        )
        assert len(startup.index.mandates) == 1


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("beneficiary_principal", "another-principal"),
        ("recipient_scope", "forged-head"),
        ("delivery_scope", "forged-head"),
        ("disclosure_scope", "forged-head"),
        ("permitted_tools", ["send"]),
    ),
)
async def test_mandate_rejects_forged_adopted_scope(
    tmp_path: Path, field: str, replacement: object
) -> None:
    async with open_r15_runtime(tmp_path / "forged.sqlite") as runtime:
        await _genesis(runtime)
        adoption = await _adoption(runtime)
        scope = json.loads(adoption.canonical_scope_bytes)
        if field.endswith("_scope"):
            scope[field]["revision"]["head"] = replacement
        else:
            scope[field] = replacement
        forged = adoption.model_copy(
            update={
                "canonical_scope_bytes": json.dumps(
                    scope, sort_keys=True, separators=(",", ":")
                ).encode()
            }
        )
        with pytest.raises((LoopRejected, ValueError)):
            await runtime.adopt_scheduler_mandate("hermetic-ingress", forged)
        startup = read_admitted_scheduler_startup(
            runtime._database,
            runtime._tenant_id,
            runtime._owner_decisions(),
            runtime._commitment_journal,
        )
        assert startup.index.mandates == ()


async def test_mandate_rechecks_registered_sources_after_owner_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_r15_runtime(tmp_path / "changed-source.sqlite") as runtime:
        await _genesis(runtime)
        adoption = await _adoption(runtime)
        owner = runtime._supervisor.runtime()
        original_call = owner.call
        original_sources = mandate_publication.registered_scope_sources

        async def changed_after_preparation(call: object) -> object:
            result = await original_call(call)
            monkeypatch.setattr(
                mandate_publication,
                "registered_scope_sources",
                lambda: replace(original_sources(), administrative_policy_bytes=b"changed-policy"),
            )
            return result

        monkeypatch.setattr(owner, "call", changed_after_preparation)
        with pytest.raises(LoopRejected, match="administrative issuance policy differs"):
            await runtime.adopt_scheduler_mandate("hermetic-ingress", adoption)
        assert (
            runtime._owner_decisions().snapshot().decisions[-1].prepared.request.operation
            != "scheduler.issue_mandate"
        )


async def test_mandate_rejects_physical_identity_change(
    tmp_path: Path,
) -> None:
    async with open_r15_runtime(tmp_path / "identity.sqlite") as runtime:
        await _genesis(runtime)
        adoption = await _adoption(runtime)
        original = runtime._database_identity
        runtime._database_identity = (original[0], original[1], original[2] + 1)
        with pytest.raises(ValueError, match="runtime physical database identity changed"):
            await runtime.adopt_scheduler_mandate("hermetic-ingress", adoption)


async def test_mandate_selected_crash_recovers_exact_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "selected-crash.sqlite"
    async with open_r15_runtime(database) as runtime:
        await _genesis(runtime)
        adoption = await _adoption(runtime)
        journal = runtime._owner_decisions()
        original_select = journal.select

        def crash_after_select(prepared: object, commitment: str) -> object:
            original_select(prepared, commitment)
            raise RuntimeError("injected crash after selection")

        monkeypatch.setattr(journal, "select", crash_after_select)
        with pytest.raises(OwnerPublicationUncertain):
            await runtime.adopt_scheduler_mandate("hermetic-ingress", adoption)
        monkeypatch.setattr(journal, "select", original_select)
        selected = journal.snapshot().decisions
        assert selected[-1].prepared.request.operation == "scheduler.issue_mandate"
    async with open_r15_runtime(database) as recovered_runtime:
        recovered = await recovered_runtime.adopt_scheduler_mandate("hermetic-ingress", adoption)
        assert recovered.kind == "EXACT_REPLAY"
        startup = read_admitted_scheduler_startup(
            recovered_runtime._database,
            recovered_runtime._tenant_id,
            recovered_runtime._owner_decisions(),
            recovered_runtime._commitment_journal,
        )
        assert len(startup.index.mandates) == 1
