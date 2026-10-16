"""Finite SQL preparation evidence; not runtime or complete graph admission."""

import ast
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from itertools import product


class UnknownSqlExpression(ValueError):
    pass


class UnregisteredReadEdge(ValueError):
    pass


@dataclass(frozen=True)
class StatementReads:
    site: int
    alternative: int
    reads: tuple[tuple[str, str, str], ...]


def planning_read_graph(source: str, schema_sql: str) -> tuple[StatementReads, ...]:
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
            value = statement.value if isinstance(statement, (ast.Assign, ast.Expr)) else None
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
                result.append(StatementReads(site, alternative, _prepare_reads(schema_sql, sql)))
    if not result:
        raise UnknownSqlExpression("planning method contains no SQL execution")
    return tuple(result)


def _prepare_reads(schema_sql: str, sql: str) -> tuple[tuple[str, str, str], ...]:
    # Fresh engine and disabled statement cache isolate every site's callbacks.
    with closing(sqlite3.connect(":memory:", cached_statements=0)) as connection:
        connection.executescript(schema_sql)
        reads: set[tuple[str, str, str]] = set()

        def authorize(action, first, second, database, trigger):
            if action == sqlite3.SQLITE_READ and database == "main" and trigger is None:
                reads.add((database, first, second))
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_FUNCTION and second == "count":
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        connection.set_authorizer(authorize)
        try:
            try:
                connection.execute("EXPLAIN " + sql).fetchall()
            except sqlite3.ProgrammingError as error:
                # Let SQLite count bindings, including '?' inside literals/comments.
                count = re.fullmatch(
                    r"Incorrect number of bindings supplied\. The current statement uses "
                    r"([0-9]+), and there are 0 supplied\.", str(error),
                )
                if count is None:
                    raise
                reads.clear()
                connection.execute("EXPLAIN " + sql, (None,) * int(count[1])).fetchall()
        except sqlite3.Error as error:
            raise UnknownSqlExpression("SQL alternative did not prepare completely") from error
        finally:
            connection.set_authorizer(None)
        return tuple(sorted(reads))


def verify_read_graph(
    observed: tuple[StatementReads, ...], expected: tuple[StatementReads, ...]
) -> None:
    if observed != expected:
        raise UnregisteredReadEdge("actual SQL read graph differs from registered graph")
