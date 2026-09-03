"""Transitive mechanical gate for the production R7 executable import closure."""

from __future__ import annotations

import ast
from pathlib import Path


class R7BypassViolation(ValueError):
    pass


def _module_path(source_root: Path, module: str) -> Path | None:
    relative = Path(*module.split("."))
    file_path = source_root / relative.with_suffix(".py")
    package_path = source_root / relative / "__init__.py"
    if file_path.is_file():
        return file_path
    if package_path.is_file():
        return package_path
    return None


def _module_name(source_root: Path, path: Path) -> str:
    parts = list(path.relative_to(source_root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _static_string(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_string(node.left)
        right = _static_string(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def verify_canonical_r7_entrypoint(path: Path) -> None:
    source_root = next(
        (parent for parent in path.parents if (parent / "chiplog").is_dir()), path.parent
    )
    entry_tree = ast.parse(path.read_text(), filename=str(path))
    entry_imports = {
        node.module
        for node in ast.walk(entry_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    if "chiplog.composition.r7_planning" not in entry_imports:
        raise R7BypassViolation("production entrypoint does not use the canonical R7 runtime")
    queue = [path]
    visited: set[Path] = set()
    while queue:
        current = queue.pop()
        if current in visited:
            continue
        visited.add(current)
        importer = _module_name(source_root, current)
        tree = ast.parse(current.read_text(), filename=str(current))
        dynamic_names = {"__import__", "exec", "eval", "compile"}
        for imported in ast.walk(tree):
            if isinstance(imported, ast.ImportFrom):
                for alias in imported.names:
                    if alias.name in {"__import__", "import_module"}:
                        dynamic_names.add(alias.asname or alias.name)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "import_module"
                and importer != "chiplog.platform.r7_runtime"
            ):
                raise R7BypassViolation("dynamic import reference can hide an in-process bypass")
            if isinstance(node, ast.Constant) and node.value in {
                "__import__",
                "import_module",
            }:
                raise R7BypassViolation("dynamic import name can hide an in-process bypass")
            if isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else ""
                attribute = node.func.attr if isinstance(node.func, ast.Attribute) else ""
                dynamic = name in dynamic_names or attribute in {
                    "__import__",
                    "import_module",
                }
                manifested_leaf_load = (
                    importer == "chiplog.platform.r7_runtime" and attribute == "import_module"
                )
                if (
                    isinstance(node.func, ast.Call)
                    and isinstance(node.func.func, ast.Name)
                    and node.func.func.id == "getattr"
                    and len(node.func.args) >= 2
                ):
                    dynamic = dynamic or _static_string(node.func.args[1]) in {
                        "__import__",
                        "import_module",
                    }
                if dynamic and not manifested_leaf_load:
                    raise R7BypassViolation("dynamic import can hide an in-process bypass")
            modules: tuple[str, ...] = ()
            imported_names: set[str] = set()
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    base = importer.split(".")
                    if current.name != "__init__.py":
                        base.pop()
                    for _ in range(node.level - 1):
                        if base:
                            base.pop()
                    module = ".".join((*base, *((node.module,) if node.module else ())))
                imported_names = {alias.name for alias in node.names}
                modules = (module,)
                modules += tuple(
                    f"{module}.{alias.name}" for alias in node.names if alias.name != "*"
                )
                if "import_module" in imported_names:
                    raise R7BypassViolation("dynamic import alias can hide an in-process bypass")
            elif isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            for module in modules:
                if module == "chiplog.composition.r6" or "open_r6_runtime" in imported_names:
                    raise R7BypassViolation("transitive production closure contains an R6 bypass")
                if "_PlanningUseCase" in imported_names:
                    raise R7BypassViolation("private in-process planning use case bypasses R7")
                if (
                    module
                    in {
                        "chiplog.adapters.driven.planning_sqlite",
                        "chiplog.platform._sqlite",
                    }
                    and importer != "chiplog.composition.r7_planning"
                ):
                    raise R7BypassViolation(
                        "raw planning authority bypasses the R7 broker boundary"
                    )
                target = _module_path(source_root, module)
                if target is not None and module not in {
                    "chiplog.adapters.driven.planning_sqlite",
                    "chiplog.platform._sqlite",
                }:
                    queue.append(target)


__all__ = ["R7BypassViolation", "verify_canonical_r7_entrypoint"]
