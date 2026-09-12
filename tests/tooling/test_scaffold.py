from __future__ import annotations

import subprocess

import pytest

import chiplog  # type: ignore[import-untyped]


def test_package_exposes_entrypoint() -> None:
    assert callable(chiplog.main)


@pytest.mark.parametrize(
    "path", ["runtime/journal.jsonl", "state/session.json", "journal.jsonl", "demo.journal.jsonl"]
)
def test_runtime_and_journal_fallback_paths_are_ignored(path: str) -> None:
    result = subprocess.run(["git", "check-ignore", "--quiet", path], check=False)

    assert result.returncode == 0
