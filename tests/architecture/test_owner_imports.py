from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _assert_owner_imports(owner: str, source: str) -> None:
    prefix = f"chiplog.capabilities.{owner}"
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("chiplog"):
            if (
                owner == "deployment_trust"
                and node.module == "chiplog.capabilities.agent_loop.delivery_contracts"
                and {alias.name for alias in node.names} == {"ExactHead"}
            ):
                continue
            assert node.module == prefix or node.module.startswith(prefix + ".")
        if isinstance(node, ast.Import):
            assert all(
                not alias.name.startswith("chiplog")
                or alias.name == prefix
                or alias.name.startswith(prefix + ".")
                for alias in node.names
            )


def test_owner_process_modules_import_no_foreign_chiplog_policy() -> None:
    for owner in ("deployment_trust", "planning", "projections"):
        source = ROOT / f"src/chiplog/capabilities/{owner}/_r7_process.py"
        _assert_owner_imports(owner, source.read_text())


def test_owner_import_check_accepts_exact_data_contract_alias() -> None:
    _assert_owner_imports(
        "deployment_trust",
        "from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead as Head",
    )


@pytest.mark.parametrize(
    "owner,statement",
    [
        ("deployment_trust", "from chiplog.capabilities.agent_loop.contracts import LoopRejected"),
        (
            "deployment_trust",
            "from chiplog.capabilities.agent_loop.delivery_contracts import delivery_manifest_head",
        ),
        (
            "deployment_trust",
            "from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, DeliveryDTO",
        ),
        ("deployment_trust", "from chiplog.capabilities.agent_loop.delivery_contracts import *"),
        ("planning", "from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead"),
        ("planning", "from chiplog.capabilities.planning_foreign import Policy"),
        ("planning", "import chiplog.capabilities.planning_foreign"),
        ("deployment_trust", "import chiplog.capabilities.agent_loop.delivery_contracts"),
    ],
)
def test_owner_import_check_rejects_foreign_policy(owner: str, statement: str) -> None:
    with pytest.raises(AssertionError):
        _assert_owner_imports(owner, statement)
