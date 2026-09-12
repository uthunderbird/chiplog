"""Observable good/bad snapshots for the test-layout gate, including partial staging."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / ".harness/scripts/checks/tests_layout.py"


def clean_environment() -> dict[str, str]:
    env = os.environ.copy()
    names = subprocess.check_output(
        ["git", "rev-parse", "--local-env-vars"], text=True
    ).splitlines()
    for name in names:
        env.pop(name, None)
    return env


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], env=clean_environment(), text=True
    )


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Layout fixture")
    git(tmp_path, "config", "user.email", "layout@example.invalid")
    (tmp_path / "tests/platform").mkdir(parents=True)
    (tmp_path / "tests/platform/test_example.py").write_text("def test_example(): pass\n")
    git(tmp_path, "add", "tests")
    git(tmp_path, "commit", "-qm", "initial fixture")
    return tmp_path


def check(root: Path, mode: str = "--index") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECK), "--root", str(root), mode],
        env=clean_environment(),
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    "bad", ["tests/r9/test_new.py", "tests/test_new.py", "tests/misc/test_new.py"]
)
def test_unknown_or_root_test_is_rejected_then_accepted_after_move(
    repository: Path, bad: str
) -> None:
    path = repository / bad
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("def test_new(): pass\n")
    assert check(repository, "--worktree").returncode == 1
    assert check(repository).returncode == 0
    git(repository, "add", bad)
    result = check(repository)
    assert result.returncode == 1 and bad in result.stderr
    path.rename(repository / "tests/platform/test_new.py")
    assert check(repository).returncode == 1
    git(repository, "add", "-A", "tests")
    assert check(repository).returncode == 0


def test_full_index_checks_unchanged_bad_path_and_staged_deletion(repository: Path) -> None:
    bad = repository / "tests/r9/test_old.py"
    bad.parent.mkdir()
    bad.write_text("def test_old(): pass\n")
    git(repository, "add", "tests")
    git(repository, "commit", "-qm", "old invalid layout")
    (repository / "unrelated.txt").write_text("change")
    git(repository, "add", "unrelated.txt")
    assert check(repository).returncode == 1
    git(repository, "rm", "--cached", "tests/r9/test_old.py")
    assert bad.exists()
    assert check(repository).returncode == 0
    assert check(repository, "--worktree").returncode == 1


def test_unstaged_deletion_and_ignored_cache_are_worktree_state(repository: Path) -> None:
    (repository / ".gitignore").write_text("__pycache__/\n")
    cache = repository / "tests/__pycache__"
    cache.mkdir()
    (cache / "example.pyc").write_bytes(b"cache")
    assert check(repository, "--worktree").returncode == 0
    (repository / "tests/platform/test_example.py").unlink()
    assert check(repository, "--worktree").returncode == 2
    assert check(repository).returncode == 0


@pytest.mark.parametrize(
    "shape",
    [
        "category-file",
        "category-symlink",
        "service-directory",
        "service-symlink",
        "nested-symlink",
        "gitlink",
    ],
)
def test_type_substitution_is_rejected(repository: Path, shape: str) -> None:
    if shape.startswith("category"):
        shutil.rmtree(repository / "tests/platform")
        if shape == "category-file":
            (repository / "tests/platform").write_text("not a directory")
        else:
            (repository / "tests/platform").symlink_to(
                repository / "outside", target_is_directory=True
            )
    elif shape == "service-directory":
        (repository / "tests/AGENTS.md").mkdir()
        (repository / "tests/AGENTS.md/x.py").write_text("x=1")
    elif shape == "service-symlink":
        (repository / "tests/AGENTS.md").symlink_to(repository / "outside.md")
    elif shape == "nested-symlink":
        (repository / "tests/platform/link.py").symlink_to(repository / "outside.py")
    else:
        oid = git(repository, "rev-parse", "--verify", "HEAD").strip()
        git(
            repository,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{oid},tests/platform/submodule",
        )
        assert check(repository).returncode == 1
        return
    assert check(repository, "--worktree").returncode != 0
    git(repository, "add", "-A", "tests")
    assert check(repository).returncode == 1


def test_service_files_and_each_responsibility_are_accepted(repository: Path) -> None:
    for name in ("AGENTS.md", "__init__.py", "conftest.py"):
        (repository / "tests" / name).write_text("")
    for category in (
        "architecture",
        "domain_primitives",
        "capabilities/planning",
        "composition",
        "cli",
        "evaluation",
        "verification",
        "tooling",
        "support",
    ):
        folder = repository / "tests" / category
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "example.py").write_text("")
    git(repository, "add", "tests")
    assert check(repository).returncode == 0
    assert check(repository, "--worktree").returncode == 0


def test_missing_git_or_test_tree_is_not_empty_success(tmp_path: Path) -> None:
    assert check(tmp_path).returncode == 2
    git(tmp_path, "init", "-q")
    assert check(tmp_path).returncode == 2


def test_real_gate_uses_full_index_and_propagates_layout_failure(repository: Path) -> None:
    scripts = repository / ".harness/scripts"
    checks = scripts / "checks"
    checks.mkdir(parents=True)
    source = (ROOT / ".harness/scripts/gate.sh").read_text()
    (scripts / "gate.sh").write_text(source)
    for name in re.findall(r"^run .*?checks/([\w]+\.py)", source, re.MULTILINE):
        if name == "tests_layout.py":
            shutil.copy2(CHECK, checks / name)
        else:
            (checks / name).write_text("raise SystemExit(0)\n")
    for name in ("lint.sh", "test.sh"):
        (scripts / name).write_text("#!/bin/sh\nexit 0\n")
    shutil.copy2(ROOT / ".harness/scripts/clean-git-env.sh", scripts / "clean-git-env.sh")
    binaries = repository / "bin"
    binaries.mkdir()
    (binaries / "uv").write_text("#!/bin/sh\nexit 0\n")
    (binaries / "uv").chmod(0o755)
    env = {**clean_environment(), "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}"}
    env.pop("HARNESS_GATE_SKIP", None)
    bad = repository / "tests/r9/test_old.py"
    bad.parent.mkdir()
    bad.write_text("def test_old(): pass\n")
    git(repository, "add", "tests")
    git(repository, "commit", "-qm", "invalid layout predates current diff")
    (repository / "unrelated.txt").write_text("changed")
    git(repository, "add", "unrelated.txt")
    # Run the actual hook, which resolves this worktree's real gate dispatcher.
    hook = ROOT / ".githooks/pre-commit"
    result = subprocess.run(
        ["sh", str(hook)], cwd=repository, env=env, text=True, capture_output=True, check=False
    )
    assert result.returncode != 0 and "tests/r9/test_old.py" in result.stderr
    git(repository, "rm", "--cached", "tests/r9/test_old.py")
    result = subprocess.run(
        ["sh", str(hook)], cwd=repository, env=env, text=True, capture_output=True, check=False
    )
    assert bad.exists() and result.returncode == 0, result.stdout + result.stderr
