"""Real offline dispatch selection and one-shot emission witnesses."""

import asyncio
import base64
import json
from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.capabilities.effects.dispatch_v2 import DispatchRecordV2
from chiplog.composition.r16_dispatch_authority import DispatchPublicationAuthority
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.composition.r16_dispatch_runtime import (
    R16DispatchRuntime,
    open_dispatch_loop,
    open_dispatch_runtime,
)
from chiplog.composition.r16_effects import HermeticEffectProposal
from chiplog.platform._owner_publication_contracts import (
    JournalSelectedPublication,
    PublicationRejected,
    RegisteredPublication,
    SingleOwnerBatch,
    WorkerAuthentication,
)
from chiplog.platform.owner_publications import PreparedOwnerPublication


def _response() -> bytes:
    proposal = HermeticEffectProposal(
        schema_id="chiplog.hermetic-effect-proposal.v1",
        purpose="My action",
        payload_base64=base64.b64encode(b"exact payload").decode(),
        bundle_members=("self-action",),
    )
    return json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": "effect",
                    "tool": "propose_intent",
                    "text": proposal.canonical_bytes().decode(),
                }
            ],
        }
    ).encode()


@pytest.mark.parametrize("crash_before_leaf", (False, True))
async def test_actual_adoption_first_send_consumes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_before_leaf: bool
) -> None:
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1)
    async with open_dispatch_loop(
        tmp_path / "dispatch.db", resources=resources, responses=(_response(), _response())
    ) as loop:
        created = await loop.create("r", "Prepare my action", BudgetPolicy())
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        runtime = loop._planning
        assert isinstance(runtime, R16DispatchRuntime)
        display = await runtime.preview_dispatch("r/turn/1/proposal/effect")
        result = await runtime.adopt_dispatch(
            "hermetic-ingress",
            display.display_id,
            display.display_digest,
            display.adoption_act_id,
            "r",
        )
        assert isinstance(result, JournalSelectedPublication), result
        record = DispatchRecordV2.model_validate_json(result.complete_records[-1].canonical_bytes)
        intent_id = record.snapshot.intent.intent_id
        authorized = await runtime.authorize_dispatch(
            "hermetic-ingress", intent_id, "authorize", "r"
        )
        assert isinstance(authorized, JournalSelectedPublication), authorized
        from chiplog.composition.r16_dispatch_authority import DispatchPublicationAuthority
        from chiplog.platform._owner_publication_contracts import PublicationRejected

        original_prepare = DispatchPublicationAuthority.prepare
        ready = asyncio.Event()
        candidates = []

        async def competing_prepare(
            authority: DispatchPublicationAuthority, request: RegisteredPublication
        ) -> PreparedOwnerPublication | PublicationRejected:
            prepared = await original_prepare(authority, request)
            if request.operation == "effects.commit_send":
                from dataclasses import replace

                from chiplog.platform.owner_publications import PreparedOwnerPublication

                assert isinstance(prepared, PreparedOwnerPublication)
                assert authority.check_prepared(replace(prepared)) == "DENIED"
                assert isinstance(
                    await original_prepare(authority, request.model_copy()), PublicationRejected
                )
                candidates.append(prepared)
                if len(candidates) == 2:
                    ready.set()
                await asyncio.wait_for(ready.wait(), timeout=15)
            return prepared

        with monkeypatch.context() as race:
            race.setattr(DispatchPublicationAuthority, "prepare", competing_prepare)
            results = await asyncio.gather(
                runtime.commit_first_send("hermetic-ingress", intent_id, "send-a", "r"),
                runtime.commit_first_send("hermetic-ingress", intent_id, "send-b", "r"),
            )
        assert len(candidates) == 2
        assert sum(isinstance(result, JournalSelectedPublication) for result in results) == 1, (
            results
        )
        assert sum(isinstance(result, PublicationRejected) for result in results) == 1, results
        from chiplog.capabilities.effects.dispatch_v2 import DispatchPreparationV2, prepare_dispatch
        from chiplog.capabilities.effects.domain import EffectRuleViolation
        from chiplog.composition.r16_dispatch_authority import DispatchIssuance
        from chiplog.composition.r16_dispatch_outbox import _emit

        sizes = []
        for prepared in candidates:
            assert isinstance(prepared.request.authentication, WorkerAuthentication)
            issuance = DispatchIssuance.model_validate_json(
                prepared.request.authentication.applicability_bytes
            )
            sent = issuance.sent
            wire = {
                "operation": sent.operation_id,
                "payload": base64.b64encode(sent.canonical_payload).decode(),
                "request_id": sent.request_id,
                "schema_id": sent.schema_id,
                "target": sent.callee.model_dump(),
            }
            sizes.append(len(json.dumps(wire, sort_keys=True, separators=(",", ":")).encode()))
        assert max(sizes) < 8 * 1024 * 1024
        print("actual competing SEND wire bytes:", sizes)
        candidate_batch = candidates[0].request
        assert isinstance(candidate_batch, SingleOwnerBatch)
        original = DispatchPreparationV2.model_validate_json(
            candidate_batch.command.canonical_bytes
        )
        history = original.current.observation.complete_effect_history
        assert len(history) >= 2
        mutations = (
            history[:-1],
            tuple(reversed(history)),
            (history[0].model_copy(update={"command_bytes": b"substitution"}), *history[1:]),
        )
        for members in mutations:
            observation = original.current.observation.model_copy(
                update={"complete_effect_history": members}
            )
            current = original.current.model_copy(update={"observation": observation})
            with pytest.raises(EffectRuleViolation):
                prepare_dispatch(original.model_copy(update={"current": current}))

        with pytest.raises(ValueError, match="unissued"):
            await _emit(runtime, object())
        if crash_before_leaf:
            from chiplog.composition import r16_dispatch_outbox

            # SEND already crossed the boundary. Later grant revocation cannot
            # reinterpret it as a proved no-send or reissue another child.
            resources.revoke()

            async def crash_after_consumption(runtime: R16DispatchRuntime, permit: object) -> bytes:
                raise OSError("injected after durable consumption, before leaf")

            with monkeypatch.context() as crash:
                crash.setattr(r16_dispatch_outbox, "_emit", crash_after_consumption)
                with pytest.raises(OSError, match="after durable consumption"):
                    await runtime.emit_committed("hermetic-ingress", intent_id)
        else:
            await runtime.emit_committed("hermetic-ingress", intent_id)
        assert len(resources._provider.transfers) == (0 if crash_before_leaf else 1)
        assert await runtime.emit_committed("hermetic-ingress", intent_id) is None
        assert len(resources._provider.transfers) == (0 if crash_before_leaf else 1)
        if not crash_before_leaf:
            from chiplog.capabilities.agent_loop.contracts import LoopRejected

            created_again = await loop.create("r2", "Prepare another action", BudgetPolicy())
            active_again = await loop.activate("r2", created_again.head)
            await loop.step("r2", active_again.head)
            fresh_display = await runtime.preview_dispatch("r2/turn/1/proposal/effect")
            with runtime._authority_gate().hold():
                before_rejected_adoption = runtime._owner_decisions().snapshot().head
            with pytest.raises(LoopRejected, match="unresolved selected effect work"):
                await runtime.adopt_dispatch(
                    "hermetic-ingress",
                    fresh_display.display_id,
                    fresh_display.display_digest,
                    fresh_display.adoption_act_id,
                    "r2",
                )
            with runtime._authority_gate().hold():
                assert runtime._owner_decisions().snapshot().head == before_rejected_adoption
            assert len(resources._provider.transfers) == 1
    # This reopens the runtime with independently retained custody. It does not
    # assert persistence of memory-only grant keys through an OS-process restart.
    async with open_dispatch_runtime(tmp_path / "dispatch.db", resources=resources) as reopened:
        assert await reopened.emit_committed("hermetic-ingress", intent_id) is None
        assert len(resources._provider.transfers) == (0 if crash_before_leaf else 1)


