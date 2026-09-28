"""The first finite Phase-C common-operation obligation inventory.

These rows only freeze public request identities already present in owner DTOs.
They deliberately do not construct an ``OperationRegistrySnapshotV1``: selecting
one requires a caller-provided runtime cut. Legacy publication routes retain their
existing bytes and are not mounts of this inventory.
"""

from typing import Literal

from ._ingress_contracts import Identity, IngressDTO
from .operation_registry_contracts import (
    COpenOperationInventoryEntryV1,
    OperationInventoryEntryV1,
    OperationKeyV1,
)
from .runtime_surface_contracts import WorkerApplicabilityKind

CLI_CUSTODY_OWNER_CALL_V1 = "CLI_CUSTODY_OWNER_CALL_V1"


class UnkeyedOperationObligationV1(IngressDTO):
    """A Phase-C obligation whose public request identity is not fixed yet."""

    state: Literal["UNKEYED_DISCOVERY"] = "UNKEYED_DISCOVERY"
    obligation: Identity
    source_component: Identity
    missing_key_fields: tuple[Identity, ...]
    reason: Identity


DRIVE_INPUT_KEY_V1 = OperationKeyV1(
    operation_id="driver.drive_input",
    request_schema_id="chiplog.common-execution-driver.drive-input-request.v1",
    request_version="v1",
    request_variant="DRIVE_INPUT_REQUEST_V1",
)
ADVANCE_EXECUTION_KEY_V1 = OperationKeyV1(
    operation_id="driver.advance_execution",
    request_schema_id="chiplog.common-execution-driver.advance-execution-request.v1",
    request_version="v1",
    request_variant="ADVANCE_EXECUTION_REQUEST_V1",
)
LOOKUP_EXECUTION_KEY_V1 = OperationKeyV1(
    operation_id="driver.lookup_execution",
    request_schema_id="chiplog.common-execution-driver.lookup-execution-request.v1",
    request_version="v1",
    request_variant="LOOKUP_EXECUTION_REQUEST_V1",
)
STAGE_RAW_BYTES_KEY_V1 = OperationKeyV1(
    operation_id="ingress.stage_raw_bytes",
    request_schema_id="chiplog.ingress.transition-request.v1",
    request_version="v1",
    request_variant="ingress.stage_raw_bytes",
)
AUTHENTICATE_CLI_CUSTODY_KEY_V1 = OperationKeyV1(
    operation_id="deployment_trust.authenticate_cli_custody",
    request_schema_id="chiplog.cli.custody-owner-call.v1",
    request_version="v1",
    request_variant=CLI_CUSTODY_OWNER_CALL_V1,
)


EXECUTION_TRANSITION_KEYS_V1: tuple[OperationKeyV1, ...] = tuple(
    OperationKeyV1(
        operation_id="agent_loop.prepare_execution_transition",
        request_schema_id="chiplog.execution.transition-request.v2",
        request_version="v2",
        request_variant=variant,
    )
    for variant in (
        "CREATE_EXECUTION_RUN_V2",
        "ACTIVATE_EXECUTION_RUN_V2",
        "START_INITIAL_EXECUTION_TURN_V2",
        "ACCUMULATE_EXECUTION_VISIBILITY_V2",
        "PREPARE_EXECUTION_REQUEST_V2",
        "EMIT_EXECUTION_ATTEMPT_V2",
        "CAPTURE_EXECUTION_RESPONSE_V2",
    )
)

SCHEDULER_CONFIGURATION_KEYS_V1: tuple[OperationKeyV1, ...] = tuple(
    OperationKeyV1(
        operation_id="scheduler.prepare_configuration",
        request_schema_id="chiplog.scheduler.configuration-preparation.v1",
        request_version="v1",
        request_variant=variant,
    )
    for variant in (
        "scheduler.genesis",
        "scheduler.amend_schedule",
        "scheduler.amend_policy",
        "scheduler.replace_bound",
    )
)

