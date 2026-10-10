from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_new_retro_generation_self_test() -> None:
    result = subprocess.run(
        ["sh", ".harness/scripts/self-tests/new-retro-generation.sh"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "new-retro generation checks passed" in result.stdout
