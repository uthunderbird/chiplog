from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from chiplog.architecture.r7_runtime import R8_PRODUCTION_MANIFEST, bind_execution_manifest
from chiplog.architecture.r8_execution_identity import resolve_runtime_identity
from chiplog.composition.r7_supervisor import R7RuntimeSupervisor, RuntimeAdmissionEvidence
from chiplog.platform.authority_ledger import (
    AuthorityLedgerConflict,
    BrokerAuthorityLedger,
    RuntimeGenerationIdentity,
)


def _identity(generation: str = "generation-1") -> RuntimeGenerationIdentity:
    return RuntimeGenerationIdentity(
        generation_id=generation,
        mode="development",
        source_snapshot_digest="a" * 64,
        selected_manifest_fingerprint="b" * 64,
    )


def test_generation_identity_is_atomic_required_and_legacy_epochs_remain_explicit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "broker.sqlite3"
    ledger = BrokerAuthorityLedger(path)
    legacy = ledger.allocate_epoch("tenant", "endpoint-legacy", "key-legacy")
    assert ledger.read_generation_identity("tenant", legacy) is None
    bound = ledger.allocate_epoch(
        "tenant", "endpoint-bound", "key-bound", generation_identity=_identity()
    )
    assert ledger.read_generation_identity("tenant", bound) == _identity()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "DELETE FROM broker_generation_identities WHERE tenant_id = ? AND epoch = ?",
            ("tenant", bound),
        )
    with pytest.raises(AuthorityLedgerConflict, match="required"):
        ledger.read_generation_identity("tenant", bound)
    with pytest.raises(AuthorityLedgerConflict, match="unknown"):
        ledger.read_generation_identity("tenant", 999)


def test_identity_insert_failure_rolls_back_epoch_transition(tmp_path: Path) -> None:
    path = tmp_path / "broker.sqlite3"
    ledger = BrokerAuthorityLedger(path)
    first = ledger.allocate_epoch("tenant", "endpoint-1", "key-1")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_identity BEFORE INSERT ON broker_generation_identities "
            "BEGIN SELECT RAISE(ABORT, 'fixture reject'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="fixture reject"):
        ledger.allocate_epoch("tenant", "endpoint-2", "key-2", generation_identity=_identity())
    assert ledger.current_epoch("tenant") == first


class _Admission:
    def authenticate_runtime_admission(
        self, tenant_id: str, _runtime: object
    ) -> RuntimeAdmissionEvidence:
        return RuntimeAdmissionEvidence(tenant_id, "db", "g", "t", "j", "m", True)


def test_source_drift_rejects_before_current_epoch_allocation(tmp_path: Path) -> None:
    root = tmp_path / "source"
    shutil.copytree(Path(__file__).parents[2] / "src/chiplog", root / "src/chiplog")
    identity = resolve_runtime_identity(root)
    manifest = bind_execution_manifest(R8_PRODUCTION_MANIFEST, identity)
    (root / "src/chiplog/drift.py").write_text("VALUE = 'changed'\n")
    path = tmp_path / "broker.sqlite3"
    supervisor = R7RuntimeSupervisor(
        "tenant", path, manifest, _Admission(), execution_identity=identity, source_root=root
    )
    with pytest.raises(RuntimeError, match="drift"):
        supervisor.start_generation("generation")
    assert BrokerAuthorityLedger(path).current_epoch("tenant") is None
