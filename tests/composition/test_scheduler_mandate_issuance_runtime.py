"""Actual R15 mandate issuance through authenticated owner and physical writer."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

import chiplog.composition.r15_mandate_publication as mandate_publication
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.composition.r15_scheduler_registry import (
    SchedulerGenesisAdoption,
)
from chiplog.composition.r15_scheduler_runtime import open_r15_runtime
from chiplog.composition.scheduler_source_registry import read_admitted_scheduler_startup
from chiplog.platform.owner_publications import OwnerPublicationUncertain
from tests.support.composition.scheduler_mandate_issuance_runtime import (
    _adoption,
    _draft,
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
