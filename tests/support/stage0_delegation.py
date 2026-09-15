"""Delegate duplicate full-suite cases to the real stage0 test, with receipts."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from chiplog.verification.runner import stage0_test_slices

STAGE0_NODEID = (
    "tests/verification/test_profiles.py::"
    "test_stage0_evidences_r0_through_r8_and_holds_deployment_eligibility"
)
_REPORT_ENV = "CHIPLOG_STAGE0_CHILD_REPORTS"


@dataclass
class _State:
    delegated: set[str] = field(default_factory=set)
    receipt: bool = False
    stage0_passed: bool = False
    phases: list[dict[str, object]] = field(default_factory=list)
    report_directory: str | None = None


_KEY = pytest.StashKey[_State]()


def _state(config: pytest.Config) -> _State:
    if _KEY not in config.stash:
        config.stash[_KEY] = _State(report_directory=os.environ.get(_REPORT_ENV))
    return config.stash[_KEY]


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--no-stage0-dedup", action="store_true", help="Run all outer tests too")


def pytest_configure(config: pytest.Config) -> None:
    _state(config)


def _full_unfiltered(config: pytest.Config) -> bool:
    if len(config.args) != 1 or Path(str(config.args[0])).resolve() != config.rootpath / "tests":
        return False
    return not any(
        config.getoption(option, default=False)
        for option in (
            "--no-stage0-dedup",
            "collectonly",
            "setuponly",
            "keyword",
            "markexpr",
            "lf",
            "newfirst",
            "failedfirst",
            "stepwise",
            "deselect",
            "ignore",
            "ignore_glob",
            "numprocesses",
        )
    )


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> Generator[None]:
    yield
    state = _state(config)
    if state.report_directory or not _full_unfiltered(config):
        return
    if STAGE0_NODEID not in {item.nodeid for item in items}:
        return
    selectors = tuple(path for paths in stage0_test_slices().values() for path in paths)
    delegated = [
        item
        for item in items
        if item.nodeid != STAGE0_NODEID
        and any(
            item.nodeid == selector
            or item.nodeid.startswith(selector + "::")
            or item.nodeid.startswith(selector + "[")
            for selector in selectors
        )
    ]
    state.delegated = {item.nodeid for item in delegated}
    items[:] = [item for item in items if item.nodeid not in state.delegated]
    config.hook.pytest_deselected(items=delegated)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    state = _state(item.config)
    xfail = getattr(report, "wasxfail", None)
    if state.report_directory:
        state.phases.append(
            {
                "nodeid": report.nodeid,
                "when": report.when,
                "outcome": report.outcome,
                "wasxfail": xfail,
            }
        )
    if report.nodeid == STAGE0_NODEID and report.when == "call":
        state.stage0_passed = report.passed and xfail is None
    return report


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    state = _state(session.config)
    if state.delegated and not (state.receipt and state.stage0_passed) and exitstatus == 0:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        terminal = session.config.pluginmanager.getplugin("terminalreporter")
        if terminal is not None:
            terminal.write_line("ERROR: delegated stage0 coverage was not confirmed", red=True)
    if state.report_directory:
        payload = {
            "root": str(session.config.rootpath.resolve()),
            "args": list(session.config.invocation_params.args),
            "collected": [item.nodeid for item in session.items],
            "phases": state.phases,
            "exitstatus": int(session.exitstatus),
        }
        directory = Path(state.report_directory)
        temporary = directory / f"{uuid.uuid4().hex}.tmp"
        temporary.write_text(json.dumps(payload))
        temporary.rename(temporary.with_suffix(".json"))


def pytest_terminal_summary(terminalreporter: Any, config: pytest.Config) -> None:
    state = _state(config)
    if state.delegated:
        confirmed = state.receipt and state.stage0_passed
        terminalreporter.write_line(
            f"stage0: {len(state.delegated)} outer cases delegated to child pytest; "
            f"coverage {'confirmed' if confirmed else 'NOT confirmed'}"
        )


def prepare_reports(config: pytest.Config, monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    if _state(config).delegated:
        path.mkdir()
        monkeypatch.setenv(_REPORT_ENV, str(path))


def confirm_reports(config: pytest.Config, result: dict[str, Any], path: Path) -> None:
    state = _state(config)
    if not state.delegated:
        return
    expected = [
        check["observed"]["command"][3:]
        for check in result["checks"]
        if check["check_id"].startswith("stage0.")
    ]
    reports = [json.loads(file.read_text()) for file in path.glob("*.json")]
    assert len(reports) == len(expected) == len(stage0_test_slices()), "missing child reports"
    covered: set[str] = set()
    for command in expected:
        matching = [report for report in reports if report["args"] == command]
        assert matching, f"no stage0 report for {command}"
        report = matching[0]
        reports.remove(report)
        assert report["root"] == str(config.rootpath.resolve())
        assert report["exitstatus"] == 0
        collected = report["collected"]
        assert collected and len(collected) == len(set(collected))
        phases = report["phases"]
        assert len(phases) == 3 * len(collected)
        assert {(p["nodeid"], p["when"]) for p in phases} == {
            (nodeid, when) for nodeid in collected for when in ("setup", "call", "teardown")
        }
        assert all(p["outcome"] == "passed" and p["wasxfail"] is None for p in phases)
        covered.update(collected)
    assert covered == state.delegated, "stage0 collection differs from delegated outer cases"
    state.receipt = True
