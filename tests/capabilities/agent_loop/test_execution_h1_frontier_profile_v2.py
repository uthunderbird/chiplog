"""V2 H1 frontier extraction fails closed before a broker proves closure."""

import asyncio
import hashlib
import json
import struct
from pathlib import Path
from typing import cast

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallSubjectHead,
    SealedResponseRecord,
)
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_h1_frontier_profile_v2 import (
    H1FrontierProfileV2Error,
    H1VerifiedWorkspaceClosure,
    H1WorkspaceClosure,
    _binding,
    derive_h1_frontier_profile_v2_members,
)
from chiplog.capabilities.agent_loop.execution_initialization_contracts import (
    SelectedAdmittedRunInput,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
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
    SelectedExecutionReceiptV1,
)
from chiplog.composition.h1_first_path_sources import H1FirstPathSources
from chiplog.composition.r14_execution_complete_seal_records import (
    EXECUTION_COMPLETE_SEAL_OPERATION,
    RetainedExecutionCompleteSealV2,
)
from chiplog.composition.r14_execution_inbox_records import RetainedInboxExecutionInitialization
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
    runtime.provision_retained("profile-v2", b"native V2 profile")
    await runtime.allocate_receipt("profile-v2")
    await runtime.stage_receipt("profile-v2")
    token = next(
        entry.token.token_id
        for entry in runtime.ingress_history().custody.entries
        if entry.token.receive_slot == "profile-v2"
    )
    async with runtime.cli_custody("profile-v2") as custody:
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


async def _genuine_v2_inputs(
    tmp_path: Path,
) -> tuple[ExecutionRunRecord, SelectedAdmittedRunInput, SealedResponseRecord, H1WorkspaceClosure]:
    database = tmp_path / "profile-v2.sqlite"
    custody = tmp_path / "dispatch-custody"
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(database, resources=resources) as runtime:
        request = await _selected_request(runtime)
        admitted = runtime._selected_input(request, resources.observe())
        response = DeliveryCompletion(
            tenant="hermetic-tenant",
            run_id=runtime._run_id(admitted),
            turn_id=runtime._run_id(admitted) + "/turn/1",
            deliveries=(ProposedDelivery(payload=(Commentary(text="profile V2"),)),),
        ).canonical_bytes()
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(response,)
    ) as runtime:
        initial = cast("SelectedExecutionReceiptV1", await runtime.drive_input(request))
        await runtime.advance_execution(
            AdvanceExecutionRequestV1(
                identity=request.identity,
                original_driver_command_fingerprint=request.original_driver_command_fingerprint(),
                expected_selected_run_head=initial.selected_run_head,
            )
        )
        seals: list[CallSubjectHead] = []
        for _, _, decision_bytes in runtime._loop_decisions().entries():
            entry = json.loads(decision_bytes)
            if entry.get("operation_kind") == EXECUTION_COMPLETE_SEAL_OPERATION:
                retained = RetainedExecutionCompleteSealV2.model_validate_json(
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
        selected_raw = reader._read_selected_cut(
            original_identity=request.identity,
            original_fingerprint=request.original_driver_command_fingerprint(),
            selected_seal=seals[0],
        )
        _, workspace = reader._resolve_workspace_closure(selected_raw, historical=False)
        initialization = RetainedInboxExecutionInitialization.model_validate_json(
            json.loads(selected_raw.initialization.raw)["inbox_initialization"]
        )
        return (
            selected_raw.lineage[-1][1],
            initialization.request.admitted,
            selected_raw.sealed_response,
            workspace,
        )


def test_v2_extractor_requires_original_workspace_closure_before_carrier_use() -> None:
    with pytest.raises(H1FrontierProfileV2Error) as raised:
        derive_h1_frontier_profile_v2_members(
            final_run=object(),  # type: ignore[arg-type]
            selected_admitted_input=object(),  # type: ignore[arg-type]
            seal=object(),  # type: ignore[arg-type]
            verified_workspace=None,
        )

    assert raised.value.code == "H1_WORKSPACE_ORIGINAL_READ_UNPROVEN"


async def test_v2_extractor_accepts_genuine_native_issuance_and_keeps_raw_binding(
    tmp_path: Path,
) -> None:
    final_run, admitted, seal, workspace = await _genuine_v2_inputs(tmp_path)
    native = workspace.proposal_context_bytes
    context = json.loads(native)
    sorted_bytes = json.dumps(
        context, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()
    assert native != sorted_bytes

    members = derive_h1_frontier_profile_v2_members(
        final_run=final_run,
        selected_admitted_input=admitted,
        seal=seal,
        verified_workspace=workspace,
    )
    assert members == derive_h1_frontier_profile_v2_members(
        final_run=final_run,
        selected_admitted_input=admitted,
        seal=seal,
        verified_workspace=workspace,
    )
    workspace_evidence = next(
        member
        for member in members
        if member.family == "EVIDENCE" and member.subject == "workspace-batch"
    )
    assert workspace_evidence.ordered_heads == (
        _binding("EVIDENCE", "workspace-batch", {"content": native.decode(), "context": context}),
    )

    with pytest.raises(H1FrontierProfileV2Error) as raised:
        derive_h1_frontier_profile_v2_members(
            final_run=final_run,
            selected_admitted_input=admitted,
            seal=seal,
            verified_workspace=H1VerifiedWorkspaceClosure(proposal_context_bytes=sorted_bytes),
        )
    assert raised.value.code == "H1_WORKSPACE_ORIGINAL_READ_UNPROVEN"
