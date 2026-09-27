"""The auxiliary H1 evidence role is an explicit enrolled capability."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from chiplog.composition.h1_launch_enrollment import (
    ExpectedH1Genesis,
    H1EvidenceMountError,
    InstalledH1Slot,
    _open_installed_h1_launch,
    _provision_h1_enrollment,
    _provision_h1_evidence_mount,
)
from chiplog.composition.h1_registration_custody import H1RegistrationCustodyV1
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.r7_trust_durability import TrustDurabilityObservation

_DEPLOYMENT = "evidence-installed-deployment"
_TENANT = "evidence-installed-tenant"
_DATABASE = "evidence-installed-database"


class _ActiveTrust:
    def __init__(self, digest: str) -> None:
        self._observation = TrustDurabilityObservation(
            _TENANT, _DATABASE, digest, "trust", "materialization", "ACTIVE"
        )

    def verify(self) -> TrustDurabilityObservation:
        return self._observation


def _slot(tmp_path: Path) -> tuple[InstalledH1Slot, ExpectedH1Genesis, H1RegistrationCustodyV1]:
    root = tmp_path / "installed"
    root.mkdir(mode=0o700)
    (root / "custody").mkdir(mode=0o700)
    database = root / "runtime.sqlite3"
    database.write_bytes(b"sqlite placeholder")
    database.chmod(0o600)
    expected = ExpectedH1Genesis(_DEPLOYMENT, _TENANT, _DATABASE)
    slot = InstalledH1Slot(
        root, "custody", database, _DEPLOYMENT, _TENANT, _DATABASE, _ActiveTrust(expected.digest)
    )
    registry = H1RegistrationCustodyV1.model_validate(
        {
            "schema_id": "chiplog.execution.h1-registration-custody.v1",
            "deployment_id": _DEPLOYMENT,
            "database_id": _DATABASE,
            "database_genesis_digest": expected.digest,
            "entries": [
                {
                    "tenant_id": _TENANT,
                    "principal_id": "principal",
                    "channel_id": "channel",
                    "registration_id": "registration",
                    "generation": 0,
                    "status": "ACTIVE",
                    "origin_recipient_id": "recipient",
                    "conversation_id": "conversation",
                    "visible_channels": ["channel"],
                }
            ],
        }
    )
    return slot, expected, registry


def _enrolled_slot(tmp_path: Path) -> InstalledH1Slot:
    slot, expected, registry = _slot(tmp_path)
    _provision_h1_enrollment(slot, expected, registry)
    _provision_h1_evidence_mount(slot, expected)
    return slot


def test_explicit_provision_reopen_preserves_role_instance_and_body_key_identity(
    tmp_path: Path,
) -> None:
    slot = _enrolled_slot(tmp_path)
    gate = AuthorityGate.for_database(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        mount = launch.open_enrolled_evidence_mount(gate)
        first = (mount.journal_instance_id, mount.body_identity, mount.key_identity)
        assert mount.tenant_id == _TENANT
        assert mount.authority_gate is gate
        mount.assert_current()
        assert mount._open_existing_evidence_journal().authority_gate is gate
    with _open_installed_h1_launch(slot) as launch:
        reopened = launch.open_enrolled_evidence_mount(gate)
        assert (
            reopened.journal_instance_id,
            reopened.body_identity,
            reopened.key_identity,
        ) == first


def test_launch_missing_evidence_marker_denies_without_creating_role_sidecars(
    tmp_path: Path,
) -> None:
    slot, expected, registry = _slot(tmp_path)
    _provision_h1_enrollment(slot, expected, registry)
    before = tuple(sorted((slot.root / "custody").iterdir()))
    with (
        _open_installed_h1_launch(slot) as launch,
        pytest.raises(H1EvidenceMountError, match="absent"),
    ):
        launch.open_enrolled_evidence_mount(AuthorityGate.for_database(slot.database_path))
    assert tuple(sorted((slot.root / "custody").iterdir())) == before


@pytest.mark.parametrize(
    "name", ("h1-delivery-evidence", "h1-delivery-evidence.key", "h1-delivery-evidence.head")
)
def test_recovery_rejects_missing_role_storage_without_repair(tmp_path: Path, name: str) -> None:
    slot = _enrolled_slot(tmp_path)
    target = slot.root / "custody" / name
    target.unlink()
    before = tuple(sorted(path.name for path in (slot.root / "custody").iterdir()))
    with (
        _open_installed_h1_launch(slot) as launch,
        pytest.raises(H1EvidenceMountError, match="absent"),
    ):
        launch.open_enrolled_evidence_mount(AuthorityGate.for_database(slot.database_path))
    assert tuple(sorted(path.name for path in (slot.root / "custody").iterdir())) == before


def test_recovery_rejects_replaced_body_or_key_and_poisoned_mount(tmp_path: Path) -> None:
    slot = _enrolled_slot(tmp_path)
    gate = AuthorityGate.for_database(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        mount = launch.open_enrolled_evidence_mount(gate)
        replacement = slot.root / "replacement"
        replacement.write_bytes(b"replacement")
        replacement.chmod(0o600)
        os.replace(replacement, slot.root / "custody" / "h1-delivery-evidence")
        with pytest.raises(H1EvidenceMountError, match=r"changed|poisoned"):
            mount.assert_current()
        with pytest.raises(H1EvidenceMountError, match="poisoned"):
            mount.assert_current()


def test_foreign_marker_tenant_is_denied(tmp_path: Path) -> None:
    slot = _enrolled_slot(tmp_path)
    marker = slot.root / "custody" / "h1-delivery-evidence-enrollment.json"
    marker.write_bytes(marker.read_bytes().replace(_TENANT.encode(), b"foreign-tenant"))
    with _open_installed_h1_launch(slot) as launch, pytest.raises(H1EvidenceMountError):
        launch.open_enrolled_evidence_mount(AuthorityGate.for_database(slot.database_path))
