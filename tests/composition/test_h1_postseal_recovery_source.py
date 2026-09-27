"""Immutable source identities for the post-seal recovery root."""

from __future__ import annotations

import hashlib
import json

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_postseal_recovery_source import (
    H1PostSealRecoveryRootSource,
    H1PostSealRecoverySourceError,
    publication_identity_for_source,
    source_commitment_for_preimage,
)
from chiplog.composition.r14_execution_complete_seal_records import RetainedExecutionCompleteSealV2
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_cli_execution import _admit
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot


def _preimage() -> dict[str, object]:
    return {
        "schema_id": "chiplog.h1.postseal-recovery-source.v1",
        "tenant_id": "tenant-a",
        "database_id": "database-a",
        "database_identity": ["/var/lib/chiplog/a.sqlite", 41, 99],
        "journal_instance_id": "h1-post-seal-recovery:a",
        "original_driver_request_base64": "eyJraW5kIjoiRFJJVkUifQ==",
        "initialization": {"decision_id": "decision:init", "raw_base64": "e30="},
        "run_lineage": [
            {"decision_id": "decision:init", "raw_base64": "e30="},
            {"decision_id": "decision:seal", "raw_base64": "eyJzZWFsIjp0cnVlfQ=="},
        ],
        "selected_seal_decision": {
            "decision_id": "decision:seal",
            "raw_base64": "eyJzZWFsIjp0cnVlfQ==",
        },
        "physical_members": [
            {
                "decision_id": "decision:init",
                "record_id": "record:run",
                "owner": "owner",
                "schema_id": "chiplog.execution.run.v1",
                "canonical_payload_base64": "cnVu",
                "fingerprint": "1" * 64,
            },
            {
                "decision_id": "decision:seal",
                "record_id": "record:seal",
                "owner": "owner",
                "schema_id": "chiplog.response-seal.v1",
                "canonical_payload_base64": "c2VhbA==",
                "fingerprint": "2" * 64,
            },
        ],
    }


def test_frozen_source_and_publication_preimages_have_stable_domain_separated_vectors() -> None:
    commitment = source_commitment_for_preimage(_preimage())
    identity = publication_identity_for_source(
        tenant_id="tenant-a",
        database_id="database-a",
        database_identity=("/var/lib/chiplog/a.sqlite", 41, 99),
        journal_instance_id="h1-post-seal-recovery:a",
        source_commitment=commitment,
    )

    assert commitment == "6777663dc9168105ce8c53f96ebb60c3fdbfdeb51ae05a317c8e48fd432bc0e0"
    assert identity.command_id == (
        "h1-recovery-root:986a9f3e69cd6a3f8c1d4e49ee5e9f454e02433e961cad3b3e0ad575b81fc207"
    )
    assert identity.command_fingerprint == (
        "5cd9d999bac3c1c32dc08c36c6e83afb0f969d4443af95bf8eecc7146fb45d96"
    )


def test_each_committed_source_byte_changes_source_commitment() -> None:
    original = _preimage()
    changed = _preimage()
    changed["selected_seal_decision"] = {
        "decision_id": "decision:seal",
        "raw_base64": "eyJzZWFsOmZhbHNlfQ==",
    }

    assert source_commitment_for_preimage(changed) != source_commitment_for_preimage(original)

    changed_member = _preimage()
    changed_member["physical_members"] = [
        *changed_member["physical_members"][:-1],
        {
            **changed_member["physical_members"][-1],
            "canonical_payload_base64": "bXV0YXRlZC1zZWFs",
        },
    ]
    assert source_commitment_for_preimage(changed_member) != source_commitment_for_preimage(
        original
    )


def test_publication_identity_is_acyclic_and_changes_only_with_its_frozen_inputs() -> None:
    commitment = source_commitment_for_preimage(_preimage())
    first = publication_identity_for_source(
        tenant_id="tenant-a",
        database_id="database-a",
        database_identity=("/var/lib/chiplog/a.sqlite", 41, 99),
        journal_instance_id="h1-post-seal-recovery:a",
        source_commitment=commitment,
    )
    second = publication_identity_for_source(
        tenant_id="tenant-a",
        database_id="database-a",
        database_identity=("/var/lib/chiplog/a.sqlite", 41, 99),
        journal_instance_id="h1-post-seal-recovery:a",
        source_commitment=commitment,
    )

    assert first == second
    assert first.command_id.startswith("h1-recovery-root:")


def test_historical_v2_source_rejects_untyped_caller_input_before_runtime_access() -> None:
    reader = object.__new__(H1PostSealRecoveryRootSource)

    with pytest.raises(TypeError, match="exact original driver identity"):
        reader.derive_on_restart(object(), object(), object())


def test_v2_source_rejects_checkpoint_substitution_before_any_root_derivation() -> None:
    raw = b'{"h1_historical_checkpoint":{},"kind":"DECIDED"}'

    with pytest.raises(H1PostSealRecoverySourceError, match="rejects a checkpoint"):
        H1PostSealRecoveryRootSource._require_exact_v2(raw)


@pytest.mark.asyncio
async def test_installed_immediate_v2_seal_derives_root_from_authenticated_source(
    tmp_path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            request = await _admit(runtime)
            initial = await runtime.drive_input(request)
            runtime._execution_model._responses = (
                DeliveryCompletion(
                    tenant=TENANT,
                    run_id=initial.stable_run_lineage_id,
                    turn_id=initial.stable_run_lineage_id + "/turn/1",
                    deliveries=(ProposedDelivery(payload=(Commentary(text="recovery root"),)),),
                ).canonical_bytes(),
            )
            started = await runtime.begin_execution(
                "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
            )
            captured = await runtime.capture_execution(
                "hermetic-ingress", initial.stable_run_lineage_id, started.head
            )
            sealed = await runtime.seal_execution_complete(
                "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V2"
            )
            raw = next(
                raw
                for _, _, raw in runtime._loop_decisions().entries()
                if json.loads(raw).get("operation_id") == sealed.head
            )
            retained = RetainedExecutionCompleteSealV2.model_validate_json(
                json.loads(raw)["execution_complete_seal"]
            )
            response_seal = retained.exchange.proposal.fan_out.response_seal
            locator = CallSubjectHead(
                subject_id=response_seal.response_seal_id,
                revision=Present(
                    head="record:" + response_seal.digest(), fingerprint=response_seal.digest()
                ),
            )

            root = H1PostSealRecoveryRootSource(runtime).derive_immediate(
                request.identity, request.original_driver_command_fingerprint(), locator
            )

            assert root.tenant_id == TENANT
            assert root.database_id == slot.database_id
            assert root.selected_seal_subject_id == locator.subject_id
            assert root.selected_seal_head == locator.revision.head
            assert root.selected_decision_digest == hashlib.sha256(raw).hexdigest()
            assert root.selected_run_head == sealed.head
            assert root.original_command_id == request.identity.driver_command_id
