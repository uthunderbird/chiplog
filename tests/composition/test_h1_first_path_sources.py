"""Bounded raw-source seam for H1 first-path completion."""

import asyncio
import hashlib
import json
import struct
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import cast

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
    execution_zero_call_frontier_registry,
)
from chiplog.composition.common_cli_execution_runtime import (
    R17_RETAINED_READER_ID,
    CommonCliExecutionRuntime,
    open_common_cli_execution_runtime,
)
from chiplog.composition.common_execution_driver_contracts import (
    AdvanceExecutionRequestV1,
    CliRetainedSelectedSourceV1,
    DriveInputRequestV1,
    DriverCommandIdentityV1,
)
from chiplog.composition.h1_first_path_sources import (
    H1FirstPathCapture,
    H1FirstPathPhysicalMember,
    H1FirstPathSources,
)
from chiplog.composition.r14_execution_complete_seal_records import (
    EXECUTION_COMPLETE_SEAL_OPERATION,
    RetainedExecutionCompleteSeal,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.ingress_transition_contracts import (
    IngressCommandIdentity,
    RetainedIngressSource,
)


async def _custody_client(path: Path) -> None:
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        size = struct.unpack("!I", await reader.readexactly(4))[0]
        from chiplog.capabilities.deployment_trust.cli_custody_contracts import (
            CliCustodyOffer,
            CliCustodyResponse,
        )

        offer = CliCustodyOffer.model_validate_json(await reader.readexactly(size))
        response = CliCustodyResponse(
            challenge_fingerprint=hashlib.sha256(offer.challenge.canonical_bytes()).hexdigest()
        ).canonical_bytes()
        writer.write(struct.pack("!I", len(response)) + response)
        await writer.drain()
        writer.write_eof()
    finally:
        writer.close()
        await writer.wait_closed()


async def _selected_request(runtime: CommonCliExecutionRuntime) -> DriveInputRequestV1:
    runtime.provision_retained("first-path", b"native first path")
    await runtime.allocate_receipt("first-path")
    await runtime.stage_receipt("first-path")
    token = next(
        entry.token.token_id
        for entry in runtime.ingress_history().custody.entries
        if entry.token.receive_slot == "first-path"
    )
    async with runtime.cli_custody("first-path") as custody:
        client = asyncio.create_task(_custody_client(custody.socket.path))
        assert (await custody.admit()).kind == "COMMITTED"
        await client
    admitted = runtime.read_admitted_inbox(token)
    assert admitted is not None
    identity = IngressCommandIdentity(
        tenant_id="hermetic-tenant", database_id="hermetic-database", command_id=token
    )
    retained = RetainedIngressSource(
        source=admitted.record.command.retention.proof,
        reader_id=R17_RETAINED_READER_ID,
        schema_id="chiplog.ingress.retained-source-observation.v1",
        canonical_source_bytes=admitted.record.command.retention.observation_bytes,
    )
    return DriveInputRequestV1(
        identity=DriverCommandIdentityV1(
            tenant_id="hermetic-tenant",
            database_id="hermetic-database",
            driver_command_id="driver:" + token,
            original_ingress_identity=identity,
            original_ingress_request_fingerprint=admitted.record.inbox.authentication_request_fingerprint,
        ),
        selected_source=CliRetainedSelectedSourceV1(
            original_ingress_identity=identity,
            source_binding=admitted.record.command.token.source,
            selected_ingress_decision=admitted.selected_decision,
            source_head=retained.source,
            retained_source=retained,
            expected_reader_id=R17_RETAINED_READER_ID,
        ),
    )


def test_reader_rejects_a_noncanonical_runtime() -> None:
    with pytest.raises(TypeError, match="canonical common CLI runtime"):
        H1FirstPathSources(cast("CommonCliExecutionRuntime", object()))


def test_physical_member_is_immutable_provenance() -> None:
    member = H1FirstPathPhysicalMember(
        decision_id="decision",
        decision_fingerprint="a" * 64,
        operation_kind="agent_loop.execution-transition.v2",
        publication_id="run",
        commit_sequence=1,
        record_id="run",
        owner="agent_loop",
        schema_id="chiplog.agent-loop.execution-record.v3",
        canonical_bytes=b"record",
    )
    with pytest.raises(FrozenInstanceError):
        member.record_id = "substituted"  # type: ignore[misc]


@pytest.mark.parametrize(
    "family",
    [row.family for row in execution_zero_call_frontier_registry().ordered_rows],
)
def test_every_unfrozen_frontier_family_fails_closed(family: str) -> None:
    with pytest.raises(ValueError, match="frontier family mapping is not frozen"):
        H1FirstPathSources._require_frozen_family_mapping(family)


def test_capture_type_checks_selected_seal_before_reading_runtime() -> None:
    reader = object.__new__(H1FirstPathSources)
    reader._runtime = cast("CommonCliExecutionRuntime", object())
    with pytest.raises(TypeError, match="exact selected response-seal locator"):
        reader.capture_current(
            original_identity=cast("DriverCommandIdentityV1", object()),
            original_fingerprint="a" * 64,
            selected_seal=cast("CallSubjectHead", object()),
        )


def test_unissued_capture_is_not_current() -> None:
    reader = object.__new__(H1FirstPathSources)
    reader._issued = {}
    assert reader.check_current(cast_capture(object())) is False


async def test_raw_reader_verifies_native_h0_lineage_and_physical_seal_before_failing_closed(
    tmp_path: Path,
) -> None:
    database = tmp_path / "first-path.sqlite"
    custody = tmp_path / "dispatch-custody"
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(database, resources=resources) as runtime:
        request = await _selected_request(runtime)
        admitted = runtime._selected_input(request, resources.observe())
        response = DeliveryCompletion(
            tenant="hermetic-tenant",
            run_id=runtime._run_id(admitted),
            turn_id=runtime._run_id(admitted) + "/turn/1",
            deliveries=(ProposedDelivery(payload=(Commentary(text="first path"),)),),
        ).canonical_bytes()
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(response,)
    ) as runtime:
        initial = await runtime.drive_input(request)
        assert initial.kind == "SELECTED_EXECUTION_RECEIPT_V1"
        completed = await runtime.advance_execution(
            AdvanceExecutionRequestV1(
                identity=request.identity,
                original_driver_command_fingerprint=request.original_driver_command_fingerprint(),
                expected_selected_run_head=initial.selected_run_head,
            )
        )
        assert completed.kind == "SELECTED_EXECUTION_RECEIPT_V1"
        seals = []
        for _, _, raw in runtime._loop_decisions().entries():
            entry = json.loads(raw)
            if entry.get("operation_kind") != EXECUTION_COMPLETE_SEAL_OPERATION:
                continue
            retained = RetainedExecutionCompleteSeal.model_validate_json(
                entry["execution_complete_seal"]
            )
            seal = retained.exchange.proposal.fan_out.response_seal
            seals.append(
                CallSubjectHead(
                    subject_id=seal.response_seal_id,
                    revision=Present(head="record:" + seal.digest(), fingerprint=seal.digest()),
                )
            )
        assert len(seals) == 1
        reader = H1FirstPathSources(runtime)
        cut = reader._read_selected_cut(
            original_identity=request.identity,
            original_fingerprint=request.original_driver_command_fingerprint(),
            selected_seal=seals[0],
        )
        assert len(cut.lineage) >= 4
        assert cut.lineage[-1][0] == cut.seal
        assert tuple(member.record_id for member in cut.physical_members[-2:]) == (
            cut.seal_record.record_id,
            cut.registry_record.record_id,
        )
        assert cut.seal_record.record_id == "record:" + seals[0].revision.fingerprint
        assert cut.registry_record.schema_id == "chiplog.recovery.frontier-registry.v1"
        with pytest.raises(ValueError, match="frontier family mapping is not frozen"):
            reader.capture_current(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=seals[0],
            )


def cast_capture(value: object) -> H1FirstPathCapture:
    """Avoid inventing a structurally valid capture in the provenance test."""
    return cast("H1FirstPathCapture", value)
