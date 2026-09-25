"""Strict private owner-prefix locator retained only by H1 V2 selection."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from chiplog.composition.h1_preseal_contracts import H1OwnerAsOfV1


def test_owner_asof_retains_explicit_empty_prefix_in_canonical_bytes() -> None:
    locator = H1OwnerAsOfV1(tenant_id="hermetic-tenant", owner_head=None)

    assert locator.canonical_bytes() == (
        b'{"kind":"H1_OWNER_AS_OF_V1","owner_head":null,"tenant_id":"hermetic-tenant"}'
    )
    assert H1OwnerAsOfV1.model_validate_json(locator.canonical_bytes()) == locator


@pytest.mark.parametrize(
    "raw",
    (
        b'{"kind":"H1_OWNER_AS_OF_V1","tenant_id":"hermetic-tenant"}',
        b'{"kind":"H1_OWNER_AS_OF_V1","owner_head":"not-a-digest","tenant_id":"hermetic-tenant"}',
        b'{"kind":"H1_OWNER_AS_OF_V1","owner_head":null,"tenant_id":"hermetic-tenant","extra":true}',
    ),
)
def test_owner_asof_rejects_missing_wrong_or_extra_fields(raw: bytes) -> None:
    with pytest.raises(ValidationError):
        H1OwnerAsOfV1.model_validate_json(raw)
