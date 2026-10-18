"""Mounted source facts for the deliberately narrow R14 cancellation recovery slice."""

from __future__ import annotations

import asyncio
import hashlib
import struct
from pathlib import Path
from unittest.mock import patch

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
    ExecutionContinue,
    SelfEffectArguments,
)
from chiplog.capabilities.agent_loop.recovery_contracts import NotApplicable, Present
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import ConsequentialAcceptedCall
from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
    execution_native_one_call_cancellation_frontier_registry_v1,
    frontier_registry_reference,
)
from chiplog.capabilities.deployment_trust.cli_custody_contracts import (
    CliCustodyOffer,
    CliCustodyResponse,
)
from chiplog.composition.common_cli_execution_runtime import (
    R17_RETAINED_READER_ID,
    CommonCliExecutionRuntime,
    open_common_cli_execution_runtime,
)
from chiplog.composition.common_execution_driver_contracts import (
    CliRetainedSelectedSourceV1,
    DriveInputRequestV1,
    DriverCommandIdentityV1,
    SelectedExecutionReceiptV1,
)
from chiplog.composition.r14_cancellation_contracts import CancelCallSubmission
from chiplog.composition.r14_execution_recovery_source import R14NativeCancelledCallSource
from chiplog.composition.r14_fanout_records import reference
from chiplog.composition.r14_loop_history import read_execution_recovery_history
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.ingress_transition_contracts import (
    IngressCommandIdentity,
    RetainedIngressSource,
)


async def _custody_client(path: Path) -> None:
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        size = struct.unpack("!I", await reader.readexactly(4))[0]
        offer = CliCustodyOffer.model_validate_json(await reader.readexactly(size))
        writer.write(
            struct.pack(
                "!I",
                len(
                    CliCustodyResponse(
                        challenge_fingerprint=hashlib.sha256(
                            offer.challenge.canonical_bytes()
                        ).hexdigest()
                    ).canonical_bytes()
                ),
            )
        )
        writer.write(
            CliCustodyResponse(
                challenge_fingerprint=hashlib.sha256(offer.challenge.canonical_bytes()).hexdigest()
            ).canonical_bytes()
        )
        await writer.drain()
        writer.write_eof()
    finally:
        writer.close()
        await writer.wait_closed()


async def _admit(runtime: CommonCliExecutionRuntime, slot: str) -> DriveInputRequestV1:
    runtime.provision_retained(slot, b"prepare one cancellable effect")
    await runtime.allocate_receipt(slot)
    await runtime.stage_receipt(slot)
    token = runtime.ingress_history().custody.entries[-1].token.token_id
    async with runtime.cli_custody(slot) as custody:
        client = asyncio.create_task(_custody_client(custody.socket.path))
        assert (await custody.admit()).kind == "COMMITTED"
        await client
    admitted = runtime.read_admitted_inbox(token)
    assert admitted is not None
    record = admitted.record
    identity = IngressCommandIdentity(
        tenant_id="hermetic-tenant", database_id="hermetic-database", command_id=token
    )
    retained = RetainedIngressSource(
        source=record.command.retention.proof,
        reader_id=R17_RETAINED_READER_ID,
        schema_id="chiplog.ingress.retained-source-observation.v1",
        canonical_source_bytes=record.command.retention.observation_bytes,
    )
    return DriveInputRequestV1(
        identity=DriverCommandIdentityV1(
            tenant_id="hermetic-tenant",
            database_id="hermetic-database",
            driver_command_id="driver:" + token,
            original_ingress_identity=identity,
            original_ingress_request_fingerprint=record.inbox.authentication_request_fingerprint,
        ),
        selected_source=CliRetainedSelectedSourceV1(
            original_ingress_identity=identity,
            source_binding=record.command.token.source,
            selected_ingress_decision=admitted.selected_decision,
            source_head=retained.source,
            retained_source=retained,
            expected_reader_id=R17_RETAINED_READER_ID,
        ),
    )


async def _selected_cancelled(runtime: CommonCliExecutionRuntime, slot: str = "slot") -> None:
    request = await _admit(runtime, slot)
    initial = await runtime.drive_input(request)
    assert isinstance(initial, SelectedExecutionReceiptV1)
    run_id = initial.stable_run_lineage_id
    started = await runtime.begin_execution(
        "hermetic-ingress", run_id, initial.selected_run_head.head
    )
    captured = await runtime.capture_execution("hermetic-ingress", run_id, started.head)
    sealed = await runtime.seal_execution("hermetic-ingress", run_id, captured.head)
    _, inventory, _, _, _, _ = read_execution_recovery_history(runtime)
    row = inventory.ordered_calls[0]
    submission = CancelCallSubmission(
        act_id="cancel-native-effect",
        original_call_id=row.original_call_id,
        initialized=reference(row.initialized_record.original_call_id, row.initialized_record),
        current_run=CallSubjectHead(
            subject_id=sealed.run_id,
            revision=Present(head=sealed.head, fingerprint=sealed.digest()),
        ),
    )
    await runtime.cancel_execution_call("hermetic-ingress", submission)


