"""Reject manual resource closing at production-source call sites.

Ruff's SIM115 covers ordinary file opens. This check additionally catches the
general ``try/finally`` + ``close`` pattern that Ruff cannot model.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


def _is_contextmanager(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        (
            isinstance(decorator, ast.Name)
            and decorator.id in {"contextmanager", "asynccontextmanager"}
        )
        or (
            isinstance(decorator, ast.Attribute)
            and decorator.attr in {"contextmanager", "asynccontextmanager"}
        )
        for decorator in node.decorator_list
    )


def _manual_close(statements: list[ast.stmt]) -> bool:
    for node in ast.walk(ast.Module(body=statements, type_ignores=[])):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Attribute) and function.attr == "close":
            return True
    return False


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self._source_lines = path.read_text(encoding="utf-8").splitlines()
        self._contextmanager_depth = 0
        self.problems: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._contextmanager_depth += _is_contextmanager(node)
        self.generic_visit(node)
        self._contextmanager_depth -= _is_contextmanager(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def _has_justified_manual_close(self, node: ast.Try) -> bool:
        if node.lineno < 2:
            return False
        comment = self._source_lines[node.lineno - 2].strip()
        return bool(re.fullmatch(r"# resource-contexts: justified-manual-close — .+", comment))

    def visit_Try(self, node: ast.Try) -> None:
        if (
            node.finalbody
            and not self._contextmanager_depth
            and _manual_close(node.finalbody)
            and not self._has_justified_manual_close(node)
        ):
            self.problems.append(
                f"{self.path}:{node.lineno}: manual close in try/finally\n"
                "  → сделай: оберни ресурс в context manager или поставь над try "
                "# resource-contexts: justified-manual-close — <reason>\n"
                "  ✓ resource lifetime выражен через with/async with либо имеет "
                "явно проверяемое исключение"
            )
        self.generic_visit(node)


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) == 2 else "src")
    problems: list[str] = []
    for path in sorted(root.rglob("*.py")):
        visitor = _Visitor(path)
        visitor.visit(ast.parse("\n".join(visitor._source_lines), filename=str(path)))
        problems.extend(visitor.problems)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
