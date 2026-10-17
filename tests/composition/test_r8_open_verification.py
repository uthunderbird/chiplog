"""Canonical open must reject actual bad source before provisioning storage."""

from pathlib import Path

import pytest

from chiplog.composition import r8
from chiplog.verification.r8_surface import R8SurfaceViolation


async def test_canonical_open_runs_real_source_verification_before_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "source"
    package = source_root / "src" / "chiplog"
    package.mkdir(parents=True)
    (package / "cli.py").write_text("")
    database = tmp_path / "not-created" / "store.sqlite"
    monkeypatch.setattr(r8, "_source_root", lambda: source_root)

    with pytest.raises(
        R8SurfaceViolation, match="canonical entrypoint bypasses PostgreSQL deployment boundary"
    ):
        async with r8.open_r8_runtime(database, tenant_id="tenant", operator_secret=b"fixture"):
            pytest.fail("unverified source reached an open runtime")
    assert not database.parent.exists()
