"""Finite identifier producers and current consumers; no runtime admission."""

import ast
import copy
import re
from dataclasses import dataclass
from hashlib import sha256

from chiplog.verification.assembly_forwarding import _binds
from chiplog.verification.placeholder_sql_families import (
    UnknownPlaceholderSql,
    _dominates,
    _frame,
    _method,
)


class UnknownIdentifierSql(ValueError):
    pass


@dataclass(frozen=True)
class IdentifierAlternative:
    bindings: tuple[tuple[str, str], ...]
    sql: str


@dataclass(frozen=True)
class IdentifierSqlFamily:
    module: str
    qualname: str
    site: int
    receiver: str
    alternatives: tuple[IdentifierAlternative, ...]
    source_digests: tuple[tuple[str, str], ...]


_REGISTRY = "architecture/r7_storage_surface.py"
_TARGETS = (
    ("platform/_sqlite.py", "SQLiteMaterializer._admit", 4, "store"),
    ("platform/_sqlite.py", "SQLiteMaterializer._activate_checkpoint_bundle", 2, "profile"),
    ("composition/r7_planning.py", "R7PlanningRuntime._locked_reconcile_read_generation", 1,
     "exists"),
    ("composition/r12.py", "R12Workspace._read_state", 4, "tables"),
    ("platform/authority_reads.py", "_schema_members", 2, "metadata"),
    ("platform/authority_reads.py", "_authority_snapshot_bytes", 1, "members"),
    ("composition/r7_planning.py", "R7PlanningRuntime._bootstrap_storage_binding", 2, "members"),
)


def _shape(node, expression):
    return ast.dump(node) == ast.dump(ast.parse(expression, mode="eval").body)


def _literal(tree, name):
    matches = [node for node in tree.body if isinstance(node, ast.Assign)
               and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
               and node.targets[0].id == name]
    if len(matches) != 1 or sum(_binds(node, name) for node in ast.walk(tree)) != 1:
        raise UnknownIdentifierSql("producer lacks one direct unshadowed assignment")
    return matches[0].value


def _identifier(node):
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str) or not re.fullmatch(
        r"[A-Za-z_][A-Za-z_0-9]*", node.value
    ):
        raise UnknownIdentifierSql("identifier is not a simple literal")
    return node.value


def _columns(node):
    if not isinstance(node, ast.Tuple) or not node.elts:
        raise UnknownIdentifierSql("columns require a literal nonempty tuple")
    columns = tuple(_identifier(item) for item in node.elts)
    if len(set(columns)) != len(columns):
        raise UnknownIdentifierSql("duplicate columns")
    return columns


def _members(tree):
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)
               and node.module == "dataclasses" and node.level == 0 and any(
                   alias.name == "dataclass" and alias.asname is None for alias in node.names)]
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "StorageMember"]
    expected = ast.parse('@dataclass(frozen=True)\nclass StorageMember:\n'
        '    table: str\n    columns: tuple[str, ...]\n    authority_bearing: bool').body[0]
    if len(imports) != 1 or len(classes) != 1 or ast.dump(classes[0]) != ast.dump(expected) or any(
        sum(_binds(node, name) for node in ast.walk(tree)) != 1
        for name in ("dataclass", "StorageMember")
    ):
        raise UnknownIdentifierSql("StorageMember constructor source is not the inert dataclass")
    value = _literal(tree, "AUTHORITY_STORAGE_MEMBERS")
    if not imports[0].lineno < classes[0].lineno < value.lineno:
        raise UnknownIdentifierSql("constructor/import do not precede manifest evaluation")
    if not isinstance(value, ast.Tuple) or not value.elts:
        raise UnknownIdentifierSql("storage registry is not a literal tuple")
    result = []
    for item in value.elts:
        if not isinstance(item, ast.Call) or not _shape(item.func, "StorageMember") or (
            len(item.args) != 3 or item.keywords or not isinstance(item.args[2], ast.Constant)
            or type(item.args[2].value) is not bool
        ):
            raise UnknownIdentifierSql("unknown member constructor expression")
        result.append((_identifier(item.args[0]), _columns(item.args[1]), item.args[2].value))
    if len({table for table, _, _ in result}) != len(result):
        raise UnknownIdentifierSql("duplicate storage members")
    return tuple(result)


def _registry_import(tree):
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)
               and node.module == "chiplog.architecture.r7_storage_surface" and node.level == 0
               and any(alias.name == "AUTHORITY_STORAGE_MEMBERS" and alias.asname is None
                       for alias in node.names)]
    if len(imports) != 1 or sum(_binds(node, "AUTHORITY_STORAGE_MEMBERS")
                                for node in ast.walk(tree)) != 1:
        raise UnknownIdentifierSql("registry import is aliased, foreign or shadowed")


