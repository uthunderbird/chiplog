"""Real V4 cancellation keeps complete trust outside its bounded physical cut."""

import base64
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import TypeAdapter

from chiplog.adapters.driven.loop_sqlite import LoopIntegrityError
from chiplog.architecture.r7_runtime import (
    R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V24_PRODUCTION_MANIFEST,
)
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
    ExecutionContinue,
    SelfEffectArguments,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_common_cli_execution_runtime,
)
from chiplog.composition.common_execution_driver_contracts import SelectedExecutionReceiptV1
from chiplog.composition.r14_cancellation_contracts import CancelCallSubmission
from chiplog.composition.r14_execution_cancellation import build_execution_cancellation_envelope
from chiplog.composition.r14_execution_cancellation_contracts import (
    MAX_EXECUTION_CANCELLATION_BYTES,
    ExecutionCancelledCallReceipt,
    RetainedExecutionCancellationPreparation,
    RetainedExecutionCancellationPreparationV2,
    decode_execution_cancellation_preparation,
)
from chiplog.composition.r14_execution_fanout_records import reference
from chiplog.composition.r14_loop_history import (
    read_execution_call_history,
    read_execution_recovery_history,
)
from chiplog.composition.r14_native_preparation_bindings import bind_native_execution_preparation_v2
from chiplog.composition.r14_native_selected_accounting_source import (
    R14NativeOneCallCancelledFacts,
    R14NativeSelectedAccountingSource,
)
from chiplog.composition.r14_original_prepare_source import R14OriginalNativePrepareSource
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_cli_execution import admit_runtime_slot


def native_response() -> bytes:
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


async def _selected_native_cancelled(
    runtime: CommonCliExecutionRuntime,
    *,
    bound: bool = True,
    act_id: str = "cancel-native-effect",
) -> tuple[CancelCallSubmission, ExecutionCancelledCallReceipt]:
    wire = await admit_runtime_slot(runtime, "native-recovery")
    receipt = await runtime.drive_input(wire)
    assert isinstance(receipt, SelectedExecutionReceiptV1)
    started = await runtime.begin_execution(
        "hermetic-ingress", receipt.stable_run_lineage_id, receipt.selected_run_head.head
    )
    captured = await runtime.capture_execution(
        "hermetic-ingress", started.run_id, started.head, native_one_call_context=bound
    )
    sealed = await runtime.seal_execution("hermetic-ingress", captured.run_id, captured.head)
    _, inventory, _, _, _, _ = read_execution_recovery_history(runtime)
    row = inventory.ordered_calls[0]
    submission = CancelCallSubmission(
        act_id=act_id,
        original_call_id=row.original_call_id,
        initialized=reference(row.original_call_id, row.initialized_record),
        current_run=CallSubjectHead(
            subject_id=sealed.run_id,
            revision=Present(head=sealed.head, fingerprint=sealed.digest()),
        ),
    )
    cancelled = await runtime.cancel_execution_call(
        "hermetic-ingress",
        submission,
    )
    return submission, cancelled


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )


