"""Installed H1 enrollment is an explicit, immutable administrative act."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from chiplog.composition.h1_launch_enrollment import (
    ExpectedH1Genesis,
    H1LaunchEnrollmentError,
    InstalledH1Slot,
    _open_installed_h1_launch,
    _provision_h1_enrollment,
)
from chiplog.composition.h1_registration_custody import H1RegistrationCustodyV1
from chiplog.platform.r7_trust_durability import TrustDurabilityObservation

DEPLOYMENT = "installed-deployment"
TENANT = "installed-tenant"
DATABASE = "installed-database"


class _ActiveTrust:
    def __init__(self, digest: str, *, phase: str = "ACTIVE") -> None:
        self._observation = TrustDurabilityObservation(
            TENANT, DATABASE, digest, "trust", "materialization", phase
        )

    def verify(self) -> TrustDurabilityObservation:
        return self._observation


def _registry(digest: str) -> H1RegistrationCustodyV1:
    return H1RegistrationCustodyV1.model_validate(
        {
            "schema_id": "chiplog.execution.h1-registration-custody.v1",
            "deployment_id": DEPLOYMENT,
            "database_id": DATABASE,
            "database_genesis_digest": digest,
            "entries": [
                {
                    "tenant_id": TENANT,
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


def _slot(tmp_path: Path) -> tuple[InstalledH1Slot, ExpectedH1Genesis, H1RegistrationCustodyV1]:
    root = tmp_path / "installed"
    root.mkdir(mode=0o700, parents=True)
    custody = root / "custody"
    custody.mkdir(mode=0o700)
    database = root / "database.sqlite3"
    database.write_bytes(b"sqlite placeholder")
    database.chmod(0o600)
    expected = ExpectedH1Genesis(DEPLOYMENT, TENANT, DATABASE)
    slot = InstalledH1Slot(
        root, "custody", database, DEPLOYMENT, TENANT, DATABASE, _ActiveTrust(expected.digest)
    )
    return slot, expected, _registry(expected.digest)


def test_provision_then_reopen_pins_marker_database_and_custody(tmp_path: Path) -> None:
    slot, expected, registry = _slot(tmp_path)
    _provision_h1_enrollment(slot, expected, registry)

    marker = slot.root / "custody" / "h1-launch-enrollment.json"
    assert marker.exists()
    with _open_installed_h1_launch(slot) as launch:
        assert launch.database_path == slot.database_path
        assert launch.database_identity == (
            os.stat(slot.database_path).st_dev,
            os.stat(slot.database_path).st_ino,
        )
        assert (
            launch.custody.select(TENANT, "principal", "channel").registration_id == "registration"
        )
        launch.assert_current()

    with _open_installed_h1_launch(slot) as launch:
        launch.assert_current()


def test_provision_rejects_nonactive_or_wrong_explicit_genesis(tmp_path: Path) -> None:
    slot, expected, registry = _slot(tmp_path)
    wrong = ExpectedH1Genesis(DEPLOYMENT, "other-tenant", DATABASE)
    with pytest.raises(H1LaunchEnrollmentError, match="slot"):
        _provision_h1_enrollment(slot, wrong, registry)
    slot, expected, registry = _slot(tmp_path / "second")
    slot._trust._observation = TrustDurabilityObservation(
        TENANT, DATABASE, expected.digest, "t", "m", "BOOTSTRAP_REQUIRED"
    )
    with pytest.raises(H1LaunchEnrollmentError, match="ACTIVE"):
        _provision_h1_enrollment(slot, expected, registry)


def test_launch_refuses_incomplete_install_and_never_repairs(tmp_path: Path) -> None:
    slot, expected, registry = _slot(tmp_path)
    with (
        pytest.raises(H1LaunchEnrollmentError, match=r"lock|marker"),
        _open_installed_h1_launch(slot),
    ):
        pass
    # P0 registry alone is deliberately not an installed launch marker.
    from chiplog.composition.h1_registration_custody import (
        H1RegistrationCustody,
        _trusted_launcher_binding_for_canonical_runtime,
    )

    fd = os.open(slot.root / "custody", os.O_RDONLY | os.O_DIRECTORY)
    H1RegistrationCustody.install(
        _trusted_launcher_binding_for_canonical_runtime(fd, DEPLOYMENT, DATABASE, expected.digest),
        registry,
    )
    with (
        pytest.raises(H1LaunchEnrollmentError, match=r"lock|marker"),
        _open_installed_h1_launch(slot),
    ):
        pass


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_id":"chiplog.execution.h1-launch-enrollment.v1","schema_id":"x"}',
        b"{}",
        b"{" + b"x" * 16_385 + b"}",
    ],
)
def test_launch_refuses_malformed_duplicate_or_oversized_marker(tmp_path: Path, raw: bytes) -> None:
    slot, expected, registry = _slot(tmp_path)
    _provision_h1_enrollment(slot, expected, registry)
    marker = slot.root / "custody" / "h1-launch-enrollment.json"
    marker.unlink()
    marker.write_bytes(raw)
    marker.chmod(0o600)
    with pytest.raises(H1LaunchEnrollmentError), _open_installed_h1_launch(slot):
        pass


def test_clone_hardlink_and_live_replacement_are_refused_and_poison(tmp_path: Path) -> None:
    slot, expected, registry = _slot(tmp_path)
    _provision_h1_enrollment(slot, expected, registry)
    clone = slot.root / "clone.sqlite3"
    clone.write_bytes(slot.database_path.read_bytes())
    clone.chmod(0o600)
    changed = InstalledH1Slot(
        slot.root, "custody", clone, DEPLOYMENT, TENANT, DATABASE, slot._trust
    )
    with (
        pytest.raises(H1LaunchEnrollmentError, match=r"locator|path"),
        _open_installed_h1_launch(changed),
    ):
        pass
    with _open_installed_h1_launch(slot) as launch:
        replacement = slot.root / "replacement.sqlite3"
        replacement.write_bytes(b"replacement")
        replacement.chmod(0o600)
        os.replace(replacement, slot.database_path)
        with pytest.raises(H1LaunchEnrollmentError, match=r"poisoned|changed"):
            launch.assert_current()
        with pytest.raises(H1LaunchEnrollmentError, match="poisoned"):
            launch.assert_current()


def test_marker_hardlink_and_concurrent_launch_are_refused(tmp_path: Path) -> None:
    slot, expected, registry = _slot(tmp_path)
    _provision_h1_enrollment(slot, expected, registry)
    marker = slot.root / "custody" / "h1-launch-enrollment.json"
    os.link(marker, slot.root / "marker-copy")
    with pytest.raises(H1LaunchEnrollmentError, match="private"), _open_installed_h1_launch(slot):
        pass
    (slot.root / "marker-copy").unlink()
    with (
        _open_installed_h1_launch(slot),
        pytest.raises(H1LaunchEnrollmentError, match="locked"),
        _open_installed_h1_launch(slot),
    ):
        pass


def test_final_fsync_failure_returns_no_capability_but_valid_later_launch_needs_no_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected, registry = _slot(tmp_path)
    actual_fsync = os.fsync
    calls = 0

    def fail_final(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls >= 6:
            raise OSError("final fsync fails")
        actual_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_final)
    with pytest.raises(H1LaunchEnrollmentError, match="persistence"):
        _provision_h1_enrollment(slot, expected, registry)
    monkeypatch.setattr(os, "fsync", actual_fsync)
    # Valid observed files are readable later; launch must not write or repair them.
    with _open_installed_h1_launch(slot) as launch:
        launch.assert_current()