async def test_revocation_after_registration_reaches_writer_without_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chiplog.composition.r16_dispatch_authority import DispatchPublicationAuthority
    from chiplog.platform._owner_publication_contracts import PublicationRejected

    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1)
    async with open_dispatch_loop(
        tmp_path / "dispatch.db", resources=resources, responses=(_response(),)
    ) as loop:
        created = await loop.create("r", "Prepare my action", BudgetPolicy())
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        runtime = loop._planning
        assert isinstance(runtime, R16DispatchRuntime)
        display = await runtime.preview_dispatch("r/turn/1/proposal/effect")
        selected = await runtime.adopt_dispatch(
            "hermetic-ingress",
            display.display_id,
            display.display_digest,
            display.adoption_act_id,
            "r",
        )
        assert isinstance(selected, JournalSelectedPublication)
        record = DispatchRecordV2.model_validate_json(selected.complete_records[-1].canonical_bytes)
        intent_id = record.snapshot.intent.intent_id
        authorized = await runtime.authorize_dispatch(
            "hermetic-ingress", intent_id, "authorize", "r"
        )
        assert isinstance(authorized, JournalSelectedPublication)
        original_prepare = DispatchPublicationAuthority.prepare
        registered = []

        async def invalidate(
            authority: DispatchPublicationAuthority, request: RegisteredPublication
        ) -> PreparedOwnerPublication | PublicationRejected:
            result = await original_prepare(authority, request)
            if request.operation == "effects.commit_send":
                registered.append(result)
                resources.revoke()
            return result

        monkeypatch.setattr(DispatchPublicationAuthority, "prepare", invalidate)
        denied = await runtime.commit_first_send("hermetic-ingress", intent_id, "send", "r")
        assert len(registered) == 1
        assert isinstance(denied, PublicationRejected), denied
        with runtime._authority_gate().hold():
            assert not any(
                decision.prepared.request.operation == "effects.commit_send"
                for decision in runtime._owner_decisions().snapshot().decisions
            )
        assert resources._provider.transfers == ()


