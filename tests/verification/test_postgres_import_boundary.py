from pathlib import Path

import pytest

from chiplog.verification.r8_surface import R8SurfaceViolation, verify_offline_import_boundary


def test_postgres_driver_is_registered_only_at_storage_boundary(tmp_path: Path) -> None:
    platform = tmp_path / "src/chiplog/platform"
    platform.mkdir(parents=True)
    leaf = platform / "postgres.py"
    leaf.write_text("import psycopg\nfrom psycopg import sql\n")
    verify_offline_import_boundary(tmp_path)
    forbidden = tmp_path / "src/chiplog/capabilities"
    forbidden.mkdir()
    (forbidden / "bypass.py").write_text("import psycopg\n")
    with pytest.raises(R8SurfaceViolation, match="unregistered dependency"):
        verify_offline_import_boundary(tmp_path)
