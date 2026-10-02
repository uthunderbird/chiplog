"""Mounted selected lease policy and private execution-session ACQUIRE."""

import base64
import json
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

import chiplog.composition.scheduler_source_registry as source_registry
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.composition.r15_lease_policy import LeasePolicyAdoptionRequest, LeasePolicyDocument
from chiplog.composition.r15_lease_publication import (
    LeaseExecutionSession,
    validate_selected_lease,
)
from chiplog.composition.r15_lease_service_auth import LeaseExecutionAuthority
from chiplog.composition.r15_mandate_contracts import MandateIssuanceAdoption
from chiplog.composition.r15_scheduler_registry import SchedulerGenesisAdoption, _canonical, _digest
from chiplog.composition.r15_scheduler_runtime import R15SchedulerRuntime, open_r15_runtime
from chiplog.composition.r15_tick_clock_v1 import EvaluationSchedulerClock, registered_clock_source
from chiplog.composition.r15_tick_contracts import TickPolicyAdoption, TickPolicyDraft
from chiplog.composition.scheduler_source_registry import (
    AdmittedSchedulerStartup,
    read_admitted_scheduler_startup,
)
from chiplog.platform._owner_publication_contracts import (
    BrokerPublicationResult,
    SingleOwnerBatch,
    WorkerAuthentication,
)
from chiplog.platform.broker import PublicPortCall, PublicPortResult
from chiplog.platform.owner_publications import OwnerPublicationUncertain, PreparedOwnerPublication
from tests.composition.test_scheduler_mandate_issuance_runtime import _adoption, _draft

_SIGNER: subprocess.Popen[str]
_AUTHORITY: LeaseExecutionAuthority


