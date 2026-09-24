"""RED contract for H1 scope issuance through the mounted broker route."""

import asyncio
import hashlib
import json
import struct
import time
from dataclasses import dataclass, replace
from pathlib import Path

from pydantic import TypeAdapter

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient
from chiplog.capabilities.deployment_trust import TrustReference
from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    BrokerSelectedH1EvidenceV1,
    H1BrokerRouteBindingV1,
    H1OwnerCandidateCallV1,
    H1RetainedSelectedWrapperV1,
)
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    CurrentHermeticExecutionScopeV1,
    HermeticTrustObservationV1,
    IssueHermeticOutputScopeResultV1,
    IssueHermeticOutputScopeV1,
    ReadCurrentHermeticExecutionScopeV1,
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
from chiplog.composition.r14_execution_inbox_records import (
    EXECUTION_INBOX_INITIALIZATION_OPERATION,
    RetainedInboxExecutionInitialization,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources, ResourceObservation
from chiplog.composition.r17_authenticated_records import decode_authentication
from chiplog.domain_primitives import PrincipalId, TenantId
from chiplog.platform.broker import (
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortRejected,
    PublicPortSuccess,
)
from chiplog.platform.ingress_record_contracts import canonical_ingress_record_bytes
from chiplog.platform.ingress_transition_contracts import (
    IngressCommandIdentity,
    RetainedIngressSource,
)
from chiplog.platform.r7_trust import TrustOwnerCall

_ISSUE_RESULT: TypeAdapter[IssueHermeticOutputScopeResultV1] = TypeAdapter(
    IssueHermeticOutputScopeResultV1
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


async def _admit(
    runtime: CommonCliExecutionRuntime, *, slot: str, raw: bytes
) -> tuple[str, DriveInputRequestV1]:
    runtime.provision_retained(slot, raw)
    await runtime.allocate_receipt(slot)
    await runtime.stage_receipt(slot)
    token = next(
        entry.token.token_id
        for entry in runtime.ingress_history().custody.entries
        if entry.token.receive_slot == slot
    )
    async with runtime.cli_custody(slot) as custody:
        peer = asyncio.create_task(_custody_client(custody.socket.path))
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
    initialization_bytes: bytes
    resource_ref: SelectedHermeticResourceObservationRefV1
    authentication_ref: ExactHead
    authentication_bytes: bytes
    authenticated_cli_ref: TrustReference
    recipient: ProviderRecipient
    admitted_record_ref: ExactHead
    admitted_record_bytes: bytes


async def _selected_sources(
    runtime: CommonCliExecutionRuntime, resources: HermeticDispatchResources
) -> _SelectedSources:
    token, request = await _admit(runtime, slot="selected", raw=b"make a native run")
    receipt = await runtime.drive_input(request)
    assert receipt.kind == "SELECTED_EXECUTION_RECEIPT_V1"
    assert receipt.disposition == "COMMITTED"
    assert receipt.phase == "INITIALIZED"

    selected = ExactHead(**receipt.selected_journal_decision.model_dump())
    matches = [
        raw
        for decision_id, _, raw in runtime._loop_decisions().entries()
        if decision_id == selected.head
        and hashlib.sha256(raw).hexdigest() == selected.fingerprint
        and json.loads(raw).get("kind") == "DECIDED"
        and json.loads(raw).get("operation_kind") == EXECUTION_INBOX_INITIALIZATION_OPERATION
    ]
    assert len(matches) == 1
    initialization_bytes = matches[0]
    evidence = RetainedInboxExecutionInitialization.model_validate_json(
        json.loads(initialization_bytes)["inbox_initialization"]
    )
    observation = ResourceObservation(
        evidence.dispatch_grant_bytes,
        evidence.dispatch_credential_bytes,
        evidence.dispatch_endpoint_bytes,
        evidence.dispatch_clock_epoch,
        evidence.dispatch_signature,
    )
    assert resources.verify_historical(observation)
    assert resources.verify_current(observation)
    resource_ref = SelectedHermeticResourceObservationRefV1(
        signature_domain="dispatch-resources.v1",
        selected_initialization=selected,
        signed_observation_fingerprint=resources.observation_digest(observation),
    )
    selected_recipient = resources.historical_recipient(observation)
    recipient = ProviderRecipient(
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
    assert evidence.request.admitted.origin.recipient == recipient

    admitted = runtime.read_admitted_inbox(token)
    assert admitted is not None
    owner_call, authenticated = decode_authentication(admitted.record.command)
    authentication_ref = ExactHead(**admitted.record.inbox.authentication.proof.model_dump())
    authentication_bytes = admitted.record.command.authentication_result_bytes
    assert hashlib.sha256(authentication_bytes).hexdigest() == authentication_ref.fingerprint
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
    admitted_record_bytes = canonical_ingress_record_bytes(admitted.record)
    admitted_record_ref = ExactHead(**admitted.physical_record.model_dump())
    assert hashlib.sha256(admitted_record_bytes).hexdigest() == admitted_record_ref.fingerprint
    return _SelectedSources(
        initialization_bytes=initialization_bytes,
        resource_ref=resource_ref,
        authentication_ref=authentication_ref,
        authentication_bytes=authentication_bytes,
        authenticated_cli_ref=authenticated_cli_ref,
        recipient=recipient,
        admitted_record_ref=admitted_record_ref,
        admitted_record_bytes=admitted_record_bytes,
    )


def _trust_observation(
    runtime: CommonCliExecutionRuntime,
) -> tuple[HermeticTrustObservationV1, bytes]:
    frozen = runtime._trust.capture_verified_observation()
    entries = runtime._trust._journal.entries()
    logical = runtime._trust.owner_snapshot_entries()
    assert entries and logical
    decision_id, _, decision_bytes = entries[-1]
    return (
        HermeticTrustObservationV1(
            physical_journal_head=ExactHead(
                identity="deployment-trust/journal",
                head=decision_id,
                fingerprint=hashlib.sha256(decision_bytes).hexdigest(),
            ),
            logical_snapshot_head=logical[-1][0],
        ),
        frozen.snapshot_bytes,
    )


def _owner_call(
    runtime: CommonCliExecutionRuntime,
    selected: _SelectedSources,
    *,
    request_id: str,
) -> PublicPortCall:
    observation, snapshot_bytes = _trust_observation(runtime)
    broker = runtime._supervisor.runtime()
    callee = broker.session("deployment_trust")
    intent = IssueHermeticOutputScopeV1(
        slot_id="h1-cli-effects-origin",
        database_id="hermetic-database",
        scope_id="h1-output-scope",
        expected_trust_observation=observation,
        expected_scope_predecessor=None,
        expected_revision=0,
        authenticated_cli_ref=selected.authenticated_cli_ref,
        admitted_authentication_ref=selected.authentication_ref,
        selected_resource_observation_ref=selected.resource_ref,
        worker_session_id="h1-worker",
    )
    route = H1BrokerRouteBindingV1(
        tenant_id="hermetic-tenant",
        database_id=intent.database_id,
        worker_session_id=intent.worker_session_id,
        broker_epoch=callee.broker_epoch,
        runtime_generation=callee.generation_id,
        broker_session_id="broker:" + callee.generation_id,
        owner_session_id=callee.session_id,
        request_id=request_id,
    )
    retained = H1RetainedSelectedWrapperV1(
        initialization_envelope_bytes=selected.initialization_bytes,
        admitted_record_bytes=selected.admitted_record_bytes,
        selected_admitted_record_ref=selected.admitted_record_ref,
        authentication_result_bytes=selected.authentication_bytes,
        admitted_record_digest=hashlib.sha256(selected.admitted_record_bytes).hexdigest(),
    )
    evidence = BrokerSelectedH1EvidenceV1(
        route=route,
        selected_request_bytes=intent.canonical_bytes(),
        request_digest=hashlib.sha256(intent.canonical_bytes()).hexdigest(),
        retained=retained,
        retained_wrapper_digest=hashlib.sha256(retained.canonical_bytes()).hexdigest(),
        recipient=selected.recipient,
        trust_observation=observation,
        trust_snapshot_digest=hashlib.sha256(snapshot_bytes).hexdigest(),
    )
    candidate = H1OwnerCandidateCallV1(
        evidence=evidence,
        evidence_digest=hashlib.sha256(evidence.canonical_bytes()).hexdigest(),
    )
    wire = TrustOwnerCall(
        mode="ISSUE_HERMETIC_OUTPUT_SCOPE_V1",
        snapshot_bytes=snapshot_bytes,
        request_bytes=candidate.canonical_bytes(),
    )
    return PublicPortCall(
        operation_id="deployment_trust.issue_hermetic_output_scope",
        request_id=request_id,
        caller=BrokerSession(
            tenant_id="hermetic-tenant",
            broker_epoch=callee.broker_epoch,
            generation_id=callee.generation_id,
            owner_id="broker",
            session_id="broker:" + callee.generation_id,
        ),
        callee=callee,
        schema_id="chiplog.deployment-trust.owner-call.v1",
        canonical_payload=wire.canonical_bytes(),
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
            policy_version=1,
        ),
    )


async def test_real_selected_r17_and_current_signed_r16_issue_one_anchored_scope(
    tmp_path: Path,
) -> None:
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    async with open_common_cli_execution_runtime(
        tmp_path / "h1.sqlite", resources=resources
    ) as runtime:
        selected = await _selected_sources(runtime, resources)
        before_decisions = runtime._trust._journal.entries()
        before_records = runtime._trust._materializer.records()
        observation, _ = _trust_observation(runtime)
        intent = IssueHermeticOutputScopeV1(
            slot_id="h1-cli-effects-origin",
            database_id="hermetic-database",
            scope_id="h1-output-scope",
            expected_trust_observation=observation,
            expected_scope_predecessor=None,
            expected_revision=0,
            authenticated_cli_ref=selected.authenticated_cli_ref,
            admitted_authentication_ref=selected.authentication_ref,
            selected_resource_observation_ref=selected.resource_ref,
            worker_session_id="h1-worker",
        )
        result = await runtime.issue_hermetic_output_scope(intent, request_id="h1-real-selected")
        assert result.disposition == "ISSUED"

        after_decisions = runtime._trust._journal.entries()
        after_records = runtime._trust._materializer.records()
        assert after_decisions[:-1] == before_decisions
        assert after_records[:-2] == before_records
        assert len(after_decisions) == len(before_decisions) + 1
        assert len(after_records) == len(before_records) + 2
        decision_id, _, decision_bytes = after_decisions[-1]
        assert result.anchor.decision.head == decision_id
        assert result.anchor.decision.fingerprint == hashlib.sha256(decision_bytes).hexdigest()
        record_bytes = after_records[-1]
        assert (
            runtime._trust._materializer.record(decision_id, result.anchor.record_ordinal)
            == record_bytes
        )
        assert result.anchor.record.fingerprint == hashlib.sha256(record_bytes).hexdigest()
        assert result.anchor.record.head == (
            result.anchor.record.identity + "/" + result.anchor.record.fingerprint
        )
        current_observation, _ = _trust_observation(runtime)
        current_request = ReadCurrentHermeticExecutionScopeV1(
            expected_trust_observation=current_observation,
            source_anchor=result.anchor,
            expected_revision=result.revision,
            admitted_authentication_ref=selected.authentication_ref,
            authenticated_cli_ref=selected.authenticated_cli_ref,
            tenant_id="hermetic-tenant",
            database_id="hermetic-database",
            scope_id="h1-output-scope",
            expected_scope_ref=result.scope_head,
            expected_worker_session_id="h1-worker",
            selected_resource_observation_ref=selected.resource_ref,
        )
        current = await runtime.read_current_hermetic_output_scope(current_request)
        assert isinstance(current, CurrentHermeticExecutionScopeV1)
        assert current.disposition == "CURRENT"
        stale = await runtime.read_current_hermetic_output_scope(
            current_request.model_copy(
                update={
                    "authenticated_cli_ref": replace(
                        selected.authenticated_cli_ref,
                        freshness_sequence=selected.authenticated_cli_ref.freshness_sequence + 1,
                    )
                }
            )
        )
        assert stale.disposition in {"STALE", "DENIED"}


async def test_forged_r17_source_returns_nonissued_and_writes_nothing(tmp_path: Path) -> None:
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    async with open_common_cli_execution_runtime(
        tmp_path / "h1.sqlite", resources=resources
    ) as runtime:
        selected = await _selected_sources(runtime, resources)
        forged_token, _ = await _admit(runtime, slot="forged", raw=b"foreign native run")
        forged = runtime.read_admitted_inbox(forged_token)
        assert forged is not None
        forged_ref = ExactHead(**forged.record.inbox.authentication.proof.model_dump())
        forged_sources = replace(
            selected,
            authentication_ref=forged_ref,
            authentication_bytes=forged.record.command.authentication_result_bytes,
            admitted_record_ref=ExactHead(**forged.physical_record.model_dump()),
            admitted_record_bytes=canonical_ingress_record_bytes(forged.record),
        )
        before_decisions = runtime._trust._journal.entries()
        before_records = runtime._trust._materializer.records()
        response = await runtime._supervisor.runtime().call(
            _owner_call(runtime, forged_sources, request_id="h1-forged-r17")
        )
        if isinstance(response, PublicPortSuccess):
            assert json.loads(response.canonical_payload)["disposition"] == "CANDIDATE"
        else:
            assert isinstance(response, PublicPortRejected)
        assert runtime._trust._journal.entries() == before_decisions
        assert runtime._trust._materializer.records() == before_records
