"""Bounded raw-source seam for H1 first-path completion."""

import asyncio
import hashlib
import json
import sqlite3
import struct
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
from typing import cast

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
    execution_h1_zero_call_frontier_registry_v2,
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
    SelectedExecutionReceiptV1,
)
from chiplog.composition.h1_first_path_sources import (
    H1CurrentFirstPathNativeCut,
    H1FirstPathCapture,
    H1FirstPathPhysicalMember,
    H1FirstPathSources,
    H1HistoricalFirstPathNativeCut,
)
from chiplog.composition.r14_execution_complete_seal_records import (
    EXECUTION_COMPLETE_SEAL_OPERATION,
    ExecutionCompleteSealPhysicalEnvelopeV2,
    RetainedExecutionCompleteSealV2,
    RetainedExecutionCompleteSealV3,
    complete_seal_physical_command,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.ingress_transition_contracts import (
    IngressCommandIdentity,
    RetainedIngressSource,
)
from tests.support.h1_cli_execution import admit_complete_script


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


def test_completion_builder_rejects_unissued_capture_before_runtime_access() -> None:
    reader = object.__new__(H1FirstPathSources)
    with pytest.raises(ValueError, match="issuer-owned first-path capture"):
        reader._prepare_first_path_completion_request(cast_capture(object()), object())


async def test_reader_captures_and_rechecks_genuine_cli_h1_v2_postseal_cut(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
        selected_decisions: list[tuple[str, bytes]] = []
        for decision_id, _, raw in runtime._loop_decisions().entries():
            entry = json.loads(raw)
            if entry.get("operation_kind") != EXECUTION_COMPLETE_SEAL_OPERATION:
                continue
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
            selected_decisions.append((decision_id, raw))
        assert len(seals) == 1
        assert len(selected_decisions) == 1
        selected_decision_id, selected_decision_bytes = selected_decisions[0]
        selected_entry = json.loads(selected_decision_bytes)
        envelope = ExecutionCompleteSealPhysicalEnvelopeV2.model_validate_json(
            selected_entry["execution_complete_seal_envelope"]
        )
        expected_command = complete_seal_physical_command(envelope)
        reader = H1FirstPathSources(runtime)
        capture = reader.capture_current(
            original_identity=request.identity,
            original_fingerprint=request.original_driver_command_fingerprint(),
            selected_seal=seals[0],
        )

        # A genuine, issuer-owned capture is the sole input the private builder
        # accepts.  The presently mounted runtime has no authenticated delivery
        # observation/fence route, so it must stop before any owner IPC.
        class NoOwnerIpc:
            calls = 0

            def runtime(self) -> object:
                self.calls += 1
                reader._gate.require_held()
                raise AssertionError("owner IPC occurred under the authority gate")

        no_owner_ipc = NoOwnerIpc()
        with monkeypatch.context() as patch:
            patch.setattr(runtime, "_supervisor", no_owner_ipc)
            with pytest.raises(
                ValueError,
                match="owner-authenticated DeliveryObservation and NonSchedulerFence",
            ):
                reader._prepare_first_path_completion_request(capture, object())
        assert no_owner_ipc.calls == 0

        copied = replace(capture)
        with pytest.raises(ValueError, match="issuer-owned first-path capture"):
            reader._prepare_first_path_completion_request(copied, object())

        foreign_reader = H1FirstPathSources(runtime)
        foreign_capture = foreign_reader.capture_current(
            original_identity=request.identity,
            original_fingerprint=request.original_driver_command_fingerprint(),
            selected_seal=seals[0],
        )
        with pytest.raises(ValueError, match="issuer-owned first-path capture"):
            reader._prepare_first_path_completion_request(foreign_capture, object())

        assert reader.check_current(capture) is True

        class NoCurrentReplayOwnerIpc:
            calls = 0

            def runtime(self) -> object:
                self.calls += 1
                raise AssertionError("current native replay called owner IPC")

        no_current_replay_owner_ipc = NoCurrentReplayOwnerIpc()
        with monkeypatch.context() as patch:
            patch.setattr(runtime, "_supervisor", no_current_replay_owner_ipc)
            current_cut = reader.replay_current_native_cut(capture)
        assert no_current_replay_owner_ipc.calls == 0
        assert type(current_cut) is H1CurrentFirstPathNativeCut
        assert current_cut.source == capture.source
        assert current_cut.initialization.raw_bytes == capture.initialization_envelope_bytes
        assert (
            tuple(item.raw_bytes for item in current_cut.lineage)
            == (capture.selected_envelopes[:-1])
        )
        assert current_cut.seal.raw_bytes == capture.selected_envelopes[-1]
        assert current_cut.physical_members == capture.physical_members
        assert (
            current_cut.database_path,
            current_cut.database_device,
            current_cut.database_inode,
        ) == (capture.database_path, capture.database_device, capture.database_inode)
        assert current_cut.commitment == capture.source.materialization_commitment

        with pytest.raises(ValueError, match="issuer-owned first-path capture"):
            reader.replay_current_native_cut(replace(capture))
        with pytest.raises(ValueError, match="issuer-owned first-path capture"):
            reader.replay_current_native_cut(foreign_capture)

        journal = database.with_suffix(database.suffix + ".loop-journal")
        journal_bytes = journal.read_bytes()
        selected_encoded = selected_decision_bytes.hex().encode()
        assert journal_bytes.count(selected_encoded) == 1
        selected_replacement = (b"0" if selected_encoded[:1] != b"0" else b"1") + selected_encoded[
            1:
        ]
        journal.write_bytes(journal_bytes.replace(selected_encoded, selected_replacement))
        with pytest.raises(ValueError, match="current first-path capture"):
            reader.replay_current_native_cut(capture)
        journal.write_bytes(journal_bytes)
        assert capture.source.frontier.registry == execution_h1_zero_call_frontier_registry_v2()
        assert capture.source.frontier.ordered_members
        seal_members = tuple(
            member
            for member in capture.physical_members
            if member.decision_id == selected_decision_id
        )
        assert len(seal_members) == 3
        assert tuple(
            (member.record_id, member.owner, member.schema_id, member.canonical_bytes)
            for member in seal_members
        ) == tuple(
            (member.record_id, member.owner, member.schema_id, member.canonical_bytes)
            for member in expected_command.records
        )
        assert capture.source.selected_response_seal == seals[0]
        assert capture.source.complete_sources[-1].canonical_record_bytes == (
            execution_h1_zero_call_frontier_registry_v2().canonical_bytes()
        )
        assert any(
            member.family == "EVIDENCE" for member in capture.source.frontier.ordered_members
        )

        original_initialization_envelope = capture.initialization_envelope_bytes
        object.__setattr__(
            capture,
            "initialization_envelope_bytes",
            original_initialization_envelope + b" ",
        )
        with pytest.raises(ValueError, match="current first-path capture"):
            reader._prepare_first_path_completion_request(capture, object())
        with pytest.raises(ValueError, match="current first-path capture"):
            reader.replay_current_native_cut(capture)
        object.__setattr__(
            capture,
            "initialization_envelope_bytes",
            original_initialization_envelope,
        )

        original_envelopes = capture.selected_envelopes
        object.__setattr__(capture, "selected_envelopes", tuple(reversed(original_envelopes)))
        with pytest.raises(ValueError, match="current first-path capture"):
            reader.replay_current_native_cut(capture)
        object.__setattr__(capture, "selected_envelopes", original_envelopes)

        original_source = capture.source
        original_seal_bytes = original_source.seal.canonical_bytes()
        object.__setattr__(
            capture,
            "source",
            capture.source.model_copy(update={"materialization_commitment": "0" * 64}),
        )
        assert reader.check_current(capture) is False
        with pytest.raises(ValueError, match="current first-path capture"):
            reader.replay_current_native_cut(capture)
        object.__setattr__(
            capture,
            "source",
            original_source,
        )

        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE records SET canonical_bytes=? WHERE tenant_id=? AND record_id=?",
                (b"tampered", "hermetic-tenant", seals[0].revision.head),
            )
        assert reader.check_current(capture) is False
        with pytest.raises(ValueError, match="independent commitment anchor"):
            reader.replay_current_native_cut(capture)
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE records SET canonical_bytes=? WHERE tenant_id=? AND record_id=?",
                (original_seal_bytes, "hermetic-tenant", seals[0].revision.head),
            )
        assert reader.check_current(capture) is True

        await runtime.create_execution("hermetic-ingress", "later-run", "Plan", BudgetPolicy())
        with pytest.raises(
            ValueError, match="post-seal inventory receipt differs from raw selected cut"
        ):
            reader.replay_current_native_cut(capture)

        journal = database.with_suffix(database.suffix + ".loop-journal")
        journal_bytes = journal.read_bytes()
        encoded = selected_decision_bytes.hex().encode()
        assert journal_bytes.count(encoded) == 1
        replacement = (b"0" if encoded[:1] != b"0" else b"1") + encoded[1:]
        journal.write_bytes(journal_bytes.replace(encoded, replacement))
        assert reader.check_current(capture) is False
        with pytest.raises(ValueError, match="current first-path capture"):
            reader._prepare_first_path_completion_request(capture, object())