def _response() -> bytes:
    return ExecutionContinue(
        kind="Continue",
        tool_calls=(
            ConsequentialToolCall(
                call_id="effect",
                tool="request_self_effect",
                arguments=SelfEffectArguments(payload=b"effect", bundle_members=("one",)),
            ),
        ),
    ).canonical_bytes()


async def test_collects_real_selected_native_cancellation_and_reopens(tmp_path: Path) -> None:
    database = tmp_path / "recovery-source.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(_response(),)
    ) as runtime:
        await _selected_cancelled(runtime)
        collected = R14NativeCancelledCallSource(runtime).collect()
        assert collected is not None
        assert len(collected.complete_ordered_native_response_seals) == 1
        registry = execution_native_one_call_cancellation_frontier_registry_v1()
        assert collected.frontier_registry_bytes == registry.canonical_bytes()
        assert collected.frontier_registry_reference == frontier_registry_reference(registry)
        assert collected.selected_response_seal == collected.physical_response_seal
        assert collected.selected_initialized == collected.physical_initialized
        assert collected.selected_sealed_run == collected.physical_sealed_run
        assert collected.cancellation_terminal.original_call_id == collected.original_call_id
        assert collected.not_executed_result.outcome == "NOT_EXECUTED"
    async with open_common_cli_execution_runtime(database, resources=resources) as reopened:
        assert R14NativeCancelledCallSource(reopened).collect() == collected


async def test_holds_for_accepted_or_any_extra_selected_seal(tmp_path: Path) -> None:
    database = tmp_path / "recovery-source-hold.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(_response(), _response())
    ) as runtime:
        await _selected_cancelled(runtime)
        source = R14NativeCancelledCallSource(runtime)
        history = read_execution_recovery_history(runtime)
        snapshot, inventory, legacy_seals, seals, cancellations, _acceptances = history
        # The cancellation's own accepted branch is ineligible. An accepted
        # sibling is allowed by the per-call projection (covered by the mounted
        # mixed-member test), so a global acceptance-list veto is obsolete.
        assert source.collect() is not None
        target = inventory.ordered_calls[0]
        accepted = Present(head="accepted-target", fingerprint="a" * 64)
        intent = Present(head="target-intent", fingerprint="b" * 64)
        accepted_target = target.model_copy(
            update={
                "acceptance": ConsequentialAcceptedCall(
                    initialized=target.initialized.revision,
                    accepted=accepted,
                    execution_intent=intent,
                    external_effect_intent=NotApplicable(),
                    complete_acceptance_manifest=(accepted, intent),
                )
            }
        )
        accepted_inventory = inventory.model_copy(update={"ordered_calls": (accepted_target,)})
        with patch(
            "chiplog.composition.r14_execution_recovery_source.read_execution_recovery_history",
            return_value=(snapshot, accepted_inventory, legacy_seals, seals, cancellations, ()),
        ):
            assert source.collect() is None
        with patch(
            "chiplog.composition.r14_execution_recovery_source.read_execution_recovery_history",
            return_value=(snapshot, inventory, (object(),), seals, cancellations, ()),
        ):
            assert source.collect() is None
        # A selected fanout cannot stand in for its separately enumerated
        # physical Run: the latter must carry the same seal and initialization.
        physical = next(
            run for run in snapshot.records if run.head == seals[0].proposal.sealed_run.head
        )
        turn = physical.turns[-1].model_copy(update={"response_seal": None})
        damaged = physical.model_copy(update={"turns": (*physical.turns[:-1], turn)})
        damaged_snapshot = snapshot.model_copy(
            update={
                "records": tuple(
                    damaged if run.head == physical.head else run for run in snapshot.records
                )
            }
        )
        with patch(
            "chiplog.composition.r14_execution_recovery_source.read_execution_recovery_history",
            return_value=(damaged_snapshot, inventory, legacy_seals, seals, cancellations, ()),
        ):
            assert source.collect() is None
        # A later real selected seal is enough to hold: collection cannot pick
        # just its first (or last) matching-looking witness.
        request = await _admit(runtime, "slot-two")
        initial = await runtime.drive_input(request)
        assert isinstance(initial, SelectedExecutionReceiptV1)
        started = await runtime.begin_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, started.head
        )
        await runtime.seal_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, captured.head
        )
        assert len(read_execution_recovery_history(runtime)[3]) == 2
        assert source.collect() is None