@pytest.mark.parametrize(
    "phase", ("after_authorize", "after_send", "consumed_provider", "consumed_resources")
)
async def test_same_contract_live_dependency_substitution_never_reaches_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
    from chiplog.composition import r16_dispatch_outbox

    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1)
    original_provider = resources.require_original_provider()
    replacement = HermeticEffectsProvider(
        receipt_key=b"replacement-key" * 3, scenarios=("CONFIRM",)
    )
    other_resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1)
    assert replacement.contract_version == original_provider.contract_version
    async with open_dispatch_loop(
        tmp_path / "dispatch.db", resources=resources, responses=(_response(),)
    ) as loop:
        created = await loop.create("r", "Prepare my action", BudgetPolicy())
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        runtime = loop._planning
        assert isinstance(runtime, R16DispatchRuntime)
        display = await runtime.preview_dispatch("r/turn/1/proposal/effect")
        initial = await runtime.adopt_dispatch(
            "hermetic-ingress",
            display.display_id,
            display.display_digest,
            display.adoption_act_id,
            "r",
        )
        assert isinstance(initial, JournalSelectedPublication)
        record = DispatchRecordV2.model_validate_json(initial.complete_records[-1].canonical_bytes)
        intent_id = record.snapshot.intent.intent_id
        authorized = await runtime.authorize_dispatch(
            "hermetic-ingress", intent_id, "authorize", "r"
        )
        assert isinstance(authorized, JournalSelectedPublication)
        if phase == "after_authorize":
            resources._provider = replacement
            with pytest.raises(ValueError, match="provider identity"):
                await runtime.commit_first_send("hermetic-ingress", intent_id, "send", "r")
            with runtime._authority_gate().hold():
                assert not any(
                    decision.prepared.request.operation == "effects.commit_send"
                    for decision in runtime._owner_decisions().snapshot().decisions
                )
        else:
            sent = await runtime.commit_first_send("hermetic-ingress", intent_id, "send", "r")
            assert isinstance(sent, JournalSelectedPublication)
            original_emit = r16_dispatch_outbox._emit
            reached = []

            async def substitute_after_consumption(
                bound_runtime: R16DispatchRuntime, permit: object
            ) -> bytes:
                reached.append(permit)
                if phase == "consumed_resources":
                    bound_runtime._dispatch_resources = other_resources
                else:
                    resources._provider = replacement
                return await original_emit(bound_runtime, permit)

            if phase == "after_send":
                resources._provider = replacement
            else:
                monkeypatch.setattr(r16_dispatch_outbox, "_emit", substitute_after_consumption)
            with pytest.raises(ValueError, match="identity differs from original custody"):
                await runtime.emit_committed("hermetic-ingress", intent_id)
            if phase != "after_send":
                assert len(reached) == 1
            assert not runtime._dispatch_permits
            with runtime._authority_gate().hold():
                assert (
                    sum(
                        decision.prepared.request.operation == "dispatch.consume_effect_send"
                        for decision in runtime._owner_decisions().snapshot().decisions
                    )
                    == 1
                )
            resources._provider = original_provider
            runtime._dispatch_resources = resources
            assert await runtime.emit_committed("hermetic-ingress", intent_id) is None
        assert original_provider.transfers == ()
        assert replacement.transfers == ()
        assert other_resources.require_original_provider().transfers == ()
    if phase in {"consumed_provider", "consumed_resources"}:
        async with open_dispatch_runtime(tmp_path / "dispatch.db", resources=resources) as reopened:
            assert await reopened.emit_committed("hermetic-ingress", intent_id) is None
            assert original_provider.transfers == ()
            assert replacement.transfers == ()
            assert other_resources.require_original_provider().transfers == ()