@pytest.mark.asyncio
async def test_historical_native_cut_replays_selected_raw_bytes_without_owner_ipc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A V3 checkpoint cut survives a later append, but not a corrupt journal tail."""
    database = tmp_path / "historical-native-cut.sqlite"
    custody = tmp_path / "dispatch-custody"
    request, complete = await admit_complete_script(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = await runtime.drive_input(request)
        assert isinstance(initial, SelectedExecutionReceiptV1)
        started = await runtime.begin_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, started.head
        )
        sealed = await runtime.seal_execution_complete(
            "hermetic-ingress",
            initial.stable_run_lineage_id,
            captured.head,
            profile="H1_V3",
        )
        decision_raw = next(
            raw
            for _, _, raw in runtime._loop_decisions().entries()
            if json.loads(raw).get("kind") == "DECIDED"
            and json.loads(raw).get("operation_id") == sealed.head
        )
        retained = RetainedExecutionCompleteSealV3.model_validate_json(
            json.loads(decision_raw)["execution_complete_seal"]
        )
        seal = retained.exchange.proposal.fan_out.response_seal
        locator = CallSubjectHead(
            subject_id=seal.response_seal_id,
            revision=Present(head="record:" + seal.digest(), fingerprint=seal.digest()),
        )
        reader = H1FirstPathSources(runtime)
        raw = reader._read_selected_cut(
            original_identity=request.identity,
            original_fingerprint=request.original_driver_command_fingerprint(),
            selected_seal=locator,
            historical=True,
        )
        source = reader._read_v2_source(raw, historical=True)

        # A publication after the sealed checkpoint must not replace its selected cut.
        await runtime.create_execution("hermetic-ingress", "later-run", "Plan", BudgetPolicy())

        class NoOwnerIpc:
            calls = 0

            def runtime(self) -> object:
                self.calls += 1
                raise AssertionError("historical raw replay called owner IPC")

        no_owner_ipc = NoOwnerIpc()
        with monkeypatch.context() as patch:
            patch.setattr(runtime, "_supervisor", no_owner_ipc)
            cut = reader.replay_selected_native_cut(
                source, initialization_envelope_bytes=raw.initialization.raw
            )
        assert no_owner_ipc.calls == 0
        assert type(cut) is H1HistoricalFirstPathNativeCut
        assert tuple(field.name for field in fields(cut)) == (
            "source",
            "initialization",
            "lineage",
            "seal",
            "physical_members",
            "database_path",
            "database_device",
            "database_inode",
        )
        assert cut.initialization.raw_bytes == raw.initialization.raw
        assert cut.initialization.decision_id == raw.initialization.decision_id
        assert (
            cut.initialization.decision_fingerprint
            == hashlib.sha256(raw.initialization.raw).hexdigest()
        )
        assert tuple(item.raw_bytes for item in cut.lineage) == tuple(
            item.raw for item, _, _ in raw.lineage
        )
        assert tuple(item.decision_id for item in cut.lineage) == tuple(
            item.decision_id for item, _, _ in raw.lineage
        )
        assert tuple(item.decision_fingerprint for item in cut.lineage) == tuple(
            hashlib.sha256(item.raw).hexdigest() for item, _, _ in raw.lineage
        )
        assert cut.seal.raw_bytes == raw.seal.raw
        assert cut.seal.decision_id == raw.seal.decision_id
        assert cut.seal.decision_fingerprint == hashlib.sha256(raw.seal.raw).hexdigest()
        assert cut.physical_members == raw.physical_members
        assert (
            cut.database_path,
            cut.database_device,
            cut.database_inode,
        ) == raw.database_identity
        with pytest.raises(FrozenInstanceError):
            cut.seal = cut.seal  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            cut.initialization.raw_bytes = b"substituted"  # type: ignore[misc]

        reader.validate_historical(source, initialization_envelope_bytes=raw.initialization.raw)
        swapped_members = source.model_copy(
            update={"complete_sources": tuple(reversed(source.complete_sources))}
        )
        with pytest.raises(ValueError, match="raw V2 replay"):
            reader.replay_selected_native_cut(
                swapped_members, initialization_envelope_bytes=raw.initialization.raw
            )
        wrong_original = source.model_copy(
            update={
                "selected_admitted_input": source.selected_admitted_input.model_copy(
                    update={"normalized_prompt": "swapped original"}
                )
            }
        )
        with pytest.raises(ValueError, match="raw V2 selection"):
            reader.replay_selected_native_cut(
                wrong_original, initialization_envelope_bytes=raw.initialization.raw
            )
        wrong_seal = source.model_copy(update={"selected_response_seal": source.current_run})
        with pytest.raises(ValueError, match="no unique selected V3 seal"):
            reader.replay_selected_native_cut(
                wrong_seal, initialization_envelope_bytes=raw.initialization.raw
            )
        with pytest.raises(ValueError, match="raw V2 selection"):
            reader.replay_selected_native_cut(
                source, initialization_envelope_bytes=raw.initialization.raw + b" "
            )

        # A byte-for-byte reconstructed DTO supplies no authority when its
        # independently selected raw entry no longer exists.
        reconstructed = type(source).model_validate_json(source.canonical_bytes())
        assert reconstructed == source
        journal = database.with_suffix(database.suffix + ".loop-journal")
        journal_bytes = journal.read_bytes()
        encoded = raw.seal.raw.hex().encode()
        assert journal_bytes.count(encoded) == 1
        replacement = (b"0" if encoded[:1] != b"0" else b"1") + encoded[1:]
        journal.write_bytes(journal_bytes.replace(encoded, replacement))
        with pytest.raises(RuntimeError, match="journal prefix or predecessor mismatch"):
            reader.replay_selected_native_cut(
                reconstructed, initialization_envelope_bytes=raw.initialization.raw
            )

        # A corrupt later append is also fail-closed, even though the historical
        # source selection itself is bounded by the V3 checkpoint.
        journal.write_bytes(journal_bytes + b"corrupt-tail")
        with pytest.raises(RuntimeError, match="journal entry is unauthentic"):
            reader.replay_selected_native_cut(
                source, initialization_envelope_bytes=raw.initialization.raw
            )


def cast_capture(value: object) -> H1FirstPathCapture:
    """Avoid inventing a structurally valid capture in the provenance test."""
    return cast("H1FirstPathCapture", value)
