"""Bounded historical native H0/R17 input-role witness checks."""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
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
from chiplog.composition.h1_preseal_contracts import H1SelectedPrepare
from chiplog.composition.h1_selected_input_role import _read_h1_historical_selected_input_role
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_candidate
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.ingress_transition_contracts import (
    IngressCommandIdentity,
    RetainedIngressSource,
)
from chiplog.platform.owner_decision_journal import OwnerJournalSnapshot


@dataclass(frozen=True)
class _Row:
    record_id: str
    owner: str
    schema: str
    raw: bytes
    sequence: int


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


async def _bounded_inputs(
    database: Path, custody: Path
) -> tuple[
    H1SelectedPrepare,
    ExecutionRunRecord,
    tuple[tuple[str, str | None, bytes], ...],
    OwnerJournalSnapshot,
    tuple[tuple[object, ...], ...],
    tuple[_Row, ...],
]:
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
        loop_entries = tuple(runtime._loop_decisions().entries())
        owner_snapshot = runtime._owner_decisions().snapshot()
        with sqlite3.connect(database) as connection:
            publications = tuple(
                connection.execute(
                    "SELECT operation_kind,idempotency_key,request_fingerprint,"
                    "commit_sequence,record_ids FROM publications WHERE tenant_id=? "
                    "ORDER BY commit_sequence,operation_kind,idempotency_key",
                    ("hermetic-tenant",),
                )
            )
            rows = tuple(
                _Row(str(record_id), str(owner), str(schema), bytes(raw), int(sequence))
                for record_id, owner, schema, raw, sequence in connection.execute(
                    "SELECT record_id,owner,schema_id,canonical_bytes,commit_sequence "
                    "FROM records WHERE tenant_id=? ORDER BY commit_sequence,record_id",
                    ("hermetic-tenant",),
                )
            )
    return selected, captured, loop_entries, owner_snapshot, publications, rows


@pytest.mark.asyncio
async def test_bounded_historical_witness_returns_only_the_four_selected_occurrences(
    tmp_path: Path,
) -> None:
    inputs = await _bounded_inputs(tmp_path / "state.sqlite", tmp_path / "custody")

    witness = _read_h1_historical_selected_input_role(*inputs)

    assert tuple(item.reason for item in witness.occurrences) == (
        "H0_NATIVE_INPUT",
        "R17_ALLOCATION_INPUT",
        "R17_STAGED_INPUT",
        "R17_ADMITTED_INPUT",
    )
    assert len(witness.inbound_owner_decisions) == 3


@pytest.mark.asyncio
async def test_bounded_historical_witness_rejects_extra_connected_sql_input(tmp_path: Path) -> None:
    selected, captured, loop_entries, owner_snapshot, publications, rows = await _bounded_inputs(
        tmp_path / "state.sqlite", tmp_path / "custody"
    )

    with pytest.raises(ValueError):
        _read_h1_historical_selected_input_role(
            selected,
            captured,
            loop_entries,
            owner_snapshot,
            publications,
            (*rows, rows[0]),
        )


@pytest.mark.asyncio
async def test_bounded_historical_witness_rejects_a_selected_publication_mutation(
    tmp_path: Path,
) -> None:
    selected, captured, loop_entries, owner_snapshot, publications, rows = await _bounded_inputs(
        tmp_path / "state.sqlite", tmp_path / "custody"
    )
    witness = _read_h1_historical_selected_input_role(
        selected, captured, loop_entries, owner_snapshot, publications, rows
    )
    mutated_publications = tuple(
        publication
        for publication in publications
        if publication[4] != witness.occurrences[0].record_id
    )

    with pytest.raises(ValueError, match="bounded command"):
        _read_h1_historical_selected_input_role(
            selected,
            captured,
            loop_entries,
            owner_snapshot,
            mutated_publications,
            rows,
        )
