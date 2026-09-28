from typing import Any, cast, get_args

from chiplog.capabilities.agent_loop._scheduler_process import ROUTES
from chiplog.capabilities.agent_loop.execution_transition_contracts import (
    AccumulateExecutionVisibility,
    ActivateExecutionRun,
    CaptureExecutionResponse,
    CreateExecutionRun,
    EmitExecutionAttempt,
    PrepareExecutionRequest,
    StartInitialExecutionTurn,
)
from chiplog.capabilities.agent_loop.scheduler_preparation import (
    ConfigurationPreparationRequest,
    IntervalPreparationRequest,
    LeasePreparationRequest,
    RolloverPreparationRequest,
)
from chiplog.capabilities.deployment_trust.cli_custody_validation import CliCustodyOwnerCall
from chiplog.composition.common_execution_driver_contracts import (
    AdvanceExecutionRequestV1,
    CliPeerSelectedSourceV1,
    DriveInputRequestV1,
    ExecutionDriverRejectedV1,
    LookupExecutionRequestV1,
    SelectedExecutionReceiptV1,
)
from chiplog.platform._ingress_contracts import Head
from chiplog.platform.ingress_transition_contracts import IngressTransitionRequest, StageIngressRaw
from chiplog.platform.operation_registry_contracts import (
    COpenOperationInventoryEntryV1,
    OperationKeyV1,
    OperationRegistrySnapshotV1,
    OperationResolutionFailureV1,
    resolve_operation,
)
from chiplog.platform.production_operation_inventory import (
    CLI_CUSTODY_OWNER_CALL_V1,
    OPERATION_INVENTORY_ROWS_V1,
    OPERATION_REQUIRED_KEYS_V1,
    UNKEYED_OPERATION_DISCOVERY_LEDGER_V1,
    WORKER_APPLICABILITY_UNIVERSE_V1,
)
from chiplog.platform.runtime_surface_contracts import RuntimeSurfaceCut

_DIGEST = "0" * 64


def _head(identity: str) -> Head:
    return Head(identity=identity, head=f"{identity}/head", fingerprint=_DIGEST)


def _selected_cut() -> RuntimeSurfaceCut:
    return RuntimeSurfaceCut(
        runtime_profile=_head("test-runtime-profile"),
        broker_generation="test-broker-generation",
        runtime_generation="test-owner-generation",
        source_inventory_fingerprint=_DIGEST,
        worker_registry=_head("test-worker-registry"),
        ingress_manifest=_head("test-ingress-manifest"),
    )


def _selected_snapshot(cut: RuntimeSurfaceCut) -> OperationRegistrySnapshotV1:
    return OperationRegistrySnapshotV1(
        head=_head("test-production-operation-inventory"),
        version="v1",
        fingerprint=_DIGEST,
        runtime_profile=cut.runtime_profile,
        source_inventory_fingerprint=cut.source_inventory_fingerprint,
        broker_generation=cut.broker_generation,
        owner_generation=cut.runtime_generation,
        applicability_universe=WORKER_APPLICABILITY_UNIVERSE_V1,
        required_keys=OPERATION_REQUIRED_KEYS_V1,
        rows=OPERATION_INVENTORY_ROWS_V1,
    )


def _resolution_kind(
    snapshot: OperationRegistrySnapshotV1, key: OperationKeyV1, cut: RuntimeSurfaceCut
) -> str:
    resolution = resolve_operation(snapshot, key, cut)
    assert isinstance(resolution, OperationResolutionFailureV1)
    return resolution.kind


def _missing_declarations(row: object) -> tuple[str, ...]:
    assert isinstance(row, COpenOperationInventoryEntryV1)
    return row.missing_declarations


