from __future__ import annotations

from pathlib import Path

import pytest

from chiplog.verification.r7_bypass import R7BypassViolation, verify_canonical_r7_entrypoint

ROOT = Path(__file__).parents[2]


def test_production_entrypoint_has_no_direct_r6_bypass() -> None:
    verify_canonical_r7_entrypoint(ROOT / "src/chiplog/cli.py")


@pytest.mark.parametrize(
    "bypass",
    [
        "from chiplog.composition.r6 import open_r6_runtime",
        "from chiplog.capabilities.planning._planning import _PlanningUseCase",
        "from chiplog.adapters.driven.planning_sqlite import SQLitePlanningRepository",
        "from chiplog.platform._sqlite import EventAppender",
    ],
)
def test_gate_rejects_every_registered_direct_in_process_bypass(
    tmp_path: Path, bypass: str
) -> None:
    candidate = tmp_path / "cli.py"
    candidate.write_text(
        "from chiplog.composition.r7_planning import open_r7_runtime\n" + bypass + "\n"
    )
    with pytest.raises(R7BypassViolation, match="bypass"):
        verify_canonical_r7_entrypoint(candidate)


def test_gate_follows_helper_imports_to_transitive_r6_bypass(tmp_path: Path) -> None:
    source = tmp_path / "src"
    package = source / "chiplog"
    package.mkdir(parents=True)
    (package / "cli.py").write_text(
        "from chiplog.composition.r7_planning import open_r7_runtime\n"
        "from chiplog.helper import run\n"
    )
    (package / "helper.py").write_text(
        "from chiplog.composition.r6 import open_r6_runtime\nrun = open_r6_runtime\n"
    )
    with pytest.raises(R7BypassViolation, match="transitive"):
        verify_canonical_r7_entrypoint(package / "cli.py")


def test_gate_follows_relative_helper_import(tmp_path: Path) -> None:
    source = tmp_path / "src"
    package = source / "chiplog"
    package.mkdir(parents=True)
    (package / "cli.py").write_text(
        "from chiplog.composition.r7_planning import open_r7_runtime\nfrom .helper import run\n"
    )
    (package / "helper.py").write_text(
        "from chiplog.composition.r6 import open_r6_runtime\nrun = open_r6_runtime\n"
    )
    with pytest.raises(R7BypassViolation, match="transitive"):
        verify_canonical_r7_entrypoint(package / "cli.py")


def test_gate_follows_relative_package_member_import(tmp_path: Path) -> None:
    package = tmp_path / "src/chiplog"
    package.mkdir(parents=True)
    (package / "cli.py").write_text(
        "from chiplog.composition.r7_planning import open_r7_runtime\nfrom . import helper\n"
    )
    (package / "helper.py").write_text("from chiplog.composition.r6 import open_r6_runtime\n")
    with pytest.raises(R7BypassViolation, match="transitive"):
        verify_canonical_r7_entrypoint(package / "cli.py")


def test_gate_follows_member_of_relative_subpackage(tmp_path: Path) -> None:
    package = tmp_path / "src/chiplog"
    helpers = package / "helpers"
    helpers.mkdir(parents=True)
    (helpers / "__init__.py").write_text("")
    (package / "cli.py").write_text(
        "from chiplog.composition.r7_planning import open_r7_runtime\nfrom .helpers import bypass\n"
    )
    (helpers / "bypass.py").write_text("from chiplog.composition.r6 import open_r6_runtime\n")
    with pytest.raises(R7BypassViolation, match="transitive"):
        verify_canonical_r7_entrypoint(package / "cli.py")


@pytest.mark.parametrize(
    "dynamic",
    [
        'getattr(__builtins__, "__import__")("chiplog.composition.r6")',
        'import importlib\ngetattr(importlib, "import_" + "module")("chiplog.composition.r6")',
        'import importlib\nloader = importlib.import_module\nloader("chiplog.composition." + "r6")',
        'exec("from chiplog.platform._sqlite import EventAppender", {})',
        'from builtins import __import__ as load\nload("chiplog.composition.r6")',
    ],
)
def test_gate_rejects_obscured_dynamic_bypass(tmp_path: Path, dynamic: str) -> None:
    candidate = tmp_path / "cli.py"
    candidate.write_text(
        "from chiplog.composition.r7_planning import open_r7_runtime\n" + dynamic + "\n"
    )
    with pytest.raises(R7BypassViolation, match="dynamic"):
        verify_canonical_r7_entrypoint(candidate)
