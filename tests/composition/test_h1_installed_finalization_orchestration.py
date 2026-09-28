"""Installed witnesses for the A-owned H1 finalization orchestration.

The fixture creates every recovery and publication input through the installed
driver.  It deliberately does not construct an issuance, marker, or selected
owner record.
"""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition import common_cli_execution_runtime
from chiplog.composition.common_cli_execution_runtime import (
    _FinalizationReceiptIntegrityError,
    open_installed_h1_runtime,
)
from chiplog.composition.common_execution_driver_contracts import (
    AcceptedTerminalDetailV1,
    CliPeerSelectedSourceV1,
    DriveInputRequestV1,
    DriverCommandIdentityV1,
    ExecutionDriverRejectedV1,
    SelectedExecutionReceiptV1,
    UncertainExecutionPublicationV1,
)
from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV2
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_live_publication_authority import (
    H1LivePublicationAuthority,
    _H1SelectedTerminalReadback,
)
from chiplog.composition.h1_postseal_recovery_coordinator import (
    H1PostSealRecoveryPublicationIntegrityError,
    _H1FinalizationOutcome,
    _H1FinalizationRejection,
    _H1PostSealRecoveryCoordinator,
)
from chiplog.composition.h1_recovery_execution_fence import _H1RecoveryExecutionFence
from chiplog.composition.r14_execution_completion_records import COMPLETE_ACCEPTANCE_OPERATION
from chiplog.composition.r14_execution_inbox_records import RetainedInboxExecutionInitialization
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._ingress_contracts import Head, SourceBinding, UnknownEndpoint
from chiplog.platform._owner_publication_contracts import (
    CompleteDeliveryBatchV2,
    JournalSelectedPublication,
    PublicationRejected,
    WorkerAuthentication,
)
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.ingress_source_contracts import CliPeerObservation
from chiplog.platform.ingress_transition_contracts import (
    IngressCommandIdentity,
    RetainedIngressSource,
)
from chiplog.platform.owner_publications import (
    OwnerPublicationUncertain,
    PreparedOwnerPublication,
    SelectedOwnerDecision,
)
from tests.support.h1_cli_execution import _admit, advance
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


def _identity() -> DriverCommandIdentityV1:
    return DriverCommandIdentityV1(
        tenant_id="tenant",
        database_id="database",
        driver_command_id="driver-command",
        original_ingress_identity=IngressCommandIdentity(
            tenant_id="tenant", database_id="database", command_id="ingress-command"
        ),
        original_ingress_request_fingerprint="a" * 64,
    )


def _head(name: str) -> Head:
    return Head(identity=name, head=name + "/head", fingerprint="a" * 64)


def _driver_request() -> DriveInputRequestV1:
    identity = _identity()
    binding = SourceBinding(
        manifest_row=_head("source-manifest"),
        source_class="CLI",
        tenant_id="tenant",
        database_id="database",
        source_identity="source",
        endpoint_account_binding=UnknownEndpoint(),
        broker_epoch="epoch",
        broker_session="session",
        admission_epoch=_head("admission"),
        admission_fence=1,
        transport_version="transport.v1",
    )
    observation = CliPeerObservation(
        source_binding=binding,
        source_head=_head("source"),
        original_identity="original",
        original_bytes=b"input",
        proof_head=_head("proof"),
    )
    retained = RetainedIngressSource(
        source=observation.source_head,
        reader_id=observation.reader_id,
        schema_id=observation.schema_id,
        canonical_source_bytes=observation.canonical_bytes(),
    )
    return DriveInputRequestV1(
        identity=identity,
        selected_source=CliPeerSelectedSourceV1(
            original_ingress_identity=identity.original_ingress_identity,
            source_binding=binding,
            selected_ingress_decision=_head("ingress-decision"),
            source_head=retained.source,
            retained_source=retained,
        ),
    )


