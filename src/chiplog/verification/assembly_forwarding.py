"""Snapshot-relative direct assembly edges, not runtime admission."""

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import cast


class UnknownAssemblyForwarding(ValueError):
    pass


class UnregisteredAssemblyMount(ValueError):
    pass


@dataclass(frozen=True)
class AssemblySymbol:
    name: str
    kind: str
    origin: str
    default: str | None = None


@dataclass(frozen=True)
class AssemblyEdge:
    module: str
    caller: str
    ordinal: int
    callee: str
    runtime: AssemblySymbol | None
    manifest: AssemblySymbol


@dataclass(frozen=True)
class AssemblyForwarding:
    edges: tuple[AssemblyEdge, ...]

    def require_closed(self) -> None:
        raise UnknownAssemblyForwarding("assembly parameter and semantic binding is incomplete")


_OWNERS = {
    "_open_runtime": "chiplog.composition.r7_planning",
    "_open_installed_h1_runtime": "chiplog.composition.common_cli_execution_runtime",
}


def _module(path: str) -> str:
    return path.removesuffix(".py").replace("/", ".")


def _import_module(module: str, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    parts = module.split(".")[: -node.level]
    if node.module:
        parts.extend(node.module.split("."))
    return ".".join(parts)


def _binds(node: ast.AST, name: str) -> bool:
    return (
        (
            isinstance(node, ast.Name)
            and node.id == name
            and isinstance(node.ctx, (ast.Store, ast.Del))
        )
        or (isinstance(node, ast.arg) and node.arg == name)
        or (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == name
        )
        or (isinstance(node, ast.ExceptHandler) and node.name == name)
        or (isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names)
        or (
            isinstance(node, (ast.Import, ast.ImportFrom))
            and any(
                (
                    alias.asname
                    or (
                        alias.name if isinstance(node, ast.ImportFrom) else alias.name.split(".")[0]
                    )
                )
                == name
                for alias in node.names
            )
        )
        or (isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == name)
        or (isinstance(node, ast.MatchMapping) and node.rest == name)
    )


def _bindings(tree: ast.Module, module: str, name: str) -> str:
    matches = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if (alias.asname or alias.name) == name:
                    if node not in tree.body:
                        raise UnknownAssemblyForwarding(f"non-module symbol import: {name}")
                    matches.append(f"{_import_module(module, node)}:{alias.name}")
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name == name
        ):
            if node not in tree.body:
                raise UnknownAssemblyForwarding(f"nested symbol definition: {name}")
            matches.append(f"{module}:{name}")
        elif _binds(node, name):
            raise UnknownAssemblyForwarding(f"symbol is rebound or shadowed: {name}")
    if len(matches) != 1:
        raise UnknownAssemblyForwarding(f"missing or duplicate symbol binding: {name}")
    return matches[0]


def _symbol(
    expression: ast.expr,
    method: ast.AsyncFunctionDef,
    tree: ast.Module,
    module: str,
) -> AssemblySymbol:
    if not isinstance(expression, ast.Name):
        raise UnknownAssemblyForwarding("unsupported assembly argument expression")
    name = expression.id
    args = method.args
    parameters = (*args.posonlyargs, *args.args, *args.kwonlyargs)
    if any(arg.arg == name for arg in parameters):
        if name != "manifest" or name not in [arg.arg for arg in args.kwonlyargs]:
            raise UnknownAssemblyForwarding("unsupported assembly parameter")
        for statement in method.body:
            if any(_binds(node, name) for node in ast.walk(statement)):
                raise UnknownAssemblyForwarding("assembly parameter is rebound or shadowed")
        index = [arg.arg for arg in args.kwonlyargs].index(name)
        default = args.kw_defaults[index]
        if default is not None and not isinstance(default, ast.Name):
            raise UnknownAssemblyForwarding("unsupported assembly parameter default")
        origin = None if default is None else _bindings(tree, module, default.id)
        return AssemblySymbol(name, "parameter", f"{module}:{method.name}.{name}", origin)
    local_imports = [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.ImportFrom)
        and any((alias.asname or alias.name) == name for alias in node.names)
    ]
    if local_imports:
        if (
            len(local_imports) != 1
            or local_imports[0] not in method.body
            or (local_imports[0].lineno >= expression.lineno)
        ):
            raise UnknownAssemblyForwarding("assembly local import does not dominate consumer")
        local_scope = ast.Module(body=method.body, type_ignores=[])
        return AssemblySymbol(name, "local_import", _bindings(local_scope, module, name))
    return AssemblySymbol(name, "symbol", _bindings(tree, module, name))


