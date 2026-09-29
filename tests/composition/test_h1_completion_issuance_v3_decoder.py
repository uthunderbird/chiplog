"""Closed schema routing for the inert H1 SCOPED_V3 issuance wire."""

from __future__ import annotations

import json

import pytest

from chiplog.composition import h1_completion_issuance as issuance


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def test_v3_dispatch_requires_matching_closed_outer_and_inner_schema() -> None:
    schema = issuance.V3_SCHEMA

    assert issuance.dispatch_h1_completion_issuance_schema(
        schema, _canonical({"schema_id": schema})
    ) == "V3"

    for outer, inner in (
        (issuance.V2_SCHEMA, schema),
        (schema, issuance.V2_SCHEMA),
        (schema, "chiplog.composition.h1-completion-issuance.v9"),
    ):
        with pytest.raises(ValueError, match="outer and inner"):
            issuance.dispatch_h1_completion_issuance_schema(
                outer, _canonical({"schema_id": inner})
            )


@pytest.mark.parametrize(
    "raw",
    (
        b'{"schema_id":"chiplog.composition.h1-completion-issuance.v3", "extra":true}',
        b'{"schema_id":"chiplog.composition.h1-completion-issuance.v3","schema_id":"chiplog.composition.h1-completion-issuance.v3"}',
        _canonical({"schema_id": issuance.V3_SCHEMA, "extra": True}),
    ),
)
def test_v3_dispatch_rejects_noncanonical_duplicate_or_extra_members(raw: bytes) -> None:
    with pytest.raises(ValueError):
        issuance.dispatch_h1_completion_issuance_schema(issuance.V3_SCHEMA, raw)


def test_v3_decoder_does_not_admit_a_schema_probe_as_issuance() -> None:
    with pytest.raises(ValueError):
        issuance.decode_h1_completion_issuance_v3(_canonical({"schema_id": issuance.V3_SCHEMA}))
