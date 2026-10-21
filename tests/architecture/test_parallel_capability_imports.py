import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_parallel_capabilities_have_no_direct_cross_imports() -> None:
    packages = (
        "chiplog.capabilities.deployment_trust",
        "chiplog.capabilities.planning",
        "chiplog.capabilities.projections",
    )
    for package in packages:
        package_path = ROOT / "src" / Path(*package.split("."))
        for source in package_path.rglob("*.py"):
            tree = ast.parse(source.read_text())
            imported = {
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module is not None
            } | {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            denied = set(packages) - {package}
            assert not any(
                name == target or name.startswith(f"{target}.")
                for name in imported
                for target in denied
            )