def _synthetic_finalization_inputs() -> tuple[
    RetainedInboxExecutionInitialization, DriveInputRequestV1, _H1FinalizationOutcome
]:
    """Make typed pre-projection evidence without mounting an installed finalizer."""
    request = _driver_request()
    fingerprint = request.original_driver_command_fingerprint()
    terminal = ExecutionRunRecord.model_construct(
        tenant="tenant",
        principal="principal",
        run_id="run",
        state="SUCCEEDED",
        head="run-head",
        event="ExecutionCompleted",
    )
    terminal_raw = terminal.canonical_bytes()
    terminal_record = PhysicalRecord(
        "run-head",
        "agent_loop",
        "chiplog.agent-loop.execution-record.v2",
        terminal_raw,
        hashlib.sha256(terminal_raw).hexdigest(),
    )
    heads = tuple(
        CallSubjectHead(
            subject_id=name,
            revision=Present(head=name + "/head", fingerprint="a" * 64),
        )
        for name in ("ingress", "custody", "inbox")
    )
    evidence = RetainedInboxExecutionInitialization.model_construct(
        driver_request_bytes=request.canonical_bytes(),
        driver_request_fingerprint=fingerprint,
        request=SimpleNamespace(admitted=SimpleNamespace(
            selected_decision=heads[0], custody=heads[1], inbox=heads[2]
        )),
        proposal=SimpleNamespace(run=terminal),
    )
    publication = JournalSelectedPublication.model_construct(
        kind="COMMITTED",
        command_id="command",
        decision_id="decision",
        decision_head="decision-head",
        decision_fingerprint="a" * 64,
        resulting_commitment="a" * 64,
        tenant_commit_sequence=1,
    )
    selected = SelectedOwnerDecision(
        prepared=PreparedOwnerPublication(
            request=cast(Any, object()),
            issuance_id="issued",
            fence_generation="fence",
            fence_frontier=0,
            predecessor_commitment="a" * 64,
        ),
        decision_id="decision",
        decision_head="decision-head",
        decision_fingerprint="a" * 64,
        resulting_commitment="a" * 64,
        tenant_commit_sequence=1,
    )
    command = PhysicalPublicationCommand(
        "tenant",
        "test.finalization",
        "command",
        "a" * 64,
        0,
        "fence",
        0,
        0,
        (terminal_record,),
    )
    readback = _H1SelectedTerminalReadback(
        retained_h0=evidence,
        selected_owner_decision=selected,
        physical_command=command,
        terminal_run=terminal,
        acceptance_head=ExactHead(
            identity="acceptance", head="acceptance", fingerprint="a" * 64
        ),
        delivery_manifest_head=ExactHead(
            identity="manifest", head="manifest", fingerprint="a" * 64
        ),
        committed_conversation_projection_head=ExactHead(
            identity="conversation", head="conversation", fingerprint="a" * 64
        ),
    )
    return evidence, request, _H1FinalizationOutcome(publication, readback)


class _ReadbackAuthority:
    def __init__(self) -> None:
        self.calls = 0

    async def _read_finalization_receipt_held(self, **_kwargs: object) -> object:
        self.calls += 1
        return object()


class _FinalizationCoordinator:
    def __init__(self, outcome: object, after_finalize: Callable[[], None] | None = None) -> None:
        self._outcome = outcome
        self._after_finalize = after_finalize

    async def finalize_selected(self, _identity: object, _fingerprint: str) -> object:
        if self._after_finalize is not None:
            self._after_finalize()
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome


def _runtime_for_finalization(
    outcome: object, evidence: object = object(), after_finalize: Callable[[], None] | None = None
) -> Any:
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime

    runtime = object.__new__(CommonCliExecutionRuntime)
    private = cast(Any, runtime)

    async def execution_actor(_peer: str) -> None:
        return None

    private._execution_actor = execution_actor
    private._find = lambda _identity, _fingerprint: (evidence, "initial-decision")
    private._h1_postseal_recovery_coordinator = _FinalizationCoordinator(outcome, after_finalize)
    return runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ("recovered", "fresh"))
async def test_finalization_selected_broker_result_requires_selected_readback(path: str) -> None:
    """The shared route retains selected-only readback on both broker paths."""
    del path
    authority = _ReadbackAuthority()
    coordinator = object.__new__(_H1PostSealRecoveryCoordinator)
    identity = _identity()
    selected = JournalSelectedPublication.model_construct()

    outcome = await coordinator._route_finalization_publication_held(
        authority=authority,
        identity=identity,
        original_fingerprint="b" * 64,
        publication=selected,
    )

    assert type(outcome) is _H1FinalizationOutcome
    assert outcome.publication is selected
    assert authority.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ("recovered", "fresh"))
