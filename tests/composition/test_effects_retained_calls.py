"""Retained frames originate in real owner IPC and preserve display leases."""

import base64
from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected
from chiplog.composition.r13 import open_r13_loop
from chiplog.composition.r13_planning import R13PlanningRuntime
from chiplog.composition.r16_effects import EffectPreviewBinding, R16EffectsProducer
from chiplog.platform.authority_gate import AuthorityGateError
from chiplog.platform.broker import PublicPortCall, PublicPortResult, PublicPortSuccess
from tests.composition.test_effects_authenticated_cut import _response


async def test_retained_actual_frames_send_original_display_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_r13_loop(tmp_path / "exact.sqlite", responses=(_response(),)) as loop:
        created = await loop.create("r", "Propose", BudgetPolicy())
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        runtime = loop._planning
        assert isinstance(runtime, R13PlanningRuntime)
        broker = runtime._supervisor.runtime()
        original_call = broker.call
        frames: list[tuple[PublicPortCall, PublicPortResult]] = []

        async def capture(request: PublicPortCall) -> PublicPortResult:
            # A synchronous authority gate must never span owner IPC.
            with pytest.raises(AuthorityGateError):
                runtime._authority_gate().require_held()
            result = await original_call(request)
            frames.append((request, result))
            return result

        monkeypatch.setattr(broker, "call", capture)
        producer = R16EffectsProducer(runtime)
        display = await producer.display_effect("r/turn/1/proposal/effect")
        binding = EffectPreviewBinding.model_validate_json(display.canonical_command)
        displayed_request = base64.b64decode(binding.planning_request_base64, validate=True)
        preview_call = frames[-1][0]
        assert preview_call.canonical_payload == displayed_request
        adoption = await producer.adopt_effect(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )
        trust, planning = frames[-2:]
        assert adoption.observed_trust.request is trust[0]
        assert adoption.observed_trust.response is trust[1]
        assert adoption.planning.sent_call is planning[0]
        assert adoption.planning.returned_result is planning[1]
        assert planning[0].canonical_payload == displayed_request
        assert adoption.planning.request_bytes == displayed_request
        assert planning[0].request_id != preview_call.request_id
        with runtime._authority_gate().hold():
            assert runtime._trust_observation_guard(adoption.observed_trust) is None


@pytest.mark.parametrize("owner", ("planning", "deployment_trust"))
@pytest.mark.parametrize("defect", ("request_id", "responder", "schema"))
async def test_actual_owner_response_identity_is_checked_before_payload_decode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner: str, defect: str
) -> None:
    async with open_r13_loop(tmp_path / "invalid.sqlite", responses=(_response(),)) as loop:
        created = await loop.create("r", "Propose", BudgetPolicy())
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        runtime = loop._planning
        assert isinstance(runtime, R13PlanningRuntime)
        broker = runtime._supervisor.runtime()
        original_call = broker.call

        async def corrupt(request: PublicPortCall) -> PublicPortResult:
            result = await original_call(request)
            if request.callee.owner_id != owner:
                return result
            assert isinstance(result, PublicPortSuccess)
            updates: dict[str, object] = {"canonical_payload": b"not-json"}
            if defect == "schema":
                updates["schema_id"] = "foreign-schema"
            elif defect == "responder":
                updates["responder"] = result.responder.model_copy(
                    update={"session_id": "foreign-session"}
                )
            else:
                updates["request_id"] = "foreign-request"
            return result.model_copy(update=updates)

        monkeypatch.setattr(broker, "call", corrupt)
        with pytest.raises(ValueError, match=r"response (identity|schema)"):
            await R16EffectsProducer(runtime).display_effect("r/turn/1/proposal/effect")


@pytest.mark.parametrize("during", ("deployment_trust", "planning"))
async def test_source_change_during_real_owner_ipc_rejects_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, during: str
) -> None:
    async with open_r13_loop(tmp_path / "mutation.sqlite", responses=(_response(),)) as loop:
        created = await loop.create("r", "Propose", BudgetPolicy())
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        runtime = loop._planning
        assert isinstance(runtime, R13PlanningRuntime)
        producer = R16EffectsProducer(runtime)
        display = await producer.display_effect("r/turn/1/proposal/effect")
        broker = runtime._supervisor.runtime()
        original_call = broker.call
        changed = False

        async def mutate(request: PublicPortCall) -> PublicPortResult:
            nonlocal changed
            result = await original_call(request)
            if not changed and request.callee.owner_id == during:
                changed = True
                await loop.create("other", "Canonical source mutation", BudgetPolicy())
            return result

        monkeypatch.setattr(broker, "call", mutate)
        with pytest.raises(LoopRejected, match="changed"):
            await producer.adopt_effect(
                "hermetic-ingress",
                display.display_id,
                display.display_digest,
                display.adoption_act_id,
            )
        assert changed
