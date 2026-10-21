from __future__ import annotations

from pathlib import Path

from chiplog.verification.r8_surface import verify_r8_surfaces

ROOT = Path(__file__).resolve().parents[2]


def test_production_entrypoint_uses_checked_postgres_boundary() -> None:
    verify_r8_surfaces(ROOT)
