"""Disposable SQLite preparation evidence; no runtime or domain admission."""

import re
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from hashlib import sha256
from sqlite3 import Connection
from typing import cast


class SqlPreparationRefused(ValueError):
    pass


class UnregisteredSqlAccess(ValueError):
    pass


SqlAction = tuple[str, str | None, str | None, str | None, str | None]
ColumnInfo = tuple[int, str, str, int, str | None, int]
IndexInfo = tuple[int, str, int, str, int]
IndexColumnInfo = tuple[int, int, str | None, int, str, int]
PhysicalCatalogRow = tuple[str, str, str, str | None]


@dataclass(frozen=True)
class SqlPreparation:
    """Raw action set and initial connection profile, not callback order or row access."""

    sql: str
    bindings: int
    actions: tuple[SqlAction, ...]
    foreign_keys: bool
    initial_schema_sha256: str
    sqlite_version: str


@dataclass(frozen=True)
class PhysicalSchemaObservation:
    """Disposable physical census, never an authenticated live schema."""

    schema_sha256: str
    initial_schema_sha256: str
    catalog: tuple[PhysicalCatalogRow, ...]
    columns: tuple[tuple[str, tuple[ColumnInfo, ...]], ...]
    indexes: tuple[tuple[str, tuple[IndexInfo, ...]], ...]
    index_columns: tuple[tuple[str, tuple[IndexColumnInfo, ...]], ...]
    units: tuple[SqlPreparation, ...]
    foreign_keys: bool
    sqlite_version: str
    recipe: str = "private-main-memory/cache=0/autocommit/sequential-create/metadata"


_ACTIONS = {
    sqlite3.SQLITE_READ: "READ",
    sqlite3.SQLITE_SELECT: "SELECT",
    sqlite3.SQLITE_INSERT: "INSERT",
    sqlite3.SQLITE_UPDATE: "UPDATE",
    sqlite3.SQLITE_DELETE: "DELETE",
    sqlite3.SQLITE_FUNCTION: "FUNCTION",
    sqlite3.SQLITE_PRAGMA: "PRAGMA",
    sqlite3.SQLITE_TRANSACTION: "TRANSACTION",
    sqlite3.SQLITE_CREATE_TABLE: "CREATE_TABLE",
    sqlite3.SQLITE_CREATE_INDEX: "CREATE_INDEX",
    sqlite3.SQLITE_REINDEX: "REINDEX",
}
_COMMENT_ONLY = re.compile(r"(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/)*", re.DOTALL)
_CREATE = re.compile(r"CREATE\s+(?:TABLE|(?:UNIQUE\s+)?INDEX)\b", re.IGNORECASE)


def split_sql_script(script: str) -> tuple[str, ...]:
    statements = []
    start = 0
    for index, character in enumerate(script):
        if character == ";" and sqlite3.complete_statement(script[start : index + 1]):
            statement = script[start : index + 1]
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
    if (
        not registered_objects
        or len(set(registered_objects)) != len(registered_objects)
        or any(
            kind not in {"table", "index"} or not isinstance(name, str) or not name
            for kind, name in registered_objects
        )
    ):
        raise SqlPreparationRefused("schema requires unique registered table/index identities")
    return frozenset(registered_objects)


def _authorizer(
    actions: set[SqlAction], objects: frozenset[tuple[str, str]], *, schema: bool
) -> Callable[[int, str | None, str | None, str | None, str | None], int]:
    def authorize(
        code: int,
        first: str | None,
        second: str | None,
        database: str | None,
        source: str | None,
    ) -> int:
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
                allowed = (
                    ("index", first) in objects
                    and ("table", second) in objects
                    and database == "main"
                )
            elif name == "REINDEX":
                allowed = ("index", first) in objects and database == "main"
        else:
            if name in {"READ", "INSERT", "UPDATE", "DELETE"}:
                allowed = database == "main" or (
                    name == "READ"
                    and database is None
                    and second == ""
                    and ("table", first) in objects
                )
            elif name == "SELECT":
                allowed = True
            elif name == "FUNCTION":
                allowed = second in {"count", "max", "min", "coalesce", "substr", "glob"}
            elif name == "TRANSACTION":
                allowed = first == "BEGIN"
            elif name == "PRAGMA":
                allowed = (
                    (first == "table_info" and ("table", second) in objects)
                    or (first == "user_version" and second in (None, "1211188048"))
                    or (first in {"foreign_keys", "query_only"} and second == "ON")
                    or (first == "query_only" and second is None)
                    or (first == "journal_mode" and second == "WAL")
                )
        if not allowed:
            return sqlite3.SQLITE_DENY
        actions.add((name, first, second, database, source))
        return sqlite3.SQLITE_OK

    return authorize


def _evidence(
    sql: str, bindings: int, actions: set[SqlAction], foreign_keys: bool, initial_schema_sql: str
) -> SqlPreparation:
    ordered = tuple(
        sorted(actions, key=lambda row: tuple((item is not None, item or "") for item in row))
    )
    return SqlPreparation(
        sql,
        bindings,
        ordered,
        foreign_keys,
        sha256(initial_schema_sql.encode()).hexdigest(),
        sqlite3.sqlite_version,
    )


def _schema_steps(
    connection: Connection,
    script: str,
    objects: frozenset[tuple[str, str]],
    foreign_keys: bool,
    initial_schema_sql: str,
) -> tuple[SqlPreparation, ...]:
    result = []
    for sql in split_sql_script(script):
        # Callback permissions alone cannot distinguish CREATE INDEX's REINDEX
        # callback from a standalone REINDEX, or EXPLAIN CREATE from real DDL.
        prefix = cast(re.Match[str], _COMMENT_ONLY.match(sql))
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