SCHEDULER_INTERVAL_KEYS_V1: tuple[OperationKeyV1, ...] = tuple(
    OperationKeyV1(
        operation_id="scheduler.prepare_interval",
        request_schema_id="chiplog.scheduler.interval-preparation.v1",
        request_version="v1",
        request_variant=variant,
    )
    for variant in ("scheduler.decide_interval", "scheduler.resolve_interval")
)

SCHEDULER_LEASE_KEYS_V1: tuple[OperationKeyV1, ...] = tuple(
    OperationKeyV1(
        operation_id="scheduler.prepare_lease",
        request_schema_id="chiplog.scheduler.lease-preparation.v1",
        request_version="v1",
        request_variant=variant,
    )
    for variant in ("scheduler.acquire", "scheduler.renew", "scheduler.takeover")
)

SCHEDULER_ROLLOVER_KEY_V1 = OperationKeyV1(
    operation_id="scheduler.prepare_rollover",
    request_schema_id="chiplog.scheduler.rollover-preparation.v1",
    request_version="v1",
    request_variant="scheduler.rollover",
)


OPERATION_REQUIRED_KEYS_V1: tuple[OperationKeyV1, ...] = (
    DRIVE_INPUT_KEY_V1,
    ADVANCE_EXECUTION_KEY_V1,
    LOOKUP_EXECUTION_KEY_V1,
    STAGE_RAW_BYTES_KEY_V1,
    AUTHENTICATE_CLI_CUSTODY_KEY_V1,
    *EXECUTION_TRANSITION_KEYS_V1,
    *SCHEDULER_CONFIGURATION_KEYS_V1,
    *SCHEDULER_INTERVAL_KEYS_V1,
    *SCHEDULER_LEASE_KEYS_V1,
    SCHEDULER_ROLLOVER_KEY_V1,
)


def _c_open_preparation_row(
    key: OperationKeyV1, route_name: Identity
) -> COpenOperationInventoryEntryV1:
    """Record the common declarations every unmapped owner route still needs."""

    return COpenOperationInventoryEntryV1(
        key=key,
        missing_declarations=(
            f"{route_name}_ordered_owner_participant_recipe",
            f"{route_name}_physical_record_pattern",
            f"{route_name}_selected_readback_decoder",
            f"{route_name}_replay_identity_policy",
            f"{route_name}_registered_publication_mount",
        ),
    )


OPERATION_INVENTORY_ROWS_V1: tuple[OperationInventoryEntryV1, ...] = (
    COpenOperationInventoryEntryV1(
        key=DRIVE_INPUT_KEY_V1,
        missing_declarations=(
            "driver_drive_input_ordered_owner_participant_recipe",
            "driver_drive_input_physical_record_pattern",
            "driver_drive_input_selected_readback_decoder",
            "driver_drive_input_replay_identity_policy",
            "driver_drive_input_registered_publication_mount",
        ),
    ),
    COpenOperationInventoryEntryV1(
        key=ADVANCE_EXECUTION_KEY_V1,
        missing_declarations=(
            "driver_advance_execution_ordered_owner_participant_recipe",
            "driver_advance_execution_physical_record_pattern",
            "driver_advance_execution_selected_readback_decoder",
            "driver_advance_execution_replay_identity_policy",
            "driver_advance_execution_registered_publication_mount",
        ),
    ),
    COpenOperationInventoryEntryV1(
        key=LOOKUP_EXECUTION_KEY_V1,
        missing_declarations=(
            "driver_lookup_execution_result_branch_contract",
            "driver_lookup_execution_selected_readback_decoder",
            "driver_lookup_execution_current_cut_validator",
            "driver_lookup_execution_historical_replay_validator",
            "driver_lookup_execution_registered_publication_mount",
        ),
    ),
    COpenOperationInventoryEntryV1(
        key=STAGE_RAW_BYTES_KEY_V1,
        missing_declarations=(
            "ingress_stage_raw_bytes_ordered_owner_participant_recipe",
            "ingress_stage_raw_bytes_complete_physical_member_pattern",
            "ingress_stage_raw_bytes_selected_readback_decoder",
            "ingress_stage_raw_bytes_replay_identity_policy",
            "ingress_stage_raw_bytes_registered_publication_mount",
        ),
    ),
    COpenOperationInventoryEntryV1(
        key=AUTHENTICATE_CLI_CUSTODY_KEY_V1,
        missing_declarations=(
            "cli_custody_owner_call_result_branch_contract",
            "cli_custody_owner_call_ordered_owner_participant_recipe",
            "cli_custody_owner_call_selected_readback_decoder",
            "cli_custody_owner_call_replay_identity_policy",
            "cli_custody_owner_call_operation_registry_mount",
        ),
    ),
    *(
        _c_open_preparation_row(key, "agent_loop_prepare_execution_transition")
        for key in EXECUTION_TRANSITION_KEYS_V1
    ),
    *(
        _c_open_preparation_row(key, "scheduler_prepare_configuration")
        for key in SCHEDULER_CONFIGURATION_KEYS_V1
    ),
    *(
        _c_open_preparation_row(key, "scheduler_prepare_interval")
        for key in SCHEDULER_INTERVAL_KEYS_V1
    ),
    *(
        _c_open_preparation_row(key, "scheduler_prepare_lease")
        for key in SCHEDULER_LEASE_KEYS_V1
    ),
    _c_open_preparation_row(SCHEDULER_ROLLOVER_KEY_V1, "scheduler_prepare_rollover"),
)


