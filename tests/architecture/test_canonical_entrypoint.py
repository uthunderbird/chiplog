from __future__ import annotations

from pathlib import Path

from chiplog.verification.r7_bypass import verify_canonical_r7_entrypoint

ROOT = Path(__file__).resolve().parents[2]


def test_production_entrypoint_has_no_direct_r6_bypass() -> None:
    verify_canonical_r7_entrypoint(ROOT / "src/chiplog/cli.py")
