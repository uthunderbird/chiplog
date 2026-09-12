from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_owner_process_modules_import_no_foreign_chiplog_policy() -> None:
    for owner in ("deployment_trust", "planning", "projections"):
        source = ROOT / f"src/chiplog/capabilities/{owner}/_r7_process.py"
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("chiplog")
            ):
                assert node.module.startswith(f"chiplog.capabilities.{owner}")
            if isinstance(node, ast.Import):
                assert all(
                    not alias.name.startswith("chiplog")
                    or alias.name.startswith(f"chiplog.capabilities.{owner}")
                    for alias in node.names
                )