def _configure(connection: Connection, foreign_keys: bool) -> None:
    if type(foreign_keys) is not bool:
        raise SqlPreparationRefused("foreign_keys must be an explicit bool")
    connection.execute("PRAGMA foreign_keys = " + ("ON" if foreign_keys else "OFF"))
    actual = connection.execute("PRAGMA foreign_keys").fetchall()
    if actual != [(int(foreign_keys),)]:
        raise SqlPreparationRefused("configured foreign_keys state differs")


def _metadata_authorizer(
    objects: frozenset[tuple[str, str]],
) -> Callable[[int, str | None, str | None, str | None, str | None], int]:
    def authorize(
        code: int,
        first: str | None,
        second: str | None,
        database: str | None,
        source: str | None,
    ) -> int:
        allowed = source is None and (
            (code == sqlite3.SQLITE_SELECT and database is None)
            or (
                code == sqlite3.SQLITE_READ
                and first == "sqlite_master"
                and second in {"type", "name", "tbl_name", "sql"}
                and database == "main"
            )
            or (
                code == sqlite3.SQLITE_PRAGMA
                and database == "main"
                and (
                    (first in {"table_info", "index_list"} and ("table", second) in objects)
                    or (first == "index_xinfo" and ("index", second) in objects)
                )
            )
        )
        return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY

    return authorize


def _physical_schema(
    connection: Connection, objects: frozenset[tuple[str, str]]
) -> tuple[
    tuple[PhysicalCatalogRow, ...],
    tuple[tuple[str, tuple[ColumnInfo, ...]], ...],
    tuple[tuple[str, tuple[IndexInfo, ...]], ...],
    tuple[tuple[str, tuple[IndexColumnInfo, ...]], ...],
]:
    connection.set_authorizer(_metadata_authorizer(objects))
    catalog = connection.execute(
        "SELECT type, name, tbl_name, sql FROM main.sqlite_master ORDER BY type, name"
    ).fetchall()
    columns = []
    indexes = []
    index_columns = []
    for kind, name in sorted(objects):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name):
            raise SqlPreparationRefused("metadata object name is not a fixed identifier")
        if kind == "table":
            rows = connection.execute(f'PRAGMA main.table_info("{name}")').fetchall()
            columns.append((name, tuple(rows)))
            rows = connection.execute(f'PRAGMA main.index_list("{name}")').fetchall()
            indexes.append((name, tuple(rows)))
        else:
            rows = connection.execute(f'PRAGMA main.index_xinfo("{name}")').fetchall()
            index_columns.append((name, tuple(rows)))
    return tuple(catalog), tuple(columns), tuple(indexes), tuple(index_columns)


def observe_registered_schema(
    schema_sql: str,
    *,
    initial_schema_sql: str,
    registered_objects: tuple[tuple[str, str], ...],
    foreign_keys: bool,
) -> PhysicalSchemaObservation:
    """Execute candidate CREATE units then inspect their result on the same memory DB."""
    objects = _objects(registered_objects)
    with closing(
        sqlite3.connect(":memory:", cached_statements=0, isolation_level=None)
    ) as connection:
        _configure(connection, foreign_keys)
        _schema_steps(connection, initial_schema_sql, objects, foreign_keys, "")
        units = _schema_steps(connection, schema_sql, objects, foreign_keys, initial_schema_sql)
        try:
            catalog, columns, indexes, index_columns = _physical_schema(connection, objects)
        except sqlite3.Error as error:
            raise SqlPreparationRefused("physical metadata observation refused") from error
        return PhysicalSchemaObservation(
            sha256(schema_sql.encode()).hexdigest(),
            sha256(initial_schema_sql.encode()).hexdigest(),
            catalog,
            columns,
            indexes,
            index_columns,
            units,
            foreign_keys,
            sqlite3.sqlite_version,
        )


def observe_statement(
    sql: str,
    *,
    registered_schema_sql: str,
    registered_objects: tuple[tuple[str, str], ...],
    foreign_keys: bool,
) -> SqlPreparation:
    """EXPLAIN one candidate on a fresh registered private schema.

    PRAGMAs can have preparation-time effects on this disposable connection;
    their callbacks do not prove execution effects. Named bindings are unsupported.
    """
    objects = _objects(registered_objects)
    with closing(
        sqlite3.connect(":memory:", cached_statements=0, isolation_level=None)
    ) as connection:
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
                    r"([0-9]+), and there are 0 supplied\.",
                    str(error),
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
    script: str,
    *,
    initial_schema_sql: str,
    registered_objects: tuple[tuple[str, str], ...],
    foreign_keys: bool,
) -> tuple[SqlPreparation, ...]:
    """Execute finite authorized CREATE units in private memory, preserving order.

    The input-schema digest identifies supplied SQL, not an observed physical
    catalog. Existing IF NOT EXISTS success does not prove the skipped body.
    """
    objects = _objects(registered_objects)
    with closing(
        sqlite3.connect(":memory:", cached_statements=0, isolation_level=None)
    ) as connection:
        _configure(connection, foreign_keys)
        _schema_steps(connection, initial_schema_sql, objects, foreign_keys, "")
        return _schema_steps(connection, script, objects, foreign_keys, initial_schema_sql)


def verify_sql_actions(observed: tuple[SqlAction, ...], expected: tuple[SqlAction, ...]) -> None:
    """Compare actions only; callers must separately bind SQL, schema and profile."""
    if observed != expected:
        raise UnregisteredSqlAccess("SQLite access actions differ from registered graph")
