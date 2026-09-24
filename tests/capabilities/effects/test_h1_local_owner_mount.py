"""The H1 local receipt has one mounted effects route and no raw request entrypoint."""

from __future__ import annotations

from chiplog.capabilities.effects._h1_local_process import ROUTES, dispatch
from chiplog.capabilities.effects.h1_local_preparation_contracts import PREPARE_SCHEMA


def test_h1_local_owner_exposes_only_the_authenticated_envelope_route() -> None:
    assert (
        "effects.prepare_h1_local_commentary",
        "broker",
        "effects",
        "chiplog.effects.h1-local-commentary-owner-call.v1",
        "chiplog.effects.prepared-h1-local-commentary.v1",
    ) in ROUTES
    # A canonical inner request is deliberately not an ambient owner API.
    rejected = dispatch(
        "effects.prepare_h1_local_commentary", b'{"schema_id":"' + PREPARE_SCHEMA.encode() + b'"}'
    )
    assert rejected["failure"] == "PROTOCOL_REJECTED"


def test_h1_local_owner_has_no_send_or_provider_operation() -> None:
    assert dispatch("effects.commit_send", b"{}")["failure"] == "PROTOCOL_REJECTED"
    assert dispatch("effects.before_send", b"{}")["failure"] == "PROTOCOL_REJECTED"
