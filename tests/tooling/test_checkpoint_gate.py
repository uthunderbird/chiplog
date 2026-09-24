"""Checkpoint runs an explicit new-test selection, never the full test directory."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def git(root: Path, env: dict[str, str], *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True)


def fixture(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    for name in subprocess.check_output(
        ["git", "rev-parse", "--local-env-vars"], text=True
    ).split():
        env.pop(name, None)
    for name in ("CHIPLOG_CHECKPOINT_TESTS", "CHIPLOG_COMMIT_MODE", "PYTEST_ADDOPTS"):
        env.pop(name, None)
    git(root, env, "init", "-q", "-b", "feature")
    git(root, env, "config", "user.name", "Fixture")
    git(root, env, "config", "user.email", "fixture@example.invalid")
    scripts = root / ".harness/scripts"
    scripts.mkdir(parents=True)
    for name in ("checkpoint.py", "clean-git-env.sh"):
        shutil.copyfile(ROOT / ".harness/scripts" / name, scripts / name)
    tests = root / "tests/tooling"
    tests.mkdir(parents=True)
    (tests / "test_selection.py").write_text(
        "def test_new():\n    assert True\n\ndef test_unselected_regression():\n    assert False\n"
    )
    git(root, env, "add", ".")
    git(root, env, "commit", "-qm", "baseline")
    binaries = root / "bin"
    binaries.mkdir()
    # Actual pytest, with an intentionally failing unselected regression beside it.
    uv = binaries / "uv"
    uv.write_text(
        f"#!{sys.executable}\nimport os, sys\n"
        "assert sys.argv[1:3] == ['run', 'pytest']\n"
        "os.execv(sys.executable, [sys.executable, '-m', 'pytest', *sys.argv[3:]])\n"
    )
    uv.chmod(0o755)
    env["PATH"] = str(binaries) + os.pathsep + env["PATH"]
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    (root / "implementation.py").write_text("value = 1\n")
    git(root, env, "add", "implementation.py")
    return env


def run(root: Path, env: dict[str, str], selectors: object) -> subprocess.CompletedProcess[str]:
    env["CHIPLOG_CHECKPOINT_TESTS"] = json.dumps(selectors)
    return subprocess.run(
        [sys.executable, str(root / ".harness/scripts/checkpoint.py")],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "name,code", [("test_new", 0), ("test_unselected_regression", 1), ("test_missing", 1)]
)
def test_only_selected_case_runs_and_failures_block(tmp_path: Path, name: str, code: int) -> None:
    env = fixture(tmp_path)
    before = subprocess.check_output(["git", "write-tree"], cwd=tmp_path, env=env)
    result = run(tmp_path, env, [f"tests/tooling/test_selection.py::{name}"])
    assert result.returncode == code, result.stdout + result.stderr
    assert subprocess.check_output(["git", "write-tree"], cwd=tmp_path, env=env) == before
    if not code:
        assert "1 passed" in result.stdout


@pytest.mark.parametrize(
    "selectors",
    [[], {}, "tests", [1], ["--collect-only"], ["tests"], ["tests/../implementation.py"]],
)
def test_invalid_or_empty_code_selection_rejects(tmp_path: Path, selectors: object) -> None:
    result = run(tmp_path, fixture(tmp_path), selectors)
    assert result.returncode == 1
    assert "→ сделай:" in result.stderr


@pytest.mark.parametrize("state", ["main", "master", "detached", "merge", "dirty"])
def test_non_checkpoint_states_reject(tmp_path: Path, state: str) -> None:
    env = fixture(tmp_path)
    if state in {"main", "master"}:
        git(tmp_path, env, "branch", "-m", state)
    elif state == "detached":
        git(tmp_path, env, "checkout", "--detach")
    elif state == "merge":
        (tmp_path / ".git/MERGE_HEAD").write_text("pending\n")
    else:
        (tmp_path / "implementation.py").write_text("value = 2\n")
    assert run(tmp_path, env, ["tests/tooling/test_selection.py::test_new"]).returncode == 1


def test_only_staged_markdown_allows_no_tests(tmp_path: Path) -> None:
    env = fixture(tmp_path)
    git(tmp_path, env, "reset", "-q", "HEAD", "implementation.py")
    (tmp_path / "plan.md").write_text("Checkpoint plan\n")
    git(tmp_path, env, "add", "plan.md")
    result = run(tmp_path, env, [])
    assert result.returncode == 0
    assert "только Markdown" in result.stdout
    # A renamed code file is still a code change, even with a Markdown destination.
    git(tmp_path, env, "mv", "tests/tooling/test_selection.py", "old.md")
    assert run(tmp_path, env, []).returncode == 1


def test_malformed_json_rejects(tmp_path: Path) -> None:
    env = fixture(tmp_path)
    env["CHIPLOG_CHECKPOINT_TESTS"] = "["
    result = subprocess.run(
        [sys.executable, str(tmp_path / ".harness/scripts/checkpoint.py")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "→ сделай:" in result.stderr


def test_zero_collected_tests_is_not_success(tmp_path: Path) -> None:
    env = fixture(tmp_path)
    (tmp_path / "tests/tooling/test_empty.py").write_text("# No tests\n")
    git(tmp_path, env, "add", "tests/tooling/test_empty.py")
    result = run(tmp_path, env, ["tests/tooling/test_empty.py"])
    assert result.returncode == 1
    assert "кодом 5" in result.stderr
