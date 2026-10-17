"""Actual module literals and script consumers, without importing their modules."""

import ast
from hashlib import sha256
from pathlib import Path

import pytest

from chiplog.verification.constant_sql_scripts import (
    UnknownConstantSql,
    extract_constant_sql_scripts,
)
from chiplog.verification.sqlite_preparation import (
    UnregisteredSqlAccess,
    observe_schema_script,
    verify_sql_actions,
)

ROOT = Path(__file__).resolve().parents[2] / "src/chiplog"
CASES = (
    (
        "platform/_sqlite.py",
        "_expected_schema_manifest",
        "SCHEMA_SQL",
        1,
        "reference",
        "76372d4b7fc019892354d7d875944bb094ca20417bd9e10c599af673fcaed9ea",
    ),
    (
        "platform/_sqlite.py",
        "SQLiteMaterializer._admit",
        "SCHEMA_SQL",
        2,
        "self._connection",
        "76372d4b7fc019892354d7d875944bb094ca20417bd9e10c599af673fcaed9ea",
    ),
    (
        "platform/authority_ledger.py",
        "BrokerAuthorityLedger.__init__",
        "_SCHEMA",
        1,
        "connection",
        "191e8fe0e9a384ccfd74ece9da8b2ce520e0b38e9c0fa26e481f9cf92c3758a7",
    ),
    (
        "platform/read_ledger.py",
        "BrokerReadLedger.__init__",
        "_SCHEMA",
        1,
        "connection",
        "0466123bc809b4638d761b8bd2bf53b251176536a478279e8ae04a0071ac03e5",
    ),
)


@pytest.mark.parametrize("relative,qualname,symbol,site,receiver,digest", CASES)
def test_actual_script_constant_and_consumer_identity(
    relative,
    qualname,
    symbol,
    site,
    receiver,
    digest,
) -> None:
    source = (ROOT / relative).read_text()
    (script,) = extract_constant_sql_scripts(source, qualname, symbol)
    assert (script.qualname, script.symbol, script.site, script.receiver) == (
        qualname,
        symbol,
        site,
        receiver,
    )
    assert sha256(script.sql.encode()).hexdigest() == digest
    assert script.source_sha256 == sha256(source.encode()).hexdigest()


def test_changed_actual_literal_exposes_added_schema_action_before_comparison() -> None:
    source = (ROOT / "platform/read_ledger.py").read_text()
    (original,) = extract_constant_sql_scripts(source, "BrokerReadLedger.__init__", "_SCHEMA")
    tree = ast.parse(source)
    (definition,) = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "_SCHEMA"
    ]
    definition.value.value += "\nCREATE TABLE unexpected_table(value TEXT);"
    (changed,) = extract_constant_sql_scripts(
        ast.unparse(tree),
        "BrokerReadLedger.__init__",
        "_SCHEMA",
    )
    assert changed.sql == original.sql + "\nCREATE TABLE unexpected_table(value TEXT);"
    assert changed.source_sha256 != original.source_sha256
    objects = (
        ("table", "authority_read_state"),
        ("table", "authority_read_releases"),
        ("index", "sqlite_autoindex_authority_read_state_1"),
        ("index", "sqlite_autoindex_authority_read_releases_1"),
        ("table", "unexpected_table"),
    )
    baseline = observe_schema_script(
        original.sql, initial_schema_sql="", registered_objects=objects, foreign_keys=False
    )
    mutant = observe_schema_script(
        changed.sql, initial_schema_sql="", registered_objects=objects, foreign_keys=False
    )
    expected = {action for statement in baseline for action in statement.actions}
    actual = {action for statement in mutant for action in statement.actions}
    assert ("CREATE_TABLE", "unexpected_table", None, "main", None) in actual
    assert actual != expected
    with pytest.raises(UnregisteredSqlAccess):
        verify_sql_actions(tuple(sorted(actual)), tuple(sorted(expected)))


BASE = 'SQL = "CREATE TABLE a(x);"\ndef use(connection):\n    connection.executescript(SQL)\n'


@pytest.mark.parametrize(
    "source",
    (
        BASE + 'SQL = "CREATE TABLE b(x);"\n',
        BASE + '\ndef unrelated():\n    global SQL\n    SQL = "other"\n',
        BASE.replace("def use(connection):", "def use(connection, SQL):"),
        BASE + '    SQL = "local after call"\n',
        BASE.replace("connection.executescript(SQL)", "connection.executescript(module.SQL)"),
        BASE.replace("connection.executescript(SQL)", "connection.executescript(OTHER)"),
        BASE.replace(
            "    connection.executescript(SQL)",
            "    def nested():\n        connection.executescript(SQL)",
        ),
        BASE.replace(
            "connection.executescript(SQL)", "[connection.executescript(SQL) for x in xs]"
        ),
        BASE.replace("connection.executescript(SQL)", "connection.executescript(SQL, extra)"),
        BASE.replace('SQL = "CREATE TABLE a(x);"', "SQL = make_sql()"),
        BASE.replace('SQL = "CREATE TABLE a(x);"', 'SQL: str = "CREATE TABLE a(x);"'),
        BASE.replace('SQL = "CREATE TABLE a(x);"', 'if condition:\n    SQL = "CREATE TABLE a(x);"'),
    ),
)
def test_unknown_binding_expression_or_frame_refuses(source) -> None:
    with pytest.raises(UnknownConstantSql):
        extract_constant_sql_scripts(source, "use", "SQL")


def test_repeated_script_consumers_keep_separate_ordinals() -> None:
    source = BASE + '    connection.execute("SELECT 1")\n    connection.executescript(SQL)\n'
    scripts = extract_constant_sql_scripts(source, "use", "SQL")
    assert [script.site for script in scripts] == [1, 3]
    assert scripts[0].sql == scripts[1].sql
