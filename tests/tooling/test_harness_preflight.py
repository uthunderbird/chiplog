"""Exercise real harness dispatchers with controlled child-command outcomes."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _stub(path: Path, label: str) -> None:
    path.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "{label}" >> "$ORDER_LOG"\n[ "${{FAIL_AT:-}}" != "{label}" ]\n'
    )
    path.chmod(0o755)


def _fixture(root: Path) -> tuple[Path, dict[str, str]]:
    scripts = root / ".harness/scripts"
    scripts.mkdir(parents=True)
    for name in ("gate.sh", "test.sh", "lint.sh", "clean-git-env.sh"):
        shutil.copyfile(ROOT / ".harness/scripts" / name, scripts / name)
    binaries = root / "bin"
    binaries.mkdir()
    uv = binaries / "uv"
    uv.write_text(
        '#!/bin/sh\ncase "$*" in\n'
        '  "run pytest") label=pytest;;\n'
        '  "run python -m chiplog.verification fast") label=fast;;\n'
        '  "run ruff check --force-exclude .") label=ruff-check;;\n'
        '  "run ruff format --check --force-exclude .") label=ruff-format;;\n'
        '  "run mypy") label=mypy;;\n'
        "  *) exit 99;;\nesac\n"
        'printf "%s\\n" "$label" >> "$ORDER_LOG"\n'
        '[ "${FAIL_AT:-}" != "$label" ]\n'
    )
    uv.chmod(0o755)
    python = binaries / "python3"
    python.write_text(
        "#!/bin/sh\nlabel=${1##*/}\n"
        'printf "%s\\n" "$label" >> "$ORDER_LOG"\n'
        '[ "${FAIL_AT:-}" != "$label" ]\n'
    )
    python.chmod(0o755)
    env = os.environ.copy()
    for name in subprocess.check_output(
        ["git", "rev-parse", "--local-env-vars"], text=True
    ).split():
        env.pop(name, None)
    env.pop("HARNESS_GATE_SKIP", None)
    env.pop("CHIPLOG_COMMIT_MODE", None)
    env.pop("CHIPLOG_CHECKPOINT_TESTS", None)
    env["PATH"] = str(binaries) + os.pathsep + env["PATH"]
    env["ORDER_LOG"] = str(root / "order.log")
    env["FAIL_AT"] = ""
    subprocess.run(["git", "init", "-q", str(root)], env=env, check=True)
    return scripts, env


def _run(
    scripts: Path, env: dict[str, str], name: str, *args: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    result = subprocess.run(
        ["sh", str(scripts / name), *args], env=env, capture_output=True, text=True
    )
    log = Path(env["ORDER_LOG"])
    return result, log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize("mode", [(), ("--preflight",)])
@pytest.mark.parametrize(
    "failure", ["", "self-test", "reproducer", "self-test.py", "deferred-contract", "pytest"]
)
def test_test_adapter_preflight_order_and_exit_status(
    tmp_path: Path, mode: tuple[str, ...], failure: str
) -> None:
    scripts, env = _fixture(tmp_path)
    _stub(scripts / "gate.sh", "self-test")
    deferred = tmp_path / ".harness/deferred"
    deferred.mkdir()
    _stub(deferred / "contract-test.sh", "deferred-contract")
    for name in ("active", "agent", "no-run"):
        directory = tmp_path / ".harness/reproducers" / name
        directory.mkdir(parents=True)
        if name != "no-run":
            _stub(directory / "run.sh", "reproducer" if name == "active" else "unexpected-agent")
        if name == "agent":
            (directory / "case.md").write_text("agent replay is a separate workflow\n")
    env["FAIL_AT"] = failure
    result, events = _run(scripts, env, "test.sh", *mode)
    expected = ["self-test", "reproducer"]
    if not mode and failure not in {"self-test", "reproducer"}:
        for step in ("self-test.py", "deferred-contract", "pytest"):
            expected.append(step)
            if failure == step:
                break
    assert events == expected
    failed = failure in {"self-test", "reproducer"} or (not mode and bool(failure))
    assert result.returncode == (1 if failed else 0)
    if failed:
        assert "→" in result.stderr


@pytest.mark.parametrize("args", [("--unknown",), ("--preflight", "extra")])
def test_preflight_rejects_unknown_arguments(tmp_path: Path, args: tuple[str, ...]) -> None:
    scripts, env = _fixture(tmp_path)
    result, events = _run(scripts, env, "test.sh", *args)
    assert result.returncode == 2
    assert events == []
    assert "→ команда:" in result.stderr


@pytest.mark.parametrize(
    "failure", ["", "rules_have_reproducers.py", "tests_layout.py", "fast", "lint", "test"]
)
def test_gate_does_not_start_test_adapter_after_prior_failure(tmp_path: Path, failure: str) -> None:
    scripts, env = _fixture(tmp_path)
    _stub(scripts / "lint.sh", "lint")
    _stub(scripts / "test.sh", "test")
    env["FAIL_AT"] = failure
    result, events = _run(scripts, env, "gate.sh")
    assert events[:8] == [
        "rules_have_reproducers.py",
        "retro_due.py",
        "commit_trail.py",
        "messages_are_actionable.py",
        "checks_are_wired.py",
        "polish_artifacts.py",
        "handoff_pending.py",
        "tests_layout.py",
    ]
    assert events[8:10] == ["fast", "lint"]
    assert events.count("test") == (0 if failure not in {"", "test"} else 1)
    assert result.returncode == (1 if failure else 0)
    assert ("гейт открыт" in result.stdout) == (not failure)


@pytest.mark.parametrize(
    "failure", ["", "ruff-check", "ruff-format", "mypy", "resource_contexts.py"]
)
def test_lint_adapter_preserves_tool_failures(tmp_path: Path, failure: str) -> None:
    scripts, env = _fixture(tmp_path)
    env["FAIL_AT"] = failure
    result, events = _run(scripts, env, "lint.sh")
    assert events == ["-", "ruff-check", "ruff-format", "resource_contexts.py", "mypy"]
    assert result.returncode == (1 if failure else 0)


@pytest.mark.parametrize("failure", ["", "checkpoint.py", "tests_layout.py"])
def test_checkpoint_gate_runs_only_selected_test_adapter(tmp_path: Path, failure: str) -> None:
    scripts, env = _fixture(tmp_path)
    env["CHIPLOG_COMMIT_MODE"] = "checkpoint"
    env["FAIL_AT"] = failure
    result, events = _run(scripts, env, "gate.sh")
    assert events == [
        "rules_have_reproducers.py",
        "commit_trail.py",
        "messages_are_actionable.py",
        "checks_are_wired.py",
        "polish_artifacts.py",
        "handoff_pending.py",
        "tests_layout.py",
        *([] if failure == "tests_layout.py" else ["checkpoint.py"]),
    ]
    assert result.returncode == (1 if failure else 0)


def test_gate_rejects_unknown_commit_mode(tmp_path: Path) -> None:
    scripts, env = _fixture(tmp_path)
    env["CHIPLOG_COMMIT_MODE"] = "typo"
    result, events = _run(scripts, env, "gate.sh")
    assert result.returncode == 1
    assert events == []


@pytest.mark.parametrize("mode", [None, "full"])
def test_full_mode_still_dispatches_complete_checks(tmp_path: Path, mode: str | None) -> None:
    scripts, env = _fixture(tmp_path)
    _stub(scripts / "lint.sh", "lint")
    _stub(scripts / "test.sh", "test")
    if mode is not None:
        env["CHIPLOG_COMMIT_MODE"] = mode
    result, events = _run(scripts, env, "gate.sh")
    assert result.returncode == 0
    assert "retro_due.py" in events
    assert events[-3:] == ["fast", "lint", "test"]
    assert "checkpoint.py" not in events


def test_fast_profile_has_real_good_and_bad_source_inputs(tmp_path: Path) -> None:
    from chiplog.verification.invariants import InvariantManifestError
    from chiplog.verification.runner import run_profile

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    docs = tmp_path / "design-docs"
    (docs / "project-architecture").mkdir(parents=True)
    source = docs / "project-architecture/NORMATIVE.md"
    shutil.copyfile(ROOT / "design-docs/project-architecture/NORMATIVE.md", source)
    shutil.copytree(ROOT / "design-docs/transcripts", docs / "transcripts")
    subprocess.run(["git", "add", "design-docs"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=tmp_path,
        check=True,
    )
    good, _ = run_profile(tmp_path, "fast")
    assert good["status"] == "PASS"
    original = source.read_text()
    assert original.count("\n108. ") == 1
    source.write_text(original.replace("\n108. ", "\n109. "))
    with pytest.raises(InvariantManifestError):
        run_profile(tmp_path, "fast")
