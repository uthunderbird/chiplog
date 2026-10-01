"""Closed schema routing for the retained dispatch-V2 H1 issuance wire."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest

from chiplog.capabilities.effects.h1_scoped_preparation_v2_contracts import (
    H1ScopedDeliveryOwnerCallV2,
    PreparedH1ScopedDeliveryV2,
)
from chiplog.composition import h1_completion_issuance as issuance


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def test_v4_dispatch_accepts_only_its_matching_closed_outer_and_inner_schema() -> None:
    assert issuance.dispatch_h1_completion_issuance_schema(
        issuance.V4_SCHEMA, _canonical({"schema_id": issuance.V4_SCHEMA})
    ) == "V4"

    for outer, inner in (
        (issuance.V3_SCHEMA, issuance.V4_SCHEMA),
        (issuance.V4_SCHEMA, issuance.V3_SCHEMA),
        (issuance.V4_SCHEMA, "chiplog.composition.h1-completion-issuance.v9"),
    ):
        with pytest.raises(ValueError, match="outer and inner"):
            issuance.dispatch_h1_completion_issuance_schema(
                outer, _canonical({"schema_id": inner})
            )


@pytest.mark.parametrize(
    "raw",
    (
        b'{"schema_id":"chiplog.composition.h1-completion-issuance.v4", "extra":true}',
        b'{"schema_id":"chiplog.composition.h1-completion-issuance.v4","schema_id":"chiplog.composition.h1-completion-issuance.v4"}',
        _canonical({"schema_id": issuance.V4_SCHEMA, "extra": True}),
    ),
)
def test_v4_dispatch_rejects_noncanonical_duplicate_or_extra_members(raw: bytes) -> None:
    with pytest.raises(ValueError):
        issuance.dispatch_h1_completion_issuance_schema(issuance.V4_SCHEMA, raw)


def test_v4_issuance_freezes_the_exact_dispatch_v2_contract_members() -> None:
    assert issuance.H1CompletionIssuanceV4.model_fields["schema_id"].default == issuance.V4_SCHEMA
    assert (
        issuance.H1CompletionIssuanceV4.model_fields["effects_call"].annotation
        is H1ScopedDeliveryOwnerCallV2
    )
    assert (
        issuance.H1CompletionIssuanceV4.model_fields["effects_result"].annotation
        is PreparedH1ScopedDeliveryV2
    )


def test_v4_decoder_does_not_admit_a_schema_probe_as_an_issuance() -> None:
    with pytest.raises(ValueError):
        issuance.decode_h1_completion_issuance_v4(_canonical({"schema_id": issuance.V4_SCHEMA}))


@pytest.mark.parametrize(
    "validator",
    (issuance._require_v4_scope_issue_exchange, issuance._require_v4_scope_current_exchange),
)
def test_v4_scope_frame_rejects_a_forged_role_before_wire_decode(validator: Any) -> None:
    forged = SimpleNamespace(role="effects", sent=object())

    with pytest.raises(ValueError, match="substituted route"):
        validator(cast(Any, forged), cast(Any, object()))


def test_v1_schema_route_and_schema_bytes_remain_stable() -> None:
    v1_wire = _canonical({"schema_id": issuance.SCHEMA})

    assert issuance.dispatch_h1_completion_issuance_schema(issuance.SCHEMA, v1_wire) == "V1"
    assert v1_wire == b'{"schema_id":"chiplog.composition.h1-completion-issuance.v1"}'
