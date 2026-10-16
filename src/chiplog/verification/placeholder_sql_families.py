"""Finite comma-placeholder source forms, not runtime iterable/domain proof."""

import ast
import re
from dataclasses import dataclass
from hashlib import sha256

from chiplog.verification.assembly_forwarding import _binds
from chiplog.verification.sqlite_preparation import SqlPreparation, observe_statement


class UnknownPlaceholderSql(ValueError):
    pass


@dataclass(frozen=True)
class PlaceholderSqlFamily:
    qualname: str
    site: int
    receiver: str
    before: str
    after: str
    iterable: str
    bound_values: str
    fixed_parameters: tuple[str, ...]
    source_sha256: str

    def sql_for(self, cardinality: int) -> str:
        if type(cardinality) is not int or cardinality < 0:
            raise UnknownPlaceholderSql("placeholder cardinality must be a nonnegative int")
        return self.before + ",".join("?" for _ in range(cardinality)) + self.after


def _method(tree: ast.Module, qualname: str):
    scope = tree
    for part in qualname.split("."):
        candidates = [node for node in scope.body if isinstance(node, (
            ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef,
        )) and node.name == part]
        if len(candidates) != 1:
            raise UnknownPlaceholderSql("missing or duplicate SQL scope")
        scope, = candidates
    if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise UnknownPlaceholderSql("SQL scope is not a function")
    return scope


def _dominates(definition: ast.Assign, consumer: ast.AST, method: ast.AST, parents) -> bool:
    parent = parents[definition]
    for _, value in ast.iter_fields(parent):
        if not isinstance(value, list) or definition not in value:
            continue
        ancestor = consumer
        while ancestor is not method and ancestor is not parent:
            if ancestor in value:
                return value.index(definition) < value.index(ancestor)
            ancestor = parents[ancestor]
    return False


def _frame(call: ast.Call, method, parents):
    parent = parents[call]
    while parent is not method:
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            raise UnknownPlaceholderSql("SQL consumer belongs to another execution frame")
        if isinstance(parent, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)) and (
            call not in ast.walk(parent.generators[0].iter)
        ):
            raise UnknownPlaceholderSql("SQL consumer belongs to a comprehension frame")
        parent = parents[parent]


def _fragments(expression: ast.expr) -> list[str | None]:
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        return [expression.value]
    if isinstance(expression, ast.Name) and expression.id == "placeholders":
        return [None]
    if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Add):
        return _fragments(expression.left) + _fragments(expression.right)
    if isinstance(expression, ast.JoinedStr):
        result = []
        for value in expression.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                result.append(value.value)
            elif isinstance(value, ast.FormattedValue) and (
                value.conversion == -1 and value.format_spec is None
                and isinstance(value.value, ast.Name) and value.value.id == "placeholders"
            ):
                result.append(None)
            else:
                raise UnknownPlaceholderSql("unsupported SQL interpolation or conversion")
        return result
    raise UnknownPlaceholderSql("unknown dynamic SQL fragment")


def _generator(expression: ast.expr) -> ast.comprehension:
    if not isinstance(expression, ast.GeneratorExp) or len(expression.generators) != 1:
        raise UnknownPlaceholderSql("expected one plain placeholder generator")
    generator, = expression.generators
    if generator.is_async or generator.ifs or not isinstance(generator.target, ast.Name):
        raise UnknownPlaceholderSql("filtered, async or unpacking generator is unknown")
    return generator


def _iterable(definition: ast.Assign) -> ast.expr:
    value = definition.value
    if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Attribute) or (
        value.func.attr != "join" or not isinstance(value.func.value, ast.Constant)
        or value.func.value.value != "," or len(value.args) != 1 or value.keywords
    ):
        raise UnknownPlaceholderSql("placeholder producer is not the comma join")
    generator = _generator(value.args[0])
    if not isinstance(value.args[0].elt, ast.Constant) or value.args[0].elt.value != "?":
        raise UnknownPlaceholderSql("placeholder generator can emit SQL other than question marks")
    iterable = generator.iter
    if not isinstance(iterable, ast.Name) and not (
        isinstance(iterable, ast.Attribute) and isinstance(iterable.value, ast.Name)
    ):
        raise UnknownPlaceholderSql("unsupported placeholder iterable expression")
    return iterable


def _bound_values(expression: ast.expr, iterable: ast.expr) -> str:
    if ast.dump(expression) == ast.dump(iterable):
        return ast.unparse(expression)
    if isinstance(expression, ast.GeneratorExp):
        generator = _generator(expression)
        if ast.dump(generator.iter) == ast.dump(iterable) and (
            isinstance(expression.elt, ast.Attribute) and expression.elt.attr == "head"
            and isinstance(expression.elt.value, ast.Name)
            and expression.elt.value.id == generator.target.id
        ):
            return ast.unparse(expression)
    raise UnknownPlaceholderSql("bound values do not follow the placeholder iterable")


