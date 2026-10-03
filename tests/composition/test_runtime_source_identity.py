from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from chiplog.architecture.r7_runtime import R8_PRODUCTION_MANIFEST, bind_execution_manifest
from chiplog.architecture.r8_execution_identity import (
    resolve_runtime_identity,
    snapshot_python_sources,
)
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


def _mounted_r14_snapshot(
    source_root: Path, database: Path, *, mode: str = "development"
) -> dict[str, object]:
    script = """
import asyncio, json, sys
from pathlib import Path
from chiplog.composition.r14_runtime import open_r14_runtime
from chiplog.architecture.r8_execution_identity import execution_identity
from chiplog.platform.authority_ledger import BrokerAuthorityLedger
async def run():
    with execution_identity(sys.argv[2]):
      async with open_r14_runtime(Path(sys.argv[1])) as runtime:
        graph = runtime._supervisor._graph
        assert graph is not None
        ledger = BrokerAuthorityLedger(Path(sys.argv[1] + '.broker.sqlite3'))
        identity = ledger.read_generation_identity('hermetic-tenant', graph.broker_epoch)
        value = {'epoch': graph.broker_epoch, 'graph': graph.manifest_digest}
        print(json.dumps({**value, 'identity': identity.model_dump()}))
asyncio.run(run())
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(source_root / "src")
    completed = subprocess.run(
        [sys.executable, "-c", script, str(database), mode],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return json.loads(completed.stdout)


def test_r14_public_mount_reopen_binds_fresh_observed_source_generation(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    shutil.copytree(Path(__file__).parents[2] / "src", source_root / "src")
    database = tmp_path / "r14.sqlite"
    first = _mounted_r14_snapshot(source_root, database)
    (source_root / "src/chiplog/current_development_edit.py").write_text("VALUE = 'B'\n")
    second = _mounted_r14_snapshot(source_root, database)
    assert first["epoch"] != second["epoch"]
    assert first["graph"] != second["graph"]
    first_identity = first["identity"]
    second_identity = second["identity"]
    assert first_identity["mode"] == second_identity["mode"] == "development"
    assert first_identity["source_snapshot_digest"] != second_identity["source_snapshot_digest"]
    assert first_identity["selected_manifest_fingerprint"] == first["graph"]
    assert second_identity["selected_manifest_fingerprint"] == second["graph"]
    ledger = BrokerAuthorityLedger(Path(str(database) + ".broker.sqlite3"))
    retained = ledger.read_generation_identity("hermetic-tenant", int(first["epoch"]))
    assert retained.model_dump() == first_identity
    with pytest.raises(subprocess.CalledProcessError):
        _mounted_r14_snapshot(source_root, database, mode="release")
    assert ledger.current_epoch("hermetic-tenant") == int(second["epoch"])
    catalog = source_root / "src/chiplog/inert_shared/r8-implementation-v1.json"
    catalog.write_text(json.dumps(snapshot_python_sources(source_root), sort_keys=True))
    release = _mounted_r14_snapshot(source_root, database, mode="release")
    release_identity = release["identity"]
    assert release_identity["mode"] == "release"
    assert release_identity["selected_manifest_fingerprint"] == release["graph"]
