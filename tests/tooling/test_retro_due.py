"""Committed retro ordering follows additions and renames, not copy guesses."""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[2] / ".harness/scripts/checks/retro_due.py"
SPEC = importlib.util.spec_from_file_location("retro_due", CHECKER)
assert SPEC is not None and SPEC.loader is not None
retro_due = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retro_due)


def git(root: Path, *args: str, check: bool = True) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(
        ["git", *args], cwd=root, env=env, text=True, capture_output=True, check=False
    )
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    (tmp_path / ".harness/retro").mkdir(parents=True)
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Retro fixture")
    git(tmp_path, "config", "user.email", "retro@example.invalid")
    git(tmp_path, "config", "core.hooksPath", "/dev/null")
    return tmp_path


def add_entry(root: Path, name: str, outcome: str, *, similar_to: Path | None = None) -> Path:
    path = root / ".harness/retro" / name
    body = similar_to.read_text() if similar_to else "---\ndate: fixture\noutcome: change\n---\nnotes: baseline\n"
    body = body.replace("outcome: change", f"outcome: {outcome}")
    path.write_text(body)
    return path


def commit(root: Path, message: str) -> str:
    git(root, "add", ".harness/retro")
    git(root, "commit", "-qm", message)
    return git(root, "rev-parse", "--verify", "HEAD^{commit}")


def test_similar_copy_keeps_its_exact_addition_as_latest_outcome(repository: Path) -> None:
    old = add_entry(repository, "z-old.md", "change")
    old_commit = commit(repository, "old retro")
    new = add_entry(repository, "a-new.md", "skip", similar_to=old)
    new_commit = commit(repository, "similar new retro")

    # The configured copy detector reproduces the old --follow misattribution.
    git(repository, "config", "diff.renames", "copies")
    copy_status = git(
        repository, "diff-tree", "--no-commit-id", "--name-status", "-r", "-M", "-C",
        "--find-copies-harder", new_commit,
    )
    assert copy_status.startswith("C")
    followed_add = git(
        repository, "log", "--follow", "--diff-filter=A", "-1", "--format=%H", "--",
        str(new.relative_to(repository)),
    )
    assert followed_add == old_commit
    exact_add = git(
        repository, "log", "--diff-filter=A", "-1", "--format=%H", "--",
        str(new.relative_to(repository)),
    )
    assert exact_add == new_commit

    assert retro_due.committed_retro_outcomes(repository) == ["change", "skip"]


def test_true_rename_keeps_original_entry_order(repository: Path) -> None:
    original = add_entry(repository, "old-name.md", "change")
    original_commit = commit(repository, "original retro")
    later = add_entry(repository, "later.md", "skip")
    commit(repository, "later retro")
    renamed = original.with_name("renamed.md")
    original.rename(renamed)
    rename_commit = commit(repository, "rename retro path")

    rename_status = git(
        repository, "diff-tree", "--no-commit-id", "--name-status", "-r", "-M",
        rename_commit,
    )
    assert rename_status.startswith("R")
    followed_add = git(
        repository, "log", "--follow", "--find-renames", "--diff-filter=A", "-1",
        "--format=%H", "--", str(renamed.relative_to(repository)),
    )
    assert followed_add == original_commit
    assert retro_due.committed_retro_outcomes(repository) == ["change", "skip"]
    assert later.exists()


def test_entry_created_by_merge_uses_merge_introduction(repository: Path) -> None:
    add_entry(repository, "base.md", "change")
    commit(repository, "base retro")
    git(repository, "checkout", "-qb", "feature")
    conflict = add_entry(repository, "conflict.md", "skip")
    commit(repository, "feature conflict")
    git(repository, "checkout", "-q", "-b", "mainline", "HEAD~1")
    conflict = add_entry(repository, "conflict.md", "change")
    commit(repository, "mainline conflict")
    result = subprocess.run(
        ["git", "merge", "--no-ff", "feature", "-m", "merge fixture"],
        cwd=repository,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    conflict.unlink()
    merged = add_entry(repository, "merged.md", "skip")
    merged.write_text("---\ndate: fixture\noutcome: skip\n---\nunique merge resolution payload\n")
    git(repository, "add", ".harness/retro")
    git(repository, "add", "-u", ".harness/retro")
    git(repository, "commit", "-qm", "resolve merge with new retro path")
    merge_commit = git(repository, "rev-parse", "--verify", "HEAD^{commit}")

    exact_add = git(
        repository, "log", "--diff-filter=A", "-1", "--format=%H", "--",
        str(merged.relative_to(repository)),
    )
    assert exact_add == ""
    merge_add = git(
        repository, "log", "--first-parent", "-m", "--diff-filter=A", "-1",
        "--format=%H", "--", str(merged.relative_to(repository)),
    )
    assert merge_add == merge_commit
    assert retro_due.committed_retro_outcomes(repository) == ["change", "skip"]
