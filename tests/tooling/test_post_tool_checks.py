from __future__ import annotations

import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / ".agents/hooks/codex-harness.py"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    shutil.copytree(ROOT / ".harness/scripts", root / ".harness/scripts")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


@pytest.fixture
def dispatcher() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "post_tool_checks", ROOT / ".harness/scripts/post-tool-checks.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def call(root: Path, *, tool: str = "apply_patch", session: str = "s") -> str:
    result = subprocess.run(
        ["python3", str(ADAPTER), "post-tool", str(root)],
        input=json.dumps(
            {
                "cwd": str(root),
                "session_id": session,
                "hook_event_name": "PostToolUse",
                "tool_name": tool,
            }
        ),
        text=True,
        capture_output=True,
        check=True,
    )
    assert not result.stderr
    if not result.stdout:
        return ""
    payload = json.loads(result.stdout)
    assert set(payload) == {"hookSpecificOutput"}
    assert payload["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    return str(payload["hookSpecificOutput"]["additionalContext"])


def break_message(root: Path) -> Path:
    script = root / ".harness/scripts/probe.sh"
    script.write_text('#!/bin/sh\necho "→ сделай: почини" >&2\n')
    return script


def test_advisory_bad_good_bad_and_shell_edit(project: Path) -> None:
    assert call(project) == ""
    script = break_message(project)
    assert "без наблюдаемого исхода" in call(project, tool="Bash")
    assert call(project) == ""
    # A separate edit is allowed: the post hook never returns a block decision.
    script.write_text('#!/bin/sh\necho "→ команда: sh probe.sh" >&2\n')
    assert call(project) == ""
    break_message(project)
    assert "без наблюдаемого исхода" in call(project)


def test_group_dispatch_and_unrelated_edits(
    project: Path, dispatcher: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def check(root: Path, name: str) -> tuple[str, bool]:
        calls.append(name)
        return "", True

    monkeypatch.setattr(dispatcher, "run_check", check)
    assert dispatcher.inspect(project, "s") == ""
    assert calls == list(dispatcher.CHECKS)  # First observation checks inherited state.
    calls.clear()
    (project / "README.md").write_text("unrelated")
    assert dispatcher.inspect(project, "s") == ""
    assert calls == []
    break_message(project)
    dispatcher.inspect(project, "s")
    assert calls == ["messages_are_actionable"]
    calls.clear()
    gate = project / ".harness/scripts/gate.sh"
    gate.write_text(gate.read_text() + "\n# edited\n")
    dispatcher.inspect(project, "s")
    assert calls == ["messages_are_actionable", "checks_are_wired"]


def test_rule_can_be_completed_across_edits_and_empty_directory_detected(project: Path) -> None:
    assert call(project) == ""
    rules = project / ".harness/rules"
    rules.mkdir()
    (rules / "probe.md").write_text("---\nid: probe\nerror_class: test\n---\n")
    assert "rules_have_reproducers" in call(project)
    repro = project / ".harness/reproducers/probe"
    repro.mkdir(parents=True)
    assert "пуст" in call(project)
    (repro / "input.txt").write_text("input")
    assert call(project) == ""
    shutil.rmtree(repro)
    assert "rules_have_reproducers" in call(project)


def test_renamed_checker_is_detected(project: Path) -> None:
    assert call(project) == ""
    checks = project / ".harness/scripts/checks"
    (checks / "polish_artifacts.py").rename(checks / "renamed.py")
    assert "не подключена" in call(project)


def test_session_and_worktree_do_not_share_warning_suppression(
    project: Path, tmp_path: Path
) -> None:
    break_message(project)
    assert call(project, session="one")
    assert call(project, session="two")
    other = tmp_path / "other"
    shutil.copytree(project, other, ignore=shutil.ignore_patterns(".git"))
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    assert call(other, session="one")


def test_dependency_change_during_scan_is_not_cached(
    project: Path, dispatcher: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def check(root: Path, name: str) -> tuple[str, bool]:
        if not calls:
            break_message(root)
        calls.append(name)
        return "", True

    monkeypatch.setattr(dispatcher, "run_check", check)
    assert "входы менялись" in dispatcher.inspect(project, "s")
    calls.clear()
    dispatcher.inspect(project, "s")
    assert calls == ["messages_are_actionable"]


def test_lock_contention_does_not_wait(project: Path, dispatcher: ModuleType) -> None:
    state = project / ".git/harness-post-tool"
    state.mkdir()
    key = hashlib.sha256(b"s").hexdigest()
    with (state / f"{key}.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert dispatcher.inspect(project, "s") == ""
    assert dispatcher.inspect(project, "s") == ""
    assert (state / f"{key}.json").is_file()


def test_timeout_is_advisory_and_retried(
    project: Path, dispatcher: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    checker = project / ".harness/scripts/checks/messages_are_actionable.py"
    checker.write_text("import time\ntime.sleep(2)\n")
    monkeypatch.setattr(dispatcher, "CHECK_TIMEOUT", 0.05)
    message, cacheable = dispatcher.run_check(project, "messages_are_actionable")
    assert "не завершилась" in message and not cacheable
    assert "не завершилась" in dispatcher.inspect(project, "s")
    # Timeout leaves no successful fingerprint, so later events can retry.
    key = hashlib.sha256(b"s").hexdigest()
    state = json.loads((project / f".git/harness-post-tool/{key}.json").read_text())
    assert state["messages_are_actionable"]["input"] is None


def test_output_is_capped(project: Path, dispatcher: ModuleType) -> None:
    checker = project / ".harness/scripts/checks/messages_are_actionable.py"
    checker.write_text('import sys\nprint("x" * 1000000)\nsys.exit(1)\n')
    message, _ = dispatcher.run_check(project, "messages_are_actionable")
    assert len(message) < 6200
    assert "сокращён" in message


@pytest.mark.parametrize("payload", ["not json", "[]", "{}"])
def test_bad_payload_cannot_block(project: Path, payload: str) -> None:
    result = subprocess.run(
        ["python3", str(ADAPTER), "post-tool", str(project)],
        input=payload,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert '"decision"' not in result.stdout


def test_unknown_tool_does_not_run_and_git_environment_is_clean(project: Path) -> None:
    break_message(project)
    assert call(project, tool="read_file") == ""
    env = os.environ.copy()
    env["GIT_DIR"] = str(ROOT / ".git")
    env["GIT_WORK_TREE"] = str(ROOT)
    result = subprocess.run(
        ["python3", str(ADAPTER), "post-tool", str(project)],
        input=json.dumps(
            {
                "cwd": str(project),
                "session_id": "s",
                "tool_name": "Bash",
                "hook_event_name": "PostToolUse",
            }
        ),
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "без наблюдаемого исхода" in result.stdout
    assert (project / ".git/harness-post-tool").is_dir()


def test_configured_command_delivers_advice_and_rejects_stale_pin(project: Path) -> None:
    (project / ".agents/hooks").mkdir(parents=True)
    shutil.copy2(ADAPTER, project / ".agents/hooks/codex-harness.py")
    config = json.loads((ROOT / ".codex/hooks.json").read_text())
    handler = config["hooks"]["PostToolUse"][0]["hooks"][0]
    assert handler["timeout"] == 8
    assert handler["additionalContextLimit"] == 0  # Dispatcher bounds all three findings itself.
    payload = json.dumps(
        {
            "cwd": str(project),
            "session_id": "wire",
            "hook_event_name": "PostToolUse",
            "tool_name": "apply_patch",
        }
    )
    break_message(project)

    def invoke() -> str:
        result = subprocess.run(
            ["sh", "-c", handler["command"]],
            cwd=project,
            input=payload,
            text=True,
            capture_output=True,
            check=True,
        )
        parsed = json.loads(result.stdout)
        assert set(parsed) == {"hookSpecificOutput"}
        return str(parsed["hookSpecificOutput"]["additionalContext"])

    assert "без наблюдаемого исхода" in invoke()
    checker = project / ".harness/scripts/checks/messages_are_actionable.py"
    checker.write_text("raise SystemExit('should not execute')\n")
    assert "early checks skipped" in invoke()


def test_rule_fingerprint_includes_calendar_date(
    project: Path, dispatcher: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = dispatcher.fingerprint(project, "rules_have_reproducers")

    class FutureDate(datetime.date):
        @classmethod
        def today(cls) -> FutureDate:
            return cls(2099, 1, 1)

    monkeypatch.setattr(dispatcher.datetime, "date", FutureDate)
    assert dispatcher.fingerprint(project, "rules_have_reproducers") != before


def test_every_large_finding_is_visible_before_suppression(
    project: Path, dispatcher: ModuleType
) -> None:
    for name in dispatcher.CHECKS:
        checker = project / ".harness/scripts/checks" / f"{name}.py"
        checker.write_text('import sys\nprint("x" * 20000)\nsys.exit(1)\n')
    first = dispatcher.inspect(project, "all-errors")
    assert len(first) < 6500
    for name in dispatcher.CHECKS:
        assert f"{name}:" in first
        assert f"python3 .harness/scripts/checks/{name}.py" in first
    assert dispatcher.inspect(project, "all-errors") == ""
