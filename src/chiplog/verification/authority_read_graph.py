"""Finite SQL preparation evidence; not runtime or complete graph admission."""

import ast
from dataclasses import dataclass
from itertools import product

from chiplog.verification.sqlite_preparation import SqlPreparationRefused, observe_statement


class UnknownSqlExpression(ValueError):
    pass


class UnregisteredReadEdge(ValueError):
    pass


@dataclass(frozen=True)
class StatementReads:
    site: int
    alternative: int
    reads: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class PlanningSqlAlternative:
    site: int
    alternative: int
    sql: str


def planning_read_graph(source: str, schema_sql: str) -> tuple[StatementReads, ...]:
    return tuple(StatementReads(item.site, item.alternative, _prepare_reads(schema_sql, item.sql))
                 for item in planning_sql_alternatives(source))


def planning_sql_alternatives(source: str) -> tuple[PlanningSqlAlternative, ...]:
    """Inspect the current planning method only; other source forms remain unproved."""
    tree = ast.parse(source)
    classes = [node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == "BrokerAuthorityReader"]
    if len(classes) != 1:
        raise UnknownSqlExpression("expected one BrokerAuthorityReader declaration")
    methods = [node for node in classes[0].body
               if isinstance(node, ast.FunctionDef) and node.name == "_execute_planning_read"]
    if len(methods) != 1:
        raise UnknownSqlExpression("expected one planning SQL method")
    method, = methods
    parameters = {argument.arg: argument for argument in method.args.args}
    if "connection" not in parameters or parameters["connection"].annotation is None or ast.unparse(
        parameters["connection"].annotation
    ) != "sqlite3.Connection":
        raise UnknownSqlExpression("planning receiver is not the declared connection parameter")
    stores: dict[str, list[ast.Name]] = {}
    for node in ast.walk(method):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            stores.setdefault(node.id, []).append(node)
    if stores.get("connection"):
        raise UnknownSqlExpression("planning connection receiver is rebound")
    definitions = {
        node.targets[0].id: node for node in method.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }

    def alternatives(
        expression: ast.expr, before: int, resolving: frozenset[str]
    ) -> tuple[str, ...]:
        if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
            return (expression.value,)
        if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Add):
            return tuple(left + right for left, right in product(
                alternatives(expression.left, before, resolving),
                alternatives(expression.right, before, resolving),
            ))
        if isinstance(expression, ast.IfExp):
            return (*alternatives(expression.body, before, resolving),
                    *alternatives(expression.orelse, before, resolving))
        if isinstance(expression, ast.Name):
            name = expression.id
            definition = definitions.get(name)
            if (
                name in resolving or name in parameters or definition is None
                or len(stores.get(name, ())) != 1 or definition.lineno >= before
            ):
                raise UnknownSqlExpression(f"SQL local lacks one dominating definition: {name}")
            return alternatives(definition.value, definition.lineno, resolving | {name})
        raise UnknownSqlExpression(f"unsupported SQL expression: {type(expression).__name__}")

    result = []
    site = 0
    for statement in method.body:
        calls = sorted((node for node in ast.walk(statement)
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in {"execute", "executemany", "executescript"}),
                       key=lambda node: (node.lineno, node.col_offset))
        for call in calls:
            execution = statement
            if isinstance(statement, ast.With):
                if (len(statement.items) != 1 or len(statement.body) != 1
                        or not isinstance(statement.items[0].context_expr, ast.Call)):
                    raise UnknownSqlExpression("unsupported planning guard frame")
                guard = statement.items[0].context_expr
                if (not isinstance(guard.func, ast.Name)
                        or guard.func.id != "_prepared_planning_statement"
                        or len(guard.args) != 4
                        or not isinstance(guard.args[0], ast.Name)
                        or guard.args[0].id != "connection"
                        or len(call.args) != 2
                        or [ast.dump(arg) for arg in guard.args[1:3]]
                        != [ast.dump(arg) for arg in call.args]):
                    raise UnknownSqlExpression(
                        "planning preparation/execution placement or identity differs")
                execution = statement.body[0]
            value = execution.value if isinstance(execution, (ast.Assign, ast.Expr)) else None
            direct = value is call or (
                isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
                and value.func.value is call and value.func.attr in {"fetchall", "fetchone"}
                and not value.args and not value.keywords
            )
            if (
                not direct
                or call.func.attr != "execute"
                or not isinstance(call.func.value, ast.Name) or call.func.value.id != "connection"
                or not call.args or call.keywords
            ):
                raise UnknownSqlExpression("unsupported SQL execution placement or receiver")
            site += 1
            sqls = alternatives(call.args[0], call.lineno, frozenset())
            for alternative, sql in enumerate(sqls):
                result.append(PlanningSqlAlternative(site, alternative, sql))
    if not result:
        raise UnknownSqlExpression("planning method contains no SQL execution")
    return tuple(result)


# Independently registered main-schema identities, not inferred from supplied SQL.
_PLANNING_TABLES = (
    "store_metadata", "tenant_heads", "publications", "records", "evidence_inbox",
    "deletion_fences", "derivatives",
)
_PLANNING_OBJECTS = tuple(("table", name) for name in _PLANNING_TABLES) + tuple(
    ("index", "sqlite_autoindex_" + name + "_1")
    for name in _PLANNING_TABLES if name != "store_metadata"
)


def _prepare_reads(schema_sql: str, sql: str) -> tuple[tuple[str, str, str], ...]:
    # The shared engine authorizes schema setup too, before any supplied statement.
    try:
        observed = observe_statement(
            sql, registered_schema_sql=schema_sql,
            registered_objects=_PLANNING_OBJECTS, foreign_keys=False,
        )
    except SqlPreparationRefused as error:
        raise UnknownSqlExpression("SQL alternative did not prepare completely") from error
    reads: set[tuple[str, str, str]] = set()
    for action, first, second, database, source in observed.actions:
        if (action == "READ" and database is None and second == ""
                and first in _PLANNING_TABLES and source is None):
            # SQLite's count(*) callback identifies only the table in private main.
            database = "main"
        if action == "READ" and database == "main" and source is None:
            reads.add((database, first, second))
        elif source is None and (action == "SELECT" or (
            action == "FUNCTION" and second == "count"
        )):
            continue
        else:
            raise UnknownSqlExpression("SQL alternative did not prepare completely: "
                                       "action outside planning read policy")
    return tuple(sorted(reads))


def verify_read_graph(
    observed: tuple[StatementReads, ...], expected: tuple[StatementReads, ...]
) -> None:
    if observed != expected:
        raise UnregisteredReadEdge("actual SQL read graph differs from registered graph")
