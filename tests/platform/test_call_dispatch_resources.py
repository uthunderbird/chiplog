"""Fixed call-policy resource issuance shares original custody and revocation."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.composition.r14_call_dispatch_policy import policy_reference as call_policy
from chiplog.composition.r16_dispatch_custody import HistoricalDispatchCustody
from chiplog.composition.r16_dispatch_registry import (
    HermeticDispatchResources,
    historical_grant_reference,
    policy_reference,
)
from chiplog.platform.authority_gate import AuthorityGate


def test_fixed_call_grant_preserves_original_policy_bytes_and_shared_custody(
    tmp_path: Path,
) -> None:
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1)
    resources.bind(AuthorityGate(tmp_path / "authority.db"))
    original = resources.observe()
    call = resources.observe_call()
    assert resources.observe() == original
    assert resources.verify_current(original) and resources.verify_current(call)
    legacy_grant, call_grant = json.loads(original.grant_bytes), json.loads(call.grant_bytes)
    assert legacy_grant["policy"] == policy_reference().model_dump(mode="json")
    assert call_grant["policy"] == call_policy().model_dump(mode="json")
    assert resources.grant_identities == (
        legacy_grant["grant_id"],
        legacy_grant["grant_id"] + "/initialized-call.v1",
    )
    assert call_grant["grant_id"] == resources.grant_identities[1]
    assert json.loads(call.credential_bytes)["grant_id"] == call_grant["grant_id"]
    assert legacy_grant["cap"] == call_grant["cap"] == resources.cap == 1
    assert original.clock_epoch == call.clock_epoch
    assert original.endpoint_bytes == call.endpoint_bytes
    assert resources.recipient(call).canonical_address == b"hermetic://effects/hermetic-principal"


def test_policy_and_grant_substitution_cannot_reuse_signed_observation(tmp_path: Path) -> None:
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1)
    resources.bind(AuthorityGate(tmp_path / "authority.db"))
    observed = resources.observe_call()
    for field, value in (
        ("policy", policy_reference().model_dump(mode="json")),
        ("policy", {"unknown": True}),
        ("grant_id", "foreign"),
    ):
        grant = {**json.loads(observed.grant_bytes), field: value}
        changed = replace(observed, grant_bytes=json.dumps(grant).encode())
        assert not resources.verify_current(changed)
        assert not resources.verify_historical(changed)


def test_call_and_legacy_grants_share_revocation_and_original_epoch(tmp_path: Path) -> None:
    path = tmp_path / "custody.json"
    gate = AuthorityGate(tmp_path / "authority.db")
    original = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=path)
    original.bind(gate)
    observations = (original.observe(), original.observe_call())
    reopened = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=path)
    reopened.bind(gate)
    assert reopened.grant_identities == original.grant_identities
    for observation in observations:
        assert reopened.verify_historical(observation)
        assert not reopened.verify_current(observation)
    reopened.revoke()
    for observation in observations:
        assert not original.verify_current(observation)
        assert original.verify_historical(observation)
    assert not original.verify_current(original.observe_call())


def test_registered_grant_reference_preserves_original_bytes_and_is_current_only(
    tmp_path: Path,
) -> None:
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1)
    resources.bind(AuthorityGate(tmp_path / "authority.db"))
    observation = resources.observe()

    current = resources.grant_reference(observation)
    assert current.subject_id == resources.grant_identity
    assert current.fingerprint == hashlib.sha256(observation.grant_bytes).hexdigest()
    resources.revoke()
    with pytest.raises(ValueError, match="no longer current"):
        resources.grant_reference(observation)

    historical = historical_grant_reference(
        HistoricalDispatchCustody(
            issuer_key=resources._key,
            credential_id=resources._credential_id,
            grant_id=resources.grant_identity,
            cap=resources.cap,
        ),
        observation,
    )
    assert historical == current
