"""Mounted native H0/R17 input-role witness checks."""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import struct
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.adapters.driven.loop_prompts import OwnedStaticPrompts
from chiplog.capabilities.agent_loop.application import AgentLoop
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.capabilities.deployment_trust.cli_custody_contracts import (
    CliCustodyOffer,
    CliCustodyResponse,
)
from chiplog.composition.common_cli_execution_runtime import (
    R17_RETAINED_READER_ID,
    open_common_cli_execution_runtime,
)
from chiplog.composition.common_execution_driver_contracts import (
    CliRetainedSelectedSourceV1,
    DriveInputRequestV1,
    DriverCommandIdentityV1,
    SelectedExecutionReceiptV1,
)
from chiplog.composition.h1_selected_input_role import read_h1_selected_input_role
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_candidate
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.composition.r16_effects import R16EffectsProducer
from chiplog.composition.r14_loop_store import R14LoopStore
from chiplog.platform._owner_publication_contracts import SelectedPlanEffectBatchV2
from chiplog.platform.ingress_custody_records import canonical
from chiplog.platform.ingress_transition_contracts import (
    IngressCommandIdentity,
    RetainedIngressSource,
)
from chiplog.platform.owner_decision_journal import OwnerJournalIntegrityError
from chiplog.capabilities.agent_loop.contracts import EndpointSelection
from tests.composition.test_plan_effect_publication import response


async def _client(path: Path) -> None:
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        size = struct.unpack("!I", await reader.readexactly(4))[0]
        offer = CliCustodyOffer.model_validate_json(await reader.readexactly(size))
        reply = CliCustodyResponse(
            challenge_fingerprint=hashlib.sha256(offer.challenge.canonical_bytes()).hexdigest()
        ).canonical_bytes()
        writer.write(struct.pack("!I", len(reply)) + reply)
        await writer.drain()
        writer.write_eof()
    finally:
        writer.close()
        await writer.wait_closed()


async def _admit(runtime: object) -> DriveInputRequestV1:
    runtime.provision_retained("selected", b"selected original input")  # type: ignore[attr-defined]
    await runtime.allocate_receipt("selected")  # type: ignore[attr-defined]
    await runtime.stage_receipt("selected")  # type: ignore[attr-defined]
    token = runtime.ingress_history().custody.entries[0].token.token_id  # type: ignore[attr-defined]
    async with runtime.cli_custody("selected") as custody:  # type: ignore[attr-defined]
        peer = asyncio.create_task(_client(custody.socket.path))
        assert (await custody.admit()).kind == "COMMITTED"
        await peer
    admitted = runtime.read_admitted_inbox(token)  # type: ignore[attr-defined]
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


async def _mounted_candidate(database: Path, custody: Path) -> tuple[DriveInputRequestV1, bytes]:
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(database, resources=resources) as runtime:
        request = await _admit(runtime)
        admitted = runtime._selected_input(request, resources.observe())
        run_id = runtime._run_id(admitted)
    complete = DeliveryCompletion(
        tenant="hermetic-tenant",
        run_id=run_id,
        turn_id=run_id + "/turn/1",
        deliveries=(ProposedDelivery(payload=(Commentary(text="Hello"),)),),
    )
    return request, complete.canonical_bytes()


async def _publish_ordinary(database: Path, resources: HermeticDispatchResources) -> None:
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(response(),)
    ) as runtime:
        model = HermeticModel((response(),))
        model.session = runtime
        loop = AgentLoop(
            R14LoopStore(runtime), model, OwnedStaticPrompts(), tenant="hermetic-tenant",
            principal="hermetic-principal", origin=EndpointSelection(
                kind="ORIGIN_EXACT", ingress_binding_head="hermetic-ingress-v1",
                endpoint_head="hermetic-endpoint-v1", endpoint_id="hermetic-local",
                provider="hermetic-local", recipient="hermetic-principal",
                canonical_address="local://hermetic-principal", credential_binding_head="hermetic-v1",
            ), contour_head="hermetic-contour-v1", policy_head="hermetic-policy-v1",
            worker_session=runtime.current_worker(), planning=runtime,
        )
        created = await loop.create("r", "Propose my exact hermetic action", BudgetPolicy())
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        display = await R16EffectsProducer(runtime).display_effect("r/turn/1/proposal/effect")
        await runtime.publish_effect(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )


@pytest.mark.asyncio
async def test_mounted_selected_h0_r17_witness_uses_retained_not_current_input(
    tmp_path: Path,
) -> None:
    database, custody = tmp_path / "state.sqlite", tmp_path / "custody"
    request, complete = await _mounted_candidate(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = cast(SelectedExecutionReceiptV1, await runtime.drive_input(request))
        started = await runtime.begin_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, started.head
        )
        selected = select_h1_v3_prepare_for_candidate(
            runtime, captured, expected_head=captured.head
        )
        object.__setattr__(
            runtime,
            "_selected_input",
            lambda *_args: (_ for _ in ()).throw(AssertionError("live read")),
        )
        witness = read_h1_selected_input_role(runtime, selected, captured)

    assert tuple(item.reason for item in witness.occurrences) == (
        "H0_NATIVE_INPUT",
        "R17_ALLOCATION_INPUT",
        "R17_STAGED_INPUT",
        "R17_ADMITTED_INPUT",
    )
    assert tuple(
        decision.prepared.request.operation for decision in witness.inbound_owner_decisions
    ) == (
        "ingress.allocate_receipt_token",
        "ingress.stage_raw_bytes",
        "ingress.publish_custody_successor",
    )
    assert witness.occurrences[0].record_id == witness.initialization.proposal.run.head
    allocation, staging, admission = witness.inbound_owner_decisions
    assert tuple(
        len(decision.prepared.request.complete_records)
        for decision in (allocation, staging, admission)
    ) == (1, 1, 4)
    assert tuple(
        member.schema_id for member in admission.prepared.request.complete_records[1:]
    ) == (
        "chiplog.ingress.admission-epoch.v2",
        "chiplog.ingress.admission-capacity.v1",
        "chiplog.ingress.admission-ready.v1",
    )
    assert (
        witness.occurrences[1].canonical_bytes
        == allocation.prepared.request.complete_records[0].canonical_bytes
    )
    assert (
        witness.occurrences[2].canonical_bytes
        == staging.prepared.request.complete_records[0].canonical_bytes
    )
    assert (
        witness.occurrences[3].canonical_bytes
        == admission.prepared.request.complete_records[0].canonical_bytes
    )
    assert witness.occurrences[3].canonical_bytes == canonical(witness.admitted_record)


@pytest.mark.asyncio
async def test_mounted_selected_h0_r17_witness_accepts_prior_ordinary_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, custody = tmp_path / "state.sqlite", tmp_path / "custody"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM", "CONFIRM"), cap=2, custody_path=custody
    )
    async with open_common_cli_execution_runtime(database, resources=resources) as runtime:
        request = await _admit(runtime)
        admitted = runtime._selected_input(request, resources.observe())
        run_id = runtime._run_id(admitted)
    complete = DeliveryCompletion(
        tenant="hermetic-tenant",
        run_id=run_id,
        turn_id=run_id + "/turn/1",
        deliveries=(ProposedDelivery(payload=(Commentary(text="Hello"),)),),
    ).canonical_bytes()
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = cast(SelectedExecutionReceiptV1, await runtime.drive_input(request))
        started = await runtime.begin_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, started.head
        )
        selected = select_h1_v3_prepare_for_candidate(
            runtime, captured, expected_head=captured.head
        )
    await _publish_ordinary(database, resources)
    async with open_common_cli_execution_runtime(database, resources=resources) as runtime:
        witness = read_h1_selected_input_role(runtime, selected, captured)
        history = runtime._owner_decisions().snapshot()
        ordinary_index = next(
            index
            for index, decision in enumerate(history.decisions)
            if isinstance(decision.prepared.request, SelectedPlanEffectBatchV2)
        )
        ordinary = history.decisions[ordinary_index]
        batch = ordinary.prepared.request
        corrupted = replace(
            ordinary,
            prepared=replace(
                ordinary.prepared, request=batch.model_copy(update={"admission_records": ()})
            ),
        )
        corrupted_history = replace(
            history,
            decisions=history.decisions[:ordinary_index]
            + (corrupted,)
            + history.decisions[ordinary_index + 1 :],
        )
        monkeypatch.setattr(
            runtime, "_owner_decisions", lambda: type("SnapshotOnly", (), {"snapshot": lambda _: corrupted_history})()
        )
        with pytest.raises(OwnerJournalIntegrityError, match="ingress_history"):
            read_h1_selected_input_role(runtime, selected, captured)

    assert witness.admitted_record.command.token.receive_slot == "selected"


@pytest.mark.asyncio
async def test_selected_input_role_rejects_truncated_admission_publication_ids(
    tmp_path: Path,
) -> None:
    database, custody = tmp_path / "state.sqlite", tmp_path / "custody"
    request, complete = await _mounted_candidate(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = cast(SelectedExecutionReceiptV1, await runtime.drive_input(request))
        started = await runtime.begin_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, started.head
        )
        selected = select_h1_v3_prepare_for_candidate(
            runtime, captured, expected_head=captured.head
        )
        witness = read_h1_selected_input_role(runtime, selected, captured)
        admission = witness.inbound_owner_decisions[-1]
        batch = admission.prepared.request
        assert len(batch.complete_records) == 4
        with sqlite3.connect(database) as connection:
            cursor = connection.execute(
                "UPDATE publications SET record_ids=? WHERE tenant_id=? "
                "AND operation_kind=? AND idempotency_key=? AND commit_sequence=?",
                (
                    batch.complete_records[0].record_id,
                    batch.identity.tenant_id,
                    batch.operation,
                    batch.identity.command_id,
                    admission.tenant_commit_sequence,
                ),
            )
            assert cursor.rowcount == 1
        with pytest.raises(
            ValueError, match="selected input publication is not exactly materialized"
        ):
            read_h1_selected_input_role(runtime, selected, captured)


@pytest.mark.asyncio
async def test_selected_input_role_rejects_a_captured_run_not_in_physical_ancestry(
    tmp_path: Path,
) -> None:
    database, custody = tmp_path / "state.sqlite", tmp_path / "custody"
    request, complete = await _mounted_candidate(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = cast(SelectedExecutionReceiptV1, await runtime.drive_input(request))
        started = await runtime.begin_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, started.head
        )
        selected = select_h1_v3_prepare_for_candidate(
            runtime, captured, expected_head=captured.head
        )
        with pytest.raises(ValueError, match="captured Run is absent"):
            read_h1_selected_input_role(
                runtime, selected, captured.model_copy(update={"head": "loop:forged"})
            )
