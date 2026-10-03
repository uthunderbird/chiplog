from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.architecture.r7_runtime import (
    R8_PRODUCTION_MANIFEST,
    RuntimeManifestViolation,
    bind_execution_manifest,
    verify_runtime_manifest,
)
from chiplog.architecture.r8_execution_identity import (
    execution_identity,
    resolve_runtime_identity,
    snapshot_python_sources,
)
from chiplog.verification.r8_surface import R8SurfaceViolation
from tests.support.authority_and_surfaces import ROOT


def _catalog_for_copy(root: Path) -> None:
    path = root / "src/chiplog/inert_shared/r8-implementation-v1.json"
    path.write_text(json.dumps(snapshot_python_sources(root), sort_keys=True))


def test_development_identity_observes_complete_current_python_tree(tmp_path: Path) -> None:
    shutil.copytree(ROOT / "src/chiplog", tmp_path / "src/chiplog")
    _catalog_for_copy(tmp_path)
    before = resolve_runtime_identity(tmp_path)
    (tmp_path / "src/chiplog/new_current_module.py").write_text("VALUE = 1\n")
    after = resolve_runtime_identity(tmp_path)
    assert before.source_identity.mode == after.source_identity.mode == "development"
    assert (
        before.source_identity.source_snapshot_digest
        != after.source_identity.source_snapshot_digest
    )
    assert "new_current_module.py" in snapshot_python_sources(tmp_path)
    selected = bind_execution_manifest(R8_PRODUCTION_MANIFEST, after)
    assert selected.broker_version.startswith("r8-development-v1:")
    assert verify_runtime_manifest(selected) == selected.fingerprint()
    assert R8_PRODUCTION_MANIFEST.broker_version.startswith("r8-broker-v1:")


def test_release_identity_requires_current_root_catalog_bytes(tmp_path: Path) -> None:
    shutil.copytree(ROOT / "src/chiplog", tmp_path / "src/chiplog")
    _catalog_for_copy(tmp_path)
    with execution_identity("release"):
        release = resolve_runtime_identity(tmp_path)
    assert release.broker_version.startswith("r8-broker-v1:")
    (tmp_path / "src/chiplog/new_current_module.py").write_text("VALUE = 1\n")
    with execution_identity("release"), pytest.raises(R8SurfaceViolation):
        resolve_runtime_identity(tmp_path)


def test_manifest_rejects_nonidentity_broker_version() -> None:
    with pytest.raises(RuntimeManifestViolation):
        verify_runtime_manifest(replace(R8_PRODUCTION_MANIFEST, broker_version="arbitrary"))
