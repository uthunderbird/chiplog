"""Mounted V23 owner exchange for the narrow cancelled native call slice."""

from __future__ import annotations

from base64 import b64encode
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from chiplog.architecture.r7_runtime import (
    R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V23_PRODUCTION_MANIFEST,
)
from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallSubjectHead,
    CancelledBeforeAcceptRecord,
    InitializedCallRecord,
    NotExecutedCallResultRecord,
    SealedResponseRecord,
)
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
    ExecutionContinue,
    ExecutionRunRecord,
    SelfEffectArguments,
)
from chiplog.capabilities.agent_loop.native_one_call_accounting_contracts import (
    NATIVE_ONE_CALL_ACCOUNTING_RESULT_SCHEMA,
    PrepareNativeOneCallCancelledAccounting,
)
from chiplog.capabilities.agent_loop.native_one_call_accounting_preparation import (
    prepare_native_one_call_cancelled_accounting,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.recovery_domain import RecoveryHold
from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
    execution_zero_call_frontier_registry,
    frontier_registry_reference,
)
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_common_cli_execution_runtime,
)
from chiplog.composition.common_execution_driver_contracts import SelectedExecutionReceiptV1
from chiplog.composition.r14_cancellation_contracts import CancelCallSubmission
from chiplog.composition.r14_execution_fanout_records import reference
from chiplog.composition.r14_execution_recovery_source import R14NativeCancelledCallSource
from chiplog.composition.r14_loop_history import read_execution_recovery_history
from chiplog.composition.r14_native_one_call_accounting_producer import (
    R14NativeOneCallCancelledAccountingProducer,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.broker import PublicPortSuccess
from tests.support.h1_cli_execution import admit_runtime_slot


def _response() -> bytes:
    return ExecutionContinue(
        kind="Continue",
        tool_calls=(
            ConsequentialToolCall(
                call_id="effect",
                tool="request_self_effect",
                arguments=SelfEffectArguments(payload=b"effect", bundle_members=("one",)),
            ),
        ),
    ).canonical_bytes()


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


async def _selected_cancelled(runtime: CommonCliExecutionRuntime) -> None:
    request = await admit_runtime_slot(runtime, "native-accounting")
    initial = await runtime.drive_input(request)
    assert isinstance(initial, SelectedExecutionReceiptV1)
    run_id = initial.stable_run_lineage_id
    started = await runtime.begin_execution(
        "hermetic-ingress", run_id, initial.selected_run_head.head
    )
    captured = await runtime.capture_execution("hermetic-ingress", run_id, started.head)
    sealed = await runtime.seal_execution("hermetic-ingress", run_id, captured.head)
    _, inventory, _, _, _, _ = read_execution_recovery_history(runtime)
    row = inventory.ordered_calls[0]
    await runtime.cancel_execution_call(
        "hermetic-ingress",
        CancelCallSubmission(
            act_id="cancel-native-effect",
            original_call_id=row.original_call_id,
            initialized=reference(row.initialized_record.original_call_id, row.initialized_record),
            current_run=CallSubjectHead(
                subject_id=sealed.run_id,
                revision=Present(head=sealed.head, fingerprint=sealed.digest()),
            ),
        ),
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
        manifest=R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V23_PRODUCTION_MANIFEST,
    ) as runtime:
        await _selected_cancelled(runtime)
        facts = R14NativeCancelledCallSource(runtime).collect()
        assert facts is not None
        producer = R14NativeOneCallCancelledAccountingProducer(runtime)
        engine = runtime._supervisor.runtime()
        with patch.object(engine, "call", new_callable=AsyncMock) as call:
            assert await producer.prepare(replace(facts, original_call_id="call:rival")) is None
            call.assert_not_awaited()
        original_call = engine.call

        async def tamper_owner_wire(sent: object) -> object:
            payload = PrepareNativeOneCallCancelledAccounting.model_validate_json(
                sent.canonical_payload
            )
            return await original_call(
                sent.model_copy(
                    update={
                        "canonical_payload": _tamper_selected_sealed_run(payload).canonical_bytes()
                    }
                )
            )

        with patch.object(engine, "call", side_effect=tamper_owner_wire):
            assert await producer.prepare(facts) is None
        captured_requests: list[PrepareNativeOneCallCancelledAccounting] = []

        async def capture_owner_wire(sent: object) -> object:
            captured_requests.append(
                PrepareNativeOneCallCancelledAccounting.model_validate_json(sent.canonical_payload)
            )
            return await original_call(sent)

        with patch.object(engine, "call", side_effect=capture_owner_wire):
            prepared = await producer.prepare(facts)
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

        async def malformed_owner_success(sent: object) -> object:
            return PublicPortSuccess(
                request_id=sent.request_id,
                responder=sent.callee,
                schema_id=NATIVE_ONE_CALL_ACCOUNTING_RESULT_SCHEMA,
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
        manifest=R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V23_PRODUCTION_MANIFEST,
    ) as reopened:
        facts = R14NativeCancelledCallSource(reopened).collect()
        assert facts is not None
        replayed = await R14NativeOneCallCancelledAccountingProducer(reopened).prepare(facts)
        assert replayed == prepared