def _stable(method, names):
    for name in names:
        if sum(_binds(node, name) for node in ast.walk(method)) != 1:
            raise UnknownIdentifierSql("identifier producer is rebound or shadowed")
    for node in ast.walk(method):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Return)) and isinstance(
            node.value, ast.Name
        ) and node.value.id in names:
            raise UnknownIdentifierSql("direct local producer alias/return is unresolved")
        if isinstance(node, ast.Call) and any(_root(argument) in names for argument in (
            *node.args, *(keyword.value for keyword in node.keywords)
        )) and not _shape(node, "len(member.columns)"):
            raise UnknownIdentifierSql("direct local producer argument escape is unresolved")
        if isinstance(node, (ast.Attribute, ast.Subscript)):
            root = node
            while isinstance(root, (ast.Attribute, ast.Subscript)):
                root = root.value
            if isinstance(root, ast.Name) and root.id in names:
                if isinstance(node.ctx, (ast.Store, ast.Del)):
                    raise UnknownIdentifierSql("identifier producer member is mutated")
                if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and (
                    node.attr not in {"table", "columns", "authority_bearing"}
                ):
                    raise UnknownIdentifierSql("unknown identifier producer attribute/method")


def _root(node):
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _global_uses(tree):
    names = {"AUTHORITY_STORAGE_MEMBERS", "StorageMember", "STORE_COLUMNS",
             "CHECKPOINT_BUNDLE_PROFILE"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Attribute, ast.Subscript)) and _root(node) in names and (
            isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            raise UnknownIdentifierSql("global producer attribute/subscript mutation")
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Return)) and isinstance(
            node.value, ast.Name
        ) and node.value.id in names:
            raise UnknownIdentifierSql("direct producer alias/return is unresolved")
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and _root(node.func.value) in names and not (
            _shape(node, "STORE_COLUMNS.items()")
        ):
            raise UnknownIdentifierSql("global producer method call is unresolved")
        if any(_root(argument) in names for argument in (
            *node.args, *(keyword.value for keyword in node.keywords)
        )) and not _shape(node, "set(STORE_COLUMNS)"):
            raise UnknownIdentifierSql("direct producer argument escape is unresolved")


def _render(expression, env):
    if not isinstance(expression, ast.JoinedStr):
        raise UnknownIdentifierSql("identifier consumer requires an f-string")
    result, used = [], []
    for item in expression.values:
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            result.append(item.value)
        elif isinstance(item, ast.FormattedValue) and item.conversion == -1 and (
            item.format_spec is None and ast.unparse(item.value) in env
        ):
            key = ast.unparse(item.value)
            used.append(key)
            result.append(env[key])
        else:
            raise UnknownIdentifierSql("unknown interpolation/conversion/format")
    if set(used) != set(env) or len(used) != len(env):
        raise UnknownIdentifierSql("missing or repeated identifier interpolation")
    return "".join(result)


def _loop(call, parents, method, kind):
    parent = parents[call]
    while parent is not method:
        if isinstance(parent, ast.For) or (isinstance(parent, ast.GeneratorExp) and any(
            isinstance(item.target, ast.Name) and item.target.id in {"table", "member"}
            for item in parent.generators
        )):
            break
        parent = parents[parent]
    if parent is method:
        raise UnknownIdentifierSql("missing identifier loop")
    if isinstance(parent, ast.For):
        if parent.orelse or parent.type_comment:
            raise UnknownIdentifierSql("unsupported loop control")
        _frame(call, method, parents)
        return parent, parent.target, parent.iter
    if kind not in {"exists", "metadata"} or len(parent.generators) != 1:
        raise UnknownIdentifierSql("unknown identifier generator frame")
    generator, = parent.generators
    if generator.is_async or generator.ifs:
        raise UnknownIdentifierSql("filtered/async identifier generator")
    clone = copy.deepcopy(parent.elt)
    candidates = [node for node in ast.walk(clone) if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute) and node.func.attr == "execute"]
    if len(candidates) != 1:
        raise UnknownIdentifierSql("unknown generator SQL consumers")
    candidates[0].args[0] = ast.Constant("__SQL__")
    expected = ('connection.execute("__SQL__").fetchone()[0]' if kind == "exists" else
        '(member.table, tuple(str(column[1]) for column in '
        'connection.execute("__SQL__").fetchall()))')
    if not _shape(clone, expected):
        raise UnknownIdentifierSql("unknown generator body")
    _frame(parent, method, parents)
    return parent, generator.target, generator.iter


def extract_current_identifier_sql(sources) -> tuple[IdentifierSqlFamily, ...]:
    """Extract only the seven named finite forms; no complete SQL/caller closure."""
    try:
        return _extract(sources)
    except UnknownPlaceholderSql as error:
        raise UnknownIdentifierSql("unsupported identifier scope/frame") from error


