"""Executable bad/good proofs for the resource-lifetime static checker."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

CHECK = Path("quality/checks/resource_contexts.py")


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECK), str(root)], check=False, capture_output=True, text=True
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "def f(resource):\n"
            "    try:\n"
            "        return resource\n"
            "    finally:\n"
            "        resource.close()\n",
            1,
        ),
        (
            "def f(resource):\n"
            "    # resource-contexts: justified-manual-close — shutdown owner\n"
            "    try:\n"
            "        return resource\n"
            "    finally:\n"
            "        resource.close()\n",
            0,
        ),
    ],
)
def test_resource_context_checker_distinguishes_manual_close(
    tmp_path: Path, source: str, expected: int
) -> None:
    (tmp_path / "subject.py").write_text(source, encoding="utf-8")
    result = _run(tmp_path)
    assert result.returncode == expected
    if expected:
        assert "manual close in try/finally" in result.stderr


def test_resource_context_checker_accepts_with_lifetime(tmp_path: Path) -> None:
    (tmp_path / "subject.py").write_text(
        "def f(resource):\n    with resource:\n        return resource.read()\n", encoding="utf-8"
    )
    assert _run(tmp_path).returncode == 0
