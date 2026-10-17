"""Finite H1 cutoff expressions; not runtime domain or admission proof."""

import ast
from dataclasses import dataclass
from hashlib import sha256
from typing import cast

from chiplog.verification.assembly_forwarding import _binds
from chiplog.verification.placeholder_sql_families import (
    UnknownPlaceholderSql,
    _dominates,
    _frame,
    _method,
)


class UnknownCutoffSql(ValueError):
    pass


@dataclass(frozen=True)
class CutoffSqlFamily:
    site: int
    receiver: str
    sql: tuple[str, str]
    parameters: tuple[tuple[str, ...], tuple[str, ...]]
    condition: str
    expression: str
    source_sha256: str


def extract_h1_cutoff_sql(source: str) -> tuple[CutoffSqlFamily, ...]:
    """Retain both current consumers and both branches; no whole-method closure."""
    try:
        return _extract(source)
    except UnknownPlaceholderSql as error:
        raise UnknownCutoffSql("unsupported cutoff scope/frame") from error


def _extract(source: str) -> tuple[CutoffSqlFamily, ...]:
    method = _method(ast.parse(source), "read_h1_scoped_owner_inventory")
    parents = {child: node for node in ast.walk(method) for child in ast.iter_child_nodes(node)}
    definitions = []
    for name, kind in (("limit", ast.Assign), ("params", ast.AnnAssign)):
        bindings = [node for node in ast.walk(method) if _binds(node, name)]
        if len(bindings) != 1 or not isinstance(bindings[0], ast.Name):
            raise UnknownCutoffSql("cutoff producer is rebound or shadowed")
        definition = parents[bindings[0]]
        if not isinstance(definition, kind) or (
            isinstance(definition, ast.Assign)
            and (len(definition.targets) != 1 or definition.targets[0] is not bindings[0])
        ):
            raise UnknownCutoffSql("unsupported cutoff assignment")
        _frame(definition, method, parents)
        definitions.append(definition)
    limit, params = definitions
    container = parents[limit]
    if parents[params] is not container or not any(
        isinstance(value, list)
        and limit in value
        and params in value
        and value.index(params) == value.index(limit) + 1
        for _, value in ast.iter_fields(container)
    ):
        raise UnknownCutoffSql("cutoff and params must be consecutive producers")
    expected_limit = ast.parse(
        '"" if boundary is None else " AND commit_sequence <= ?"',
        mode="eval",
    ).body
    expected_params = ast.parse(
        "(scope.tenant,) if boundary is None else (scope.tenant, boundary)",
        mode="eval",
    ).body
    if (
        ast.dump(cast(ast.Assign, limit).value) != ast.dump(expected_limit)
        or params.value is None
        or (ast.dump(params.value) != ast.dump(expected_params))
    ):
        raise UnknownCutoffSql("unsupported cutoff/parameter branch relation")
    calls = sorted(
        (
            node
            for node in ast.walk(method)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"execute", "executemany", "executescript"}
        ),
        key=lambda node: (node.lineno, node.col_offset),
    )

    def fragments(node: ast.expr) -> list[str | None]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.Name) and node.id == "limit":
            return [None]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return fragments(node.left) + fragments(node.right)
        raise UnknownCutoffSql("unknown cutoff SQL fragment")

    result = []
    consumers = []
    for site, call in enumerate(calls, 1):
        if not call.args or not any(
            isinstance(node, ast.Name) and node.id == "limit" for node in ast.walk(call.args[0])
        ):
            continue
        _frame(call, method, parents)
        if not all(_dominates(definition, call, method, parents) for definition in definitions):
            raise UnknownCutoffSql("cutoff producers do not dominate consumer")
        if (
            cast(ast.Attribute, call.func).attr != "execute"
            or len(call.args) != 2
            or call.keywords
            or (not isinstance(call.args[1], ast.Name) or call.args[1].id != "params")
            or ast.unparse(cast(ast.Attribute, call.func).value) != "connection"
        ):
            raise UnknownCutoffSql("unknown cutoff consumer")
        parts = fragments(call.args[0])
        if parts.count(None) != 1:
            raise UnknownCutoffSql("expected one cutoff fragment")
        sql = tuple(
            "".join(fragment if fragment is not None else suffix for fragment in parts)
            for suffix in ("", " AND commit_sequence <= ?")
        )
        result.append(
            CutoffSqlFamily(
                site,
                "connection",
                cast(tuple[str, str], sql),
                (("scope.tenant",), ("scope.tenant", "boundary")),
                "boundary is None",
                ast.unparse(call.args[0]),
                sha256(source.encode()).hexdigest(),
            )
        )
        consumers.append(call)
    if [item.site for item in result] != [2, 3]:
        raise UnknownCutoffSql("expected both current cutoff consumers at sites 2 and 3")
    last = max(cast(int, call.end_lineno) for call in consumers)
    for node in ast.walk(method):
        if (isinstance(node, (ast.Global, ast.Nonlocal)) and "boundary" in node.names) or (
            _binds(node, "boundary")
            and limit.lineno
            <= cast(ast.stmt | ast.expr | ast.arg | ast.ExceptHandler | ast.pattern, node).lineno
            <= last
        ):
            raise UnknownCutoffSql("boundary can change during cutoff evaluation/use")
    return tuple(result)
