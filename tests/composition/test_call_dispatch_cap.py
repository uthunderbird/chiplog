"""Actual legacy/call dispatch shares one entitlement across original grant identities."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.adapters.driven.effects_hermetic import HermeticResponseLost
from chiplog.adapters.driven.loop_prompts import OwnedStaticPrompts
from chiplog.capabilities.agent_loop.application import AgentLoop
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, EndpointSelection, LoopRejected
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
    ExecutionContinue,
    SelfEffectArguments,
)
from chiplog.capabilities.effects.dispatch_authority_contracts import CapturedSource
from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
from chiplog.capabilities.effects.dispatch_v2 import DispatchRecordV2, digest
from chiplog.composition.r13_workspace import R13Workspace
from chiplog.composition.r14_call_acceptance_port import (
    CallAcceptanceAdoption,
    CallAcceptanceTarget,
)
from chiplog.composition.r14_call_preview import execution_run_head
from chiplog.composition.r14_loop_history import read_execution_call_history
from chiplog.composition.r14_loop_store import R14LoopStore
from chiplog.composition.r16_dispatch_authority import (
    validate_dispatch_issuance_value,
    validate_issuance,
)
from chiplog.composition.r16_dispatch_inputs import source_inventory
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.composition.r16_dispatch_runtime import open_execution_dispatch_runtime
from chiplog.platform._owner_publication_contracts import JournalSelectedPublication
from tests.support.dispatch import _response


@pytest.mark.parametrize("first", ("legacy", "call"))
@pytest.mark.parametrize("unknown", (False, True))
async def test_shared_cap_survives_closed_or_unknown_send_across_both_policies(
    tmp_path: Path,
    first: str,
    unknown: bool,
) -> None:
    response = ExecutionContinue(
        kind="Continue",
        tool_calls=(
            ConsequentialToolCall(
                call_id="effect",
                tool="request_self_effect",
                arguments=SelfEffectArguments(payload=b"call payload", bundle_members=("one",)),
            ),
        ),
    )
    resources = HermeticDispatchResources(
        scenarios=("LOST_RESPONSE_AFTER_EFFECT",) if unknown else ("CONFIRM",),
        cap=1,
        custody_path=tmp_path / "custody.json",
    )
    async with open_execution_dispatch_runtime(
        tmp_path / "mixed.sqlite",
        resources=resources,
        responses=(_response(), response.model_dump_json().encode()),
    ) as runtime:
        loop = AgentLoop(
            R14LoopStore(runtime),
            runtime._execution_model,
            OwnedStaticPrompts(),
            tenant="hermetic-tenant",
            principal="hermetic-principal",
            origin=EndpointSelection(
                kind="ORIGIN_EXACT",
                ingress_binding_head="hermetic-ingress-v1",
                endpoint_head="hermetic-endpoint-v1",
                endpoint_id="hermetic-local",
                provider="hermetic-local",
                recipient="hermetic-principal",
                canonical_address="local://hermetic-principal",
                credential_binding_head="hermetic-v1",
            ),
            contour_head="hermetic-contour-v1",
            policy_head="hermetic-policy-v1",
            worker_session=runtime.current_worker(),
            planning=runtime,
            workspace=R13Workspace(runtime),
            session=runtime,
        )
        old = await loop.create("legacy", "Legacy request", BudgetPolicy())
        active = await loop.activate("legacy", old.head)
        await loop.step("legacy", active.head)
        created = await runtime.create_execution(
            "hermetic-ingress", "call", "Call request", BudgetPolicy()
        )
        started = await runtime.begin_execution("hermetic-ingress", "call", created.head)
        captured = await runtime.capture_execution("hermetic-ingress", "call", started.head)
        sealed = await runtime.seal_execution("hermetic-ingress", "call", captured.head)
        row = next(
            row
            for row in read_execution_call_history(runtime)[1].ordered_calls
            if row.initialized_record.call.classification == "CONSEQUENTIAL"
        )
        target = CallAcceptanceTarget(
            original_call_id=row.original_call_id,
            initialized=row.initialized,
            current_run=execution_run_head(sealed),
        )

        async def adopt(policy: str) -> str:
            if policy == "call":
                preview = await runtime.preview_call_acceptance("hermetic-ingress", target)
                result = await runtime.accept_call(
                    "hermetic-ingress",
                    CallAcceptanceAdoption(
                        act_id="call-adopt",
                        preview_bytes=preview.canonical_bytes(),
                    ),
                )
                return result.external_intent.subject_id
            display = await runtime.preview_dispatch("legacy/turn/1/proposal/effect")
            result_legacy = await runtime.adopt_dispatch(
                "hermetic-ingress",
                display.display_id,
                display.display_digest,
                display.adoption_act_id,
                "legacy",
            )
            assert isinstance(result_legacy, JournalSelectedPublication), result_legacy
            record = DispatchRecordV2.model_validate_json(
                result_legacy.complete_records[-1].canonical_bytes
            )
            selected = runtime._owner_decisions().lookup(
                runtime._tenant_id, record.command.identity.command_id
            )
            assert selected is not None
            value = validate_issuance(selected.prepared.request, runtime)
            intent = record.snapshot.intent
            source = intent.acquisition.original_sources.runtime_and_fence
            assert isinstance(source, CapturedSource) and source.source_version == "3"
            worker = value.captured.cut.worker
            assert worker is not None
            assert json.loads(source.canonical_value)["run"] == {
                "run_id": worker.run.run_id,
                "head": worker.run.head,
                "fingerprint": digest(worker.run.canonical_bytes()),
                "state": worker.run.state,
            }
            old = source_inventory(
                value.captured,
                intent.mandate,
                intent.acquisition.adoption.canonical_bytes(),
                None,
                run_source_version="2",
            ).runtime_and_fence
            assert isinstance(old, CapturedSource) and old.source_version == "2"
            assert json.loads(old.canonical_value)["run"] == worker.run.model_dump(mode="json")
            with pytest.raises(LoopRejected, match="cannot override"):
                source_inventory(
                    value.captured,
                    intent.mandate,
                    intent.acquisition.adoption.canonical_bytes(),
                    intent,
                    run_source_version="2",
                )
            with pytest.raises(LoopRejected, match="unregistered"):
                source_inventory(
                    value.captured,
                    intent.mandate,
                    intent.acquisition.adoption.canonical_bytes(),
                    None,
                    run_source_version="unknown",
                )
            changed = replace(
                value.captured,
                cut=replace(
                    value.captured.cut,
                    worker=replace(worker, run=worker.run.model_copy(update={"head": "changed"})),
                ),
            )
            with pytest.raises(ValueError, match="source interpretation"):
                validate_dispatch_issuance_value(
                    value.model_copy(update={"captured": changed}),
                    selected.prepared.request,
                    runtime,
                )
            return intent.intent_id

        original = await adopt(first)
        authorized = await runtime.authorize_dispatch(
            "hermetic-ingress", original, "authorize-first", first
        )
        assert isinstance(authorized, JournalSelectedPublication), authorized
        send = await runtime.commit_first_send("hermetic-ingress", original, "send-first", first)
        assert isinstance(send, JournalSelectedPublication), send
        if unknown:
            with pytest.raises(HermeticResponseLost):
                await runtime.emit_committed("hermetic-ingress", original)
        else:
            receipt = await runtime.emit_committed("hermetic-ingress", original)
            assert receipt is not None
            evidence = await runtime.observe_dispatch_outcome(original, receipt)
            assert isinstance(evidence, JournalSelectedPublication), evidence
            resolved = await runtime.resolve_dispatch_outcome(
                "hermetic-ingress", original, "resolve-first"
            )
            assert isinstance(resolved, JournalSelectedPublication), resolved
            outcome = DispatchOutcomeRecordV2.model_validate_json(
                resolved.complete_records[0].canonical_bytes
            )
            assert outcome.snapshot.state == "CONFIRMED"
            assert outcome.snapshot.obligation.state == "CLOSED"
        assert len(resources.require_original_provider().transfers) == 1
        other = "call" if first == "legacy" else "legacy"
        if unknown:
            with pytest.raises(LoopRejected, match="unresolved selected effect work"):
                await adopt(other)
        else:
            second = await adopt(other)
            authorized_second = await runtime.authorize_dispatch(
                "hermetic-ingress", second, "authorize-second", other
            )
            assert isinstance(authorized_second, JournalSelectedPublication), authorized_second
            with pytest.raises(LoopRejected, match="cap exhausted"):
                await runtime.commit_first_send("hermetic-ingress", second, "send-second", other)
        assert len(resources.require_original_provider().transfers) == 1
        sends = tuple(
            record
            for decision in runtime._owner_decisions().snapshot().decisions
            for record in decision.prepared.request.complete_records
            if record.record_kind == "effects.SEND_COMMITTED"
        )
        assert len(sends) == 1
        final_history = read_execution_call_history(runtime)
        original_transfers = resources.require_original_provider().transfers
    restarted_resources = HermeticDispatchResources(
        scenarios=("LOST_RESPONSE_AFTER_EFFECT",) if unknown else ("CONFIRM",),
        cap=1,
        custody_path=tmp_path / "custody.json",
    )
    async with open_execution_dispatch_runtime(
        tmp_path / "mixed.sqlite", resources=restarted_resources
    ) as reopened:
        assert read_execution_call_history(reopened) == final_history
        assert restarted_resources.require_original_provider().transfers == original_transfers
