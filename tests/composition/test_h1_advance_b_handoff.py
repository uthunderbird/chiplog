"""Installed advance owns the first bounded hand-off into enrolled H1 B."""

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
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_cli_execution import _admit, advance
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


@pytest.mark.asyncio
async def test_installed_advance_hands_its_h1_v2_seal_to_one_enrolled_b_session(
    tmp_path: Path,
) -> None:
    """The mounted owner, not a caller, creates B and all four real exchanges."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request = await _admit(runtime)
            initial = await runtime.drive_input(request)
            runtime._execution_model._responses = (
                DeliveryCompletion(
                    tenant=TENANT,
                    run_id=initial.stable_run_lineage_id,
                    turn_id=initial.stable_run_lineage_id + "/turn/1",
                    deliveries=(ProposedDelivery(payload=(Commentary(text="B handoff"),)),),
                ).canonical_bytes(),
            )

            result = await runtime.advance_execution(advance(initial, request))

            assert result.phase == "RUNNING"
            enrollment = runtime._h1_live_completion_enrollment
            assert len(enrollment._records) == 1
            record = next(iter(enrollment._records.values()))
            session = record.session
            # Recovery enrollment carries its own source/context/lease tuple;
            # this route never constructs a first-path cut.
            assert record.cut is None and session._cut is None
            assert len(enrollment._recovery_records) == 1
            recovery_record = enrollment._recovery_records[id(session)]
            recovery = session._recovery
            assert recovery is not None
            coordinator = runtime._h1_postseal_recovery_coordinator
            assert coordinator is not None
            assert recovery_record.session is session
            assert recovery_record.source is recovery.source is coordinator._source
            assert recovery_record.context is recovery.context
            assert recovery_record.lease is recovery.lease
            assert recovery_record.state == "REVOKED"
            journal = runtime._h1_postseal_recovery_journal
            assert journal is not None
            before = journal.scan()
            assert len(before.states_by_root) == 1
            state = before.states_by_root[0][1]
            assert state.root.tenant_id == request.identity.tenant_id
            assert state.root.database_id == request.identity.database_id
            assert state.root.original_command_id == request.identity.driver_command_id
            assert (
                state.root.original_command_fingerprint
                == request.original_driver_command_fingerprint()
            )
            assert session._completion_exchange is not None
            assert session._conversation_exchange is not None
            assert session._effects_exchange is not None
            assert session._terminal_work_exchange is not None

            exchanges = (
                session._completion_exchange,
                session._conversation_exchange,
                session._effects_exchange,
                session._terminal_work_exchange,
            )
            stages = ("COMPLETION", "CONVERSATION", "EFFECTS", "TERMINAL_WORK")
            assert recovery.dispatched_stages == stages
            assert recovery.result_bytes == dict(state.results)
            assert tuple(stage for stage, _raw, _command in state.inputs) == stages
            assert tuple(stage for stage, _raw in state.results) == stages
            for stage, exchange, stage_input, stage_result in zip(
                stages, exchanges, state.inputs, state.results, strict=True
            ):
                sent_input = exchange.sent.canonical_payload
                if stage == "EFFECTS":
                    sent_input = H1LocalCommentaryOwnerCallV1.model_validate_json(
                        sent_input
                    ).request.canonical_bytes()
                assert sent_input == stage_input[1]
                assert exchange.returned.canonical_payload == stage_result[1]

            # An exact replay is receipt-only: it neither opens a second B nor
            # reruns any of the owner calls retained by the first invocation.
            replay = await runtime.advance_execution(advance(initial, request))
            assert replay.disposition == "EXACT_REPLAY"
            assert tuple(enrollment._records.values()) == (record,)
            assert tuple(enrollment._recovery_records.values()) == (recovery_record,)
            assert session._completion_exchange is exchanges[0]
            assert session._conversation_exchange is exchanges[1]
            assert session._effects_exchange is exchanges[2]
            assert session._terminal_work_exchange is exchanges[3]
            assert journal.scan() == before
