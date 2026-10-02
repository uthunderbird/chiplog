"""The retro pin metric uses the same Git period it prints."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / ".harness/scripts/retro-inputs.sh"
_CATALOG = "src/chiplog/inert_shared/r8-implementation-v1.json"


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", "core.fsmonitor=false", *args], cwd=root, text=True
    ).strip()


def _run_retro(root: Path) -> str:
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.check_output(
        ["sh", ".harness/scripts/retro-inputs.sh"],
        cwd=root,
        env=env,
        text=True,
    )


def test_pin_counts_include_root_and_follow_last_committed_retro(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Retro Fixture")
    _git(tmp_path, "config", "user.email", "retro@example.invalid")
    script = tmp_path / ".harness/scripts/retro-inputs.sh"
    script.parent.mkdir(parents=True)
    script.write_bytes(_SCRIPT.read_bytes())
    catalog = tmp_path / _CATALOG
    catalog.parent.mkdir(parents=True)
    catalog.write_text("{}\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "root")

    first = _run_retro(tmp_path)
    assert "коммитов периода: 1   (git rev-list --count HEAD)" in first
    assert f"коммитов с R8 catalog: 1   (git rev-list --count HEAD -- {_CATALOG})" in first

    retro = tmp_path / ".harness/retro/2026-01-01.md"
    retro.parent.mkdir(parents=True)
    retro.write_text("---\ndate: 2026-01-01\noutcome: change\n---\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "retro")
    base = _git(tmp_path, "rev-parse", "--short", "HEAD")
    (tmp_path / "other.txt").write_text("one\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "other")
    catalog.write_text('{"source":"new"}\n')
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "pin")

    current = _run_retro(tmp_path)
    assert f"коммитов периода: 2   (git rev-list --count {base}..HEAD)" in current
    assert (
        f"коммитов с R8 catalog: 1   "
        f"(git rev-list --count {base}..HEAD -- {_CATALOG})"
    ) in current