def test_inventory_freezes_five_outer_request_key_literals() -> None:
    assert tuple(key.operation_id for key in OPERATION_REQUIRED_KEYS_V1[:5]) == (
        "driver.drive_input",
        "driver.advance_execution",
        "driver.lookup_execution",
        "ingress.stage_raw_bytes",
        "deployment_trust.authenticate_cli_custody",
    )
    assert tuple(key.request_schema_id for key in OPERATION_REQUIRED_KEYS_V1[:3]) == (
        DriveInputRequestV1.model_fields["schema_id"].default,
        AdvanceExecutionRequestV1.model_fields["schema_id"].default,
        LookupExecutionRequestV1.model_fields["schema_id"].default,
    )
    assert tuple(key.request_variant for key in OPERATION_REQUIRED_KEYS_V1[:3]) == (
        DriveInputRequestV1.model_fields["kind"].default,
        AdvanceExecutionRequestV1.model_fields["kind"].default,
        LookupExecutionRequestV1.model_fields["kind"].default,
    )
    assert OPERATION_REQUIRED_KEYS_V1[3].request_schema_id == (
        IngressTransitionRequest.model_fields["schema_id"].default
    )
    assert OPERATION_REQUIRED_KEYS_V1[3].request_variant == (
        StageIngressRaw.model_fields["kind"].default
    )
    assert OPERATION_REQUIRED_KEYS_V1[4].request_schema_id == (
        CliCustodyOwnerCall.model_fields["schema_id"].default
    )
    assert OPERATION_REQUIRED_KEYS_V1[4].request_variant == CLI_CUSTODY_OWNER_CALL_V1
    assert all(row.state == "C_OPEN" for row in OPERATION_INVENTORY_ROWS_V1)


def _route_schema_id(operation_id: str) -> str:
    return next(route[3] for route in ROUTES if route[0] == operation_id)


def _scheduler_variants(request_type: Any) -> tuple[str, ...]:
    return cast(tuple[str, ...], get_args(request_type.model_fields["operation"].annotation))


def _expanded_owner_request_literals() -> tuple[tuple[str, str, str, str], ...]:
    execution_variants = tuple(
        request_type.model_fields["kind"].default
        for request_type in (
            CreateExecutionRun,
            ActivateExecutionRun,
            StartInitialExecutionTurn,
            AccumulateExecutionVisibility,
            PrepareExecutionRequest,
            EmitExecutionAttempt,
            CaptureExecutionResponse,
        )
    )
    return (
        *(
            (
                "agent_loop.prepare_execution_transition",
                "chiplog.execution.transition-request.v2",
                "v2",
                variant,
            )
            for variant in execution_variants
        ),
        *(
            (
                "scheduler.prepare_configuration",
                _route_schema_id("scheduler.prepare_configuration"),
                "v1",
                variant,
            )
            for variant in _scheduler_variants(ConfigurationPreparationRequest)
        ),
        *(
            (
                "scheduler.prepare_interval",
                _route_schema_id("scheduler.prepare_interval"),
                "v1",
                variant,
            )
            for variant in _scheduler_variants(IntervalPreparationRequest)
        ),
        *(
            (
                "scheduler.prepare_lease",
                _route_schema_id("scheduler.prepare_lease"),
                "v1",
                variant,
            )
            for variant in _scheduler_variants(LeasePreparationRequest)
        ),
        (
            "scheduler.prepare_rollover",
            _route_schema_id("scheduler.prepare_rollover"),
            "v1",
            _scheduler_variants(RolloverPreparationRequest)[0],
        ),
    )


def test_inventory_freezes_22_route_envelope_and_payload_literals() -> None:
    expected_new = _expanded_owner_request_literals()

    assert len(OPERATION_REQUIRED_KEYS_V1) == 22
    assert tuple(
        (key.operation_id, key.request_schema_id, key.request_version, key.request_variant)
        for key in OPERATION_REQUIRED_KEYS_V1[5:]
    ) == expected_new


