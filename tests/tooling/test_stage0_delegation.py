from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("mode", "options", "success", "leaf_runs"),
    [
        ("valid", (), True, 1),
        ("valid", ("--no-stage0-dedup",), True, 2),
        ("skip", (), False, 0),
        ("xfail", (), False, 0),
        ("unconfirmed", (), False, 1),
        ("missing", (), False, 1),
        ("badphase", (), False, 1),
        ("skip", ("--no-stage0-dedup",), True, 1),
        ("skip", ("-k", "leaf"), True, 1),
        ("skip", ("--stepwise",), True, 1),
        ("skip", ("--collect-only",), True, 0),
        ("skip", ("--setup-only",), True, 0),
    ],
)
def test_delegation_requires_actual_child_coverage_and_a_passing_driver(
    tmp_path: Path, mode: str, options: tuple[str, ...], success: bool, leaf_runs: int
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    (tests / "conftest.py").write_text(
        "from tests.support import stage0_delegation as plugin\n"
        "pytest_plugins = ('tests.support.stage0_delegation',)\n"
        "plugin.STAGE0_NODEID = 'tests/test_driver.py::test_driver'\n"
        "plugin.stage0_test_slices = lambda: {'R1': ('tests/test_leaf.py',)}\n"
    )
    (tests / "test_leaf.py").write_text(
        "from pathlib import Path\n"
        "def test_leaf():\n"
        "    with Path('executions').open('a') as output:\n"
        "        output.write('executed\\n')\n"
    )
    decorator = {
        "skip": "@pytest.mark.skip(reason='negative probe')\n",
        "xfail": "@pytest.mark.xfail(run=False, reason='negative probe')\n",
    }.get(mode, "")
    body = f"""
import json, subprocess, sys
import pytest
from tests.support.stage0_delegation import prepare_reports, confirm_reports
{decorator}def test_driver(request, monkeypatch, tmp_path):
    path = tmp_path / 'reports'
    prepare_reports(request.config, monkeypatch, path)
    subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests/test_leaf.py'], check=True)
    if {mode!r} == 'unconfirmed':
        return
    if {mode!r} == 'missing':
        for file in path.glob('*.json'):
            file.unlink()
    if {mode!r} == 'badphase':
        for file in path.glob('*.json'):
            data = json.loads(file.read_text())
            data['phases'].pop()
            file.write_text(json.dumps(data))
    result = {{'checks': [{{'check_id': 'stage0.r1', 'observed': {{
        'command': ['uv', 'run', 'pytest', '-q', 'tests/test_leaf.py']
    }}}}]}}
    confirm_reports(request.config, result, path)
"""
    (tests / "test_driver.py").write_text(body)
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "CHIPLOG_STAGE0_CHILD_REPORTS"):
        env.pop(key, None)
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *options],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert (completed.returncode == 0) == success, completed.stdout + completed.stderr
    executions = tmp_path / "executions"
    assert (len(executions.read_text().splitlines()) if executions.exists() else 0) == leaf_runs
