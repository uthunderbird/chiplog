"""Administrative enrollment and existing-only opening for post-seal recovery."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import cast

import pytest

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.composition.h1_launch_enrollment import (
    _RECOVERY_BODY,
    ExpectedH1Genesis,
    H1RecoveryMountError,
    InstalledH1Slot,
    _open_installed_h1_launch,
    _provision_h1_enrollment,
    _provision_h1_evidence_mount,
    _provision_h1_recovery_mount,
)
from chiplog.platform.authority_gate import AuthorityGate
from tests.composition.test_h1_evidence_mount_enrollment import _slot


def _enrolled_slot(tmp_path: Path) -> tuple[InstalledH1Slot, ExpectedH1Genesis]:
    slot, expected, registry = _slot(tmp_path)
    _provision_h1_enrollment(slot, expected, registry)
    _provision_h1_evidence_mount(slot, expected)
    _provision_h1_recovery_mount(slot, expected)
    return slot, expected


def test_admin_provision_issues_fixed_existing_only_mount(tmp_path: Path) -> None:
    slot, _expected = _enrolled_slot(tmp_path)
    custody = slot.root / slot.custody_name
    assert (custody / "h1-post-seal-recovery-enrollment.json").exists()
    gate = AuthorityGate.for_database(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        mount = launch.open_enrolled_recovery_mount(gate)
        assert mount.tenant_id == slot.tenant_id
        assert mount.authority_gate is gate
        assert mount.journal_instance_id.startswith("h1-post-seal-recovery:")
        journal = cast(IndependentTenantDecisionJournal, mount._open_existing_recovery_journal())
        assert journal.authority_gate is gate
        mount.close()
        with pytest.raises(H1RecoveryMountError, match=r"closed|poisoned"):
            mount.assert_current()


def test_missing_recovery_marker_never_self_provisions_at_open(tmp_path: Path) -> None:
    slot, _expected = _enrolled_slot(tmp_path)
    custody = slot.root / slot.custody_name
    (custody / "h1-post-seal-recovery-enrollment.json").unlink()
    before = tuple(sorted(path.name for path in custody.iterdir()))
    with (
        _open_installed_h1_launch(slot) as launch,
        pytest.raises(H1RecoveryMountError, match="absent"),
    ):
        launch.open_enrolled_recovery_mount(AuthorityGate.for_database(slot.database_path))
    assert tuple(sorted(path.name for path in custody.iterdir())) == before


def test_admin_provision_rejects_empty_preseeded_known_key(tmp_path: Path) -> None:
    slot, expected = _enrolled_slot(tmp_path)
    custody = slot.root / slot.custody_name
    for name in (
        "h1-post-seal-recovery-enrollment.json",
        _RECOVERY_BODY,
        _RECOVERY_BODY + ".key",
        _RECOVERY_BODY + ".head",
        _RECOVERY_BODY + ".lock",
    ):
        (custody / name).unlink()
    (custody / _RECOVERY_BODY).touch(mode=0o600)
    (custody / (_RECOVERY_BODY + ".key")).write_bytes(b"k" * 32)
    (custody / (_RECOVERY_BODY + ".key")).chmod(0o600)
    with pytest.raises(H1RecoveryMountError, match="already exists"):
        _provision_h1_recovery_mount(slot, expected)
    assert (custody / (_RECOVERY_BODY + ".key")).read_bytes() == b"k" * 32


def test_restart_rejects_paired_body_key_head_lock_substitution_without_repair(
    tmp_path: Path,
) -> None:
    slot, _ = _enrolled_slot(tmp_path)
    gate = AuthorityGate.for_database(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        mount = launch.open_enrolled_recovery_mount(gate)
        journal = cast(IndependentTenantDecisionJournal, mount._open_existing_recovery_journal())
        journal.append(b'{"real":true}', None)
        journal.close()

    fake_dir = tmp_path / "fake"
    fake_dir.mkdir(mode=0o700)
    fake_gate = AuthorityGate.for_database(fake_dir / "fake.sqlite3")
    with fake_gate.hold():
        fake = IndependentTenantDecisionJournal.for_authority_bundle(
            fake_dir / _RECOVERY_BODY, authority_gate=fake_gate
        )
        fake.append(b'{"forged":true}', None)
    custody = slot.root / slot.custody_name
    for suffix in ("", ".key", ".head", ".lock"):
        replacement = custody / ("replacement" + suffix)
        shutil.copyfile(fake_dir / (_RECOVERY_BODY + suffix), replacement)
        replacement.chmod(0o600)
        os.replace(replacement, custody / (_RECOVERY_BODY + suffix))
    replaced = {
        suffix: (custody / (_RECOVERY_BODY + suffix)).read_bytes()
        for suffix in ("", ".key", ".head", ".lock")
    }
    marker = (custody / "h1-post-seal-recovery-enrollment.json").read_bytes()
    with (
        _open_installed_h1_launch(slot) as launch,
        pytest.raises(H1RecoveryMountError, match=r"changed|foreign"),
    ):
        launch.open_enrolled_recovery_mount(gate)
    assert (custody / "h1-post-seal-recovery-enrollment.json").read_bytes() == marker
    assert {
        suffix: (custody / (_RECOVERY_BODY + suffix)).read_bytes()
        for suffix in ("", ".key", ".head", ".lock")
    } == replaced
