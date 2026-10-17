"""Mounted V24 owner exchange for the narrow cancelled native call slice."""

from __future__ import annotations

import sqlite3
from base64 import b64encode
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from chiplog.architecture.r7_runtime import (
    R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V23_PRODUCTION_MANIFEST,
    R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V24_PRODUCTION_MANIFEST,
)
from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallSubjectHead,
    CancelledBeforeAcceptRecord,
    InitializedCallRecord,
    NotExecutedCallResultRecord,
    SealedResponseRecord,
)
from chiplog.capabilities.agent_loop.execution_contracts import (
    ExecutionRunRecord,
)
from chiplog.capabilities.agent_loop.native_one_call_accounting_contracts import (
    NATIVE_ONE_CALL_ACCOUNTING_REQUEST_SCHEMA,
    NATIVE_ONE_CALL_ACCOUNTING_V2_RESULT_SCHEMA,
    PrepareNativeOneCallCancelledAccounting,
    PrepareNativeOneCallCancelledAccountingV2,
)
from chiplog.capabilities.agent_loop.native_one_call_accounting_preparation import (
    prepare_native_one_call_cancelled_accounting,
    prepare_native_one_call_cancelled_accounting_v2,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.recovery_domain import RecoveryHold
from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
    execution_zero_call_frontier_registry,
    frontier_registry_reference,
)
from chiplog.composition.common_cli_execution_runtime import (
    open_common_cli_execution_runtime,
)
from chiplog.composition.r14_execution_fanout_records import reference
from chiplog.composition.r14_execution_recovery_source import R14NativeCancelledCallSource
from chiplog.composition.r14_native_one_call_accounting_producer import (
    R14NativeOneCallCancelledAccountingProducer,
    R14NativeOneCallCancelledAccountingV24Producer,
)
from chiplog.composition.r14_native_selected_accounting_source import (
    R14NativeSelectedAccountingSource,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.broker import PublicPortSuccess
from tests.support.composition.r14_native_one_call_accounting_producer import (
    _response,
    _selected_cancelled,
)


def _rehead_attempt(attempt: object) -> object:
    pending = attempt.model_copy(update={"head": "pending"})
    return pending.model_copy(update={"head": "execution-attempt:" + pending.digest()})


def _rehead_turn(turn: object) -> object:
    pending = turn.model_copy(update={"head": "pending"})
    return pending.model_copy(update={"head": "execution-turn:" + pending.digest()})


def _rehead_run(run: ExecutionRunRecord) -> ExecutionRunRecord:
    pending = run.model_copy(update={"head": "pending"})
    return pending.model_copy(update={"head": "loop:" + pending.digest()})


def _tamper_selected_sealed_run(
    request: PrepareNativeOneCallCancelledAccounting,
) -> PrepareNativeOneCallCancelledAccounting:
    sealed = ExecutionRunRecord.model_validate_json(request.selected_sealed_run_bytes)
    turn = sealed.turns[-1]
    attempt = _rehead_attempt(turn.attempts[-1].model_copy(update={"receipt": "rival"}))
    changed_turn = _rehead_turn(
        turn.model_copy(update={"attempts": (*turn.attempts[:-1], attempt)})
    )
    changed = _rehead_run(sealed.model_copy(update={"turns": (*sealed.turns[:-1], changed_turn)}))
    return request.model_copy(
        update={
            "selected_sealed_run": reference(changed.run_id, changed),
            "physical_sealed_run": CallSubjectHead(
                subject_id=changed.run_id,
                revision=Present(head=changed.head, fingerprint=changed.digest()),
            ),
            "selected_sealed_run_bytes": changed.canonical_bytes(),
        }
    )


def _replace_captured_run(
    request: PrepareNativeOneCallCancelledAccounting, captured: ExecutionRunRecord
) -> PrepareNativeOneCallCancelledAccounting:
    return request.model_copy(
        update={
            "captured_run": CallSubjectHead(
                subject_id=captured.run_id,
                revision=Present(head=captured.head, fingerprint=captured.digest()),
            ),
            "captured_run_bytes": captured.canonical_bytes(),
        }
    )


def _tamper_captured_turn(
    request: PrepareNativeOneCallCancelledAccounting,
) -> PrepareNativeOneCallCancelledAccounting:
    captured = ExecutionRunRecord.model_validate_json(request.captured_run_bytes)
    changed_turn = _rehead_turn(captured.turns[-1].model_copy(update={"turn_id": "turn:rival"}))
    return _replace_captured_run(
        request,
        _rehead_run(captured.model_copy(update={"turns": (*captured.turns[:-1], changed_turn)})),
    )


def _tamper_captured_response(
    request: PrepareNativeOneCallCancelledAccounting,
) -> PrepareNativeOneCallCancelledAccounting:
    captured = ExecutionRunRecord.model_validate_json(request.captured_run_bytes)
    turn = captured.turns[-1]
    attempt = _rehead_attempt(
        turn.attempts[-1].model_copy(
            update={"response_base64": b64encode(b" " + _response()).decode()}
        )
    )
    changed_turn = _rehead_turn(
        turn.model_copy(update={"attempts": (*turn.attempts[:-1], attempt)})
    )
    return _replace_captured_run(
        request,
        _rehead_run(captured.model_copy(update={"turns": (*captured.turns[:-1], changed_turn)})),
    )


def _replace_response(
    request: PrepareNativeOneCallCancelledAccounting, response: SealedResponseRecord
) -> PrepareNativeOneCallCancelledAccounting:
    response_ref = reference(response.response_seal_id, response)
    sealed = ExecutionRunRecord.model_validate_json(request.selected_sealed_run_bytes)
    changed_turn = _rehead_turn(sealed.turns[-1].model_copy(update={"response_seal": response_ref}))
    changed = _rehead_run(sealed.model_copy(update={"turns": (*sealed.turns[:-1], changed_turn)}))
    return request.model_copy(
        update={
            "selected_response": response_ref,
            "selected_response_bytes": response.canonical_bytes(),
            "selected_sealed_run": reference(changed.run_id, changed),
            "physical_sealed_run": CallSubjectHead(
                subject_id=changed.run_id,
                revision=Present(head=changed.head, fingerprint=changed.digest()),
            ),
            "selected_sealed_run_bytes": changed.canonical_bytes(),
        }
    )


def _replace_initialized(
    request: PrepareNativeOneCallCancelledAccounting, initialized: InitializedCallRecord
) -> PrepareNativeOneCallCancelledAccounting:
    initialized_ref = reference(initialized.original_call_id, initialized)
    response = SealedResponseRecord.model_validate_json(request.selected_response_bytes).model_copy(
        update={"complete_ordered_initialized": (initialized_ref,)}
    )
    response_ref = reference(response.response_seal_id, response)
    sealed = ExecutionRunRecord.model_validate_json(request.selected_sealed_run_bytes)
    changed_turn = _rehead_turn(
        sealed.turns[-1].model_copy(
            update={"response_seal": response_ref, "initialized_calls": (initialized_ref,)}
        )
    )
    changed = _rehead_run(sealed.model_copy(update={"turns": (*sealed.turns[:-1], changed_turn)}))
    return request.model_copy(
        update={
            "initialized": initialized_ref,
            "initialized_record_bytes": initialized.canonical_bytes(),
            "selected_response": response_ref,
            "selected_response_bytes": response.canonical_bytes(),
            "selected_sealed_run": reference(changed.run_id, changed),
            "physical_sealed_run": CallSubjectHead(
                subject_id=changed.run_id,
                revision=Present(head=changed.head, fingerprint=changed.digest()),
            ),
            "selected_sealed_run_bytes": changed.canonical_bytes(),
        }
    )


async def test_prepares_real_cancelled_native_accounting_and_reopens(tmp_path: Path) -> None:
    database = tmp_path / "native-one-call-accounting.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(_response(),),
        manifest=R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V24_PRODUCTION_MANIFEST,
    ) as runtime:
        await _selected_cancelled(runtime)
        facts = R14NativeCancelledCallSource(runtime).collect()
        assert facts is not None
        producer = R14NativeOneCallCancelledAccountingV24Producer(runtime)
        engine = runtime._supervisor.runtime()
        with patch.object(engine, "call", new_callable=AsyncMock) as call:
            assert await producer.prepare(replace(facts, original_call_id="call:rival")) is None
            call.assert_not_awaited()
        original_call = engine.call

        async def tamper_owner_wire(sent: object) -> object:
            payload = PrepareNativeOneCallCancelledAccountingV2.model_validate_json(
                sent.canonical_payload
            )
            return await original_call(
                sent.model_copy(
                    update={
                        "canonical_payload": payload.model_copy(
                            update={"request": _tamper_selected_sealed_run(payload.request)}
                        ).canonical_bytes()
                    }
                )
            )

        with patch.object(engine, "call", side_effect=tamper_owner_wire):
            assert await producer.prepare(facts) is None
        captured_requests: list[PrepareNativeOneCallCancelledAccounting] = []
        captured_v2_requests: list[PrepareNativeOneCallCancelledAccountingV2] = []

        async def capture_owner_wire(sent: object) -> object:
            wire = PrepareNativeOneCallCancelledAccountingV2.model_validate_json(
                sent.canonical_payload
            )
            captured_v2_requests.append(wire)
            captured_requests.append(wire.request)
            return await original_call(sent)

        with patch.object(engine, "call", side_effect=capture_owner_wire):
            prepared = await producer.prepare(facts)
        assert len(captured_v2_requests) == 1
        assert prepare_native_one_call_cancelled_accounting_v2(
            captured_v2_requests[0]
        ).result == prepare_native_one_call_cancelled_accounting(captured_requests[0])
        assert prepared is not None
        assert len(captured_requests) == 1
        direct_request = captured_requests[0]
        assert prepare_native_one_call_cancelled_accounting(direct_request) == prepared

        response = SealedResponseRecord.model_validate_json(direct_request.selected_response_bytes)
        initialized = InitializedCallRecord.model_validate_json(
            direct_request.initialized_record_bytes
        )
        terminal = CancelledBeforeAcceptRecord.model_validate_json(
            direct_request.terminal_record_bytes
        )
        result = NotExecutedCallResultRecord.model_validate_json(direct_request.result_record_bytes)
        alternate_registry = execution_zero_call_frontier_registry()
        mutations = {
            "sealed-ref": lambda request: request.model_copy(
                update={
                    "selected_sealed_run": request.selected_sealed_run.model_copy(
                        update={"subject_id": "run:rival"}
                    )
                }
            ),
            "sealed-transition": _tamper_selected_sealed_run,
            "captured-run": lambda request: request.model_copy(
                update={
                    "captured_run": request.physical_sealed_run,
                    "captured_run_bytes": request.selected_sealed_run_bytes,
                }
            ),
            "captured-turn": _tamper_captured_turn,
            "captured-response": _tamper_captured_response,
            "response-order": lambda request: _replace_response(
                request, response.model_copy(update={"complete_ordered_initialized": ()})
            ),
            "initialized": lambda request: _replace_initialized(
                request, initialized.model_copy(update={"original_call_id": "call:rival"})
            ),
            "classification": lambda request: _replace_initialized(
                request,
                initialized.model_copy(
                    update={
                        "call": initialized.call.model_copy(
                            update={"classification": "PROPOSAL_ONLY"}
                        )
                    }
                ),
            ),
            "terminal": lambda request: request.model_copy(
                update={
                    "terminal": reference(
                        terminal.terminal_id,
                        terminal.model_copy(update={"not_executed_result_id": "result:rival"}),
                    ),
                    "terminal_record_bytes": terminal.model_copy(
                        update={"not_executed_result_id": "result:rival"}
                    ).canonical_bytes(),
                }
            ),
            "result": lambda request: request.model_copy(
                update={
                    "result": reference(
                        result.result_id,
                        result.model_copy(update={"original_call_id": "call:rival"}),
                    ),
                    "result_record_bytes": result.model_copy(
                        update={"original_call_id": "call:rival"}
                    ).canonical_bytes(),
                }
            ),
            "not-executed": lambda request: request.model_copy(
                update={
                    "result": reference(
                        result.result_id, result.model_copy(update={"outcome": "EXECUTED"})
                    ),
                    "result_record_bytes": result.model_copy(
                        update={"outcome": "EXECUTED"}
                    ).canonical_bytes(),
                }
            ),
            "registry": lambda request: request.model_copy(
                update={
                    "frontier_registry": frontier_registry_reference(alternate_registry),
                    "frontier_registry_bytes": alternate_registry.canonical_bytes(),
                }
            ),
        }
        for _label, mutate in mutations.items():
            with pytest.raises(RecoveryHold, match=r".+"):
                prepare_native_one_call_cancelled_accounting(mutate(direct_request))

        expected_outer = prepare_native_one_call_cancelled_accounting_v2(captured_v2_requests[0])
        binding = captured_v2_requests[0].preparation_bindings
        for changed in (
            binding.model_copy(update={"prepared_run_bytes": b"{}"}),
            binding.model_copy(update={"artifact_bytes": b"{}"}),
            binding.model_copy(update={"rendered_prompt_bytes": b"rival"}),
            binding.model_copy(update={"generated_schema_bytes": b"{}"}),
            binding.model_copy(update={"ordered_tool_spec_bytes": ()}),
            binding.model_copy(update={"policy_bytes": b"{}"}),
        ):
            with pytest.raises(RecoveryHold, match=r"native preparation binding"):
                prepare_native_one_call_cancelled_accounting_v2(
                    captured_v2_requests[0].model_copy(update={"preparation_bindings": changed})
                )

        async def forged_outer_fingerprint(sent: object) -> object:
            forged = expected_outer.model_copy(
                update={
                    "result": expected_outer.result.model_copy(
                        update={"sealed_manifest_fingerprint": "0" * 64}
                    )
                }
            )
            return PublicPortSuccess(
                request_id=sent.request_id,
                responder=sent.callee,
                schema_id=NATIVE_ONE_CALL_ACCOUNTING_V2_RESULT_SCHEMA,
                canonical_payload=forged.canonical_bytes(),
            )

        with patch.object(engine, "call", side_effect=forged_outer_fingerprint):
            assert await producer.prepare(facts) is None

        async def cross_schema_reply(sent: object) -> object:
            return PublicPortSuccess(
                request_id=sent.request_id,
                responder=sent.callee,
                schema_id=NATIVE_ONE_CALL_ACCOUNTING_REQUEST_SCHEMA,
                canonical_payload=expected_outer.canonical_bytes(),
            )

        with patch.object(engine, "call", side_effect=cross_schema_reply):
            assert await producer.prepare(facts) is None

        original_acquire = producer._acquire_held
        with runtime._authority_gate().hold():
            acquired = original_acquire(facts)
        assert acquired is not None
        provenance = acquired[3]
        assert provenance.complete_ordered_run_lineage == acquired[1].source.target_run_lineage
        assert len(provenance.ordered_members) == len(provenance.complete_ordered_run_lineage) + 4
        assert all(member.selected_decision_bytes for member in provenance.ordered_members)
        assert all(member.physical_commit_sequence > 0 for member in provenance.ordered_members)
        acquired_calls = 0

        def state_changes_during_ipc(
            supplied: object,
        ) -> object:
            nonlocal acquired_calls
            acquired_calls += 1
            return original_acquire(supplied) if acquired_calls == 1 else None

        with patch.object(producer, "_acquire_held", side_effect=state_changes_during_ipc):
            assert await producer.prepare(facts) is None
        assert acquired_calls == 2

        for field, changed in (
            ("selected_decision_id", "decision:rival"),
            ("selected_decision_bytes", b"rival"),
            ("selected_decision_fingerprint", "0" * 64),
            (
                "physical_commit_sequence",
                provenance.ordered_members[0].physical_commit_sequence + 1,
            ),
        ):
            changed_member = replace(provenance.ordered_members[0], **{field: changed})
            changed_provenance = replace(
                provenance,
                ordered_members=(changed_member, *provenance.ordered_members[1:]),
            )
            manifest_calls = 0

            def source_member_changes_after_ipc(
                supplied: object, changed_provenance: object = changed_provenance
            ) -> object:
                nonlocal manifest_calls
                manifest_calls += 1
                return acquired if manifest_calls == 1 else (*acquired[:3], changed_provenance)

            with patch.object(
                producer, "_acquire_held", side_effect=source_member_changes_after_ipc
            ):
                assert await producer.prepare(facts) is None
            assert manifest_calls == 2

        original_stamp = producer._stamp_held
        stamp_calls = 0

        def commitment_changes_after_ipc() -> object:
            nonlocal stamp_calls
            stamp_calls += 1
            stamp = original_stamp()
            if stamp_calls == 4 and stamp is not None:
                return stamp.model_copy(update={"authority_materialization_commitment": "0" * 64})
            return stamp

        with patch.object(producer, "_stamp_held", side_effect=commitment_changes_after_ipc):
            assert await producer.prepare(facts) is None
        # Three stamps bracket each acquisition (pre/source/post); the fourth
        # read is the post-IPC acquisition's pre-stamp and must force HOLD.
        assert stamp_calls == 6

        with (
            patch.object(
                R14NativeSelectedAccountingSource,
                "collect_one_cancelled",
                side_effect=lambda target: None,
            ),
            patch.object(engine, "call", new_callable=AsyncMock) as owner_call,
        ):
            assert await producer.prepare(facts) is None
            owner_call.assert_not_awaited()

        entries = runtime._loop_decisions().entries()
        target_raw = next(
            raw
            for decision_id, _previous, raw in entries
            if decision_id == provenance.ordered_members[0].selected_decision_id
        )
        with (
            patch.object(
                runtime._loop_decisions(),
                "entries",
                return_value=(*entries, ("decision:rival", None, target_raw)),
            ),
            patch.object(engine, "call", new_callable=AsyncMock) as owner_call,
        ):
            assert await producer.prepare(facts) is None
            owner_call.assert_not_awaited()

        async def malformed_owner_success(sent: object) -> object:
            return PublicPortSuccess(
                request_id=sent.request_id,
                responder=sent.callee,
                schema_id=NATIVE_ONE_CALL_ACCOUNTING_V2_RESULT_SCHEMA,
                canonical_payload=b"{}",
            )

        with patch.object(engine, "call", side_effect=malformed_owner_success):
            assert await producer.prepare(facts) is None
        with (
            patch.object(engine, "call", side_effect=RuntimeError("broker unavailable")),
            pytest.raises(RuntimeError, match="broker unavailable"),
        ):
            await producer.prepare(facts)
        assert prepared.selected_response == facts.selected_response_seal
        assert len(prepared.complete_ordered_calls) == 1
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V24_PRODUCTION_MANIFEST,
    ) as reopened:
        facts = R14NativeCancelledCallSource(reopened).collect()
        assert facts is not None
        replayed = await R14NativeOneCallCancelledAccountingV24Producer(reopened).prepare(facts)
        assert replayed == prepared
        producer = R14NativeOneCallCancelledAccountingV24Producer(reopened)
        with reopened._authority_gate().hold():
            acquired = producer._acquire_held(facts)
        assert acquired is not None
        source_member = acquired[3].ordered_members[0]
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE records SET commit_sequence=? WHERE tenant_id=? AND record_id=?",
                (
                    source_member.physical_commit_sequence + 1,
                    facts.tenant_id,
                    source_member.record_id,
                ),
            )
        with patch.object(reopened._supervisor.runtime(), "call", new_callable=AsyncMock) as call:
            assert await producer.prepare(facts) is None
            call.assert_not_awaited()


async def test_v23_producer_preserves_the_v1_route_and_wire(tmp_path: Path) -> None:
    database = tmp_path / "native-one-call-accounting-v23.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(_response(),),
        manifest=R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V23_PRODUCTION_MANIFEST,
    ) as runtime:
        await _selected_cancelled(runtime)
        facts = R14NativeCancelledCallSource(runtime).collect()
        assert facts is not None
        engine = runtime._supervisor.runtime()
        original = engine.call
        wires: list[PrepareNativeOneCallCancelledAccounting] = []

        async def capture(sent: object) -> object:
            assert sent.operation_id == "agent_loop.prepare_native_one_call_cancelled_accounting"
            assert sent.schema_id == NATIVE_ONE_CALL_ACCOUNTING_REQUEST_SCHEMA
            wires.append(
                PrepareNativeOneCallCancelledAccounting.model_validate_json(sent.canonical_payload)
            )
            return await original(sent)

        with patch.object(engine, "call", side_effect=capture):
            result = await R14NativeOneCallCancelledAccountingProducer(runtime).prepare(facts)
        assert result is not None
        assert len(wires) == 1
        assert prepare_native_one_call_cancelled_accounting(wires[0]) == result
