"""Shared builders extracted without changing their behavior."""

from __future__ import annotations

from tests.support.h1_cli_execution import _admit
from tests.support.h1_installed_launch import (
    DATABASE,
    TENANT,
)

from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    ReadCurrentHermeticExecutionScopeV1,
)
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
)
from chiplog.composition.common_execution_driver_contracts import (
    AdvanceExecutionRequestV1,
    SelectedExecutionReceiptV1,
)


async def _current_request(
    runtime: CommonCliExecutionRuntime,
) -> ReadCurrentHermeticExecutionScopeV1:
    request = await _admit(runtime)
    initial = await runtime.drive_input(request)
    assert isinstance(initial, SelectedExecutionReceiptV1)
    completion = DeliveryCompletion(
        tenant=TENANT,
        run_id=initial.stable_run_lineage_id,
        turn_id=initial.stable_run_lineage_id + "/turn/1",
        deliveries=(ProposedDelivery(payload=(Commentary(text="current wire"),)),),
    )
    runtime._execution_model._responses = (completion.canonical_bytes(),)
    advanced = await runtime.advance_execution(
        AdvanceExecutionRequestV1(
            identity=request.identity,
            original_driver_command_fingerprint=request.original_driver_command_fingerprint(),
            expected_selected_run_head=initial.selected_run_head,
        )
    )
    assert isinstance(advanced, SelectedExecutionReceiptV1)
    assert advanced.phase == "RUNNING"
    port = runtime._h1_preissuance_registration_source_port
    assert port is not None
    cut = next(iter(port._cuts.values()))[1]
    return ReadCurrentHermeticExecutionScopeV1(
        expected_trust_observation=port._trust_observation(),
        source_anchor=cut.issued.anchor,
        expected_revision=cut.issued.revision,
        admitted_authentication_ref=cut.scope.admitted_authentication,
        authenticated_cli_ref=cut.prepared.source.verified.authenticated_cli_ref,
        tenant_id=TENANT,
        database_id=DATABASE,
        scope_id=cut.scope.scope_id,
        expected_scope_ref=cut.issued.scope_head,
        expected_worker_session_id=cut.prepared.run.worker_session,
        selected_resource_observation_ref=cut.scope.selected_resource_observation_ref,
    )
