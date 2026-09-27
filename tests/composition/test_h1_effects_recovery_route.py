"""Installed H1 Effects recovery route contract.

This is deliberately an installed restart witness.  It does not replace the
fast mounted-owner purity test: the assertions here require the recovery
journal, a real broker frame, and a fresh broker generation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.effects.h1_local_preparation_contracts import (
    H1LocalCommentaryOwnerCallV1,
)
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.h1_completion_issuance import H1CompletionOwnerExchangeV1
from chiplog.composition.h1_completion_preparation_session import H1CompletionPreparationSession
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.broker import PublicPortSuccess
from tests.support.h1_cli_execution import _admit, advance
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot


class _CrashAfterEffectsReply(RuntimeError):
    """Represents process death after the real Effects reply and before its CAS."""


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


def _effects_owner_call(exchange: H1CompletionOwnerExchangeV1) -> H1LocalCommentaryOwnerCallV1:
    sent = exchange.sent
    assert sent.operation_id == "effects.prepare_h1_local_commentary"
    call = H1LocalCommentaryOwnerCallV1.model_validate_json(sent.canonical_payload)
    assert call.canonical_bytes() == sent.canonical_payload
    assert sent.request_id == call.request.identity.command_id
    assert call.route.request_id == call.request.identity.command_id
    return call


@pytest.mark.asyncio
async def test_installed_effects_recovery_reuses_pinned_inner_request_across_fresh_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Crash after the real reply; restart must replay the pin through a fresh route.

    The initial native seal is made without B.  This avoids the broad horizontal
    fixture: the only owner calls observed are those made by installed recovery.
    It does not exercise concurrent issuers or cancellation.
    """
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = _resources(tmp_path)
    first_effects: list[H1CompletionOwnerExchangeV1] = []
    recovered_effects: list[H1CompletionOwnerExchangeV1] = []

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            request = await _admit(runtime)
            initial = await runtime.drive_input(request)
            runtime._execution_model._responses = (
                DeliveryCompletion(
                    tenant=TENANT,
                    run_id=initial.stable_run_lineage_id,
                    turn_id=initial.stable_run_lineage_id + "/turn/1",
                    deliveries=(
                        ProposedDelivery(payload=(Commentary(text="pinned Effects recovery"),)),
                    ),
                ).canonical_bytes(),
            )

            # Leave a physical V2 seal with no ROOT or B session, exactly as a
            # process death between seal commit and recovery coordinator entry.
            started = await runtime.begin_execution(
                "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
            )
            captured = await runtime.capture_execution(
                "hermetic-ingress", initial.stable_run_lineage_id, started.head
            )
            await runtime.seal_execution_complete(
                "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V2"
            )
            assert runtime._h1_postseal_recovery_journal.scan().states_by_root == ()

        original = H1CompletionPreparationSession.prepare_local_commentary

        async def crash_after_real_effects_reply(self: H1CompletionPreparationSession):
            exchange = await original(self)
            first_effects.append(exchange)
            raise _CrashAfterEffectsReply()

        # This preserves the real mounted Effects owner result.  The injected
        # failure is only the coordinator's post-reply/pre-CAS crash cut.
        with monkeypatch.context() as patch:
            patch.setattr(
                H1CompletionPreparationSession,
                "prepare_local_commentary",
                crash_after_real_effects_reply,
            )
            async with open_installed_h1_runtime(launch, resources=resources) as runtime:
                with pytest.raises(_CrashAfterEffectsReply):
                    await runtime.advance_execution(advance(initial, request))

                assert len(first_effects) == 1
                first_call = _effects_owner_call(first_effects[0])
                state = next(
                    state
                    for _, state in runtime._h1_postseal_recovery_journal.scan().states_by_root
                )
                pinned_bytes, effects_command_id = state.stage_input("EFFECTS")
                assert pinned_bytes == first_call.request.canonical_bytes()
                assert effects_command_id == first_call.request.identity.command_id
                assert "EFFECTS" not in dict(state.results)

        async def observe_recovered_effects(self: H1CompletionPreparationSession):
            exchange = await original(self)
            recovered_effects.append(exchange)
            return exchange

        with monkeypatch.context() as patch:
            patch.setattr(
                H1CompletionPreparationSession,
                "prepare_local_commentary",
                observe_recovered_effects,
            )
            async with open_installed_h1_runtime(launch, resources=resources) as runtime:
                replay = await runtime.advance_execution(advance(initial, request))
                assert replay.phase == "RUNNING"
                assert len(recovered_effects) == 1

                first = first_effects[0]
                recovered = recovered_effects[0]
                first_call = _effects_owner_call(first)
                recovered_call = _effects_owner_call(recovered)
                assert (
                    recovered_call.request.canonical_bytes() == first_call.request.canonical_bytes()
                )
                assert (
                    recovered_call.request.identity.command_id
                    == first_call.request.identity.command_id
                )
                assert recovered_call.request.intent_id == first_call.request.intent_id

                assert isinstance(first.returned, PublicPortSuccess)
                assert isinstance(recovered.returned, PublicPortSuccess)
                assert recovered.returned.canonical_payload == first.returned.canonical_payload

                # IDs are logical identity, while these live route values come
                # from the fresh installed broker generation.
                assert recovered.sent.request_id == first.sent.request_id
                assert recovered_call.route.request_id == first_call.route.request_id
                assert (
                    recovered_call.route.runtime_generation != first_call.route.runtime_generation
                )
                assert recovered_call.route.broker_session_id != first_call.route.broker_session_id
                assert recovered_call.route.owner_session_id != first_call.route.owner_session_id

                state = next(
                    state
                    for _, state in runtime._h1_postseal_recovery_journal.scan().states_by_root
                )
                assert state.stage_input("EFFECTS") == (
                    first_call.request.canonical_bytes(),
                    first_call.request.identity.command_id,
                )
                assert dict(state.results)["EFFECTS"] == first.returned.canonical_payload
