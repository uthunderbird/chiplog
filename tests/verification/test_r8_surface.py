from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.verification.r8_surface import (
    SURFACES,
    R8SurfaceViolation,
    verify_offline_import_boundary,
    verify_r8_surfaces,
)
from tests.support.authority_and_surfaces import ROOT


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "alias", "downstream"])
def test_surface_inventory_mutants_reject(mutation: str) -> None:
    changed = {
        "missing": SURFACES[:1],
        "extra": (*SURFACES, replace(SURFACES[0], surface_id="unknown")),
        "duplicate": (*SURFACES, SURFACES[0]),
        "alias": (replace(SURFACES[0], capability="other"), SURFACES[1]),
        "downstream": (replace(SURFACES[0], downstream=("cli._create",)), SURFACES[1]),
    }[mutation]
    with pytest.raises(R8SurfaceViolation):
        verify_r8_surfaces(ROOT, changed)


@pytest.mark.parametrize("mutant", ["old-runtime", "independent-replay", "external-provider"])
def test_reached_executable_mutants_reject(tmp_path: Path, mutant: str) -> None:
    source = tmp_path / "src/chiplog"
    shutil.copytree(ROOT / "src/chiplog", source)
    verify_r8_surfaces(tmp_path)
    verify_offline_import_boundary(tmp_path)
    cli = source / "cli.py"
    if mutant == "old-runtime":
        cli.write_text(
            cli.read_text().replace("chiplog.composition.r8", "chiplog.composition.r7_planning")
        )
    elif mutant == "independent-replay":
        cli.write_text(
            cli.read_text().replace(
                "outcome = await runtime.create(", "outcome = await runtime._create("
            )
        )
    else:
        (source / "new_adapter.py").write_text("import openai\n")
    with pytest.raises(R8SurfaceViolation):
        verify_r8_surfaces(tmp_path)
        verify_offline_import_boundary(tmp_path)


@pytest.mark.parametrize(
    "mutation", ["bootstrap-output", "main-output", "dead-gate", "plain-helper"]
)
def test_audited_implementation_identity_rejects_unreviewed_behavior(
    tmp_path: Path, mutation: str
) -> None:
    source = tmp_path / "src/chiplog"
    shutil.copytree(ROOT / "src/chiplog", source)
    verify_r8_surfaces(tmp_path)
    if mutation == "plain-helper":
        (source / "new_helper.py").write_text("def extra():\n    return 1\n")
    elif mutation == "dead-gate":
        path = source / "composition/r8.py"
        original = path.read_text()
        assert "result = self._gate.commit_handoff(" in original
        path.write_text(
            original.replace(
                "result = self._gate.commit_handoff(",
                "return None\n        result = self._gate.commit_handoff(",
                1,
            )
        )
    else:
        path = source / "cli.py"
        marker = (
            "async def _bootstrap(args: argparse.Namespace) -> None:"
            if mutation == "bootstrap-output"
            else "def main() -> None:"
        )
        original = path.read_text()
        assert marker in original
        path.write_text(
            original.replace(marker, marker + '\n    print("unexpected fixture output")', 1)
        )
    with pytest.raises(R8SurfaceViolation, match="implementation"):
        verify_r8_surfaces(tmp_path)


@pytest.mark.parametrize(
    "relative,statement",
    [
        ("composition/r9.py", "from agent_dashboard import render_screen, screen_to_dict"),
        ("capabilities/projections/workspace.py", "from agent_dashboard import DashboardScreen"),
    ],
)
def test_offline_gate_accepts_registered_stateless_dashboard_imports(
    tmp_path: Path, relative: str, statement: str
) -> None:
    target = tmp_path / "src/chiplog" / relative
    target.parent.mkdir(parents=True)
    target.write_text(statement + "\n")
    verify_offline_import_boundary(tmp_path)


@pytest.mark.parametrize(
    "relative,statement",
    [
        ("new_adapter.py", "from agent_dashboard import render_screen"),
        ("composition/r9.py", "import agent_dashboard"),
        ("composition/r9.py", "from agent_dashboard import ScreenHub"),
        ("composition/r9.py", "from agent_dashboard import *"),
        ("composition/r9.py", "from agent_dashboard.hub import ScreenHub"),
        ("composition/r9.py", "from agent_dashboard import DashboardScreen"),
        ("capabilities/projections/workspace.py", "from agent_dashboard.tui import run"),
        ("composition/r9.py", "import openai"),
        ("capabilities/projections/workspace.py", "import socket"),
    ],
)
def test_offline_gate_rejects_unregistered_dashboard_and_transport_imports(
    tmp_path: Path, relative: str, statement: str
) -> None:
    target = tmp_path / "src/chiplog" / relative
    target.parent.mkdir(parents=True)
    target.write_text(statement + "\n")
    # Exercise this gate directly: the separate source-hash gate must not mask it.
    with pytest.raises(R8SurfaceViolation):
        verify_offline_import_boundary(tmp_path)


def test_offline_gate_accepts_only_registered_scheduler_version_query(tmp_path: Path) -> None:
    target = tmp_path / "src/chiplog/composition/scheduler_source_registry.py"
    target.parent.mkdir(parents=True)
    target.write_text("from importlib.metadata import version\n")
    verify_offline_import_boundary(tmp_path)


@pytest.mark.parametrize(
    "relative,statement",
    [
        ("new_adapter.py", "from importlib.metadata import version"),
        ("composition/scheduler_source_registry.py", "import importlib"),
        ("composition/scheduler_source_registry.py", "import importlib.metadata"),
        ("composition/scheduler_source_registry.py", "from importlib import import_module"),
        ("composition/scheduler_source_registry.py", "from importlib.metadata import *"),
        (
            "composition/scheduler_source_registry.py",
            "from importlib.metadata import version, entry_points",
        ),
        ("composition/scheduler_source_registry.py", "import socket"),
        ("composition/scheduler_source_registry.py", "import ssl"),
        ("composition/scheduler_source_registry.py", "import ctypes"),
        ("composition/scheduler_source_registry.py", "import openai"),
    ],
)
def test_scheduler_profile_does_not_admit_dynamic_or_transport_imports(
    tmp_path: Path, relative: str, statement: str
) -> None:
    target = tmp_path / "src/chiplog" / relative
    target.parent.mkdir(parents=True)
    target.write_text(statement + "\n")
    with pytest.raises(R8SurfaceViolation):
        verify_offline_import_boundary(tmp_path)
