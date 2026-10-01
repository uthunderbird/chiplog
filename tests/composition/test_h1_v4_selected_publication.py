"""Mounted V22 selection retains a V4 issuance across historical replay."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chiplog.composition.common_cli_execution_runtime import (
    open_installed_h1_scoped_v22_runtime,
)
from chiplog.composition.h1_completion_issuance import V4_SCHEMA, H1CompletionIssuanceV4
from chiplog.composition.h1_delivery_work_sources import (
    DeliveryWorkLocatorV1,
    H1DeliveryWorkSources,
)
from chiplog.composition.h1_historical_selected_sources import bind_selected_h1_completion
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_scoped_delivery_authority import H1ScopedDeliveryAuthorityReader
from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot
from tests.support.h1_scoped_first_b import scoped_resources, write_scoped_pins
from tests.support.h1_v3_recovery import (
    _issue_reconstructed_grant,
    _recover_b_prefix,
    _selected_scoped_root,
)


def _selected_batch(runtime: Any) -> tuple[Any, CompleteDeliveryBatchV2]:
    selected = tuple(
        decision
        for decision in runtime._owner_decisions().snapshot().decisions
        if type(decision.prepared.request) is CompleteDeliveryBatchV2
    )
    assert len(selected) == 1
    return selected[0], selected[0].prepared.request


@pytest.mark.asyncio
async def test_v22_selected_v4_publication_reopens_through_historical_binder_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A V22-selected SCOPED_V3 chain retains its exact V2 Effects exchange."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    write_scoped_pins(slot.database_path)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_v22_runtime(
            launch, resources=scoped_resources(tmp_path)
        ) as runtime:
            assert runtime._supervisor._j7_operator_policy_key_pin is not None
            assert runtime._supervisor._j7_operator_grant_key_pin is not None
            broker = runtime._supervisor.runtime()
            assert broker._j7_operator_policy_key_pin is not None
            assert broker._j7_operator_grant_key_pin is not None
            request, locator, state = await _selected_scoped_root(runtime)
            assert state.selected_producer == "SCOPED_V3"
            state = await _recover_b_prefix(runtime, request, locator, state)
            await _issue_reconstructed_grant(
                runtime,
                request,
                locator,
                authorization_horizon_ns=3_600_000_000_000,
            )
            coordinator = runtime._h1_postseal_recovery_coordinator
            assert coordinator is not None
            completed = await coordinator.resume_selected(
                request.identity, request.original_driver_command_fingerprint(), locator
            )
            assert len(completed.inputs) == len(completed.results) == 4

            calls: list[str] = []
            broker = runtime._supervisor.runtime()
            original = broker._call_with_admission_guard

            async def counted(sent: Any, **kwargs: Any) -> Any:
                calls.append(sent.operation_id)
                return await original(sent, **kwargs)

            monkeypatch.setattr(broker, "_call_with_admission_guard", counted)
            receipt = await runtime.finalize_execution(
                request.identity, request.original_driver_command_fingerprint()
            )
            assert getattr(receipt, "disposition", None) == "COMMITTED", receipt
            assert calls.count("effects.prepare_h1_scoped_delivery_v2") == 1

            decision, batch = _selected_batch(runtime)
            assert batch.authentication.applicability_schema == V4_SCHEMA
            enrollment = runtime._h1_live_completion_enrollment
            assert enrollment is not None
            retained = tuple(enrollment._issuances.values())
            assert len(retained) == 1
            assert type(retained[0].issuance) is H1CompletionIssuanceV4
            issuance = retained[0].issuance
            assert issuance.canonical_bytes() == batch.authentication.applicability_bytes
            assert (
                issuance.effects_call.canonical_bytes()
                == batch.prepared_effects_commands[0].canonical_bytes
            )
            assert (
                issuance.effects_result.intent_result.intent
                == issuance.assembly.ordered_effects[0].intent_result.intent
            )
            assert retained[0].batch == batch and retained[0].state == "CONSUMED"
            assert bind_selected_h1_completion(batch, runtime).decision == decision
            delivery_work = H1DeliveryWorkSources(runtime).read_absent(
                DeliveryWorkLocatorV1(issuance.effects_result.intent_result.intent.intent_id)
            )
            assert delivery_work.decision_id == decision.decision_id
            assert (
                delivery_work.intent.record_id
                == issuance.effects_result.intent_result.intent.intent_id
            )
            # The selected V4 envelope preserves a V2 scoped Effects exchange,
            # but its mutable authority must be reread from the independently
            # authenticated recovery prefix.  This is the only admissible
            # source for a later delivery-work claim; the retained V4 evidence
            # itself is historical and cannot stand in for current authority.
            registry = runtime._h1_completion_exchange_registry
            authority_reader = runtime._h1_scoped_delivery_authority_reader
            assert registry is not None
            assert type(authority_reader) is H1ScopedDeliveryAuthorityReader
            historical = registry.capture_recovered_prepared_delivery_historical(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )
            authority = await authority_reader.read_reconstructed(historical_b_capture=historical)
            assert authority.clock[1] > 0
            assert (
                authority.historical_projection.delivery.delivery_id
                == issuance.effects_result.delivery_id
            )
            selected_recipient = authority.historical_projection.delivery.selection.recipient
            effect_recipient = issuance.effects_result.intent_result.intent.mandate.recipient
            assert (
                selected_recipient.provider_id,
                selected_recipient.account_id,
                selected_recipient.recipient_id,
                selected_recipient.canonical_address,
            ) == (
                effect_recipient.provider,
                effect_recipient.account,
                effect_recipient.recipient,
                effect_recipient.canonical_address,
            )
            assert (
                selected_recipient.endpoint.identity,
                selected_recipient.endpoint.head,
                selected_recipient.endpoint.fingerprint,
            ) == (
                effect_recipient.endpoint.subject_id,
                effect_recipient.endpoint.head,
                effect_recipient.endpoint.fingerprint,
            )
            assert (
                selected_recipient.credential_binding.identity,
                selected_recipient.credential_binding.head,
                selected_recipient.credential_binding.fingerprint,
            ) == (
                effect_recipient.credential_binding.subject_id,
                effect_recipient.credential_binding.head,
                effect_recipient.credential_binding.fingerprint,
            )

            replay = await runtime.finalize_execution(
                request.identity, request.original_driver_command_fingerprint()
            )
            assert replay.disposition == "EXACT_REPLAY"
            assert calls.count("effects.prepare_h1_scoped_delivery_v2") == 1

    with _open_installed_h1_launch(slot) as reopened_launch:
        async with open_installed_h1_scoped_v22_runtime(
            reopened_launch, resources=scoped_resources(tmp_path)
        ) as reopened:
            calls = []
            broker = reopened._supervisor.runtime()
            original = broker._call_with_admission_guard

            async def counted_reopened(sent: Any, **kwargs: Any) -> Any:
                calls.append(sent.operation_id)
                return await original(sent, **kwargs)

            monkeypatch.setattr(broker, "_call_with_admission_guard", counted_reopened)
            replay = await reopened.finalize_execution(
                request.identity, request.original_driver_command_fingerprint()
            )
            assert replay.disposition == "EXACT_REPLAY"
            assert calls.count("effects.prepare_h1_scoped_delivery_v2") == 0
            decision, batch = _selected_batch(reopened)
            historical = bind_selected_h1_completion(batch, reopened)
            assert historical.decision == decision
            assert type(historical.issuance) is H1CompletionIssuanceV4
            assert historical.issuance.canonical_bytes() == batch.authentication.applicability_bytes
