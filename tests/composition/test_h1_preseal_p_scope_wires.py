"""Mounted RED witnesses for the retained H1 P scope-exchange sibling.

Every path begins with an installed V3 Prepare and the real V2 DECIDED
writer.  The tests neither supply a P exchange DTO nor replace the journal
with an unsigned in-memory imitation.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal, cast

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    H1OwnerCandidateCallV1,
    H1OwnerCandidateV1,
    H1OwnerCurrentCallV1,
    H1OwnerCurrentCandidateV1,
)
from chiplog.composition.common_cli_execution_runtime import (
    R17_RETAINED_READER_ID,
    CommonCliExecutionRuntime,
    open_installed_h1_runtime,
)
from chiplog.composition.common_execution_driver_contracts import (
    CliRetainedSelectedSourceV1,
    DriveInputRequestV1,
    DriverCommandIdentityV1,
    SelectedExecutionReceiptV1,
)
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_preissuance_registration import H1PreissuanceSourceViolation
from chiplog.composition.h1_preseal_pe_anchor_records import (
    decode_h1_preseal_pe_anchor_record,
)
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_candidate
from chiplog.composition.h1_v2_recovery_native_source import H1V2RecoveryNativeSource
from chiplog.composition.r14_execution_complete_seal_records import (
    ExecutionCompleteSealPhysicalEnvelopeV2,
    RetainedExecutionCompleteSealV2,
    build_complete_seal_envelope,
    complete_seal_physical_command,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._sqlite import PhysicalPublicationCommand
from chiplog.platform.ingress_transition_contracts import (
    IngressCommandIdentity,
    RetainedIngressSource,
)
from chiplog.platform.r7_trust import decode_trust_owner_call_canonical
from tests.support.h1_cli_execution import _admit, _client
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot

_WIRE_FIELDS = {"kind", "version", "binding", "issue", "current"}
_PAIR_FIELDS = {"sent_payload_base64", "returned_payload_base64"}


def _resources(tmp_path: Path, suffix: str = "") -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / f"dispatch-custody{suffix}"
    )


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _text(decision: dict[str, object], field: str) -> str:
    value = decision.get(field)
    if not isinstance(value, str):
        raise AssertionError(f"selected DECIDED entry lacks canonical {field}")
    return value


def _selected_decision(
    runtime: CommonCliExecutionRuntime, operation_id: str
) -> tuple[str, bytes, dict[str, object]]:
    decision_id, _predecessor, raw = next(
        entry
        for entry in runtime._loop_decisions().entries()
        if json.loads(entry[2]).get("kind") == "DECIDED"
        and json.loads(entry[2]).get("operation_id") == operation_id
    )
    decision = json.loads(raw)
    assert isinstance(decision, dict)
    return decision_id, raw, decision


def _decision_by_id(
    runtime: CommonCliExecutionRuntime, decision_id: str
) -> tuple[str, bytes, dict[str, object]]:
    found_id, _predecessor, raw = next(
        entry for entry in runtime._loop_decisions().entries() if entry[0] == decision_id
    )
    decision = json.loads(raw)
    assert isinstance(decision, dict)
    return found_id, raw, decision


def _new_complete_seal_decision(
    runtime: CommonCliExecutionRuntime, before_ids: set[str]
) -> tuple[str, bytes, dict[str, object]]:
    matches: list[tuple[str, bytes, dict[str, object]]] = []
    for decision_id, _predecessor, raw in runtime._loop_decisions().entries():
        if decision_id in before_ids:
            continue
        decision = json.loads(raw)
        if (
            isinstance(decision, dict)
            and decision.get("kind") == "DECIDED"
            and decision.get("operation_kind") == "agent_loop.execution-complete-seal.v1"
        ):
            matches.append((decision_id, raw, decision))
    assert len(matches) == 1
    return matches[0]


async def _seal_v3_prepare_as_v2(
    runtime: CommonCliExecutionRuntime,
    *,
    text: str,
    original: DriveInputRequestV1 | None = None,
) -> tuple[DriveInputRequestV1, CallSubjectHead, str]:
    """Use only the installed V3 Prepare -> V2 seal route."""
    if original is None:
        original = await _admit(runtime)
    initial = await runtime.drive_input(original)
    assert isinstance(initial, SelectedExecutionReceiptV1)
    run_id = initial.stable_run_lineage_id
    runtime._execution_model._responses = (
        DeliveryCompletion(
            tenant=TENANT,
            run_id=run_id,
            turn_id=run_id + "/turn/1",
            deliveries=(ProposedDelivery(payload=(Commentary(text=text),)),),
        ).canonical_bytes(),
    )
    started = await runtime.begin_execution(
        "hermetic-ingress", run_id, initial.selected_run_head.head
    )
    captured = await runtime.capture_execution("hermetic-ingress", run_id, started.head)
    selected = select_h1_v3_prepare_for_candidate(runtime, captured, expected_head=captured.head)
    assert selected.decision_id
    sealed = await runtime.seal_execution_complete(
        "hermetic-ingress", run_id, captured.head, profile="H1_V2"
    )
    decision_id, _raw, decision = _selected_decision(runtime, sealed.head)
    retained = RetainedExecutionCompleteSealV2.model_validate_json(
        _text(decision, "execution_complete_seal")
    )
    response_seal = retained.exchange.proposal.fan_out.response_seal
    return (
        original,
        CallSubjectHead(
            subject_id=response_seal.response_seal_id,
            revision=Present(
                head="record:" + response_seal.digest(), fingerprint=response_seal.digest()
            ),
        ),
        decision_id,
    )


async def _admit_distinct_ingress(
    runtime: CommonCliExecutionRuntime, ingress_slot: str
) -> DriveInputRequestV1:
    """Create a second authenticated ingress without reusing immutable ``slot``."""
    runtime.provision_retained(ingress_slot, b"later unrelated P scope wires input")
    await runtime.allocate_receipt(ingress_slot)
    await runtime.stage_receipt(ingress_slot)
    token = next(
        entry.token.token_id
        for entry in runtime.ingress_history().custody.entries
        if entry.token.receive_slot == ingress_slot
    )
    async with runtime.cli_custody(ingress_slot) as custody:
        peer = asyncio.create_task(_client(custody.socket.path))
        assert (await custody.admit()).kind == "COMMITTED"
        await peer
    admitted = runtime.read_admitted_inbox(token)
    assert admitted is not None
    record = admitted.record
    ingress = IngressCommandIdentity(
        tenant_id=TENANT, database_id="hermetic-database", command_id=token
    )
    retained = RetainedIngressSource(
        source=record.command.retention.proof,
        reader_id=R17_RETAINED_READER_ID,
        schema_id="chiplog.ingress.retained-source-observation.v1",
        canonical_source_bytes=record.command.retention.observation_bytes,
    )
    return DriveInputRequestV1(
        identity=DriverCommandIdentityV1(
            tenant_id=TENANT,
            database_id="hermetic-database",
            driver_command_id="driver:" + token,
            original_ingress_identity=ingress,
            original_ingress_request_fingerprint=record.inbox.authentication_request_fingerprint,
        ),
        selected_source=CliRetainedSelectedSourceV1(
            original_ingress_identity=ingress,
            source_binding=record.command.token.source,
            selected_ingress_decision=admitted.selected_decision,
            source_head=retained.source,
            retained_source=retained,
            expected_reader_id=R17_RETAINED_READER_ID,
        ),
    )


def _scope_wires(decision: dict[str, object]) -> dict[str, object]:
    value = decision.get("h1_preseal_p_scope_wires_v1")
    if not isinstance(value, dict):
        raise AssertionError("selected V2 DECIDED lacks P scope-exchange sibling")
    return value


def _strict_base64(value: object) -> bytes:
    if not isinstance(value, str):
        raise AssertionError("P scope-exchange payload is not base64 text")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as error:
        raise AssertionError("P scope-exchange payload is invalid base64") from error
    assert base64.b64encode(decoded).decode("ascii") == value
    return decoded


def _assert_pair(
    pair: object, *, role: Literal["issue", "current"], expected_digest: object
) -> tuple[str, str]:
    assert isinstance(pair, dict)
    assert set(pair) == _PAIR_FIELDS
    sent_b64 = pair["sent_payload_base64"]
    returned_b64 = pair["returned_payload_base64"]
    sent = _strict_base64(sent_b64)
    returned = _strict_base64(returned_b64)
    assert isinstance(sent_b64, str)
    assert isinstance(returned_b64, str)
    assert isinstance(expected_digest, str)
    assert hashlib.sha256(_canonical([sent_b64, returned_b64])).hexdigest() == expected_digest

    outer = decode_trust_owner_call_canonical(sent)
    assert outer.canonical_bytes() == sent
    if role == "issue":
        assert outer.mode == "ISSUE_HERMETIC_OUTPUT_SCOPE_V1"
        request = H1OwnerCandidateCallV1.model_validate_json(outer.request_bytes)
        result = H1OwnerCandidateV1.model_validate_json(returned)
        assert request.canonical_bytes() == outer.request_bytes
        assert result.canonical_bytes() == returned
        result.check_pinned_call(request)
    else:
        assert outer.mode == "READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1"
        request = H1OwnerCurrentCallV1.model_validate_json(outer.request_bytes)
        result = H1OwnerCurrentCandidateV1.model_validate_json(returned)
        assert request.canonical_bytes() == outer.request_bytes
        assert result.canonical_bytes() == returned
        result.check_pinned_call(request)
    return sent_b64, returned_b64


def _assert_descriptor(
    runtime: CommonCliExecutionRuntime,
    original: DriveInputRequestV1,
    selected_seal: CallSubjectHead,
    decision_id: str,
    decision: dict[str, object],
) -> tuple[bytes, tuple[str, str, str, str]]:
    """Check wire bytes against independently selected native/anchor evidence."""
    wires = _scope_wires(decision)
    assert set(wires) == _WIRE_FIELDS
    assert wires["kind"] == "H1_PRESEAL_P_SCOPE_WIRES_V1"
    assert wires["version"] == 1
    anchor = decode_h1_preseal_pe_anchor_record(_text(decision, "h1_preseal_pe_anchor").encode())
    anchor_data = anchor.as_dict()
    assert _canonical(wires["binding"]) == _canonical(anchor_data["binding"])
    p = cast(dict[str, object], anchor_data["p"])
    issue = _assert_pair(
        wires["issue"], role="issue", expected_digest=p["accepted_issue_wire_digest"]
    )
    current = _assert_pair(
        wires["current"], role="current", expected_digest=p["accepted_current_wire_digest"]
    )

    native = H1V2RecoveryNativeSource(runtime).select(
        original_identity=original.identity,
        original_fingerprint=original.original_driver_command_fingerprint(),
        selected_seal=selected_seal,
    )
    assert native.seal.decision_id == decision_id
    retained = RetainedExecutionCompleteSealV2.model_validate_json(
        _text(decision, "execution_complete_seal")
    )
    envelope = ExecutionCompleteSealPhysicalEnvelopeV2.model_validate_json(
        _text(decision, "execution_complete_seal_envelope")
    )
    assert envelope == build_complete_seal_envelope(retained)
    command = complete_seal_physical_command(envelope)
    assert len(envelope.records) == len(command.records) == 3
    assert tuple(record.record_id for record in command.records) == tuple(
        record.record_id for record in envelope.records
    )
    return _canonical(wires), (*issue, *current)


@pytest.mark.asyncio
async def test_installed_v3_prepare_v2_seal_retains_exact_p_scope_exchange_wires_without_member(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            original, selected_seal, decision_id = await _seal_v3_prepare_as_v2(
                runtime, text="preseal P scope wires"
            )
            _id, _raw, decision = _decision_by_id(runtime, decision_id)
            descriptor_bytes, _payloads = _assert_descriptor(
                runtime, original, selected_seal, decision_id, decision
            )
            assert descriptor_bytes == _canonical(_scope_wires(decision))


@pytest.mark.asyncio
async def test_p_scope_exchange_sibling_is_unchanged_after_restart_and_later_publication(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            original, selected_seal, decision_id = await _seal_v3_prepare_as_v2(
                runtime, text="original P scope wires"
            )
            _id, _raw, decision = _decision_by_id(runtime, decision_id)
            before, payloads = _assert_descriptor(
                runtime, original, selected_seal, decision_id, decision
            )

        async with open_installed_h1_runtime(
            launch, resources=_resources(tmp_path, "-later")
        ) as later:
            unrelated = await _admit_distinct_ingress(later, "p-scope-wires-later")
            assert unrelated.identity != original.identity
            assert unrelated.original_driver_command_fingerprint() != (
                original.original_driver_command_fingerprint()
            )
            later_initial = await later.drive_input(unrelated)
            assert isinstance(later_initial, SelectedExecutionReceiptV1)
            assert later_initial.disposition == "COMMITTED"
            assert later_initial.identity == unrelated.identity
            _reselected = H1V2RecoveryNativeSource(later).select(
                original_identity=original.identity,
                original_fingerprint=original.original_driver_command_fingerprint(),
                selected_seal=selected_seal,
            )
            _id, _raw, decision = _decision_by_id(later, decision_id)
            after, later_payloads = _assert_descriptor(
                later, original, selected_seal, decision_id, decision
            )
            assert after == before
            assert later_payloads == payloads


def _mutate_residual(
    kind: Literal["missing", "unknown", "malformed", "payload", "anchor"],
) -> Callable[[dict[str, object]], None]:
    def mutate(decision: dict[str, object]) -> None:
        if kind == "missing":
            del decision["h1_preseal_p_scope_wires_v1"]
            return
        residual = _scope_wires(decision)
        if kind == "unknown":
            residual["version"] = 2
        elif kind == "malformed":
            residual["issue"] = {"sent_payload_base64": "%%%"}
        elif kind == "payload":
            issue = cast(dict[str, object], residual["issue"])
            issue["sent_payload_base64"] = base64.b64encode(b"{}").decode("ascii")
        else:
            binding = cast(dict[str, object], residual["binding"])
            binding["tenant_id"] = "different-tenant"

    return mutate


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ("missing", "unknown", "malformed", "payload", "anchor"))
@pytest.mark.xfail(
    strict=True,
    reason="the installed V2 writer/reader has no retained P scope-exchange integrity boundary",
)
async def test_historical_p_reader_holds_on_genuinely_appended_invalid_scope_residual(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["missing", "unknown", "malformed", "payload", "anchor"],
) -> None:
    """The mutation is journal-authenticated because it precedes the real append."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            original_append = runtime._append_decision

            def append_mutated(decision: dict[str, object]) -> None:
                if decision.get("kind") == "DECIDED":
                    _mutate_residual(kind)(decision)
                original_append(decision)

            monkeypatch.setattr(runtime, "_append_decision", append_mutated)
            original, selected_seal, _decision_id = await _seal_v3_prepare_as_v2(
                runtime, text="invalid retained P scope wires"
            )
            port = runtime._h1_preissuance_registration_source_port
            issue = getattr(port, "_issue_historical_recovery_source", None)
            assert callable(issue)
            with pytest.raises(
                H1PreissuanceSourceViolation, match=r"scope|wire|residual|integrity"
            ):
                issue(
                    original_identity=original.identity,
                    original_fingerprint=original.original_driver_command_fingerprint(),
                    selected_seal=selected_seal,
                )


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="the installed historical P reader does not yet validate retained-wire seal binding",
)
async def test_historical_p_reader_rejects_an_actual_other_v2_seal_locator(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            original, _selected_seal, _decision_id = await _seal_v3_prepare_as_v2(
                runtime, text="first retained P scope wires"
            )
            _other, other_seal, _other_decision = await _seal_v3_prepare_as_v2(
                runtime, text="second retained P scope wires"
            )
            port = runtime._h1_preissuance_registration_source_port
            issue = getattr(port, "_issue_historical_recovery_source", None)
            assert callable(issue)
            with pytest.raises(H1PreissuanceSourceViolation, match=r"selected|seal|binding|native"):
                issue(
                    original_identity=original.identity,
                    original_fingerprint=original.original_driver_command_fingerprint(),
                    selected_seal=other_seal,
                )


@pytest.mark.asyncio
async def test_pending_v2_replay_preserves_scope_wire_bytes_without_new_current_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    calls = {"capture": 0}
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            owner = cast(Any, runtime)._h1_preseal_pe_decision_owner
            owner_type = type(owner)
            original_capture = owner_type.capture

            async def traced_capture(owner: object, preflight: object) -> object:
                calls["capture"] += 1
                return await original_capture(owner, preflight)

            monkeypatch.setattr(owner_type, "capture", traced_capture)
            request = await _admit(runtime)
            initial = await runtime.drive_input(request)
            assert isinstance(initial, SelectedExecutionReceiptV1)
            run_id = initial.stable_run_lineage_id
            runtime._execution_model._responses = (
                DeliveryCompletion(
                    tenant=TENANT,
                    run_id=run_id,
                    turn_id=run_id + "/turn/1",
                    deliveries=(
                        ProposedDelivery(payload=(Commentary(text="pending scope wires"),)),
                    ),
                ).canonical_bytes(),
            )
            started = await runtime.begin_execution(
                "hermetic-ingress", run_id, initial.selected_run_head.head
            )
            captured = await runtime.capture_execution("hermetic-ingress", run_id, started.head)
            select_h1_v3_prepare_for_candidate(runtime, captured, expected_head=captured.head)
            submit = runtime._appender.submit

            async def crash_before_commit(command: PhysicalPublicationCommand) -> object:
                return await submit(replace(command, fault="before_commit"))

            monkeypatch.setattr(runtime._appender, "submit", crash_before_commit)
            before_ids = {entry[0] for entry in runtime._loop_decisions().entries()}
            with pytest.raises(RuntimeError, match="before commit"):
                await runtime.seal_execution_complete(
                    "hermetic-ingress", run_id, captured.head, profile="H1_V2"
                )
            decision_id, raw, decision = _new_complete_seal_decision(runtime, before_ids)
            operation_id = _text(decision, "operation_id")
            retained_bytes = _canonical(_scope_wires(decision))
            selected = runtime._publication(runtime._pending()[0])
            database = runtime._database
            assert calls == {"capture": 1}

        async def forbid_current(*_args: object, **_kwargs: object) -> object:
            pytest.fail("pending replay made a fresh CURRENT call")

        monkeypatch.setattr(
            CommonCliExecutionRuntime,
            "_read_current_hermetic_output_scope_with_wire",
            forbid_current,
        )
        async with open_installed_h1_runtime(
            launch, resources=_resources(tmp_path, "-restart")
        ) as reopened:
            replay_id, replay_raw, replay = _selected_decision(reopened, operation_id)
            assert replay_id == decision_id
            assert replay_raw == raw
            assert _canonical(_scope_wires(replay)) == retained_bytes
            assert reopened._pending() == ()
            assert calls == {"capture": 1}
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM publications WHERE tenant_id=? AND operation_kind=?",
                (TENANT, "agent_loop.execution-complete-seal.v1"),
            ).fetchone() == (1,)
            physical = connection.execute(
                "SELECT record_id FROM records WHERE commit_sequence=? ORDER BY rowid",
                (selected.expected_head + 1,),
            ).fetchall()
        assert len(physical) == 3


__all__ = []
