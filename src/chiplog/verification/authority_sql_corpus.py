"""Source-only SQL discovery accounting; unresolved nodes grant no admission."""

import ast
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast


class CorpusInventoryChanged(ValueError):
    pass


class UnresolvedSqlCorpus(ValueError):
    pass


@dataclass(frozen=True)
class CorpusNode:
    module: str
    qualname: str
    ordinal: int
    kind: str
    expression: str
    shape_sha256: str
    scope_sha256: str


@dataclass(frozen=True)
class CurrentSqlCorpus:
    nodes: tuple[CorpusNode, ...]
    source_digests: tuple[tuple[str, str], ...]

    def require_exact_inventory(self, expected: CurrentSqlCorpus) -> None:
        if self.nodes != expected.nodes or tuple(module for module, _ in self.source_digests) != (
            tuple(module for module, _ in expected.source_digests)
        ):
            raise CorpusInventoryChanged("source callable/import/mount inventory differs")

    def require_closed(self) -> None:
        identities = tuple((node.module, node.qualname, node.ordinal) for node in self.nodes)
        modules = tuple(module for module, _ in self.source_digests)
        if (
            not self.nodes
            or len(set(identities)) != len(identities)
            or len(set(modules)) != len(modules)
            or any(node.module not in modules for node in self.nodes)
        ):
            raise UnresolvedSqlCorpus("source accounting is empty, duplicate or has orphan nodes")
        # There is deliberately no path from syntactic accounting to admission.
        # Receiver/instance/assembly resolution is a separate, not-yet-proved stage.
        raise UnresolvedSqlCorpus("receiver, domain, alias and assembly resolution is incomplete")


def inspect_sources(sources: tuple[tuple[str, str], ...]) -> CurrentSqlCorpus:
    return _inspect_sources_with_occurrences(sources)[0]


def _inspect_sources_with_occurrences(
    sources: tuple[tuple[str, str], ...],
) -> tuple[CurrentSqlCorpus, tuple[tuple[CorpusNode, ast.Call, ast.Module], ...]]:
    """Keep exact AST occurrences for the expression join; public accounting is unchanged."""
    if not sources or len({module for module, _ in sources}) != len(sources):
        raise UnresolvedSqlCorpus("source accounting requires unique nonempty input files")
    nodes: list[CorpusNode] = []
    digests = []
    occurrences = []
    for module, source in sorted(sources):
        if not module:
            raise UnresolvedSqlCorpus("source accounting cannot contain an unnamed file")
        digests.append((module, sha256(source.encode()).hexdigest()))
        tree = ast.parse(source, filename=module)
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        scope_hashes: dict[ast.AST, str] = {}
        events: list[tuple[str, int, int, str, ast.AST, str]] = []

        for node in ast.walk(tree):
            kind = None
            if isinstance(node, ast.Call):
                # Keep EVERY call. Terminal-name filtering is only a review tag;
                # renamed imports, aliases and unknown callables must not disappear.
                kind = _call_kind(node.func)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                kind = "import"
            elif isinstance(
                node,
                (
                    ast.Assign,
                    ast.AnnAssign,
                    ast.AugAssign,
                    ast.NamedExpr,
                    ast.Delete,
                ),
            ):
                kind = "binding"
            elif isinstance(node, ast.arg):
                kind = "parameter"
            elif isinstance(node, ast.Attribute) and node.attr in _SQL_NAMES:
                parent = parents.get(node)
                if not isinstance(parent, ast.Call) or parent.func is not node:
                    kind = "callable_reference_candidate"
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                node.name.startswith(("open_", "_open_"))
            ):
                kind = "opener_declaration"
            if kind is not None:
                qualname, scope_digest = _scope(node, parents, tree, scope_hashes)
                located = cast(ast.stmt | ast.expr | ast.arg, node)
                events.append(
                    (qualname, located.lineno, located.col_offset, kind, node, scope_digest)
                )
        ordinals: dict[str, int] = {}
        for qualname, _, _, kind, node, scope_digest in sorted(events, key=lambda event: event[:4]):
            ordinals[qualname] = ordinals.get(qualname, 0) + 1
            evidence = CorpusNode(
                module,
                qualname,
                ordinals[qualname],
                kind,
                ast.unparse(node),
                _shape(node),
                scope_digest,
            )
            nodes.append(evidence)
            if kind == "sql_call_candidate":
                occurrences.append((evidence, cast(ast.Call, node), tree))
    return CurrentSqlCorpus(tuple(nodes), tuple(digests)), tuple(occurrences)


_SQL_NAMES = frozenset(
    {
        "execute",
        "executemany",
        "executescript",
        "connect",
        "cursor",
        "read_connection",
        "workspace_snapshot",
        "join_workspace_snapshot",
    }
)


def _shape(node: ast.AST) -> str:
    return sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()


def _scope(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
    tree: ast.Module,
    scope_hashes: dict[ast.AST, str],
) -> tuple[str, str]:
    scopes = []
    parent = parents.get(node)
    while parent is not None:
        if isinstance(parent, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes.append(parent)
        parent = parents.get(parent)
    nearest = scopes[0] if scopes else tree
    if nearest not in scope_hashes:
        scope_hashes[nearest] = _shape(nearest)
    return ".".join(item.name for item in reversed(scopes)) or "<module>", scope_hashes[nearest]


def _call_kind(function: ast.expr) -> str:
    name = (
        function.id
        if isinstance(function, ast.Name)
        else (function.attr if isinstance(function, ast.Attribute) else None)
    )
    if name in {"execute", "executemany", "executescript"}:
        return "sql_call_candidate"
    if name in _SQL_NAMES:
        return "factory_or_wrapper_call_candidate"
    if name in {"_open_runtime", "_open_installed_h1_runtime"}:
        return "mount_call_candidate"
    if name in {"getattr", "setattr", "delattr", "__import__", "import_module"}:
        return "reflection_call"
    if not isinstance(function, (ast.Name, ast.Attribute)):
        return "dynamic_call"
    return "other_call"


def current_sql_corpus(root: Path) -> CurrentSqlCorpus:
    sources = tuple(
        (str(path.relative_to(root)), path.read_bytes().decode("utf-8"))
        for path in sorted((root / "src").rglob("*.py"))
    )
    return inspect_sources(sources)
