"""Installed H1 first path must bind its workspace only after scope issuance.

The production mount is deliberately required here.  A direct common-runtime
fixture or a private preissuance-port construction would manufacture the very
authority this test is meant to prove.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sqlite3
import struct
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.deployment_trust.cli_custody_contracts import (
    CliCustodyOffer,
    CliCustodyResponse,
)
from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import H1OwnerCurrentCallV1
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    CurrentHermeticExecutionScopeV1,
    HermeticOutputScopeAnchorV1,
    HermeticOutputScopeV1,
    ReadCurrentHermeticExecutionScopeV1,
)
from chiplog.composition import common_cli_execution_runtime
from chiplog.composition.common_cli_execution_runtime import R17_RETAINED_READER_ID
from chiplog.composition.common_execution_driver_contracts import (
    AdvanceExecutionRequestV1,
    CliRetainedSelectedSourceV1,
    DriveInputRequestV1,
    DriverCommandIdentityV1,
    ExecutionDriverRejectedV1,
)
from chiplog.composition.h1_launch_enrollment import (
    ExpectedH1Genesis,
    InstalledH1Slot,
    _open_installed_h1_launch,
    _provision_h1_enrollment,
    _provision_h1_evidence_mount,
)
from chiplog.composition.h1_registration_custody import H1RegistrationCustodyV1
from chiplog.composition.h1_workspace_policy_v2 import (
    H1OriginalWorkspaceIssuanceV2,
    decode_h1_workspace_policy_v2,
)
from chiplog.composition.r14_h1_workspace_issuance import H1WorkspaceIssuanceJournal
from chiplog.composition.r14_h1_workspace_issuance_contracts import H1WorkspaceIssuanceRefV1
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.ingress_transition_contracts import (
    IngressCommandIdentity,
    RetainedIngressSource,
)
from chiplog.platform.r7_trust import TrustOwnerCall
from chiplog.platform.r7_trust_durability import TrustDurabilityObservation

DEPLOYMENT = "installed-deployment"
TENANT = "hermetic-tenant"
DATABASE = "hermetic-database"
PRINCIPAL = "hermetic-principal"
CHANNEL = "hermetic-local"


class _ActiveTrust:
    def __init__(self, digest: str) -> None:
        self._observation = TrustDurabilityObservation(
            TENANT, DATABASE, digest, "trust", "materialization", "ACTIVE"
        )

    def verify(self) -> TrustDurabilityObservation:
        return self._observation


def _registry(digest: str) -> H1RegistrationCustodyV1:
    return H1RegistrationCustodyV1.model_validate(
        {
            "schema_id": "chiplog.execution.h1-registration-custody.v1",
            "deployment_id": DEPLOYMENT,
            "database_id": DATABASE,
            "database_genesis_digest": digest,
            "entries": [
                {
                    "tenant_id": TENANT,
                    "principal_id": PRINCIPAL,
                    "channel_id": CHANNEL,
                    "registration_id": "installed-registration",
                    "generation": 0,
                    "status": "ACTIVE",
                    "origin_recipient_id": PRINCIPAL,
                    "conversation_id": "installed-conversation",
                    "visible_channels": [CHANNEL],
                }
            ],
        }
    )


def _installed_slot(tmp_path: Path) -> tuple[InstalledH1Slot, ExpectedH1Genesis]:
    root = tmp_path / "installed"
    root.mkdir(mode=0o700)
    (root / "custody").mkdir(mode=0o700)
    database = root / "runtime.sqlite3"
    with sqlite3.connect(database):
        pass
    database.chmod(0o600)
    expected = ExpectedH1Genesis(DEPLOYMENT, TENANT, DATABASE)
    return (
        InstalledH1Slot(
            root,
            "custody",
            database,
            DEPLOYMENT,
            TENANT,
            DATABASE,
            _ActiveTrust(expected.digest),
        ),
        expected,
    )


async def _bootstrap_installed_slot(
    slot: InstalledH1Slot, expected: ExpectedH1Genesis, tmp_path: Path
) -> None:
    """Create the runtime's durable ACTIVE trust before installer enrollment."""
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "bootstrap-custody"
    )
    async with common_cli_execution_runtime.open_common_cli_execution_runtime(
        slot.database_path, resources=resources
    ) as runtime:
        observed = runtime._trust.verify()
        assert observed is not None
        assert observed.phase == "ACTIVE"
        assert (
            observed.tenant_id,
            observed.database_instance_id,
            observed.genesis_head,
        ) == (TENANT, DATABASE, expected.digest)
    # The installer receives the independently observed expected trust state;
    # it does not create runtime trust on behalf of launch.
    slot._trust = _ActiveTrust(expected.digest)


