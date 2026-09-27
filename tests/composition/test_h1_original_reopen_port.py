"""Installed H1 issuer-owned reopen of the selected physical V2 original."""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import struct
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.deployment_trust.cli_custody_contracts import (
    CliCustodyOffer,
    CliCustodyResponse,
)
from chiplog.composition import common_cli_execution_runtime
from chiplog.composition.common_cli_execution_runtime import R17_RETAINED_READER_ID
from chiplog.composition.common_execution_driver_contracts import (
    CliRetainedSelectedSourceV1,
    DriveInputRequestV1,
    DriverCommandIdentityV1,
    SelectedExecutionReceiptV1,
)
from chiplog.composition.h1_launch_enrollment import (
    ExpectedH1Genesis,
    InstalledH1Slot,
    _open_installed_h1_launch,
    _provision_h1_enrollment,
    _provision_h1_evidence_mount,
)
from chiplog.composition.h1_preissuance_registration import H1PreissuanceSourceViolation
from chiplog.composition.h1_registration_custody import H1RegistrationCustodyV1
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_candidate
from chiplog.composition.h1_workspace_policy_v2 import H1OriginalWorkspaceIssuanceV2
from chiplog.composition.r13_workspace import R13Workspace
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.ingress_transition_contracts import (
    IngressCommandIdentity,
    RetainedIngressSource,
)
from chiplog.platform.r7_trust_durability import TrustDurabilityObservation

_DEPLOYMENT = "installed-deployment"
_TENANT = "hermetic-tenant"
_DATABASE = "hermetic-database"
_PRINCIPAL = "hermetic-principal"
_CHANNEL = "hermetic-local"


class _ActiveTrust:
    def __init__(self, digest: str) -> None:
        self._observation = TrustDurabilityObservation(
            _TENANT, _DATABASE, digest, "trust", "materialization", "ACTIVE"
        )

    def verify(self) -> TrustDurabilityObservation:
        return self._observation


def _registry(digest: str) -> H1RegistrationCustodyV1:
    return H1RegistrationCustodyV1.model_validate(
        {
            "schema_id": "chiplog.execution.h1-registration-custody.v1",
            "deployment_id": _DEPLOYMENT,
            "database_id": _DATABASE,
            "database_genesis_digest": digest,
            "entries": [
                {
                    "tenant_id": _TENANT,
                    "principal_id": _PRINCIPAL,
                    "channel_id": _CHANNEL,
                    "registration_id": "installed-registration",
                    "generation": 0,
                    "status": "ACTIVE",
                    "origin_recipient_id": _PRINCIPAL,
                    "conversation_id": "installed-conversation",
                    "visible_channels": [_CHANNEL],
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
    expected = ExpectedH1Genesis(_DEPLOYMENT, _TENANT, _DATABASE)
    return (
        InstalledH1Slot(
            root,
            "custody",
            database,
            _DEPLOYMENT,
            _TENANT,
            _DATABASE,
            _ActiveTrust(expected.digest),
        ),
        expected,
    )


async def _bootstrap(slot: InstalledH1Slot, expected: ExpectedH1Genesis, tmp_path: Path) -> None:
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "bootstrap-custody"
    )
    async with common_cli_execution_runtime.open_common_cli_execution_runtime(
        slot.database_path, resources=resources
    ) as runtime:
        observed = runtime._trust.verify()
        assert observed is not None and observed.phase == "ACTIVE"
        assert observed.genesis_head == expected.digest
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
    identity = IngressCommandIdentity(tenant_id=_TENANT, database_id=_DATABASE, command_id=token)
    retained = RetainedIngressSource(
        source=record.command.retention.proof,
        reader_id=R17_RETAINED_READER_ID,
        schema_id="chiplog.ingress.retained-source-observation.v1",
        canonical_source_bytes=record.command.retention.observation_bytes,
    )
    return DriveInputRequestV1(
        identity=DriverCommandIdentityV1(
            tenant_id=_TENANT,
            database_id=_DATABASE,
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


@pytest.mark.asyncio
async def test_installed_selected_v3_original_reopens_through_issuer_current_scope(
    tmp_path: Path,
) -> None:
    """A native selected ref, not a caller DTO, is the only positive input."""
    slot, expected = _installed_slot(tmp_path)
    await _bootstrap(slot, expected, tmp_path)
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
            initial = cast(SelectedExecutionReceiptV1, await runtime.drive_input(request))
            completion = DeliveryCompletion(
                tenant=_TENANT,
                run_id=initial.stable_run_lineage_id,
                turn_id=initial.stable_run_lineage_id + "/turn/1",
                deliveries=(ProposedDelivery(payload=(Commentary(text="H1 native response"),)),),
            )
            runtime._execution_model._responses = (completion.canonical_bytes(),)
            started = await runtime.begin_execution(
                "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
            )
            captured = await runtime.capture_execution(
                "hermetic-ingress",
                initial.stable_run_lineage_id,
                started.head,
            )
            selected = select_h1_v3_prepare_for_candidate(
                runtime, captured, expected_head=captured.head
            )
            physical = R13Workspace(runtime).open_h1_workspace_issuance().load(
                selected.issuance_ref
            )
            assert type(physical) is H1OriginalWorkspaceIssuanceV2

            port = runtime._h1_preissuance_registration_source_port
            assert port is not None
            with pytest.raises(H1PreissuanceSourceViolation, match="V3 evidence differs"):
                await port.verify_selected_original(
                    replace(
                        selected,
                        retained_bytes=hashlib.sha256(selected.retained_bytes).digest(),
                    )
                )
            original = await port.verify_selected_original(selected)
            reopened = port.reopen_original(original)

            assert port.validate_original_selection(reopened, original) is True
            retained_original = port._originals[id(original)][1]
            assert retained_original.selected.issuance_ref is selected.issuance_ref
            assert retained_original.selected is selected
            assert reopened is not retained_original.selection
