"""Versioned completion batches leave historical completion bytes untouched."""

from typing import cast

import pytest
from pydantic import TypeAdapter, ValidationError

from chiplog.platform._owner_publication_contracts import (
    BrokerOperation,
    CompleteDeliveryBatch,
    CompleteDeliveryBatchV2,
    RegisteredPublication,
    RejectedCompletionBatchV1,
)
from chiplog.platform.owner_publications import source_commands


def _batch_values() -> dict[str, object]:
    digest = "a" * 64
    command = {
        "owner": "agent_loop",
        "schema_id": "fixture.v1",
        "canonical_bytes": b"fixture",
        "fingerprint": digest,
    }
    return {
        "identity": {
            "tenant_id": "tenant",
            "command_id": "command",
            "command_fingerprint": digest,
            "canonicalization_version": "chiplog.owner-publication.v1",
        },
        "authentication": {
            "invocation": {
                "issuance_id": "issued",
                "issuance_fingerprint": digest,
                "broker_epoch": "epoch",
                "broker_session": "session",
                "runtime_generation": "generation",
                "operation_subject": "completion",
            },
            "applicability_schema": "fixture.v1",
            "applicability_bytes": b"fixture",
            "applicability_fingerprint": digest,
        },
        "expected": {
            "tenant_id": "tenant",
            "tenant_frontier": 0,
            "expected_materialization_commitment": digest,
            "registry_head": "registry",
            "registry_fingerprint": digest,
            "ordered_heads": (),
            "complete_manifest_fingerprint": digest,
        },
        "complete_records": (
            {
                "owner": "agent_loop",
                "record_kind": "fixture",
                "record_id": "record",
                "schema_id": "fixture.v1",
                "canonical_bytes": b"record",
                "fingerprint": digest,
            },
        ),
        "complete_batch_fingerprint": digest,
        "command": command,
    }


def test_completion_batch_v2_registers_new_variants_without_repurposing_v1() -> None:
    mapping = TypeAdapter(RegisteredPublication).json_schema()["discriminator"]["mapping"]
    assert {
        "COMPLETE_DELIVERY_ATOMIC",
        "COMPLETE_DELIVERY_ATOMIC_V2",
        "REJECTED_COMPLETION_ATOMIC_V1",
    } <= set(mapping)
    assert "conversation_command" not in CompleteDeliveryBatch.model_fields
    assert CompleteDeliveryBatchV2.model_fields["schema_id"].default == (
        "chiplog.owner-publication.complete-delivery.v2"
    )
    assert RejectedCompletionBatchV1.model_fields["schema_id"].default == (
        "chiplog.owner-publication.rejected-completion.v1"
    )
    with pytest.raises(ValidationError):
        TypeAdapter(BrokerOperation).validate_python("agent_loop.complete_acceptance.v2")


def test_versioned_batches_have_fixed_exact_replay_role_order() -> None:
    base = _batch_values()
    command = cast(dict[str, object], base["command"])
    values = dict(base)
    values.pop("command")
    values.update(
        loop_command={**command, "owner": "agent_loop"},
        conversation_command={**command, "owner": "conversation"},
        terminal_work_command={**command, "owner": "agent_loop"},
        prepared_effects_commands=({**command, "owner": "effects"},),
    )
    accepted = CompleteDeliveryBatchV2.model_validate(values)
    assert [item.owner for item in source_commands(accepted)] == [
        "agent_loop",
        "conversation",
        "effects",
        "agent_loop",
    ]
    rejected_values = dict(base)
    rejected_values.pop("command")
    rejected_values.update(
        loop_rejection_command={**command, "owner": "agent_loop"},
        rejected_terminalization_command={**command, "owner": "agent_loop"},
        conversation_no_change_command={**command, "owner": "conversation"},
        terminal_work_command={**command, "owner": "agent_loop"},
    )
    rejected = RejectedCompletionBatchV1.model_validate(rejected_values)
    assert [item.owner for item in source_commands(rejected)] == [
        "agent_loop",
        "agent_loop",
        "conversation",
        "agent_loop",
    ]
