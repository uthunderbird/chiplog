"""Observe pytest phases and compare a bijective placement-only nodeid migration.

Plugin: PYTHONPATH=quality MIGRATION_REPORT=report.json uv run pytest -p migration_outcomes ...
Audit: python quality/migration_outcomes.py BEFORE AFTER MAP
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

_items: list[str] = []
_reports: list[dict[str, object]] = []


def pytest_collection_finish(session: pytest.Session) -> None:
    _items.extend(item.nodeid for item in session.items)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    _reports.append(
        {
            "nodeid": report.nodeid,
            "when": report.when,
            "outcome": report.outcome,
            "wasxfail": getattr(report, "wasxfail", None),
            "reason": str(report.longrepr) if report.skipped or report.failed else None,
        }
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    Path(os.environ["MIGRATION_REPORT"]).write_text(
        json.dumps({"items": _items, "reports": _reports, "exitstatus": int(exitstatus)}, indent=2)
        + "\n"
    )


def compare(before: dict[str, Any], after: dict[str, Any], mapping: dict[str, str]) -> None:
    assert before["exitstatus"] == after["exitstatus"] == 0
    assert len(before["items"]) == len(set(before["items"])) == len(mapping)
    assert len(after["items"]) == len(set(after["items"])) == len(mapping)
    assert set(before["items"]) == set(mapping)
    assert set(after["items"]) == set(mapping.values())
    assert len(set(mapping.values())) == len(mapping)
    for old, new in mapping.items():
        assert old.split("::", 1)[1] == new.split("::", 1)[1], (old, new)
    normalized = [{**report, "nodeid": mapping[report["nodeid"]]} for report in before["reports"]]

    def key(report: dict[str, Any]) -> tuple[str, str]:
        return str(report["nodeid"]), str(report["when"])

    assert sorted(normalized, key=key) == sorted(after["reports"], key=key)
    print(
        f"Matched {len(mapping)} full nodeids and {len(normalized)} phase reports "
        "including skip/xfail"
    )


if __name__ == "__main__":
    old_path, new_path, map_path = map(Path, sys.argv[1:])
    compare(
        json.loads(old_path.read_text()),
        json.loads(new_path.read_text()),
        json.loads(map_path.read_text())["nodeids"],
    )
