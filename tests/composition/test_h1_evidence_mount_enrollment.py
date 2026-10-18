"""The auxiliary H1 evidence role is an explicit enrolled capability."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.composition.h1_launch_enrollment import (
    H1EvidenceMountError,
    InstalledH1Slot,
    _open_installed_h1_launch,
    _provision_h1_enrollment,
    _provision_h1_evidence_mount,
)
from chiplog.platform.authority_gate import AuthorityGate
from tests.support.composition.h1_evidence_mount_enrollment import (
    _TENANT,
    _slot,
)


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
        journal = mount._open_existing_evidence_journal()
        assert isinstance(journal, IndependentTenantDecisionJournal)
        try:
            assert journal.authority_gate is gate
        finally:
            journal.close()
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


def test_failed_evidence_opener_closes_existing_journal_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot = _enrolled_slot(tmp_path)
    opened: list[IndependentTenantDecisionJournal] = []
    descriptors: list[int] = []
    original = IndependentTenantDecisionJournal.physical_sources
    sentinel = RuntimeError("injected post-open source read failure")

    def fail_after_open(
        journal: IndependentTenantDecisionJournal,
    ) -> tuple[tuple[str, int, int], tuple[str, int, int], tuple[str, int, int]]:
        if journal._existing_only:
            opened.append(journal)
            descriptors.extend(journal._existing_descriptors.values())
            assert journal._parent_descriptor is not None
            descriptors.append(journal._parent_descriptor)
            raise sentinel
        return original(journal)

    with _open_installed_h1_launch(slot) as launch:
        mount = launch.open_enrolled_evidence_mount(AuthorityGate.for_database(slot.database_path))
        monkeypatch.setattr(IndependentTenantDecisionJournal, "physical_sources", fail_after_open)
        try:
            with pytest.raises(
                H1EvidenceMountError, match="existing evidence journal is unavailable"
            ) as rejected:
                mount._open_existing_evidence_journal()
            assert rejected.value.__cause__ is sentinel
            assert len(opened) == 1
            assert len(descriptors) == 4
            for descriptor in descriptors:
                with pytest.raises(OSError) as closed:
                    os.fstat(descriptor)
                assert closed.value.errno == errno.EBADF
        finally:
            for journal in opened:
                journal.close()