def _simple_value(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) or (
        isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
    )


def _require_local_iterable_binding(iterable, definition, method, parents):
    name = iterable.id if isinstance(iterable, ast.Name) else iterable.value.id
    arguments = method.args
    parameters = {arg.arg for arg in (*arguments.posonlyargs, *arguments.args,
                                     *arguments.kwonlyargs)}
    bindings = [node for statement in method.body for node in ast.walk(statement)
                if _binds(node, name)]
    if bindings:
        if len(bindings) != 1 or name in parameters:
            raise UnknownPlaceholderSql("placeholder iterable is rebound or shadowed")
        binding = bindings[0]
        producer = parents[binding]
        if not isinstance(producer, ast.Assign) or len(producer.targets) != 1 or (
            producer.targets[0] is not binding
        ) or not _dominates(producer, definition, method, parents):
            raise UnknownPlaceholderSql("local iterable lacks one dominating assignment")
    for node in ast.walk(method):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)) and (
            ast.unparse(node) == ast.unparse(iterable)
        ):
            raise UnknownPlaceholderSql("placeholder iterable attribute is rebound")


def extract_placeholder_sql_families(
    source: str, qualname: str,
) -> tuple[PlaceholderSqlFamily, ...]:
    """Check supported syntax only; callers must prove stable iteration and receivers."""
    method = _method(ast.parse(source), qualname)
    parents = {child: node for node in ast.walk(method) for child in ast.iter_child_nodes(node)}
    definitions = [node for node in ast.walk(method) if isinstance(node, ast.Assign)
                   and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                   and node.targets[0].id == "placeholders"]
    if len(definitions) != 1 or sum(_binds(node, "placeholders") for node in ast.walk(method)) != 1:
        raise UnknownPlaceholderSql("placeholder name lacks one unshadowed definition")
    definition, = definitions
    iterable = _iterable(definition)
    _require_local_iterable_binding(iterable, definition, method, parents)
    calls = sorted((node for node in ast.walk(method) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"execute", "executemany", "executescript"}),
                   key=lambda node: (node.lineno, node.col_offset))
    result = []
    for site, call in enumerate(calls, 1):
        if not call.args or not any(isinstance(node, ast.Name) and node.id == "placeholders"
                                    for node in ast.walk(call.args[0])):
            continue
        _frame(call, method, parents)
        if not _dominates(definition, call, method, parents):
            raise UnknownPlaceholderSql("placeholder producer does not dominate consumer")
        if call.func.attr != "execute" or len(call.args) != 2 or call.keywords or not isinstance(
            call.args[1], ast.Tuple
        ):
            raise UnknownPlaceholderSql("unsupported SQL call or bound parameter shape")
        parameters = call.args[1].elts
        if not parameters or not isinstance(parameters[-1], ast.Starred) or any(
            not _simple_value(parameter) for parameter in parameters[:-1]
        ):
            raise UnknownPlaceholderSql("expected one final bound iterable expansion")
        bound_values = _bound_values(parameters[-1].value, iterable)
        fragments = _fragments(call.args[0])
        if fragments.count(None) != 1:
            raise UnknownPlaceholderSql("expected one dynamic IN placeholder group")
        index = fragments.index(None)
        before, after = "".join(fragments[:index]), "".join(fragments[index + 1:])
        if not re.search(r"\bIN\s*\(\s*$", before, re.IGNORECASE) or not re.match(r"\s*\)", after):
            raise UnknownPlaceholderSql("placeholder group is not the supported IN list")
        result.append(PlaceholderSqlFamily(qualname, site, ast.unparse(call.func.value), before,
            after, ast.unparse(iterable), bound_values,
            tuple(ast.unparse(parameter) for parameter in parameters[:-1]),
            sha256(source.encode()).hexdigest()))
    if not result:
        raise UnknownPlaceholderSql("placeholder producer has no supported SQL consumer")
    return tuple(result)


def prepare_placeholder_sql_family(
    family: PlaceholderSqlFamily, *, schema_sql: str,
    registered_objects: tuple[tuple[str, str], ...], foreign_keys: bool,
) -> tuple[tuple[int, SqlPreparation], ...]:
    result = []
    for cardinality in (0, 1, 2):
        observed = observe_statement(family.sql_for(cardinality), registered_schema_sql=schema_sql,
            registered_objects=registered_objects, foreign_keys=foreign_keys)
        if observed.bindings != len(family.fixed_parameters) + cardinality:
            raise UnknownPlaceholderSql("engine binding count differs from source family")
        result.append((cardinality, observed))
    return tuple(result)
