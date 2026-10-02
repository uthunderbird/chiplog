"""Durable selector launcher evidence, locking, drift, and signal behavior."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_installed_launcher_is_executable() -> None:
    assert os.access(ROOT / ".harness/scripts/run-selector-durable.sh", os.X_OK)


def _git(root: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=root, check=True, capture_output=True)


def _fixture(tmp_path: Path, mode: str) -> dict[str, str]:
    scripts = tmp_path / ".harness/scripts"
    scripts.mkdir(parents=True)
    for name in (
        "run-selector-durable.sh",
        "run-selector-durable.py",
        "run-selector-durable-child.py",
    ):
        target = scripts / name
        target.write_bytes((ROOT / ".harness/scripts" / name).read_bytes())
        target.chmod(0o755)
    (tmp_path / "src/chiplog").mkdir(parents=True)
    (tmp_path / "src/chiplog/owned.py").write_text("VALUE = 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_short.py").write_text("def test_short():\n    assert True\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "baseline")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    uv = binaries / "uv"
    uv.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, signal, sys, time\n"
        "assert sys.argv[1:4] == ['run', 'pytest', '--']\n"
        "mode = os.environ['FAKE_UV_MODE']\n"
        "if mode == 'ignore-term': signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "pathlib.Path(os.environ['FAKE_UV_LOG']).write_text('started')\n"
        "if mode == 'fail': sys.exit(7)\n"
        "if mode == 'drift': pathlib.Path('src/chiplog/new_input.py').write_text('X = 1\\n')\n"
        "if mode == 'wait': time.sleep(30)\n"
        "if mode == 'ignore-term':\n"
        "    time.sleep(30)\n"
        "if mode == 'real':\n"
        "    os.execv(sys.executable, [sys.executable, '-m', 'pytest', *sys.argv[4:]])\n"
    )
    uv.chmod(0o755)
    env = os.environ.copy()
    local_vars = subprocess.check_output(
        ("git", "rev-parse", "--local-env-vars"), text=True
    ).split()
    for name in local_vars:
        env.pop(name, None)
    env["PATH"] = str(binaries) + os.pathsep + env["PATH"]
    env["FAKE_UV_MODE"] = mode
    env["FAKE_UV_LOG"] = str(tmp_path / "uv.log")
    return env


def _run(root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (str(root / ".harness/scripts/run-selector-durable.sh"), "tests/test_short.py"),
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )


def _run_dir(result: subprocess.CompletedProcess[str]) -> Path:
    return Path(result.stdout.strip().split("=", 1)[1])


@pytest.mark.parametrize("mode", ["pass", "real"])
def test_durable_selector_verifies_clean_short_pytest(tmp_path: Path, mode: str) -> None:
    env = _fixture(tmp_path, mode)
    result = _run(tmp_path, env)
    run = _run_dir(result)
    exit_record = json.loads((run / "exit.json").read_text())
    assert result.returncode == 0, result.stderr
    assert exit_record["status"] == "verified"
    assert exit_record["verified"] is True
    assert (run / "start.json").exists() and (run / "child.pid").exists()
    if mode == "real":
        assert "1 passed" in (run / "stdout.log").read_text()


def test_durable_selector_records_child_failure(tmp_path: Path) -> None:
    result = _run(tmp_path, _fixture(tmp_path, "fail"))
    exit_record = json.loads((_run_dir(result) / "exit.json").read_text())
    assert result.returncode == 1
    assert exit_record["status"] == "failed"
    assert exit_record["return_code"] == 7


def test_durable_selector_rejects_duplicate_lock_before_fake_uv(tmp_path: Path) -> None:
    env = _fixture(tmp_path, "wait")
    first = subprocess.Popen(
        (str(tmp_path / ".harness/scripts/run-selector-durable.sh"), "tests/test_short.py"),
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 5
    while not Path(env["FAKE_UV_LOG"]).exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert Path(env["FAKE_UV_LOG"]).exists()
    sibling = tmp_path.parent / (tmp_path.name + "-sibling")
    _git(tmp_path, "worktree", "add", "--detach", str(sibling), "HEAD")
    second = _run(sibling, env)
    assert second.returncode == 1
    assert "lock exists" in second.stderr
    first.send_signal(signal.SIGTERM)
    stdout, _ = first.communicate(timeout=5)
    result: subprocess.CompletedProcess[Any] = subprocess.CompletedProcess((), 1, stdout, "")
    exit_record = json.loads((_run_dir(result) / "exit.json").read_text())
    assert exit_record["status"] == "interrupted"


@pytest.mark.skipif(os.name != "posix", reason="SIGKILL process evidence requires POSIX")
def test_sigkill_leaves_partial_evidence_without_exit_marker(tmp_path: Path) -> None:
    env = _fixture(tmp_path, "wait")
    launcher = subprocess.Popen(
        (str(tmp_path / ".harness/scripts/run-selector-durable.sh"), "tests/test_short.py"),
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 5
    run: Path | None = None
    while time.monotonic() < deadline:
        candidates = list((tmp_path / ".harness/runs").glob("*"))
        if candidates and (candidates[0] / "child.pid").exists():
            run = candidates[0]
            break
        time.sleep(0.02)
    assert run is not None
    child = json.loads((run / "child.pid").read_text())
    launcher.kill()
    launcher.wait(timeout=5)
    os.killpg(child["pgid"], signal.SIGKILL)
    assert not (run / "exit.json").exists()
    assert list((tmp_path / ".git/selector-locks").iterdir())


def test_durable_selector_marks_new_untracked_source_as_drift(tmp_path: Path) -> None:
    result = _run(tmp_path, _fixture(tmp_path, "drift"))
    exit_record = json.loads((_run_dir(result) / "exit.json").read_text())
    assert result.returncode == 1
    assert exit_record["status"] == "drift"
    assert exit_record["untracked_python"] == ["src/chiplog/new_input.py"]


def test_durable_selector_rejects_non_selector_argument_before_launch(tmp_path: Path) -> None:
    env = _fixture(tmp_path, "pass")
    result = subprocess.run(
        (str(tmp_path / ".harness/scripts/run-selector-durable.sh"), "--collect-only"),
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 64
    assert "usage:" in result.stderr
    assert not Path(env["FAKE_UV_LOG"]).exists()


def test_repeated_term_escalates_when_child_ignores_term(tmp_path: Path) -> None:
    env = _fixture(tmp_path, "ignore-term")
    env["CHIPLOG_SELECTOR_SIGNAL_GRACE_SECONDS"] = "1"
    launcher = subprocess.Popen(
        (str(tmp_path / ".harness/scripts/run-selector-durable.sh"), "tests/test_short.py"),
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 5
    while not Path(env["FAKE_UV_LOG"]).exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert Path(env["FAKE_UV_LOG"]).exists()
    launcher.send_signal(signal.SIGTERM)
    launcher.send_signal(signal.SIGTERM)
    stdout, _ = launcher.communicate(timeout=5)
    result: subprocess.CompletedProcess[Any] = subprocess.CompletedProcess((), 1, stdout, "")
    exit_record = json.loads((_run_dir(result) / "exit.json").read_text())
    assert launcher.returncode == 1
    assert exit_record["status"] == "interrupted"
    assert exit_record["escalated"] is True