UNKEYED_OPERATION_DISCOVERY_LEDGER_V1: tuple[UnkeyedOperationObligationV1, ...] = (
    UnkeyedOperationObligationV1(
        obligation="execution.prepare_completion",
        source_component="ExecutionCompletionPreparationPort.prepare_completion",
        missing_key_fields=("operation_id", "request_schema_id"),
        reason="PrepareExecutionCompletion has a kind but no public request wire schema",
    ),
    UnkeyedOperationObligationV1(
        obligation="call.preview_acceptance",
        source_component="CallAcceptancePort.preview_call_acceptance",
        missing_key_fields=("operation_id", "request_schema_id"),
        reason="CallAcceptanceTarget has a kind but no public request wire schema",
    ),
    UnkeyedOperationObligationV1(
        obligation="call.accept",
        source_component="CallAcceptancePort.accept_call",
        missing_key_fields=("operation_id", "request_schema_id"),
        reason="CallAcceptanceAdoption has a kind but no public request wire schema",
    ),
    UnkeyedOperationObligationV1(
        obligation="h1.completion_issuance",
        source_component="H1CompletionIssuanceV1/H1CompletionIssuanceV2",
        missing_key_fields=(
            "operation_id",
            "request_schema_id",
            "request_version",
            "request_variant",
        ),
        reason="H1 completion issuance schemas are evidence wires, not a public completion request",
    ),
)


WORKER_APPLICABILITY_UNIVERSE_V1: tuple[WorkerApplicabilityKind, ...] = (
    "NON_SCHEDULER_NOT_APPLICABLE",
    "EXECUTION_ROOT_LIVE_LEASE",
    "PRE_ROOT_SCHEDULER_DECISION",
    "POST_TERMINAL_RECOVERY_WORK",
    "PHYSICAL_ROOT_ROLLOVER",
    "WORK_EPOCH_ROLLOVER",
)


__all__ = [
    "ADVANCE_EXECUTION_KEY_V1",
    "AUTHENTICATE_CLI_CUSTODY_KEY_V1",
    "CLI_CUSTODY_OWNER_CALL_V1",
    "DRIVE_INPUT_KEY_V1",
    "EXECUTION_TRANSITION_KEYS_V1",
    "LOOKUP_EXECUTION_KEY_V1",
    "OPERATION_INVENTORY_ROWS_V1",
    "OPERATION_REQUIRED_KEYS_V1",
    "SCHEDULER_CONFIGURATION_KEYS_V1",
    "SCHEDULER_INTERVAL_KEYS_V1",
    "SCHEDULER_LEASE_KEYS_V1",
    "SCHEDULER_ROLLOVER_KEY_V1",
    "STAGE_RAW_BYTES_KEY_V1",
    "UNKEYED_OPERATION_DISCOVERY_LEDGER_V1",
    "WORKER_APPLICABILITY_UNIVERSE_V1",
    "UnkeyedOperationObligationV1",
]