def inspect_assembly_forwarding(sources: tuple[tuple[str, str], ...]) -> AssemblyForwarding:
    if not sources or len({path for path, _ in sources}) != len(sources):
        raise UnknownAssemblyForwarding("unique nonempty source inventory required")
    edges = []
    for path, source in sorted(sources):
        module = _module(path)
        tree = ast.parse(source, filename=path)
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in _OWNERS and alias.asname not in (None, alias.name):
                        raise UnknownAssemblyForwarding("aliased assembly opener is unresolved")
            if isinstance(node, ast.Attribute) and node.attr in _OWNERS:
                raise UnknownAssemblyForwarding("attribute assembly opener is unresolved")
        calls = sorted(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in _OWNERS
            ),
            key=lambda node: (node.lineno, node.col_offset),
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in _OWNERS:
                parent = parents.get(node)
                if not isinstance(parent, ast.Call) or parent.func is not node:
                    raise UnknownAssemblyForwarding("assembly opener escapes direct calls")
        ordinals: dict[str, int] = {}
        for call in calls:
            name = cast(ast.Name, call.func).id
            callee = _bindings(tree, module, name)
            if callee != f"{_OWNERS[name]}:{name}":
                raise UnknownAssemblyForwarding("foreign assembly opener binding")
            parent = parents.get(call)
            frames = []
            while parent is not None:
                if isinstance(
                    parent,
                    (
                        ast.FunctionDef,
                        ast.AsyncFunctionDef,
                        ast.ClassDef,
                        ast.Lambda,
                        ast.ListComp,
                        ast.SetComp,
                        ast.DictComp,
                        ast.GeneratorExp,
                    ),
                ):
                    frames.append(parent)
                parent = parents.get(parent)
            if (
                len(frames) != 1
                or not isinstance(frames[0], ast.AsyncFunctionDef)
                or (frames[0] not in tree.body)
            ):
                raise UnknownAssemblyForwarding("unsupported assembly consumer frame")
            method = frames[0]
            if any(isinstance(arg, ast.Starred) for arg in call.args) or any(
                keyword.arg is None for keyword in call.keywords
            ):
                raise UnknownAssemblyForwarding("unknown expanded assembly arguments")
            keywords = {keyword.arg: keyword.value for keyword in call.keywords}
            if len(keywords) != len(call.keywords) or "manifest" not in keywords:
                raise UnknownAssemblyForwarding("missing or duplicate manifest argument")
            runtime = None
            if name == "_open_runtime":
                if "runtime_type" not in keywords:
                    raise UnknownAssemblyForwarding("missing runtime argument")
                runtime = _symbol(keywords["runtime_type"], method, tree, module)
            elif "runtime_type" in keywords:
                raise UnknownAssemblyForwarding("installed helper has no runtime parameter")
            manifest = _symbol(keywords["manifest"], method, tree, module)
            ordinals[method.name] = ordinals.get(method.name, 0) + 1
            edges.append(
                AssemblyEdge(path, method.name, ordinals[method.name], callee, runtime, manifest)
            )
    return AssemblyForwarding(
        tuple(
            sorted(
                edges,
                key=lambda row: (
                    row.module,
                    row.caller,
                    row.ordinal,
                ),
            )
        )
    )


def current_assembly_forwarding(root: Path) -> AssemblyForwarding:
    return inspect_assembly_forwarding(
        tuple(
            (str(path.relative_to(root / "src")), path.read_text())
            for path in sorted((root / "src").rglob("*.py"))
        )
    )


def verify_assembly_forwarding(
    observed: tuple[AssemblyEdge, ...],
    expected: tuple[AssemblyEdge, ...],
) -> None:
    if observed != expected:
        raise UnregisteredAssemblyMount("direct assembly edges differ from registered inventory")