def _evidence(runtime: CommonCliExecutionRuntime) -> RetainedExecutionCancellationPreparation:
    entries = [json.loads(raw) for _, _, raw in runtime._loop_decisions().entries()]
    selected = [entry for entry in entries if "execution_cancellation_preparation" in entry]
    return decode_execution_cancellation_preparation(
        selected[-1]["execution_cancellation_preparation"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", (False, True))
async def test_real_v4_cancellation_and_restart_replay(tmp_path: Path, bound: bool) -> None:
    database = tmp_path / "db"
    manifest = R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V24_PRODUCTION_MANIFEST
    async with open_common_cli_execution_runtime(
        database,
        resources=_resources(tmp_path),
        responses=(native_response(),),
        manifest=manifest,
    ) as runtime:
        submission, receipt = await _selected_native_cancelled(runtime, bound=bound)
        retained = _evidence(runtime)
        assert isinstance(retained, RetainedExecutionCancellationPreparationV2) is bound
        envelope = build_execution_cancellation_envelope(retained)
        assert len(envelope.canonical_bytes()) < MAX_EXECUTION_CANCELLATION_BYTES
        actor = retained.request.cut.sources[1]
        if bound:
            commitment = json.loads(base64.b64decode(actor.canonical_value_base64))
            assert commitment["retained_trust_fingerprint"] == retained.trust.digest()
            assert len(actor.canonical_value_base64) < 1024
            assert len(retained.trust.canonical_bytes()) > 500_000
            # Neither a substituted full exchange nor a substituted source
            # commitment can reuse the original owner terminal.
            changed_trust = retained.trust.model_copy(update={"journal_head": "rival"})
            with pytest.raises(ValueError, match="retained trust differs"):
                build_execution_cancellation_envelope(
                    retained.model_copy(update={"trust": changed_trust})
                )
            changed_actor = actor.model_copy(
                update={"canonical_value_base64": base64.b64encode(b"{}").decode()}
            )
            changed_cut = retained.request.cut.model_copy(
                update={
                    "sources": (
                        retained.request.cut.sources[0],
                        changed_actor,
                        retained.request.cut.sources[2],
                    )
                }
            )
            with pytest.raises(ValueError, match="V2 retained source bytes or cut differ"):
                build_execution_cancellation_envelope(
                    retained.model_copy(
                        update={"request": retained.request.model_copy(update={"cut": changed_cut})}
                    )
                )
            journal = runtime._loop_decisions()
            entries = list(journal.entries())
            substituted = []
            for sequence, head, raw in entries:
                entry = json.loads(raw)
                if "execution_cancellation_preparation" in entry:
                    wrong = retained.model_copy(update={"original_prepare_fingerprint": "0" * 64})
                    entry["execution_cancellation_preparation"] = wrong.canonical_bytes().decode()
                    raw = json.dumps(entry, separators=(",", ":")).encode()
                substituted.append((sequence, head, raw))
            with (
                patch.object(journal, "entries", return_value=tuple(substituted)),
                pytest.raises(LoopIntegrityError) as rejected,
            ):
                read_execution_call_history(runtime)
            assert "exact selected V4 Prepare" in str(rejected.value.__cause__)
        else:
            assert (
                base64.b64decode(actor.canonical_value_base64) == retained.trust.canonical_bytes()
            )
        assert await runtime.cancel_execution_call("hermetic-ingress", submission) == receipt
        if bound:
            _, _, preparations = read_execution_call_history(runtime)
            seal = preparations[-1].proposal.fan_out.response_seal
            projection = R14OriginalNativePrepareSource(runtime).collect(
                reference(seal.response_seal_id, seal)
            )
            assert projection is not None
            selected_facts = R14NativeSelectedAccountingSource(
                runtime, projection, bind_native_execution_preparation_v2(projection)
            ).collect_one_cancelled(projection.prepared_run)
            assert selected_facts is not None
            adapter = TypeAdapter(R14NativeOneCallCancelledFacts)
            roundtrip = adapter.validate_json(adapter.dump_json(selected_facts))
            assert roundtrip == selected_facts
            assert isinstance(
                roundtrip.source.target_cancellations[0], RetainedExecutionCancellationPreparationV2
            )
    async with open_common_cli_execution_runtime(
        database,
        resources=_resources(tmp_path),
        manifest=manifest,
    ) as reopened:
        assert await reopened.cancel_execution_call("hermetic-ingress", submission) == receipt
        assert _evidence(reopened).canonical_bytes() == retained.canonical_bytes()


@pytest.mark.asyncio
async def test_real_v4_over_bound_cancellation_is_not_selected(tmp_path: Path) -> None:
    async with open_common_cli_execution_runtime(
        tmp_path / "db",
        resources=_resources(tmp_path),
        responses=(native_response(),),
        manifest=R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V24_PRODUCTION_MANIFEST,
    ) as runtime:
        with pytest.raises(ValueError, match="physical byte bound exceeded"):
            await _selected_native_cancelled(runtime, act_id="a" * 300_000)
        assert not any(
            "execution_cancellation_preparation" in json.loads(raw)
            for _, _, raw in runtime._loop_decisions().entries()
        )
        _, inventory, _ = read_execution_call_history(runtime)
        assert inventory.ordered_calls[0].terminal.kind == "ABSENT"
