"""Public consumer checks the independently versioned outcome boundary."""

import pytest
from pydantic import ValidationError

from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchEvidenceV2


def test_evidence_is_closed_and_keeps_binary_preimage() -> None:
    value = {
        "evidence_id": "source/1",
        "source_identity": "original-source",
        "transmission": {"subject_id": "child", "head": "child/1", "fingerprint": "a"},
        "raw_bytes": b"\x00\xff",
        "raw_digest": "a" * 64,
        "observation": "PROVIDER_RECEIPT",
        "occurred_members": (),
        "permanently_incapable_members": (),
    }
    evidence = DispatchEvidenceV2.model_validate(value)
    assert DispatchEvidenceV2.model_validate_json(evidence.canonical_bytes()) == evidence
    with pytest.raises(ValidationError):
        DispatchEvidenceV2.model_validate({**value, "allow_send": True})
    with pytest.raises(ValidationError):
        DispatchEvidenceV2.model_validate({**value, "observation": "ABSENCE_PROVES_NO_EFFECT"})


def test_outcome_route_partition_is_closed() -> None:
    from dataclasses import replace

    from chiplog.architecture.r7_runtime import R16_DISPATCH_PRODUCTION_MANIFEST
    from chiplog.platform.r7_runtime import OwnerProcessFailure, OwnerProcessIdentity, _owner_module

    owner = next(
        row for row in R16_DISPATCH_PRODUCTION_MANIFEST.owners if row.owner_id == "effects"
    )
    identity = OwnerProcessIdentity(
        "tenant", 1, "effects", "generation", "session", owner.capability_ids
    )
    assert _owner_module(identity) == "chiplog.capabilities.effects._dispatch_process"
    for capabilities in (owner.capability_ids[:-1], (*owner.capability_ids, "effects.unknown")):
        with pytest.raises(OwnerProcessFailure):
            _owner_module(replace(identity, capability_ids=capabilities))
    with pytest.raises((OwnerProcessFailure, KeyError)):
        _owner_module(replace(identity, owner_id="unregistered"))
