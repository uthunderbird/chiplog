"""Immutable source identities for the post-seal recovery root."""

from __future__ import annotations

import pytest

from chiplog.composition.h1_postseal_recovery_source import (
    H1PostSealRecoveryRootSource,
    H1PostSealRecoverySourceError,
    publication_identity_for_source,
    source_commitment_for_preimage,
)


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