async def test_late_grant_revocation_does_not_redirect_or_reauthorize_selected_send(
    tmp_path: Path,
) -> None:
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1)
    provider = resources.require_original_provider()
    async with open_dispatch_loop(
        tmp_path / "dispatch.db", resources=resources, responses=(_response(),)
    ) as loop:
        created = await loop.create("r", "Prepare my action", BudgetPolicy())
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        runtime = loop._planning
        assert isinstance(runtime, R16DispatchRuntime)
        display = await runtime.preview_dispatch("r/turn/1/proposal/effect")
        initial = await runtime.adopt_dispatch(
            "hermetic-ingress",
            display.display_id,
            display.display_digest,
            display.adoption_act_id,
            "r",
        )
        assert isinstance(initial, JournalSelectedPublication)
        record = DispatchRecordV2.model_validate_json(initial.complete_records[-1].canonical_bytes)
        intent_id = record.snapshot.intent.intent_id
        authorized = await runtime.authorize_dispatch(
            "hermetic-ingress", intent_id, "authorize", "r"
        )
        assert isinstance(authorized, JournalSelectedPublication)
        sent = await runtime.commit_first_send("hermetic-ingress", intent_id, "send", "r")
        assert isinstance(sent, JournalSelectedPublication)
        resources.revoke()
        assert await runtime.emit_committed("hermetic-ingress", intent_id) is not None
        assert len(provider.transfers) == 1
        assert await runtime.emit_committed("hermetic-ingress", intent_id) is None
        assert len(provider.transfers) == 1


async def test_send_writer_rejects_lease_expiring_during_final_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time
    from typing import Literal

    from chiplog.composition import r16_dispatch_authority as module
    from chiplog.composition.r7_planning import ObservedTrustCall
    from chiplog.composition.r16_dispatch_inputs import DispatchCapture, capture_dispatch

    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1)
    async with open_dispatch_loop(
        tmp_path / "expiry.db", resources=resources, responses=(_response(),)
    ) as loop:
        created = await loop.create("r", "Prepare my action", BudgetPolicy())
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        runtime = loop._planning
        assert isinstance(runtime, R16DispatchRuntime)
        display = await runtime.preview_dispatch("r/turn/1/proposal/effect")
        selected = await runtime.adopt_dispatch(
            "hermetic-ingress",
            display.display_id,
            display.display_digest,
            display.adoption_act_id,
            "r",
        )
        assert isinstance(selected, JournalSelectedPublication)
        record = DispatchRecordV2.model_validate_json(selected.complete_records[-1].canonical_bytes)
        intent_id = record.snapshot.intent.intent_id
        authorized = await runtime.authorize_dispatch(
            "hermetic-ingress", intent_id, "authorize", "r"
        )
        assert isinstance(authorized, JournalSelectedPublication)
        reached = []
        original_check = DispatchPublicationAuthority.check_prepared
        original_capture = capture_dispatch

        def check(
            authority: DispatchPublicationAuthority, prepared: PreparedOwnerPublication
        ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
            value = next(
                item.issuance for item in authority.preparations if item.prepared is prepared
            )

            def capture(
                owner: R16DispatchRuntime,
                custody: HermeticDispatchResources,
                observed: ObservedTrustCall,
                run_id: str,
            ) -> DispatchCapture:
                actual = original_capture(owner, custody, observed, run_id)
                assert actual == value.captured
                deadline = min(
                    value.sent.budget.absolute_deadline_ns,
                    value.request.current.lease_expires_at_ns,
                )
                assert time.monotonic_ns() < deadline
                reached.append(deadline)
                time.sleep((deadline - time.monotonic_ns()) / 1_000_000_000 + 0.01)
                return actual

            with monkeypatch.context() as patch:
                patch.setattr(module, "capture_dispatch", capture)
                return original_check(authority, prepared)

        monkeypatch.setattr(DispatchPublicationAuthority, "check_prepared", check)
        result = await runtime.commit_first_send("hermetic-ingress", intent_id, "send", "r")
        assert len(reached) == 1
        assert isinstance(result, PublicationRejected), result
        assert result.kind == "STALE"
        with runtime._authority_gate().hold():
            assert not any(
                decision.prepared.request.operation == "effects.commit_send"
                for decision in runtime._owner_decisions().snapshot().decisions
            )
        assert resources._provider.transfers == ()
