"""Source-only literal script extraction; runtime bindings remain unproved."""

import ast
from dataclasses import dataclass
from hashlib import sha256

from chiplog.verification.assembly_forwarding import _binds


class UnknownConstantSql(ValueError):
    pass


@dataclass(frozen=True)
class ConstantSqlScript:
    qualname: str
    site: int
    receiver: str
    symbol: str
    sql: str
    source_sha256: str


def extract_constant_sql_scripts(
    source: str, qualname: str, symbol: str,
) -> tuple[ConstantSqlScript, ...]:
    """Extract one literal symbol's direct scripts, not complete SQL/module closure."""
    tree = ast.parse(source)
    definitions = [node for node in tree.body if isinstance(node, ast.Assign)
                   and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                   and node.targets[0].id == symbol]
    if len(definitions) != 1 or sum(_binds(node, symbol) for node in ast.walk(tree)) != 1:
        raise UnknownConstantSql("script symbol lacks one unshadowed module assignment")
    definition, = definitions
    if not isinstance(definition.value, ast.Constant) or not isinstance(
        definition.value.value, str
    ):
        raise UnknownConstantSql("script constant is not a literal string")
    scope = tree
    for part in qualname.split("."):
        candidates = [node for node in scope.body if isinstance(
            node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.name == part]
        if len(candidates) != 1:
            raise UnknownConstantSql("missing or duplicate script scope")
        scope, = candidates
        if definition.lineno >= scope.lineno:
            raise UnknownConstantSql("script definition does not precede scope")
    if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise UnknownConstantSql("script scope is not a function")
    parents = {child: node for node in ast.walk(scope) for child in ast.iter_child_nodes(node)}
    calls = sorted((node for node in ast.walk(scope) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"execute", "executemany", "executescript"}),
                   key=lambda node: (node.lineno, node.col_offset))
    result = []
    for site, call in enumerate(calls, 1):
        if call.func.attr != "executescript":
            continue
        if len(call.args) != 1 or call.keywords or not isinstance(call.args[0], ast.Name) or (
            call.args[0].id != symbol
        ):
            raise UnknownConstantSql("unsupported script consumer expression")
        parent = parents[call]
        while parent is not scope:
            if isinstance(parent, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp,
                                   ast.GeneratorExp)):
                raise UnknownConstantSql("script consumer belongs to another frame")
            parent = parents[parent]
        result.append(ConstantSqlScript(qualname, site, ast.unparse(call.func.value), symbol,
            definition.value.value, sha256(source.encode()).hexdigest()))
    if not result:
        raise UnknownConstantSql("no supported script consumer")
    return tuple(result)