async def test_finalization_rejection_skips_selected_readback_on_each_broker_path(
    path: str,
) -> None:
    """A broker refusal never becomes a selected physical receipt."""
    del path
    authority = _ReadbackAuthority()
    coordinator = object.__new__(_H1PostSealRecoveryCoordinator)
    identity = _identity()
    rejection = PublicationRejected(
        kind="STALE", tenant_id="tenant", command_id="command", reason="writer declined"
    )

    outcome = await coordinator._route_finalization_publication_held(
        authority=authority,
        identity=identity,
        original_fingerprint="b" * 64,
        publication=rejection,
    )

    assert type(outcome) is _H1FinalizationRejection
    assert outcome.publication is rejection
    assert authority.calls == 0
    public = await _runtime_for_finalization(outcome).finalize_execution(identity, "b" * 64)
    assert type(public) is ExecutionDriverRejectedV1
    assert public.code == rejection.kind
    assert public.reason == rejection.reason


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ("recovered", "fresh"))
async def test_finalization_unknown_broker_result_is_integrity_without_readback(path: str) -> None:
    """Neither reconciliation nor commit may route a foreign result to readback."""
    del path
    authority = _ReadbackAuthority()
    coordinator = object.__new__(_H1PostSealRecoveryCoordinator)
    identity = _identity()

    with pytest.raises(H1PostSealRecoveryPublicationIntegrityError):
        await coordinator._route_finalization_publication_held(
            authority=authority,
            identity=identity,
            original_fingerprint="b" * 64,
            publication=object(),
        )

    assert authority.calls == 0
    public = await _runtime_for_finalization(
        H1PostSealRecoveryPublicationIntegrityError("foreign broker result")
    ).finalize_execution(identity, "b" * 64)
    assert type(public) is ExecutionDriverRejectedV1
    assert public.code == "INTEGRITY_FAULT"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ("recovered", "fresh"))
async def test_finalization_uncertainty_stays_publicly_uncertain_on_each_broker_path(
    path: str,
) -> None:
    """Uncertain recovery or commit is never collapsed into a HOLD refusal."""
    identity = _identity()
    error = OwnerPublicationUncertain(path + "-command")

    public = await _runtime_for_finalization(error).finalize_execution(identity, "b" * 64)

    assert type(public) is UncertainExecutionPublicationV1
    assert public.identity == identity
    assert public.original_driver_command_fingerprint == "b" * 64
    assert public.operation == "h1.finalize_execution"
    assert public.reason == str(error)


async def _durable_recovery(runtime: Any) -> Any:
    request = await _admit(runtime)
    initial = await runtime.drive_input(request)
    runtime._execution_model._responses = (
        DeliveryCompletion(
            tenant=TENANT,
            run_id=initial.stable_run_lineage_id,
            turn_id=initial.stable_run_lineage_id + "/turn/1",
            deliveries=(ProposedDelivery(payload=(Commentary(text="A finalizer witness"),)),),
        ).canonical_bytes(),
    )
    await runtime.advance_execution(advance(initial, request))
    state = runtime._h1_postseal_recovery_journal.scan().states_by_root[0][1]
    assert len(state.inputs) == len(state.results) == 4
    return request


class H1FinalizationReceiptUnavailable(RuntimeError):
    """The installed finalizer cannot yet project its exact terminal receipt."""


def _require_terminal_receipt(value: object, *, disposition: str) -> SelectedExecutionReceiptV1:
    if (
        type(value) is not SelectedExecutionReceiptV1
        or value.phase != "TERMINAL"
        or value.disposition != disposition
        or value.selected_run_state != "SUCCEEDED"
        or type(value.terminal_detail) is not AcceptedTerminalDetailV1
    ):
        raise H1FinalizationReceiptUnavailable(
            "installed finalizer lacks exact selected V2/readback terminal receipt"
        )
    detail = value.terminal_detail
    assert detail.acceptance_head.identity
    assert detail.delivery_manifest_head.identity
    assert detail.committed_conversation_projection_head.identity
    return value


def _assert_selected_terminal_receipt_is_physical(
    runtime: Any, receipt: SelectedExecutionReceiptV1
) -> None:
    from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2

    selected = [
        decision
        for decision in runtime._owner_decisions().snapshot().decisions
        if decision.decision_id == receipt.selected_journal_decision.head
        and type(decision.prepared.request) is CompleteDeliveryBatchV2
    ]
    assert len(selected) == 1
    decision = selected[0]
    command = runtime._owner_command(decision)
    assert receipt.selected_journal_decision == Head(
        identity=command.idempotency_key,
        head=decision.decision_head,
        fingerprint=decision.decision_fingerprint,
    )
    assert receipt.commit_sequence == decision.tenant_commit_sequence
    terminal_rows = [
        row
        for row in command.records
        if row.owner == "agent_loop" and row.schema_id == "chiplog.agent-loop.execution-record.v2"
    ]
    assert len(terminal_rows) == 1
    terminal = ExecutionRunRecord.model_validate_json(terminal_rows[0].canonical_bytes)
    assert receipt.selected_run_head == Head(
        identity=terminal.head,
        head=terminal.head,
        fingerprint=hashlib.sha256(terminal.canonical_bytes()).hexdigest(),
    )
    assert terminal.state == receipt.selected_run_state == "SUCCEEDED"
    assert type(receipt.terminal_detail) is AcceptedTerminalDetailV1
    for head in (
        receipt.terminal_detail.acceptance_head,
        receipt.terminal_detail.delivery_manifest_head,
        receipt.terminal_detail.committed_conversation_projection_head,
    ):
        matching = [row for row in command.records if row.record_id == head.identity]
        assert len(matching) == 1
        assert head.head == matching[0].record_id
        assert head.fingerprint == matching[0].fingerprint
        assert head.fingerprint == hashlib.sha256(matching[0].canonical_bytes).hexdigest()


