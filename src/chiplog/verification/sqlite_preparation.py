"""Disposable SQLite preparation evidence; no runtime or domain admission."""

import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from hashlib import sha256


class SqlPreparationRefused(ValueError):
    pass


class UnregisteredSqlAccess(ValueError):
    pass


SqlAction = tuple[str, str | None, str | None, str | None, str | None]


@dataclass(frozen=True)
class SqlPreparation:
    """Raw action set and initial connection profile, not callback order or row access."""

    sql: str
    bindings: int
    actions: tuple[SqlAction, ...]
    foreign_keys: bool
    initial_schema_sha256: str
    sqlite_version: str


_ACTIONS = {
    sqlite3.SQLITE_READ: "READ", sqlite3.SQLITE_SELECT: "SELECT",
    sqlite3.SQLITE_INSERT: "INSERT", sqlite3.SQLITE_UPDATE: "UPDATE",
    sqlite3.SQLITE_DELETE: "DELETE", sqlite3.SQLITE_FUNCTION: "FUNCTION",
    sqlite3.SQLITE_PRAGMA: "PRAGMA", sqlite3.SQLITE_TRANSACTION: "TRANSACTION",
    sqlite3.SQLITE_CREATE_TABLE: "CREATE_TABLE", sqlite3.SQLITE_CREATE_INDEX: "CREATE_INDEX",
    sqlite3.SQLITE_REINDEX: "REINDEX",
}
_COMMENT_ONLY = re.compile(r"(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/)*", re.DOTALL)
_CREATE = re.compile(r"CREATE\s+(?:TABLE|(?:UNIQUE\s+)?INDEX)\b", re.IGNORECASE)


def split_sql_script(script: str) -> tuple[str, ...]:
    statements = []
    start = 0
    for index, character in enumerate(script):
        if character == ";" and sqlite3.complete_statement(script[start:index + 1]):
            statement = script[start:index + 1]
            if not _COMMENT_ONLY.fullmatch(statement):
                statements.append(statement)
            start = index + 1
    tail = script[start:]
    if not _COMMENT_ONLY.fullmatch(tail):
        if not sqlite3.complete_statement(tail + ";"):
            raise SqlPreparationRefused("SQL script has an incomplete trailing statement")
        statements.append(tail)
    return tuple(statements)


def _objects(registered_objects: tuple[tuple[str, str], ...]) -> frozenset[tuple[str, str]]:
    if not registered_objects or len(set(registered_objects)) != len(registered_objects) or any(
        kind not in {"table", "index"} or not isinstance(name, str) or not name
        for kind, name in registered_objects
    ):
        raise SqlPreparationRefused("schema requires unique registered table/index identities")
    return frozenset(registered_objects)


def _authorizer(actions: set[SqlAction], objects: frozenset[tuple[str, str]], *, schema: bool):
    def authorize(code, first, second, database, source):
        name = _ACTIONS.get(code)
        if name is None or source is not None or database not in (None, "main"):
            return sqlite3.SQLITE_DENY
        allowed = False
        if schema:
            if name in {"INSERT", "UPDATE", "DELETE"}:
                allowed = first == "sqlite_master" and database == "main"
            elif name == "READ":
                allowed = database == "main" and (
                    first == "sqlite_master" or ("table", first) in objects
                )
            elif name == "CREATE_TABLE":
                allowed = ("table", first) in objects and database == "main"
            elif name == "CREATE_INDEX":
                allowed = (("index", first) in objects and ("table", second) in objects
                           and database == "main")
            elif name == "REINDEX":
                allowed = ("index", first) in objects and database == "main"
        else:
            if name in {"READ", "INSERT", "UPDATE", "DELETE"}:
                allowed = database == "main" or (
                    name == "READ" and database is None and second == ""
                    and ("table", first) in objects
                )
            elif name == "SELECT":
                allowed = True
            elif name == "FUNCTION":
                allowed = second in {"count", "max", "min", "coalesce", "substr"}
            elif name == "TRANSACTION":
                allowed = first == "BEGIN"
            elif name == "PRAGMA":
                allowed = (
                    first == "table_info" and ("table", second) in objects
                ) or (
                    first == "user_version" and second in (None, "1211188048")
                ) or (
                    first in {"foreign_keys", "query_only"} and second == "ON"
                ) or (first == "query_only" and second is None) or (
                    first == "journal_mode" and second == "WAL"
                )
        if not allowed:
            return sqlite3.SQLITE_DENY
        actions.add((name, first, second, database, source))
        return sqlite3.SQLITE_OK
    return authorize