def test_expanded_keys_reject_mutated_schema_version_variant_and_route_pairing() -> None:
    required_keys = OPERATION_REQUIRED_KEYS_V1

    for key in required_keys[5:]:
        wrong_schema = key.model_copy(
            update={"request_schema_id": key.request_schema_id + ".wrong"}
        )
        assert wrong_schema not in required_keys
        assert key.model_copy(update={"request_version": "v0"}) not in required_keys
        wrong_variant = key.model_copy(update={"request_variant": key.request_variant + ".wrong"})
        assert wrong_variant not in required_keys
        different_route = (
            "scheduler.prepare_lease"
            if key.operation_id == "scheduler.prepare_rollover"
            else "scheduler.prepare_rollover"
        )
        assert key.model_copy(
            update={"operation_id": different_route}
        ) not in required_keys


def test_expanded_rows_are_exact_c_open_declarations_without_a_mount() -> None:
    expected_declarations = (
        *(
            (
                "agent_loop_prepare_execution_transition_ordered_owner_participant_recipe",
                "agent_loop_prepare_execution_transition_physical_record_pattern",
                "agent_loop_prepare_execution_transition_selected_readback_decoder",
                "agent_loop_prepare_execution_transition_replay_identity_policy",
                "agent_loop_prepare_execution_transition_registered_publication_mount",
            )
            for _ in range(7)
        ),
        *(
            (
                "scheduler_prepare_configuration_ordered_owner_participant_recipe",
                "scheduler_prepare_configuration_physical_record_pattern",
                "scheduler_prepare_configuration_selected_readback_decoder",
                "scheduler_prepare_configuration_replay_identity_policy",
                "scheduler_prepare_configuration_registered_publication_mount",
            )
            for _ in range(4)
        ),
        *(
            (
                "scheduler_prepare_interval_ordered_owner_participant_recipe",
                "scheduler_prepare_interval_physical_record_pattern",
                "scheduler_prepare_interval_selected_readback_decoder",
                "scheduler_prepare_interval_replay_identity_policy",
                "scheduler_prepare_interval_registered_publication_mount",
            )
            for _ in range(2)
        ),
        *(
            (
                "scheduler_prepare_lease_ordered_owner_participant_recipe",
                "scheduler_prepare_lease_physical_record_pattern",
                "scheduler_prepare_lease_selected_readback_decoder",
                "scheduler_prepare_lease_replay_identity_policy",
                "scheduler_prepare_lease_registered_publication_mount",
            )
            for _ in range(3)
        ),
        (
            "scheduler_prepare_rollover_ordered_owner_participant_recipe",
            "scheduler_prepare_rollover_physical_record_pattern",
            "scheduler_prepare_rollover_selected_readback_decoder",
            "scheduler_prepare_rollover_replay_identity_policy",
            "scheduler_prepare_rollover_registered_publication_mount",
        ),
    )

    assert tuple(_missing_declarations(row) for row in OPERATION_INVENTORY_ROWS_V1[5:]) == (
        expected_declarations
    )
    assert all(row.state == "C_OPEN" for row in OPERATION_INVENTORY_ROWS_V1[5:])
    assert all(not hasattr(row, "contract") for row in OPERATION_INVENTORY_ROWS_V1[5:])