@pytest.mark.asyncio
async def test_finalizer_worker_owns_lease_and_settles_after_repeated_caller_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A public cancellation cannot borrow or release the worker's task lease."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    observed_tasks: list[asyncio.Task[object]] = []
    entered = asyncio.Event()
    release = asyncio.Event()

    original_acquire = _H1RecoveryExecutionFence.acquire

    async def record_acquire(fence: _H1RecoveryExecutionFence) -> object:
        task = asyncio.current_task()
        assert task is not None
        observed_tasks.append(task)
        return await original_acquire(fence)

    async def parked_recover(
        self: H1LivePublicationAuthority,
        *,
        identity: object,
        original_fingerprint: object,
        lease: Any,
    ) -> object:
        del self, identity, original_fingerprint
        lease.require_owned()
        task = asyncio.current_task()
        assert task is not None
        observed_tasks.append(task)
        entered.set()
        await release.wait()
        return object()

    async def record_readback(
        self: H1LivePublicationAuthority,
        *,
        identity: object,
        original_fingerprint: object,
        publication: object,
    ) -> object:
        del self, identity, original_fingerprint, publication
        task = asyncio.current_task()
        assert task is not None
        observed_tasks.append(task)
        return object()

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request = await _durable_recovery(runtime)
            monkeypatch.setattr(_H1RecoveryExecutionFence, "acquire", record_acquire)
            monkeypatch.setattr(
                H1LivePublicationAuthority, "_recover_finalization_held", parked_recover
            )
            monkeypatch.setattr(
                H1LivePublicationAuthority, "_read_finalization_receipt_held", record_readback
            )
            coordinator = runtime._h1_postseal_recovery_coordinator
            assert coordinator is not None
            caller = asyncio.create_task(
                coordinator.finalize_selected(
                    request.identity, request.original_driver_command_fingerprint()
                )
            )
            await entered.wait()
            caller.cancel()
            await asyncio.sleep(0)
            assert not caller.done()
            caller.cancel()
            await asyncio.sleep(0)
            assert not caller.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert coordinator._fence._lease is None

    assert len(observed_tasks) == 3
    assert len(set(observed_tasks)) == 1
    assert observed_tasks[0] is not caller


@pytest.mark.asyncio
async def test_finalization_projection_binds_the_prelease_retained_h0_and_caller() -> None:
    """Projection rejects a terminal readback whose H0 or caller was substituted."""
    evidence, request, outcome = _synthetic_finalization_inputs()
    fingerprint = request.original_driver_command_fingerprint()
    runtime = _runtime_for_finalization(outcome)

    divergent_h0 = evidence.model_copy(update={"driver_request_fingerprint": "f" * 64})
    divergent_outcome = replace(
        outcome,
        readback=replace(
            cast(_H1SelectedTerminalReadback, outcome.readback), retained_h0=divergent_h0
        ),
    )
    with pytest.raises(_FinalizationReceiptIntegrityError, match="retained H0 differs"):
        runtime._project_finalization_receipt(
            evidence, request.identity, fingerprint, divergent_outcome
        )

    foreign_identity = request.identity.model_copy(
        update={"driver_command_id": "foreign-driver-command"}
    )
    with pytest.raises(_FinalizationReceiptIntegrityError, match="retained H0 caller differs"):
        runtime._project_finalization_receipt(evidence, foreign_identity, fingerprint, outcome)
    with pytest.raises(_FinalizationReceiptIntegrityError, match="retained H0 caller differs"):
        runtime._project_finalization_receipt(evidence, request.identity, "e" * 64, outcome)


