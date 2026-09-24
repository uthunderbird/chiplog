"""Fail-closed wire boundary for the unfinished H1 completion issuer."""

import hashlib
from typing import Literal

import pytest

from chiplog.composition.h1_completion_issuance import (
    SCHEMA,
    H1CompletionCaptureV1,
    decode_h1_completion_issuance,
    h1_completion_exchange,
)
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    CompleteDeliveryBatchV2,
    InvocationProofRef,
    OwnerCommandBytes,
    OwnerRecordBytes,
    PublicationIdentity,
    WorkerAuthentication,
)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _batch(*, applicability_schema: str) -> CompleteDeliveryBatchV2:
    def command(
        owner: Literal[
            "agent_loop",
            "effects",
            "planning",
            "broker_ingress",
            "broker_dispatch",
            "conversation",
        ],
    ) -> OwnerCommandBytes:
        return OwnerCommandBytes(
            owner=owner,
            schema_id="test.command.v1",
            canonical_bytes=b"command",
            fingerprint=_digest(b"command"),
        )

    return CompleteDeliveryBatchV2(
        identity=PublicationIdentity(
            tenant_id="tenant",
            command_id="command",
            command_fingerprint="0" * 64,
            canonicalization_version="chiplog.owner-publication.v1",
        ),
        authentication=WorkerAuthentication(
            invocation=InvocationProofRef(
                issuance_id="issuance",
                issuance_fingerprint="0" * 64,
                broker_epoch="epoch",
                broker_session="session",
                runtime_generation="generation",
                operation_subject="subject",
            ),
            applicability_schema=applicability_schema,
            applicability_bytes=b"not-an-h1-issuance",
            applicability_fingerprint=_digest(b"not-an-h1-issuance"),
        ),
        expected=AuthoritativeReadManifest(
            tenant_id="tenant",
            tenant_frontier=0,
            expected_materialization_commitment="0" * 64,
            registry_head="registry",
            registry_fingerprint="0" * 64,
            ordered_heads=(),
            complete_manifest_fingerprint="0" * 64,
        ),
        loop_command=command("agent_loop"),
        conversation_command=command("conversation"),
        terminal_work_command=command("agent_loop"),
        prepared_effects_commands=(command("effects"),),
        complete_records=(
            OwnerRecordBytes(
                owner="agent_loop",
                record_kind="record",
                record_id="record",
                schema_id="test.record.v1",
                canonical_bytes=b"record",
                fingerprint=_digest(b"record"),
            ),
        ),
        complete_batch_fingerprint="0" * 64,
    )


def test_h1_decoder_fails_closed_for_an_unknown_applicability_schema() -> None:
    batch = _batch(applicability_schema="chiplog.other-issuance.v1")

    with pytest.raises(ValueError, match="exact issuance applicability"):
        decode_h1_completion_issuance(batch)


def test_h1_projection_does_not_reinterpret_another_v2_applicability() -> None:
    batch = _batch(applicability_schema="chiplog.other-issuance.v1")

    with pytest.raises(ValueError, match="exact issuance applicability"):
        h1_completion_exchange(batch)


def test_h1_decoder_requires_the_exact_applicability_fingerprint() -> None:
    batch = _batch(applicability_schema=SCHEMA).model_copy(
        update={
            "authentication": _batch(applicability_schema=SCHEMA).authentication.model_copy(
                update={"applicability_fingerprint": "0" * 64}
            )
        }
    )

    with pytest.raises(ValueError, match="exact issuance applicability"):
        decode_h1_completion_issuance(batch)


def test_h1_issuance_schema_is_a_closed_versioned_identifier() -> None:
    assert SCHEMA == "chiplog.composition.h1-completion-issuance.v1"


def test_h1_capture_requires_an_explicit_invocation_proof() -> None:
    assert H1CompletionCaptureV1.model_fields["invocation"].is_required()
