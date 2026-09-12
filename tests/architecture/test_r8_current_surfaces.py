from __future__ import annotations

from chiplog.verification.r8_surface import (
    verify_offline_import_boundary,
    verify_r8_surfaces,
)
from tests.support.authority_and_surfaces import ROOT


def test_surface_inventory_equals_actual_current_boundaries() -> None:
    verify_r8_surfaces(ROOT)
    verify_offline_import_boundary(ROOT)