@pytest.mark.asyncio
async def test_finalization_projection_does_not_reread_mutable_h0_after_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The post-lease projection uses the coordinator's immutable terminal readback."""
    evidence, request, outcome = _synthetic_finalization_inputs()

    def forbid_postlease_reads() -> None:
        def forbidden(*_args: object, **_kwargs: object) -> object:
            pytest.fail("finalization projection reread mutable H0 after its lease")

        monkeypatch.setattr(common_cli_execution_runtime, "read_execution_history", forbidden)
        monkeypatch.setattr(runtime, "_receipt", forbidden)

    runtime = _runtime_for_finalization(outcome, evidence, forbid_postlease_reads)
    receipt = await runtime.finalize_execution(
        request.identity, request.original_driver_command_fingerprint()
    )

    terminal = _require_terminal_receipt(receipt, disposition="COMMITTED")
    assert terminal.identity == request.identity
    assert (
        terminal.original_driver_command_fingerprint
        == request.original_driver_command_fingerprint()
    )
    assert type(terminal.terminal_detail) is AcceptedTerminalDetailV1
    assert terminal.terminal_detail.acceptance_head.identity == "acceptance"
    assert terminal.terminal_detail.delivery_manifest_head.identity == "manifest"
    assert (
        terminal.terminal_detail.committed_conversation_projection_head.identity
        == "conversation"
    )


@pytest.mark.asyncio
async def test_finalization_projection_rejects_untyped_selected_physical_readback() -> None:
    """A retained H0 cannot make an untyped selected decision public."""
    evidence, request, outcome = _synthetic_finalization_inputs()
    malformed = replace(
        outcome,
        readback=replace(
            cast(_H1SelectedTerminalReadback, outcome.readback),
            selected_owner_decision=cast(Any, object()),
        ),
    )

    with pytest.raises(_FinalizationReceiptIntegrityError, match="physical readback is untyped"):
        _runtime_for_finalization(malformed)._project_finalization_receipt(
            evidence, request.identity, request.original_driver_command_fingerprint(), malformed
        )


@pytest.mark.asyncio
async def test_installed_finalizer_returns_the_exact_selected_readback_on_replay(
    tmp_path: Path,
) -> None:
    """A selected retry bypasses B/P and returns the retained physical decision."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request = await _durable_recovery(runtime)
            first = _require_terminal_receipt(
                await runtime.finalize_execution(
                    request.identity, request.original_driver_command_fingerprint()
                ),
                disposition="COMMITTED",
            )
            selected = tuple(
                decision
                for decision in runtime._owner_decisions().snapshot().decisions
                if type(decision.prepared.request) is CompleteDeliveryBatchV2
                and decision.prepared.request.operation == COMPLETE_ACCEPTANCE_OPERATION
            )
            assert len(selected) == 1
            batch = selected[0].prepared.request
            enrollment = cast(Any, runtime)._h1_live_completion_enrollment
            markers = tuple(enrollment._issuances.values())
            assert len(markers) == 1
            marker = markers[0]
            assert marker.state == "CONSUMED"
            assert type(marker.issuance) is H1CompletionIssuanceV2
            assert marker.batch == batch
            assert type(batch.authentication) is WorkerAuthentication
            assert batch.authentication.applicability_bytes == marker.issuance.canonical_bytes()
            assert tuple(exchange.role for exchange in marker.issuance.owner_exchanges) == (
                "completion", "conversation", "effects", "terminal_work"
            )
            current_ids = (
                marker.issuance.scope_current_exchange.sent.request_id,
                marker.issuance.terminal_admission.preterminal_current_exchange.sent.request_id,
                marker.issuance.final_current_exchange.sent.request_id,
            )
            assert len(set(current_ids)) == 3
            command = runtime._owner_command(selected[0])
            with sqlite3.connect(runtime._database) as connection:
                physical = tuple(
                    row[0]
                    for record in command.records
                    for row in connection.execute(
                        "SELECT canonical_bytes FROM records WHERE record_id = ?",
                        (record.record_id,),
                    ).fetchall()
                )
            assert physical == tuple(record.canonical_bytes for record in command.records)
            replay = _require_terminal_receipt(
                await runtime.finalize_execution(
                    request.identity, request.original_driver_command_fingerprint()
                ),
                disposition="EXACT_REPLAY",
            )
            _assert_selected_terminal_receipt_is_physical(runtime, first)
            _assert_selected_terminal_receipt_is_physical(runtime, replay)
            assert replay.identity == first.identity
            assert (
                replay.original_driver_command_fingerprint
                == first.original_driver_command_fingerprint
            )
            assert replay.selected_journal_decision == first.selected_journal_decision
            assert replay.selected_run_head == first.selected_run_head
            assert replay.terminal_detail == first.terminal_detail
            assert len(enrollment._issuances) == 1
