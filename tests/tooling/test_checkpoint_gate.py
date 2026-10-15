"""Checkpoint runs an explicit new-test selection, never the full test directory."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from hashlib import sha256
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
        "open('pytest-started', 'w').write('started')\n"
        "os.execv(sys.executable, [sys.executable, '-m', 'pytest', *sys.argv[3:]])\n"
    )
    uv.chmod(0o755)
    env["PATH"] = str(binaries) + os.pathsep + env["PATH"]
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    (root / "implementation.py").write_text("value = 1\n")
    git(root, env, "add", "implementation.py")
    return env


def r8_fixture(root: Path) -> dict[str, str]:
    env = fixture(root)
    source = root / "src/chiplog"
    architecture = source / "architecture"
    composition = source / "composition"
    verification = source / "verification"
    inert_shared = source / "inert_shared"
    for directory in (architecture, composition, verification, inert_shared):
        directory.mkdir(parents=True, exist_ok=True)
    for package in (source, architecture, composition, verification):
        (package / "__init__.py").write_text("")
    shutil.copyfile(
        ROOT / "src/chiplog/architecture/r8_implementation.py",
        architecture / "r8_implementation.py",
    )
    shutil.copyfile(ROOT / "src/chiplog/verification/r8_surface.py", verification / "r8_surface.py")
    shutil.copyfile(ROOT / "src/chiplog/cli.py", source / "cli.py")
    shutil.copyfile(ROOT / "src/chiplog/composition/r8.py", composition / "r8.py")
    implementation = source / "implementation.py"
    implementation.write_text("value = 1\n")
    catalog = {
        path.relative_to(source).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in source.rglob("*.py")
    }
    (inert_shared / "r8-implementation-v1.json").write_text(json.dumps(catalog, sort_keys=True))
    git(root, env, "add", "src")
    git(root, env, "commit", "-qm", "R8 baseline")
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


def run_strict(root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(root / ".harness/scripts/checkpoint.py"), "--strict-r8"],
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


def test_selected_pytest_imports_from_checkpoint_checkout(tmp_path: Path) -> None:
    env = fixture(tmp_path)
    candidate = tmp_path / "src/sentinelpkg"
    candidate.mkdir(parents=True)
    (candidate / "__init__.py").write_text('SOURCE_ID = "candidate"\n')
    foreign = tmp_path / "foreign/sentinelpkg"
    foreign.mkdir(parents=True)
    (foreign / "__init__.py").write_text('SOURCE_ID = "foreign"\n')
    selected = tmp_path / "tests/tooling/test_source_root.py"
    selected.write_text(
        "import sentinelpkg\n\n"
        "def test_source_root():\n"
        "    assert sentinelpkg.SOURCE_ID == 'candidate', sentinelpkg.__file__\n"
    )
    git(tmp_path, env, "add", "src/sentinelpkg", "tests/tooling/test_source_root.py")
    env["PYTHONPATH"] = str(tmp_path / "foreign")

    result = run(tmp_path, env, ["tests/tooling/test_source_root.py::test_source_root"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


@pytest.mark.parametrize(
    "selectors",
    [[], {}, "tests", [1], ["--collect-only"], ["tests"], ["tests/../implementation.py"]],
)
def test_invalid_or_empty_code_selection_rejects(tmp_path: Path, selectors: object) -> None:
    result = run(tmp_path, fixture(tmp_path), selectors)
    assert result.returncode == 1
    assert "→ сделай:" in result.stderr


@pytest.mark.parametrize(
    "state,action",
    [
        ("main", "используй CHIPLOG_COMMIT_MODE=full"),
        ("master", "используй CHIPLOG_COMMIT_MODE=full"),
        ("detached", "создай или переключись на именованную рабочую ветку"),
        ("merge", "заверши merge и запусти полный гейт"),
        ("dirty", "добавь изменения в index или верни worktree к index"),
    ],
)
def test_non_checkpoint_states_reject(tmp_path: Path, state: str, action: str) -> None:
    env = fixture(tmp_path)
    staged_before = subprocess.check_output(["git", "write-tree"], cwd=tmp_path, env=env)
    if state in {"main", "master"}:
        git(tmp_path, env, "branch", "-m", state)
    elif state == "detached":
        git(tmp_path, env, "checkout", "--detach")
    elif state == "merge":
        (tmp_path / ".git/MERGE_HEAD").write_text("pending\n")
    else:
        (tmp_path / "implementation.py").write_text("value = 2\n")
    result = run(tmp_path, env, ["tests/tooling/test_selection.py::test_new"])
    assert result.returncode == 1
    assert f"→ сделай: {action}" in result.stderr
    assert "checkpoint: только выбранные тесты" not in result.stdout
    assert subprocess.check_output(["git", "write-tree"], cwd=tmp_path, env=env) == staged_before


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


def test_r8_development_checkpoint_allows_stale_catalog_after_safe_python_edit(
    tmp_path: Path,
) -> None:
    env = r8_fixture(tmp_path)
    target = tmp_path / "src/chiplog/implementation.py"
    target.write_text("value = 2\n")
    git(tmp_path, env, "add", target.relative_to(tmp_path).as_posix())
    result = run(tmp_path, env, ["tests/tooling/test_selection.py::test_new"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "pytest-started").exists()


def test_r8_strict_catalog_accepts_a_stable_matching_tree(tmp_path: Path) -> None:
    env = r8_fixture(tmp_path)
    result = run_strict(tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / "pytest-started").exists()


def test_r8_strict_catalog_rejects_a_stale_python_file(tmp_path: Path) -> None:
    env = r8_fixture(tmp_path)
    source = tmp_path / "src/chiplog"
    (source / "implementation.py").write_text("value = 3\n")
    result = run_strict(tmp_path, env)
    assert result.returncode == 1
    assert "→ сделай:" in result.stderr
    assert "строгий R8 catalog, структура и import-граница проверены" in result.stderr
    assert not (tmp_path / "pytest-started").exists()


def test_r8_development_checkpoint_rejects_untracked_python_before_selected_pytest(
    tmp_path: Path,
) -> None:
    env = r8_fixture(tmp_path)
    source = tmp_path / "src/chiplog"
    (source / "untracked.py").write_text("value = 3\n")
    result = run(tmp_path, env, ["tests/tooling/test_selection.py::test_new"])
    assert result.returncode == 1
    assert "→ сделай:" in result.stderr
    assert not (tmp_path / "pytest-started").exists()


@pytest.mark.parametrize(
    "path,contents,reason",
    (
        (
            "src/chiplog/cli.py",
            "from chiplog.composition.r7 import open_r7_runtime\n",
            "структурная или import-проверка",
        ),
        (
            "src/chiplog/forbidden.py",
            "import requests\n",
            "структурная или import-проверка",
        ),
    ),
)
def test_r8_development_checkpoint_rejects_structural_or_import_mutation(
    tmp_path: Path, path: str, contents: str, reason: str
) -> None:
    env = r8_fixture(tmp_path)
    target = tmp_path / path
    target.write_text(contents)
    git(tmp_path, env, "add", path)

    result = run(tmp_path, env, ["tests/tooling/test_selection.py::test_new"])

    assert result.returncode == 1
    assert reason in result.stderr
    assert not (tmp_path / "pytest-started").exists()
