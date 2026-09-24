"""Read-only historical R16 custody bindings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chiplog.composition.r16_dispatch_custody import load_existing_historical_custody
from chiplog.composition.r16_dispatch_registry import (
    HermeticDispatchResources,
    ResourceObservation,
    historical_recipient,
    verify_historical_observation,
)
from chiplog.platform.authority_gate import AuthorityGate


def _original_observation(tmp_path: Path) -> tuple[Path, ResourceObservation]:
    path = tmp_path / "custody.json"
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=path)
    resources.bind(AuthorityGate(tmp_path / "authority.db"))
    return path, resources.observe()


def test_historical_custody_loads_existing_binding_without_provider_or_creation(
    tmp_path: Path,
) -> None:
    absent = tmp_path / "missing" / "custody.json"
    with pytest.raises(FileNotFoundError):
        load_existing_historical_custody(absent)
    assert not absent.parent.exists()

    path, observation = _original_observation(tmp_path)
    custody = load_existing_historical_custody(path)
    assert verify_historical_observation(custody, observation)
    assert (
        historical_recipient(custody, observation).credential_binding.subject_id
        == custody.credential_id
    )


def test_historical_custody_rejects_tampering_and_does_not_accept_another_key(
    tmp_path: Path,
) -> None:
    path, observation = _original_observation(tmp_path)
    other_path, _ = _original_observation(tmp_path / "other")
    other = load_existing_historical_custody(other_path)
    assert not verify_historical_observation(other, observation)
    with pytest.raises(ValueError, match="unauthentic"):
        historical_recipient(other, observation)

    retained = json.loads(path.read_text())
    retained["issuer_key"] = "0" * 64
    path.write_text(json.dumps(retained, sort_keys=True, separators=(",", ":")))
    custody = load_existing_historical_custody(path)
    assert not verify_historical_observation(custody, observation)


def test_historical_custody_requires_private_canonical_regular_file(tmp_path: Path) -> None:
    path, _ = _original_observation(tmp_path)
    alias = tmp_path / "alias.json"
    alias.symlink_to(path)
    with pytest.raises(OSError):
        load_existing_historical_custody(alias)

    retained = json.loads(path.read_text())
    path.write_text(json.dumps(retained, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(ValueError, match="canonical"):
        load_existing_historical_custody(path)

    path.write_text(json.dumps(retained, sort_keys=True, separators=(",", ":")))
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        load_existing_historical_custody(path)


def test_historical_binding_survives_current_revocation_and_resource_expiry(tmp_path: Path) -> None:
    path, observation = _original_observation(tmp_path)
    custody = load_existing_historical_custody(path)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=path)
    resources.bind(AuthorityGate(tmp_path / "new-authority.db"))
    resources.revoke()

    assert not resources.verify_current(observation)
    assert verify_historical_observation(custody, observation)
    assert historical_recipient(custody, observation).canonical_address == (
        b"hermetic://effects/hermetic-principal"
    )


def test_resources_retains_only_the_trusted_custody_locator(tmp_path: Path) -> None:
    custody_path = tmp_path / "custody.json"
    durable = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody_path)
    ephemeral = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1)
    assert durable._custody_path == custody_path
    assert ephemeral._custody_path is None