def _evidence(sql, bindings, actions, foreign_keys, initial_schema_sql):
    ordered = tuple(sorted(actions, key=lambda row: tuple(
        (item is not None, item or "") for item in row
    )))
    return SqlPreparation(sql, bindings, ordered, foreign_keys,
                          sha256(initial_schema_sql.encode()).hexdigest(), sqlite3.sqlite_version)


def _schema_steps(connection, script, objects, foreign_keys, initial_schema_sql):
    result = []
    for sql in split_sql_script(script):
        # Callback permissions alone cannot distinguish CREATE INDEX's REINDEX
        # callback from a standalone REINDEX, or EXPLAIN CREATE from real DDL.
        prefix = _COMMENT_ONLY.match(sql)
        if not _CREATE.match(sql, prefix.end()):
            raise SqlPreparationRefused("schema statement is not a supported CREATE form")
        actions: set[SqlAction] = set()
        connection.set_authorizer(_authorizer(actions, objects, schema=True))
        try:
            connection.execute(sql).fetchall()
        except sqlite3.Error as error:
            raise SqlPreparationRefused(
                "schema script did not complete under its policy"
            ) from error
        result.append(_evidence(sql, 0, actions, foreign_keys, initial_schema_sql))
    return tuple(result)


def _configure(connection, foreign_keys):
    if type(foreign_keys) is not bool:
        raise SqlPreparationRefused("foreign_keys must be an explicit bool")
    connection.execute("PRAGMA foreign_keys = " + ("ON" if foreign_keys else "OFF"))


def observe_statement(
    sql: str, *, registered_schema_sql: str,
    registered_objects: tuple[tuple[str, str], ...], foreign_keys: bool,
) -> SqlPreparation:
    """EXPLAIN one candidate on a fresh registered private schema.

    PRAGMAs can have preparation-time effects on this disposable connection;
    their callbacks do not prove execution effects. Named bindings are unsupported.
    """
    objects = _objects(registered_objects)
    with closing(sqlite3.connect(":memory:", cached_statements=0,
                                isolation_level=None)) as connection:
        _configure(connection, foreign_keys)
        _schema_steps(connection, registered_schema_sql, objects, foreign_keys, "")
        actions: set[SqlAction] = set()
        connection.set_authorizer(_authorizer(actions, objects, schema=False))
        bindings = 0
        try:
            try:
                connection.execute("EXPLAIN " + sql).fetchall()
            except sqlite3.ProgrammingError as error:
                count = re.fullmatch(
                    r"Incorrect number of bindings supplied\. The current statement uses "
                    r"([0-9]+), and there are 0 supplied\.", str(error),
                )
                if count is None:
                    raise
                bindings = int(count[1])
                actions.clear()
                connection.execute("EXPLAIN " + sql, (None,) * bindings).fetchall()
        except sqlite3.Error as error:
            raise SqlPreparationRefused(
                "statement did not prepare completely under its policy"
            ) from error
        return _evidence(sql, bindings, actions, foreign_keys, registered_schema_sql)


def observe_schema_script(
    script: str, *, initial_schema_sql: str,
    registered_objects: tuple[tuple[str, str], ...], foreign_keys: bool,
) -> tuple[SqlPreparation, ...]:
    """Execute finite authorized CREATE units in private memory, preserving order.

    The input-schema digest identifies supplied SQL, not an observed physical
    catalog. Existing IF NOT EXISTS success does not prove the skipped body.
    """
    objects = _objects(registered_objects)
    with closing(sqlite3.connect(":memory:", cached_statements=0,
                                isolation_level=None)) as connection:
        _configure(connection, foreign_keys)
        _schema_steps(connection, initial_schema_sql, objects, foreign_keys, "")
        return _schema_steps(connection, script, objects, foreign_keys, initial_schema_sql)


def verify_sql_actions(observed: tuple[SqlAction, ...], expected: tuple[SqlAction, ...]) -> None:
    """Compare actions only; callers must separately bind SQL, schema and profile."""
    if observed != expected:
        raise UnregisteredSqlAccess("SQLite access actions differ from registered graph")