@pytest.fixture(autouse=True)
def independent_signer() -> Iterator[None]:
    global _SIGNER, _AUTHORITY
    # The private key exists only in this separate process, never in broker/admin objects.
    program = """import base64,sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
key=Ed25519PrivateKey.generate()
print(base64.b64encode(key.public_key().public_bytes_raw()).decode(),flush=True)
for line in sys.stdin:
 print(base64.b64encode(key.sign(base64.b64decode(line))).decode(),flush=True)
"""
    _SIGNER = subprocess.Popen(
        [sys.executable, "-u", "-c", program],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert _SIGNER.stdout is not None
    _AUTHORITY = LeaseExecutionAuthority(public_key=base64.b64decode(_SIGNER.stdout.readline()))
    yield
    _SIGNER.terminate()
    _SIGNER.wait(timeout=5)


def _sign(message: bytes) -> bytes:
    assert _SIGNER.stdin is not None and _SIGNER.stdout is not None
    _SIGNER.stdin.write(base64.b64encode(message).decode() + "\n")
    _SIGNER.stdin.flush()
    return base64.b64decode(_SIGNER.stdout.readline())


async def _acquire(
    runtime: R15SchedulerRuntime,
    handle: LeaseExecutionSession,
    root: str,
    command: str,
    duration: int = 60_000_000_000,
) -> BrokerPublicationResult:
    signature = (
        _sign(handle.signed_message(root, command, duration))
        if isinstance(handle, LeaseExecutionSession)
        else b""
    )
    return await runtime.acquire_scheduler_lease(
        handle, root, command, duration, signature=signature
    )


def _startup(runtime: R15SchedulerRuntime) -> AdmittedSchedulerStartup:
    result = read_admitted_scheduler_startup(
        runtime._database,
        runtime._tenant_id,
        runtime._owner_decisions(),
        runtime._commitment_journal,
    )
    assert isinstance(result, AdmittedSchedulerStartup)
    return result


async def _genesis_mandate(runtime: R15SchedulerRuntime) -> MandateIssuanceAdoption:
    draft = _draft().model_copy(update={"missed_policy": "MATERIALIZE_EACH"})
    genesis = await runtime.preview_scheduler_genesis("hermetic-ingress", draft)
    assert (
        await runtime.adopt_scheduler_genesis(
            "hermetic-ingress",
            SchedulerGenesisAdoption(
                adoption_act_id=draft.adoption_act_id, command_bytes=genesis.canonical_bytes()
            ),
        )
    ).kind == "COMMITTED"
    mandate = await _adoption(runtime)
    assert (await runtime.adopt_scheduler_mandate("hermetic-ingress", mandate)).kind == "COMMITTED"
    return mandate


async def _setup(
    runtime: R15SchedulerRuntime, legacy: pytest.MonkeyPatch | None = None
) -> tuple[LeaseExecutionSession, str, MandateIssuanceAdoption, LeasePolicyAdoptionRequest]:
    if legacy is None:
        mandate = await _genesis_mandate(runtime)
    else:
        registration = source_registry._historical_v2a_registration(source_registry._observe()[0])
        with legacy.context() as patch:
            patch.setattr(source_registry, "current_scheduler_registration", lambda: registration)
            mandate = await _genesis_mandate(runtime)
        assert (
            runtime._owner_decisions()
            .snapshot()
            .decisions[-1]
            .prepared.request.expected.registry_head.startswith("scheduler-source-registry-v2:")
        )
    draft = _draft()
    tick = await runtime.preview_scheduler_tick_policy(
        "hermetic-ingress",
        TickPolicyDraft(
            adoption_act_id="lease-tick",
            schedule_id=draft.schedule_id,
            delivery_id="lease-tick-delivery",
        ),
    )
    assert (
        await runtime.adopt_scheduler_tick(
            "hermetic-ingress",
            TickPolicyAdoption(adoption_act_id="lease-tick", policy_bytes=tick.canonical_bytes()),
        )
    ).kind == "COMMITTED"
    startup = _startup(runtime)
    policy = LeasePolicyDocument(
        execution_session_id="r15-hermetic-execution-session-v1",
        selected_mandate=startup.index.mandates[0].mandate_head,
        clock_source=registered_clock_source(runtime._tick_clock),
        max_lease_ns=60_000_000_000,
    )
    request = LeasePolicyAdoptionRequest(
        adoption_act_id="adopt-lease-policy", policy_bytes=policy.canonical_bytes()
    )
    assert (
        await runtime.adopt_scheduler_lease_policy("hermetic-ingress", request, _AUTHORITY)
    ).kind == "COMMITTED"
    assert (
        await runtime.adopt_scheduler_lease_policy("hermetic-ingress", request, _AUTHORITY)
    ).kind == "EXACT_REPLAY"
    handle = await runtime.open_scheduler_execution_session("hermetic-ingress")
    return handle, startup.index.lineages[0].lineage.root_id, mandate, request


async def test_mounted_acquire_selected_readback_restart_and_authenticated_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = EvaluationSchedulerClock(1_000_000_000)
    database = tmp_path / "lease.sqlite"
    async with open_r15_runtime(database, clock=clock) as runtime:
        handle, root, mandate, policy = await _setup(runtime, legacy=monkeypatch)
        first = await _acquire(runtime, handle, root, "lease-once")
        assert first.kind == "COMMITTED"
        assert _startup(runtime).index.lineages[0].lease.kind == "HELD"
        replay = await _acquire(runtime, handle, root, "lease-once")
        assert replay.kind == "EXACT_REPLAY" and replay.complete_records == first.complete_records
        with pytest.raises(LoopRejected):
            await _acquire(runtime, replace(handle, nonce="forged"), root, "lease-once")
        changed = await _acquire(runtime, handle, root, "lease-once", 1)
        assert changed.kind == "CONFLICT"
        clock.unix_ns += 61_000_000_000
        expired = await _acquire(runtime, handle, root, "lease-once")
        assert expired.kind == "EXACT_REPLAY"
    async with open_r15_runtime(database, clock=clock) as runtime:
        with pytest.raises(LoopRejected):
            await _acquire(runtime, handle, root, "lease-once")
        fresh = await runtime.open_scheduler_execution_session("hermetic-ingress")
        replay = await _acquire(runtime, fresh, root, "lease-once")
        assert replay.kind == "EXACT_REPLAY" and replay.complete_records == first.complete_records
        assert (
            await runtime.adopt_scheduler_mandate("hermetic-ingress", mandate)
        ).kind == "EXACT_REPLAY"
        assert (
            await runtime.adopt_scheduler_lease_policy("hermetic-ingress", policy, _AUTHORITY)
        ).kind == "EXACT_REPLAY"


async def test_mounted_acquire_rejects_missing_policy_and_wrong_session(tmp_path: Path) -> None:
    async with open_r15_runtime(
        tmp_path / "missing.sqlite", clock=EvaluationSchedulerClock(1_000_000_000)
    ) as runtime:
        with pytest.raises(LoopRejected):
            await runtime.open_scheduler_execution_session("hermetic-ingress")
        with pytest.raises(LoopRejected):
            await _acquire(runtime, cast(LeaseExecutionSession, "hermetic-ingress"), "root", "cmd")
        assert not any(
            item.prepared.request.operation == "scheduler.acquire"
            for item in runtime._owner_decisions().snapshot().decisions
        )


async def test_mounted_acquire_expiry_between_owner_and_writer_selects_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = EvaluationSchedulerClock(1_000_000_000)
    async with open_r15_runtime(tmp_path / "expired.sqlite", clock=clock) as runtime:
        handle, root, _, _ = await _setup(runtime)
        owner = runtime._supervisor.runtime()
        original = owner.call

        async def expire(call: PublicPortCall) -> PublicPortResult:
            response = await original(call)
            if call.operation_id == "scheduler.prepare_lease":
                clock.unix_ns += 61_000_000_000
            return response

        monkeypatch.setattr(owner, "call", expire)
        with pytest.raises(LoopRejected, match="expired"):
            await _acquire(runtime, handle, root, "expires-before-cas")
        assert _startup(runtime).index.lineages[0].lease.kind == "UNLEASED"
        assert not any(
            item.prepared.request.operation == "scheduler.acquire"
            for item in runtime._owner_decisions().snapshot().decisions
        )


async def test_mounted_selection_expiry_after_staged_inserts_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chiplog.platform.authority_reads as reads

    clock = EvaluationSchedulerClock(1_000_000_000)
    async with open_r15_runtime(tmp_path / "late-expiry.sqlite", clock=clock) as runtime:
        handle, root, _, _ = await _setup(runtime)
        original = reads.capture_authority_snapshot_bytes

        def expire(connection: sqlite3.Connection, tenant: str) -> bytes:
            raw = original(connection, tenant)
            if connection.execute(
                "SELECT COUNT(*) FROM records "
                "WHERE schema_id='chiplog.scheduler.lease-transition.v1'"
            ).fetchone()[0]:
                clock.unix_ns += 61_000_000_000
            return raw

        monkeypatch.setattr(reads, "capture_authority_snapshot_bytes", expire)
        with pytest.raises(LoopRejected, match="before durable selection"):
            await _acquire(runtime, handle, root, "late-expiry")
        assert _startup(runtime).index.lineages[0].lease.kind == "UNLEASED"
        assert not any(
            item.prepared.request.operation == "scheduler.acquire"
            for item in runtime._owner_decisions().snapshot().decisions
        )


async def test_mounted_selected_acquire_crash_recovers_exact_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = EvaluationSchedulerClock(1_000_000_000)
    database = tmp_path / "lease-crash.sqlite"
    async with open_r15_runtime(database, clock=clock) as runtime:
        handle, root, _, _ = await _setup(runtime)
        journal = runtime._owner_decisions()
        original = journal.select

        def crash(prepared: PreparedOwnerPublication, commitment: str) -> None:
            original(prepared, commitment)
            raise RuntimeError("injected after lease selection")

        monkeypatch.setattr(journal, "select", crash)
        with pytest.raises(OwnerPublicationUncertain):
            await _acquire(runtime, handle, root, "crash-lease")
        selected = journal.snapshot().decisions[-1]
        assert selected.prepared.request.operation == "scheduler.acquire"
        retained = selected.prepared.request.complete_records
        monkeypatch.setattr(journal, "select", original)
    clock.unix_ns += 61_000_000_000
    async with open_r15_runtime(database, clock=clock) as runtime:
        fresh = await runtime.open_scheduler_execution_session("hermetic-ingress")
        replay = await _acquire(runtime, fresh, root, "crash-lease")
        assert replay.kind == "EXACT_REPLAY" and replay.complete_records == retained
        assert _startup(runtime).index.lineages[0].lease.kind == "HELD"


async def test_mounted_acquire_bounded_scope_and_current_auth_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_r15_runtime(
        tmp_path / "lease-refusals.sqlite", clock=EvaluationSchedulerClock(1_000_000_000)
    ) as runtime:
        handle, root, _, policy = await _setup(runtime)
        for rejected in (
            replace(handle, service_identity="forged-service"),
            replace(handle, session_id="hermetic-session"),
        ):
            with pytest.raises(LoopRejected):
                await _acquire(runtime, rejected, root, "refused")
        with pytest.raises(LoopRejected, match="sixty seconds"):
            await _acquire(runtime, handle, root, "too-long", 60_000_000_001)
        with pytest.raises(LoopRejected, match="tick-created root"):
            await _acquire(runtime, handle, "forged-root", "wrong-root")
        changed = policy.model_copy(update={"policy_bytes": policy.policy_bytes + b" "})
        assert (
            await runtime.adopt_scheduler_lease_policy("hermetic-ingress", changed, _AUTHORITY)
        ).kind == "CONFLICT"
        assert not any(
            item.prepared.request.operation == "scheduler.acquire"
            for item in runtime._owner_decisions().snapshot().decisions
        )
        selected = await _acquire(runtime, handle, root, "auth-replay")
        assert selected.kind == "COMMITTED"
        with pytest.raises(LoopRejected, match="genesis differs"):
            await _acquire(runtime, handle, root, "already-held")
        with monkeypatch.context() as patch:
            patch.setattr(runtime, "_trust_observation_guard", lambda observed: "DENIED")
            with pytest.raises(LoopRejected, match="authentication"):
                await _acquire(runtime, handle, root, "auth-replay")


def _changed_evidence(batch: SingleOwnerBatch, body: dict[str, object]) -> SingleOwnerBatch:
    assert isinstance(batch.authentication, WorkerAuthentication)
    auth = batch.authentication
    raw = _canonical(body)
    fingerprint = _digest(raw)
    proof = auth.invocation.model_copy(
        update={
            "issuance_fingerprint": _digest(
                _canonical(
                    [
                        auth.invocation.issuance_id,
                        batch.identity.model_dump(mode="json"),
                        batch.command.model_dump(mode="json"),
                        fingerprint,
                    ]
                )
            )
        }
    )
    modified = batch.model_copy(
        update={
            "authentication": auth.model_copy(
                update={
                    "invocation": proof,
                    "applicability_bytes": raw,
                    "applicability_fingerprint": fingerprint,
                }
            )
        }
    )
    return modified.model_copy(
        update={
            "complete_batch_fingerprint": _digest(
                _canonical(modified.model_dump(mode="json", exclude={"complete_batch_fingerprint"}))
            )
        }
    )


async def test_selected_lease_history_rejects_rehashed_denied_trust_and_wrong_invocation(
    tmp_path: Path,
) -> None:
    async with open_r15_runtime(
        tmp_path / "lease-history.sqlite", clock=EvaluationSchedulerClock(1_000_000_000)
    ) as runtime:
        handle, root, _, _ = await _setup(runtime)
        result = await _acquire(runtime, handle, root, "history-lease")
        assert result.kind == "COMMITTED"
        for decision in runtime._owner_decisions().snapshot().decisions:
            if decision.prepared.request.operation not in (
                "r15.adopt_lease_execution_policy",
                "scheduler.acquire",
            ):
                continue
            batch = decision.prepared.request
            assert isinstance(batch, SingleOwnerBatch) and isinstance(
                batch.authentication, WorkerAuthentication
            )
            body = json.loads(batch.authentication.applicability_bytes)
            generations = json.loads(base64.b64decode(body["generation_preimages"]))
            generations["authentication"]["response"]["disposition"] = "REJECTED"
            body["generation_preimages"] = base64.b64encode(_canonical(generations)).decode()
            with pytest.raises(LoopRejected, match="trust IPC"):
                validate_selected_lease(_changed_evidence(batch, body))
            body = json.loads(batch.authentication.applicability_bytes)
            wrong = _changed_evidence(batch, body)
            auth = wrong.authentication
            assert isinstance(auth, WorkerAuthentication)
            wrong = wrong.model_copy(
                update={
                    "authentication": auth.model_copy(
                        update={
                            "invocation": auth.invocation.model_copy(
                                update={"operation_subject": "wrong"}
                            )
                        }
                    )
                }
            )
            wrong = wrong.model_copy(
                update={
                    "complete_batch_fingerprint": _digest(
                        _canonical(
                            wrong.model_dump(mode="json", exclude={"complete_batch_fingerprint"})
                        )
                    )
                }
            )
            with pytest.raises(LoopRejected, match="trust IPC"):
                validate_selected_lease(wrong)


async def test_independent_execution_signature_required_for_acquire_and_replay(
    tmp_path: Path,
) -> None:
    async with open_r15_runtime(
        tmp_path / "signed.sqlite", clock=EvaluationSchedulerClock(1_000_000_000)
    ) as runtime:
        handle, root, _, _ = await _setup(runtime)
        # Admin can adopt and obtain an inert challenge, but possession of its CLI
        # credential/peer spelling does not authenticate the execution principal.
        for signature in (b"", b"x" * 64, _sign(handle.signed_message(root, "other-command"))):
            with pytest.raises(LoopRejected, match="execution signature"):
                await runtime.acquire_scheduler_lease(
                    handle, root, "signed-once", signature=signature
                )
        valid = _sign(handle.signed_message(root, "signed-once"))
        with pytest.raises(LoopRejected, match="execution signature"):
            await runtime.acquire_scheduler_lease(handle, root, "signed-once", 1, signature=valid)
        first = await runtime.acquire_scheduler_lease(handle, root, "signed-once", signature=valid)
        assert first.kind == "COMMITTED"
        with pytest.raises(LoopRejected, match="execution signature"):
            await runtime.acquire_scheduler_lease(handle, root, "signed-once")
        fresh = await runtime.open_scheduler_execution_session("hermetic-ingress")
        with pytest.raises(LoopRejected, match="execution signature"):
            await runtime.acquire_scheduler_lease(fresh, root, "signed-once", signature=valid)
        result = await _acquire(runtime, fresh, root, "signed-once")
        assert result.kind == "EXACT_REPLAY" and result.complete_records == first.complete_records


async def test_execution_challenge_removed_at_final_selection_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_r15_runtime(
        tmp_path / "signed-cas.sqlite", clock=EvaluationSchedulerClock(1_000_000_000)
    ) as runtime:
        handle, root, _, _ = await _setup(runtime)
        journal = runtime._owner_decisions()
        import chiplog.platform.authority_reads as reads

        original = reads.capture_authority_snapshot_bytes

        def withdraw(connection: sqlite3.Connection, tenant: str) -> bytes:
            value = original(connection, tenant)
            if connection.execute(
                "SELECT COUNT(*) FROM records "
                "WHERE schema_id='chiplog.scheduler.lease-transition.v1'"
            ).fetchone()[0]:
                runtime._lease_execution_sessions.clear()
            return value

        monkeypatch.setattr(reads, "capture_authority_snapshot_bytes", withdraw)
        before = len(journal.snapshot().decisions)
        with pytest.raises(LoopRejected):
            await _acquire(runtime, handle, root, "withdraw-before-select")
        assert len(journal.snapshot().decisions) == before
        assert _startup(runtime).index.lineages[0].lease.kind == "UNLEASED"
