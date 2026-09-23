"""Public loop-consumer shape checks, not evidence of authentication or sending."""

import pytest
from pydantic import TypeAdapter, ValidationError

from chiplog.capabilities.agent_loop.delivery_contracts import (
    DeliveryPublicationResult,
    EndpointSelection,
    ExactHead,
    ModelSelection,
    OriginSelection,
    ProviderRecipient,
)


def test_origin_requires_exact_ingress_but_model_selection_cannot_forge_origin() -> None:
    head = ExactHead(identity="endpoint", head="head", fingerprint="a" * 64)
    recipient = ProviderRecipient(
        provider_id="telegram",
        account_id="account",
        recipient_id="recipient",
        endpoint=head,
        canonical_address=b"\xff\x00exact-address",
        credential_binding=head,
    )
    origin = OriginSelection(ingress_binding=head, recipient=recipient)
    selected = ModelSelection(recipient=recipient)
    adapter: TypeAdapter[OriginSelection | ModelSelection] = TypeAdapter(EndpointSelection)
    assert adapter.validate_python(origin.model_dump()) == origin
    assert adapter.validate_json(origin.canonical_bytes()) == origin
    assert adapter.validate_python(selected.model_dump()) == selected
    invalid_origin = origin.model_dump()
    del invalid_origin["ingress_binding"]
    with pytest.raises(ValidationError):
        adapter.validate_python(invalid_origin)
    with pytest.raises(ValidationError):
        adapter.validate_python({**selected.model_dump(), "ingress_binding": head.model_dump()})


@pytest.mark.parametrize("value", [{}, {"kind": "ORIGIN"}, {"kind": "ALIAS"}])
def test_durable_selection_cannot_be_omitted_or_use_an_alias(value: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(EndpointSelection).validate_python(value)


def test_published_result_requires_a_durable_head() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(DeliveryPublicationResult).validate_python(
            {"disposition": "PUBLISHED", "publication_head": None}
        )
