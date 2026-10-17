"""Public wire shape only; authentication, atomicity and ACK remain runtime work."""

import pytest
from pydantic import ValidationError

from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead,
    OriginSelection,
    ProviderRecipient,
)
from chiplog.capabilities.agent_loop.ingress_client_contracts import (
    NativeIngressIdentityV1,
    NativeIngressReceiptV1,
    NativeIngressSubmissionV1,
    NativeTransportWitnessV1,
)


@pytest.mark.parametrize("channel", ["CLI", "TELEGRAM"])
def test_public_channel_wire_retains_exact_non_utf8_raw_bytes(channel: str) -> None:
    identity = NativeIngressIdentityV1.model_validate(
        dict(tenant_id="tenant", channel=channel, registered_source_id="source", replay_id="1")
    )
    head = ExactHead(identity="registered", head="selected", fingerprint="a" * 64)
    raw = b'\xff\x00 { "message": "hello" }\n'
    submission = NativeIngressSubmissionV1(
        identity=identity,
        raw_bytes=raw,
        witness=NativeTransportWitnessV1(
            issuer_id="trusted-adapter", source_binding=head, evidence=b"opaque-proof"
        ),
    )
    assert NativeIngressSubmissionV1.model_validate_json(submission.canonical_bytes()) == submission
    receipt = NativeIngressReceiptV1(
        identity=identity,
        inbox_id="inbox",
        run_id="run",
        raw_bytes=raw,
        origin=OriginSelection(
            ingress_binding=head,
            recipient=ProviderRecipient(
                provider_id="provider",
                account_id="account",
                recipient_id="recipient",
                endpoint=head,
                credential_binding=head,
                canonical_address=b"exact-address",
            ),
        ),
        selected_record=head,
        commit_sequence=1,
    )
    assert NativeIngressReceiptV1.model_validate_json(receipt.canonical_bytes()) == receipt
    assert receipt.raw_bytes == raw
    assert "ack" not in NativeIngressReceiptV1.model_fields
    with pytest.raises(ValidationError):
        NativeIngressReceiptV1.model_validate({**receipt.model_dump(), "commit_sequence": 0})
    with pytest.raises(ValidationError):
        NativeTransportWitnessV1.model_validate(
            {**submission.witness.model_dump(), "private_key": "not-a-wire-field"}
        )


def test_source_and_witness_cannot_be_omitted_from_submission() -> None:
    with pytest.raises(ValidationError):
        NativeIngressSubmissionV1.model_validate(
            {
                "identity": {"tenant_id": "tenant", "channel": "CLI", "replay_id": "1"},
                "raw_bytes": b"hello",
            }
        )
