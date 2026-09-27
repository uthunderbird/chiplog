"""The absent H1 broker proof owner cannot be replaced with caller evidence."""

from __future__ import annotations

import pytest


def test_uninstalled_invocation_source_rejects_completion_and_dto_shaped_inputs() -> None:
    """No session, identity, principal, or trust claim can mint a live invocation."""
    from chiplog.composition.h1_live_invocation_source import (
        H1LiveInvocationSource,
        H1LiveInvocationSourceUnavailable,
    )

    source = H1LiveInvocationSource()
    forged = {
        "session": object(),
        "identity": {"tenant_id": "forged", "command_id": "forged"},
        "principal_bytes": b'{"principal_id":"forged"}',
        "observed_trust": {"phase": "ACTIVE"},
    }

    with pytest.raises(H1LiveInvocationSourceUnavailable, match="not installed"):
        source.capture_invocation(session=forged["session"], identity=forged["identity"])

    assert source.issued_count == 0


def test_invocation_source_names_required_broker_trust_owner_proof() -> None:
    """The extension must authenticate a completed B call, not trust a DTO."""
    from chiplog.composition.h1_live_invocation_source import (
        H1InvocationOwnerExtension,
        H1LiveInvocationSource,
    )

    extension = H1LiveInvocationSource().required_owner_extension

    assert isinstance(extension, H1InvocationOwnerExtension)
    assert extension.operation == "agent_loop.complete_acceptance.v2"
    assert extension.reason == "no installed H1 completion broker/trust proof owner"
    assert extension.required_observations == (
        "exact enrolled B session and completion exchange",
        "canonical authenticated principal bytes",
        "immutable observed trust evidence",
        "broker epoch/session/runtime generation",
    )
    assert extension.requires_shared_gate_currentness is True
    assert extension.requires_one_use_unpredictable_issuer is True