async def _custody_client(path: Path) -> None:
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        size = struct.unpack("!I", await reader.readexactly(4))[0]
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


async def _admit(runtime: Any) -> DriveInputRequestV1:
    runtime.provision_retained("slot", b"make a native run")
    await runtime.allocate_receipt("slot")
    await runtime.stage_receipt("slot")
    token = runtime.ingress_history().custody.entries[0].token.token_id
    async with runtime.cli_custody("slot") as custody:
        peer = asyncio.create_task(_custody_client(custody.socket.path))
        assert (await custody.admit()).kind == "COMMITTED"
        await peer
    admitted = runtime.read_admitted_inbox(token)
    assert admitted is not None
    record = admitted.record
    identity = IngressCommandIdentity(tenant_id=TENANT, database_id=DATABASE, command_id=token)
    retained = RetainedIngressSource(
        source=record.command.retention.proof,
        reader_id=R17_RETAINED_READER_ID,
        schema_id="chiplog.ingress.retained-source-observation.v1",
        canonical_source_bytes=record.command.retention.observation_bytes,
    )
    return DriveInputRequestV1(
        identity=DriverCommandIdentityV1(
            tenant_id=TENANT,
            database_id=DATABASE,
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


def _captured_scope(
    runtime: Any,
) -> tuple[HermeticOutputScopeV1, HermeticOutputScopeAnchorV1, ExactHead]:
    candidates = [
        (decision_id, raw)
        for decision_id, _, raw in runtime._trust._journal.entries()
        if json.loads(raw).get("kind") == "HERMETIC_OUTPUT_SCOPE_V1"
    ]
    assert len(candidates) == 1
    decision_id, decision_raw = candidates[0]
    records = [
        raw
        for raw in runtime._trust._materializer.records()
        if json.loads(raw).get("decision_id") == decision_id
        and json.loads(raw).get("operation_kind") == "HERMETIC_OUTPUT_SCOPE_V1"
        and json.loads(raw).get("record_type_id")
        == "chiplog.deployment_trust.hermetic_output_scope"
    ]
    assert len(records) == 1
    record_raw = records[0]
    record = json.loads(record_raw)
    scope = HermeticOutputScopeV1.model_validate_json(
        json.dumps(record["scope"], sort_keys=True, separators=(",", ":"))
    )
    scope_bytes = scope.canonical_bytes()
    anchor = HermeticOutputScopeAnchorV1(
        owner_id="deployment_trust",
        decision=ExactHead(
            identity="deployment-trust/journal",
            head=decision_id,
            fingerprint=hashlib.sha256(decision_raw).hexdigest(),
        ),
        record_ordinal=1,
        record_type_id=record["record_type_id"],
        schema_id=record["schema_id"],
        record=ExactHead(
            identity="trust-record:" + decision_id + ":1",
            head="trust-record:"
            + decision_id
            + ":1/"
            + hashlib.sha256(record_raw).hexdigest(),
            fingerprint=hashlib.sha256(record_raw).hexdigest(),
        ),
        scope_revision=scope.revision,
        predecessor=scope.predecessor,
        selected_resource_observation_ref=scope.selected_resource_observation_ref,
    )
    return (
        scope,
        anchor,
        ExactHead(
            identity=scope.scope_id,
            head=scope.scope_id + "/" + hashlib.sha256(scope_bytes).hexdigest(),
            fingerprint=hashlib.sha256(scope_bytes).hexdigest(),
        ),
    )


@pytest.mark.asyncio
async def test_installed_launch_drives_native_h1_v2_workspace_after_physical_scope(
    tmp_path: Path,
) -> None:
    slot, expected = _installed_slot(tmp_path)
    await _bootstrap_installed_slot(slot, expected, tmp_path)
    _provision_h1_enrollment(slot, expected, _registry(expected.digest))
    _provision_h1_evidence_mount(slot, expected)

    with _open_installed_h1_launch(slot) as launch:
        opener = getattr(common_cli_execution_runtime, "open_installed_h1_runtime", None)
        assert callable(opener), "installed H1 runtime opener is absent"

        resources = HermeticDispatchResources(
            scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
        )
        async with opener(launch, resources=resources) as runtime:
            request = await _admit(runtime)
            initial = await runtime.drive_input(request)
            assert initial.phase == "INITIALIZED"
            complete = DeliveryCompletion(
                tenant=TENANT,
                run_id=initial.stable_run_lineage_id,
                turn_id=initial.stable_run_lineage_id + "/turn/1",
                deliveries=(ProposedDelivery(payload=(Commentary(text="H1 reached model"),)),),
            )
            runtime._execution_model._responses = (complete.canonical_bytes(),)
            running = await runtime.advance_execution(
                AdvanceExecutionRequestV1(
                    identity=request.identity,
                    original_driver_command_fingerprint=request.original_driver_command_fingerprint(),
                    expected_selected_run_head=initial.selected_run_head,
                )
            )
            assert not isinstance(running, ExecutionDriverRejectedV1), (
                f"installed H1 advance rejected code={running.code!r} reason={running.reason}"
            )
            assert running.phase == "RUNNING"

            scope, anchor, scope_ref = _captured_scope(runtime)
            with sqlite3.connect(slot.database_path) as connection:
                rows = connection.execute(
                    "SELECT rowid, record_id, owner, schema_id, canonical_bytes, commit_sequence "
                    "FROM records ORDER BY commit_sequence, rowid"
                ).fetchall()
            policies = [
                row
                for row in rows
                if row[2:4] == ("workspace_policy", "chiplog.workspace.policy.v2")
            ]
            assert len(policies) == 1
            _, policy_record_id, _, _, policy_raw, _ = policies[0]
            policy = decode_h1_workspace_policy_v2(policy_raw)
            assert policy.registration.output_scope_anchor == anchor
            assert policy.registration.output_scope_ref == scope_ref
            assert policy.registration.selected_resource_observation_ref == (
                scope.selected_resource_observation_ref
            )
            assert policy.registration.admitted_authentication_ref == scope.admitted_authentication

            journal = H1WorkspaceIssuanceJournal.open(
                slot.database_path.with_suffix(".h1-workspace-issuance"),
                runtime._authority_gate(),
                TENANT,
            )
            entries = journal._journal.entries()
            assert len(entries) == 1
            entry_id, _, issuance_raw = entries[0]
            issuance = H1OriginalWorkspaceIssuanceV2.model_validate_json(issuance_raw)
            reference = H1WorkspaceIssuanceRefV1(
                tenant=TENANT,
                batch_id=json.loads(issuance.proposal_context_json)["batch"]["batch_id"],
                entry_id=entry_id,
                payload_digest=hashlib.sha256(issuance_raw).hexdigest(),
            )
            assert journal.load(reference) == issuance
            assert issuance.run_id == initial.stable_run_lineage_id
            assert issuance.sources.policy_record_id == policy_record_id
            assert issuance.sources.policy_payload_digest == hashlib.sha256(policy_raw).hexdigest()


@pytest.mark.asyncio
async def test_installed_h1_rejects_unrelated_append_after_scope_before_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = _installed_slot(tmp_path)
    await _bootstrap_installed_slot(slot, expected, tmp_path)
    _provision_h1_enrollment(slot, expected, _registry(expected.digest))
    _provision_h1_evidence_mount(slot, expected)
    with _open_installed_h1_launch(slot) as launch:
        resources = HermeticDispatchResources(
            scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
        )
        async with common_cli_execution_runtime.open_installed_h1_runtime(
            launch, resources=resources
        ) as runtime:
            port = runtime._h1_preissuance_registration_source_port
            assert port is not None
            original = type(port).prepare_preissuance
            injected = False

            async def append_after_scope(self: object, *args: object) -> object:
                nonlocal injected
                selection = await original(self, *args)
                cut = self._cuts[id(selection)][1]  # type: ignore[attr-defined]
                unrelated = cut.scope.model_copy(update={"scope_id": "unrelated-h1-scope"})
                with runtime._authority_gate().hold():
                    runtime._trust.append_hermetic_output_scope(unrelated.canonical_bytes())
                injected = True
                return selection

            monkeypatch.setattr(type(port), "prepare_preissuance", append_after_scope)
            request = await _admit(runtime)
            initial = await runtime.drive_input(request)
            result = await runtime.advance_execution(
                AdvanceExecutionRequestV1(
                    identity=request.identity,
                    original_driver_command_fingerprint=request.original_driver_command_fingerprint(),
                    expected_selected_run_head=initial.selected_run_head,
                )
            )
            assert injected
            assert isinstance(result, ExecutionDriverRejectedV1)
            assert runtime._execution_model.requests == []


@pytest.mark.asyncio
async def test_installed_h1_keeps_original_actor_deadline_at_final_invoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = _installed_slot(tmp_path)
    await _bootstrap_installed_slot(slot, expected, tmp_path)
    _provision_h1_enrollment(slot, expected, _registry(expected.digest))
    _provision_h1_evidence_mount(slot, expected)
    with _open_installed_h1_launch(slot) as launch:
        resources = HermeticDispatchResources(
            scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
        )
        async with common_cli_execution_runtime.open_installed_h1_runtime(
            launch, resources=resources
        ) as runtime:
            original = runtime._execution_actor
            calls = 0

            async def expired_first_actor(peer: str) -> object:
                nonlocal calls
                observed = await original(peer)
                calls += 1
                if calls == 1:
                    return replace(
                        observed,
                        request=observed.request.model_copy(
                            update={
                                "budget": observed.request.budget.model_copy(
                                    update={"absolute_deadline_ns": 1}
                                )
                            }
                        ),
                    )
                return observed

            monkeypatch.setattr(runtime, "_execution_actor", expired_first_actor)
            request = await _admit(runtime)
            initial = await runtime.drive_input(request)
            result = await runtime.advance_execution(
                AdvanceExecutionRequestV1(
                    identity=request.identity,
                    original_driver_command_fingerprint=request.original_driver_command_fingerprint(),
                    expected_selected_run_head=initial.selected_run_head,
                )
            )
            assert calls >= 2
            assert isinstance(result, ExecutionDriverRejectedV1)
            assert runtime._execution_model.requests == []


@pytest.mark.asyncio
async def test_installed_h1_repeats_identical_current_read_with_distinct_owner_wire(
    tmp_path: Path,
) -> None:
    slot, expected = _installed_slot(tmp_path)
    await _bootstrap_installed_slot(slot, expected, tmp_path)
    _provision_h1_enrollment(slot, expected, _registry(expected.digest))
    _provision_h1_evidence_mount(slot, expected)
    with _open_installed_h1_launch(slot) as launch:
        resources = HermeticDispatchResources(
            scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
        )
        async with common_cli_execution_runtime.open_installed_h1_runtime(
            launch, resources=resources
        ) as runtime:
            request = await _admit(runtime)
            initial = await runtime.drive_input(request)
            complete = DeliveryCompletion(
                tenant=TENANT,
                run_id=initial.stable_run_lineage_id,
                turn_id=initial.stable_run_lineage_id + "/turn/1",
                deliveries=(ProposedDelivery(payload=(Commentary(text="current reads"),)),),
            )
            runtime._execution_model._responses = (complete.canonical_bytes(),)
            assert (
                await runtime.advance_execution(
                    AdvanceExecutionRequestV1(
                        identity=request.identity,
                        original_driver_command_fingerprint=request.original_driver_command_fingerprint(),
                        expected_selected_run_head=initial.selected_run_head,
                    )
                )
            ).phase == "RUNNING"
            port = runtime._h1_preissuance_registration_source_port
            assert port is not None
            cut = next(iter(port._cuts.values()))[1]
            current_request = ReadCurrentHermeticExecutionScopeV1(
                expected_trust_observation=port._trust_observation(),
                source_anchor=cut.issued.anchor,
                expected_revision=cut.issued.revision,
                admitted_authentication_ref=cut.prepared.source.verified.admitted_authentication_ref,
                authenticated_cli_ref=cut.prepared.source.verified.authenticated_cli_ref,
                tenant_id=TENANT,
                database_id=DATABASE,
                scope_id=cut.scope.scope_id,
                expected_scope_ref=cut.issued.scope_head,
                expected_worker_session_id=cut.prepared.run.worker_session,
                selected_resource_observation_ref=cut.scope.selected_resource_observation_ref,
            )
            before = tuple(runtime._h1_scope_wires)
            first = await runtime.read_current_hermetic_output_scope(current_request)
            second = await runtime.read_current_hermetic_output_scope(current_request)
            assert type(first) is CurrentHermeticExecutionScopeV1
            assert type(second) is CurrentHermeticExecutionScopeV1
            added = [key for key in runtime._h1_scope_wires if key not in before]
            assert len(added) == 2
            captured = [runtime._h1_scope_wires[key][0] for key in added]
            assert captured[0].request_id != captured[1].request_id
            wires = [
                TrustOwnerCall.model_validate_json(call.canonical_payload) for call in captured
            ]
            reads = [
                H1OwnerCurrentCallV1.model_validate_json(base64.b64decode(wire.request_bytes))
                for wire in wires
            ]
            assert (
                reads[0].read_request_bytes
                == reads[1].read_request_bytes
                == current_request.canonical_bytes()
            )
            assert reads[0].request_digest == reads[1].request_digest
            assert reads[0].route.request_id != reads[1].route.request_id
            with runtime._authority_gate().hold():
                runtime._trust.append_hermetic_output_scope(
                    cut.scope.model_copy(
                        update={"scope_id": "unrelated-current-read"}
                    ).canonical_bytes()
                )
            assert (
                await runtime.read_current_hermetic_output_scope(current_request)
            ).disposition == "STALE"