def test_inventory_freezes_missing_components_and_unkeyed_discovery_obligations() -> None:
    assert tuple(_missing_declarations(row) for row in OPERATION_INVENTORY_ROWS_V1[:5]) == (
        (
            "driver_drive_input_ordered_owner_participant_recipe",
            "driver_drive_input_physical_record_pattern",
            "driver_drive_input_selected_readback_decoder",
            "driver_drive_input_replay_identity_policy",
            "driver_drive_input_registered_publication_mount",
        ),
        (
            "driver_advance_execution_ordered_owner_participant_recipe",
            "driver_advance_execution_physical_record_pattern",
            "driver_advance_execution_selected_readback_decoder",
            "driver_advance_execution_replay_identity_policy",
            "driver_advance_execution_registered_publication_mount",
        ),
        (
            "driver_lookup_execution_result_branch_contract",
            "driver_lookup_execution_selected_readback_decoder",
            "driver_lookup_execution_current_cut_validator",
            "driver_lookup_execution_historical_replay_validator",
            "driver_lookup_execution_registered_publication_mount",
        ),
        (
            "ingress_stage_raw_bytes_ordered_owner_participant_recipe",
            "ingress_stage_raw_bytes_complete_physical_member_pattern",
            "ingress_stage_raw_bytes_selected_readback_decoder",
            "ingress_stage_raw_bytes_replay_identity_policy",
            "ingress_stage_raw_bytes_registered_publication_mount",
        ),
        (
            "cli_custody_owner_call_result_branch_contract",
            "cli_custody_owner_call_ordered_owner_participant_recipe",
            "cli_custody_owner_call_selected_readback_decoder",
            "cli_custody_owner_call_replay_identity_policy",
            "cli_custody_owner_call_operation_registry_mount",
        ),
    )
    assert tuple(
        (item.obligation, item.source_component, item.missing_key_fields, item.reason)
        for item in UNKEYED_OPERATION_DISCOVERY_LEDGER_V1
    ) == (
        (
            "execution.prepare_completion",
            "ExecutionCompletionPreparationPort.prepare_completion",
            ("operation_id", "request_schema_id"),
            "PrepareExecutionCompletion has a kind but no public request wire schema",
        ),
        (
            "call.preview_acceptance",
            "CallAcceptancePort.preview_call_acceptance",
            ("operation_id", "request_schema_id"),
            "CallAcceptanceTarget has a kind but no public request wire schema",
        ),
        (
            "call.accept",
            "CallAcceptancePort.accept_call",
            ("operation_id", "request_schema_id"),
            "CallAcceptanceAdoption has a kind but no public request wire schema",
        ),
        (
            "h1.completion_issuance",
            "H1CompletionIssuanceV1/H1CompletionIssuanceV2",
            ("operation_id", "request_schema_id", "request_version", "request_variant"),
            "H1 completion issuance schemas are evidence wires, not a public completion request",
        ),
    )


def test_outer_or_nested_schema_and_another_ingress_variant_do_not_register() -> None:
    cut = _selected_cut()
    snapshot = _selected_snapshot(cut)
    ingress_key = OPERATION_REQUIRED_KEYS_V1[3]

    nested_source_schema_key = ingress_key.model_copy(
        update={
            "request_schema_id": CliPeerSelectedSourceV1.model_fields["expected_schema_id"].default
        }
    )
    another_ingress_command_key = ingress_key.model_copy(
        update={"request_variant": "ingress.allocate_receipt_token"}
    )

    assert _resolution_kind(snapshot, ingress_key, cut) == "UNKNOWN_VERSION"
    assert _resolution_kind(snapshot, nested_source_schema_key, cut) == "UNKNOWN_VERSION"
    assert _resolution_kind(snapshot, another_ingress_command_key, cut) == "UNKNOWN_VERSION"
    assert len(snapshot.rows) == 22


def test_result_kinds_cannot_masquerade_as_driver_request_variants() -> None:
    cut = _selected_cut()
    snapshot = _selected_snapshot(cut)

    selected_as_request = OPERATION_REQUIRED_KEYS_V1[0].model_copy(
        update={"request_variant": SelectedExecutionReceiptV1.model_fields["kind"].default}
    )
    rejected_as_request = OPERATION_REQUIRED_KEYS_V1[2].model_copy(
        update={"request_variant": ExecutionDriverRejectedV1.model_fields["kind"].default}
    )

    assert _resolution_kind(snapshot, selected_as_request, cut) == "UNKNOWN_VERSION"
    assert _resolution_kind(snapshot, rejected_as_request, cut) == "UNKNOWN_VERSION"


def test_exact_c_open_keys_and_their_versions_resolve_as_unknown_version() -> None:
    cut = _selected_cut()
    snapshot = _selected_snapshot(cut)

    for key in OPERATION_REQUIRED_KEYS_V1:
        assert _resolution_kind(snapshot, key, cut) == "UNKNOWN_VERSION"
        changed_version = key.model_copy(update={"request_version": "v2"})
        assert _resolution_kind(snapshot, changed_version, cut) == "UNKNOWN_VERSION"