def _extract(sources):
    texts = dict(sources)
    if len(texts) != len(sources) or set(texts) != {_REGISTRY, *(row[0] for row in _TARGETS)}:
        raise UnknownIdentifierSql("exact unique consumer and producer sources required")
    trees = {name: ast.parse(source) for name, source in texts.items()}
    for tree in trees.values():
        _global_uses(tree)
    members = _members(trees[_REGISTRY])
    result = []
    for module, qualname, site, kind in _TARGETS:
        tree = trees[module]
        method = _method(tree, qualname)
        for builtin in ("str", "range", "len", "tuple", "any", "set"):
            if any(_binds(node, builtin) for node in ast.walk(tree)):
                raise UnknownIdentifierSql("producer builtin is shadowed")
        parents = {child: node for node in ast.walk(method) for child in ast.iter_child_nodes(node)}
        calls = sorted((node for node in ast.walk(method) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in {"execute", "executemany", "executescript"}),
                       key=lambda node: (node.lineno, node.col_offset))
        if len(calls) < site:
            raise UnknownIdentifierSql("missing identifier SQL site")
        call = calls[site - 1]
        receiver = "self._connection" if module == "platform/_sqlite.py" else "connection"
        if call.func.attr != "execute" or ast.unparse(call.func.value) != receiver or (
            call.keywords or len(call.args) != (2 if kind == "tables" else 1)
        ):
            raise UnknownIdentifierSql("unknown identifier consumer")
        environments, labels = [], []
        dependencies = {module}
        if kind == "profile":
            _frame(call, method, parents)
            value = _literal(tree, "CHECKPOINT_BUNDLE_PROFILE")
            if not isinstance(value, ast.Constant) or type(value.value) is not int:
                raise UnknownIdentifierSql("profile is not a direct integer")
            environments = [{"CHECKPOINT_BUNDLE_PROFILE": str(value.value)}]
            labels = environments
        else:
            loop, target, iterable = _loop(call, parents, method, kind)
            if kind == "store":
                if ast.unparse(target) != "(table, expected_columns)" or not _shape(
                    iterable, "STORE_COLUMNS.items()"
                ):
                    raise UnknownIdentifierSql("unknown store identifier loop")
                _stable(method, ("table", "expected_columns"))
                value = _literal(tree, "STORE_COLUMNS")
                if not isinstance(value, ast.Dict) or not value.keys:
                    raise UnknownIdentifierSql("store columns is not a literal dictionary")
                names = tuple(_identifier(key) for key in value.keys)
                if len(set(names)) != len(names):
                    raise UnknownIdentifierSql("duplicate store table")
                for columns in value.values:
                    _columns(columns)
                environments = [{"table": name} for name in names]
                labels = environments
            elif kind in {"tables", "exists"}:
                if not isinstance(target, ast.Name) or target.id != "table":
                    raise UnknownIdentifierSql("unknown table loop target")
                _stable(method, ("table",))
                names = _columns(iterable)
                environments = [{"table": name} for name in names]
                labels = environments
            else:
                _registry_import(tree)
                dependencies.add(_REGISTRY)
                if not isinstance(target, ast.Name) or target.id != "member" or not _shape(
                    iterable, "AUTHORITY_STORAGE_MEMBERS"
                ):
                    raise UnknownIdentifierSql("unknown storage member loop")
                # Metadata's equality check has its own isolated member generator.
                _stable(loop if kind == "metadata" else method, ("member",))
                selected = members
                if kind == "members":
                    filter_node = ast.parse(
                        'if not member.authority_bearing:\n    continue'
                    ).body[0]
                    if not isinstance(loop, ast.For) or not loop.body or (
                        ast.dump(loop.body[0]) != ast.dump(filter_node)
                    ):
                        raise UnknownIdentifierSql("unknown authority member filter")
                    _stable(method, ("columns", "order"))
                    producers = loop.body[1:3]
                    expected = ast.parse('columns = ", ".join(f\'"{column}"\' '
                        'for column in member.columns)\norder = ", ".join(str(index) '
                        'for index in range(1, len(member.columns) + 1))').body
                    if len(producers) != 2 or any(ast.dump(a) != ast.dump(b)
                        for a, b in zip(producers, expected, strict=True)) or not all(
                            _dominates(producer, call, method, parents) for producer in producers):
                        raise UnknownIdentifierSql("unknown column/order producers")
                    selected = tuple(item for item in members if item[2])
                else:
                    guard = ast.parse('tuple(row[0] for row in rows) != '
                        'tuple(member.table for member in AUTHORITY_STORAGE_MEMBERS)',
                        mode="eval").body
                    guards = [node for node in method.body if isinstance(node, ast.If)
                              and ast.dump(node.test) == ast.dump(guard)
                              and len(node.body) == 1 and isinstance(node.body[0], ast.Raise)
                              and node.body[0].exc is not None and not node.orelse
                              and node.lineno < loop.lineno]
                    if len(guards) != 1:
                        raise UnknownIdentifierSql("missing observed-name equality rejection")
                labels = [{"table": table} for table, _, _ in selected]
                environments = [{"member.table": table, **({
                    "columns": ", ".join('"' + column + '"' for column in columns),
                    "order": ", ".join(str(index) for index in range(1, len(columns) + 1)),
                } if kind == "members" else {})} for table, columns, _ in selected]
        alternatives = tuple(IdentifierAlternative(tuple(label.items()), _render(call.args[0], env))
            for env, label in zip(environments, labels, strict=True))
        digests = tuple((name, sha256(texts[name].encode()).hexdigest())
                        for name in sorted(dependencies))
        result.append(IdentifierSqlFamily(module, qualname, site, receiver, alternatives, digests))
    return tuple(result)
