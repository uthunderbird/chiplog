"""Exact retained broker sources at the terminal-fault owner boundary."""

from __future__ import annotations

from typing import Literal

import pytest
from tests.support.fault_observer_sources import fixture

from chiplog.capabilities.agent_loop.terminal_fault_observer_sources import (
    FaultObserverSourceIntegrityError,
    decode_fault_capture,
    decode_fault_issuance,
    decode_fault_observation,
    decode_fault_observer_registry,
    validate_fault_observer_sources,
)
from chiplog.capabilities.agent_loop.terminal_recovery_fault_contracts import (
    FaultBrokerSourceRefV1,
    RegisteredRecoveryFaultObserverAuthorityV1,
)


@pytest.mark.parametrize("version", ("v2", "v3"))
async def test_owner_mirror_decodes_every_real_broker_body_with_byte_parity(
    version: Literal["v2", "v3"],
) -> None:
    request, retained = await fixture(version=version)
    bodies = (
        (decode_fault_capture, retained.ordered_captures[0]),
        (decode_fault_observer_registry, retained.observer_registry),
        (decode_fault_observation, retained.observation),
        (decode_fault_issuance, retained.issuance),
    )

    for decode, source in bodies:
        decoded = decode(source.canonical_source_bytes)
        assert decoded.canonical_bytes() == source.canonical_source_bytes
        assert decoded.schema_id == source.reference.schema_id
        assert source.reference.source_id == (
            source.reference.schema_id + ":" + source.reference.fingerprint
        )
    validate_fault_observer_sources(request, retained)


async def test_retained_sources_reject_model_copy_forged_reference() -> None:
    request, retained = await fixture()
    forged_ref = retained.observer_registry.reference.model_copy(update={"source_id": "forged"})
    forged_registry = retained.observer_registry.model_copy(update={"reference": forged_ref})
    forged_retained = retained.model_copy(update={"observer_registry": forged_registry})
    authority = request.authority
    assert isinstance(authority, RegisteredRecoveryFaultObserverAuthorityV1)
    forged_authority = authority.model_copy(
        update={"observer_registry": FaultBrokerSourceRefV1.model_validate(forged_ref.model_dump())}
    )
    forged_request = request.model_copy(update={"authority": forged_authority})

    with pytest.raises(FaultObserverSourceIntegrityError, match="retained observer sources"):
        validate_fault_observer_sources(forged_request, forged_retained)
