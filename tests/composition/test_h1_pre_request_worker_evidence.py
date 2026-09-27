"""Private E issuance admits only a live installed worker source."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_pre_request_worker_evidence import H1PreRequestWorkerEvidence
from chiplog.composition.r14_execution_complete_seal_records import RetainedExecutionCompleteSealV3
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_cli_execution import _admit
from tests.support.h1_installed_launch import (
    DATABASE,
    DEPLOYMENT,
    TENANT,
    installed_slot,
    prepare_installed_slot,
)


async def _current_native_cut(runtime: Any) -> object:
    request = await _admit(runtime)
    initial = await runtime.drive_input(request)
    completion = DeliveryCompletion(
        tenant=TENANT,
        run_id=initial.stable_run_lineage_id,
        turn_id=initial.stable_run_lineage_id + "/turn/1",
        deliveries=(ProposedDelivery(payload=(Commentary(text="mounted E worker"),)),),
    )
    runtime._execution_model._responses = (completion.canonical_bytes(),)
    started = await runtime.begin_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
    )
    captured = await runtime.capture_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, started.head
    )
    sealed = await runtime.seal_execution_complete(
        "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V3"
    )
    decision = next(
        json.loads(raw)
        for _, _, raw in runtime._loop_decisions().entries()
        if json.loads(raw).get("operation_id") == sealed.head
    )
    retained = RetainedExecutionCompleteSealV3.model_validate_json(
        decision["execution_complete_seal"]
    )
    response_seal = retained.exchange.proposal.fan_out.response_seal
    first_capture = runtime._h1_first_path_sources.capture_current(
        original_identity=request.identity,
        original_fingerprint=request.original_driver_command_fingerprint(),
        selected_seal=CallSubjectHead(
            subject_id=response_seal.response_seal_id,
            revision=Present(
                head="record:" + response_seal.digest(), fingerprint=response_seal.digest()
            ),
        ),
    )
    return runtime._h1_native_member_sources.capture_current(first_capture)


@pytest.mark.asyncio
async def test_installed_worker_issuer_appends_authenticated_fence_and_replays_exact_head(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            native_cap = await _current_native_cut(runtime)
            worker_owner = runtime._h1_installed_worker_evidence_owner
            worker_cap = worker_owner._capture_for_pre_request(native_cap)
            issuer = runtime._h1_pre_request_worker_evidence
            assert type(issuer) is H1PreRequestWorkerEvidence
            journal = runtime._h1_delivery_evidence_journal
            before = journal._entries()

            receipt = issuer._issue_worker(native_cap, worker_cap)
            locator, record, head = issuer._replay_worker(receipt)
            readback = journal.read_exact(locator)
            verified = worker_owner._replay_for_pre_request(worker_cap, native_cap)

            assert len(journal._entries()) == len(before) + 1
            assert readback.journal_instance_id == journal._mount.journal_instance_id
            assert type(readback.record) is type(record)
            assert readback.record._value == record._value
            assert readback.canonical_bytes() == record.canonical_bytes()
            assert record._value == {
                "schema_id": "chiplog.execution.h1-worker-fence.v1",
                "deployment_id": DEPLOYMENT,
                "database_id": DATABASE,
                "database_genesis_digest": expected.digest,
                "tenant": TENANT,
                "principal": verified.run.principal,
                "runtime_instance_id": verified.runtime_instance_id,
                "owner_id": "agent_loop",
                "owner_route_generation": verified.owner_route_generation,
                "run": {
                    "identity": verified.run.run_id,
                    "head": verified.run.head,
                    "fingerprint": hashlib.sha256(verified.run.canonical_bytes()).hexdigest(),
                },
                "worker_session_id": verified.worker_session_id,
                "fence": {
                    "kind": "NON_SCHEDULER",
                    "run_id": verified.fence.run_id,
                    "run_head": verified.fence.run_head,
                    "worker_session": verified.fence.worker_session_id,
                    "runtime_generation": verified.fence.runtime_generation,
                    "scheduler_id": "NOT_APPLICABLE",
                    "scheduler_generation": "NOT_APPLICABLE",
                    "scheduler_lease": "NOT_APPLICABLE",
                    "scheduler_lease_generation": "NOT_APPLICABLE",
                },
            }
            assert head.identity.startswith("h1-evidence:worker:")
            assert head.head == "record:" + locator.payload_digest
            assert head.fingerprint == locator.payload_digest

            retry = issuer._issue_worker(native_cap, worker_cap)
            assert issuer._replay_worker(retry)[0] == locator
            assert len(journal._entries()) == len(before) + 1


@pytest.mark.asyncio
async def test_installed_worker_issuer_denies_forged_copied_foreign_issuer_and_public_append(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            native_cap = await _current_native_cut(runtime)
            owner = runtime._h1_installed_worker_evidence_owner
            worker_cap = owner._capture_for_pre_request(native_cap)
            issuer = runtime._h1_pre_request_worker_evidence
            journal = runtime._h1_delivery_evidence_journal
            before = journal._entries()

            with pytest.raises((TypeError, ValueError), match=r"capability|issuer"):
                issuer._issue_worker(native_cap, object())
            with pytest.raises(TypeError, match="copied"):
                copy.copy(worker_cap)
            with pytest.raises(TypeError, match="worker evidence"):
                journal._issue_from_bound_worker_owner(object(), object())
            assert journal._entries() == before

            receipt = issuer._issue_worker(native_cap, worker_cap)
            locator, record, _ = issuer._replay_worker(receipt)
            with pytest.raises((TypeError, ValueError), match="issuer"):
                journal._issue_from_bound_worker_owner(record, object())
            with pytest.raises(RuntimeError, match="awaits private owner"):
                journal.issue_worker(record)
            assert journal.read_exact(locator).canonical_bytes() == record.canonical_bytes()
            assert len(journal._entries()) == len(before) + 1
            with pytest.raises(ValueError, match="unbind issuer"):
                journal._unbind_private_worker_issuer(object())
            issuer._revoke_all()
            with pytest.raises(ValueError, match=r"closed|issuer-owned"):
                issuer._replay_worker(receipt)
            with pytest.raises(ValueError, match="issuer"):
                journal._issue_from_bound_worker_owner(record, issuer)
            assert len(journal._entries()) == len(before) + 1


def test_private_issuer_rejects_unmounted_or_cross_owned_dependencies_before_authority() -> None:
    with pytest.raises(TypeError, match="canonical"):
        H1PreRequestWorkerEvidence(object(), object(), object())


@pytest.mark.asyncio
async def test_installed_worker_issuer_rejects_a_mutated_native_fence_before_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            native_cap = await _current_native_cut(runtime)
            owner = runtime._h1_installed_worker_evidence_owner
            worker_cap = owner._capture_for_pre_request(native_cap)
            issuer = runtime._h1_pre_request_worker_evidence
            journal = runtime._h1_delivery_evidence_journal
            before = journal._entries()
            original = type(owner)._replay_for_pre_request

            def replay_with_mutated_fence(self: object, cap: object, native: object) -> object:
                verified = original(self, cap, native)
                return replace(
                    verified,
                    fence=verified.fence.model_copy(
                        update={"runtime_generation": "mutated-generation"}
                    ),
                )

            monkeypatch.setattr(type(owner), "_replay_for_pre_request", replay_with_mutated_fence)
            with pytest.raises(ValueError, match=r"owner facts|native inverse"):
                issuer._issue_worker(native_cap, worker_cap)
            assert journal._entries() == before
