"""Selected H0/R16/R17 source checks for the H1 composition seam."""

import asyncio
import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient
from chiplog.capabilities.deployment_trust import TrustReference
from chiplog.capabilities.deployment_trust._output_scope_profile import VerifiedH1SelectedSources
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    SelectedHermeticResourceObservationRefV1,
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
)
from chiplog.composition.h1_selected_output_sources import H1SelectedOutputSources
from chiplog.composition.r14_execution_inbox_records import (
    EXECUTION_INBOX_INITIALIZATION_OPERATION,
    RetainedInboxExecutionInitialization,
)
from chiplog.composition.r16_dispatch_registry import (
    HermeticDispatchResources,
    ResourceObservation,
)
from chiplog.composition.r17_authenticated_records import decode_authentication
from chiplog.domain_primitives import PrincipalId, TenantId
from chiplog.platform.ingress_transition_contracts import IngressCommandIdentity, RetainedIngressSource
from tests.capabilities.deployment_trust.test_output_scope_owner_seam import request


async def _client(path: Path) -> None:
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


async def _admit(
    runtime: CommonCliExecutionRuntime, *, slot: str, raw: bytes
) -> tuple[str, DriveInputRequestV1]:
    runtime.provision_retained(slot, raw)
    await runtime.allocate_receipt(slot)
    await runtime.stage_receipt(slot)
    token = next(entry.token.token_id for entry in runtime.ingress_history().custody.entries if entry.token.receive_slot == slot)
    async with runtime.cli_custody(slot) as custody:
        peer = asyncio.create_task(_client(custody.socket.path))
        assert (await custody.admit()).kind == "COMMITTED"
        await peer
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
    return token, DriveInputRequestV1(
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


@dataclass(frozen=True)
class _SelectedSources:
    resource_ref: SelectedHermeticResourceObservationRefV1
    authentication_ref: ExactHead
    authenticated_cli_ref: TrustReference
    expected: VerifiedH1SelectedSources


async def _selected_sources(
    runtime: CommonCliExecutionRuntime, resources: HermeticDispatchResources
) -> _SelectedSources:
    token, request = await _admit(runtime, slot="selected", raw=b"make a native run")
    receipt = await runtime.drive_input(request)
    assert receipt.kind == "SELECTED_EXECUTION_RECEIPT_V1"
    assert receipt.disposition == "COMMITTED"
    assert receipt.phase == "INITIALIZED"
    assert receipt.selected_run_state == "CREATED"

    selected = ExactHead(**receipt.selected_journal_decision.model_dump())
    rows = [
        (decision_id, raw)
        for decision_id, _, raw in runtime._loop_decisions().entries()
        if decision_id == selected.head
        and hashlib.sha256(raw).hexdigest() == selected.fingerprint
        and json.loads(raw).get("kind") == "DECIDED"
        and json.loads(raw).get("operation_kind") == EXECUTION_INBOX_INITIALIZATION_OPERATION
    ]
    assert len(rows) == 1
    evidence = RetainedInboxExecutionInitialization.model_validate_json(
        json.loads(rows[0][1])["inbox_initialization"]
    )
    observation = ResourceObservation(
        evidence.dispatch_grant_bytes,
        evidence.dispatch_credential_bytes,
        evidence.dispatch_endpoint_bytes,
        evidence.dispatch_clock_epoch,
        evidence.dispatch_signature,
    )
    resource_ref = SelectedHermeticResourceObservationRefV1(
        signature_domain="dispatch-resources.v1",
        selected_initialization=selected,
        signed_observation_fingerprint=resources.observation_digest(observation),
    )
    assert resources.verify_historical(observation)
    assert resources.verify_current(observation)
    selected_recipient = resources.historical_recipient(observation)
    assert evidence.request.admitted.origin.recipient == ProviderRecipient(
        provider_id=selected_recipient.provider,
        account_id=selected_recipient.account,
        recipient_id=selected_recipient.recipient,
        endpoint=ExactHead(
            identity=selected_recipient.endpoint.subject_id,
            head=selected_recipient.endpoint.head,
            fingerprint=selected_recipient.endpoint.fingerprint,
        ),
        canonical_address=selected_recipient.canonical_address,
        credential_binding=ExactHead(
            identity=selected_recipient.credential_binding.subject_id,
            head=selected_recipient.credential_binding.head,
            fingerprint=selected_recipient.credential_binding.fingerprint,
        ),
    )

    admitted = runtime.read_admitted_inbox(token)
    assert admitted is not None
    owner_call, authenticated = decode_authentication(admitted.record.command)
    authentication_ref = ExactHead(**admitted.record.inbox.authentication.proof.model_dump())
    custody_ref = authenticated.reference
    authenticated_cli_ref = TrustReference(
        tenant_id=TenantId(custody_ref.tenant_id),
        principal_id=PrincipalId(custody_ref.principal_id),
        contour=custody_ref.contour,
        credential_head=custody_ref.credential_head,
        session_head=custody_ref.session_head,
        source_head="local",
        trust_head=custody_ref.trust_head,
        materialization_head=custody_ref.materialization_head,
        freshness_sequence=custody_ref.freshness_sequence,
        peer_credential=f"uid:{owner_call.request.socket.peer.uid}",
    )
    assert evidence.request.admitted.source_authentication.subject_id == authentication_ref.identity
    assert evidence.request.admitted.source_authentication.revision.head == authentication_ref.head
    assert authenticated_cli_ref.principal_id.value == evidence.request.admitted.principal_id
    return _SelectedSources(
        resource_ref=resource_ref,
        authentication_ref=authentication_ref,
        authenticated_cli_ref=authenticated_cli_ref,
        expected=VerifiedH1SelectedSources(
            recipient=evidence.request.admitted.origin.recipient,
            authenticated_cli_ref=authenticated_cli_ref,
            admitted_authentication_ref=authentication_ref,
            selected_resource_observation_ref=resource_ref,
        ),
    )


async def test_selected_h0_r16_r17_sources_resolve_to_the_exact_binding(tmp_path: Path) -> None:
    custody = tmp_path / "dispatch-custody"
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(tmp_path / "h1.sqlite", resources=resources) as runtime:
        sources = await _selected_sources(runtime, resources)
        assert H1SelectedOutputSources(runtime).resolve_selected_current(
            sources.resource_ref,
            sources.authentication_ref,
            sources.authenticated_cli_ref,
        ) == sources.expected


async def test_selected_sources_reject_a_borrowed_r17_authentication_proof(tmp_path: Path) -> None:
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    async with open_common_cli_execution_runtime(tmp_path / "h1.sqlite", resources=resources) as runtime:
        sources = await _selected_sources(runtime, resources)
        token, _ = await _admit(runtime, slot="borrowed", raw=b"another native run")
        borrowed = runtime.read_admitted_inbox(token)
        assert borrowed is not None
        borrowed_proof = ExactHead(**borrowed.record.inbox.authentication.proof.model_dump())
        assert borrowed_proof != sources.authentication_ref
        assert H1SelectedOutputSources(runtime).resolve_selected_current(
            sources.resource_ref,
            borrowed_proof,
            sources.authenticated_cli_ref,
        ) is None




def test_wrong_runtime_type_is_rejected() -> None:
    wrong: Any = object()
    with pytest.raises(TypeError, match="canonical common CLI runtime"):
        H1SelectedOutputSources(wrong)


async def test_typed_locators_do_not_invent_verified_sources(tmp_path: Path) -> None:
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1)
    async with open_common_cli_execution_runtime(
        tmp_path / "h1.sqlite", resources=resources
    ) as runtime:
        verifier = H1SelectedOutputSources(runtime)
        issue = request()
        assert verifier.resolve_selected_current(
            issue.selected_resource_observation_ref,
            issue.admitted_authentication_ref,
            issue.authenticated_cli_ref,
        ) is None


@pytest.mark.parametrize("argument", [0, 1, 2])
async def test_each_wrong_locator_type_is_rejected(tmp_path: Path, argument: int) -> None:
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1)
    async with open_common_cli_execution_runtime(
        tmp_path / "h1.sqlite", resources=resources
    ) as runtime:
        verifier = H1SelectedOutputSources(runtime)
        issue = request()
        arguments: list[Any] = [
            issue.selected_resource_observation_ref,
            issue.admitted_authentication_ref,
            issue.authenticated_cli_ref,
        ]
        arguments[argument] = object()
        with pytest.raises(TypeError):
            verifier.resolve_selected_current(*arguments)
