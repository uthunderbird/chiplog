"""Real source SQL drives private schema and EXPLAIN-only access observations."""

import ast
import sqlite3
from pathlib import Path

import pytest

from chiplog.platform._sqlite import SCHEMA_SQL
from chiplog.verification.sqlite_preparation import (
    SqlPreparationRefused,
    UnregisteredSqlAccess,
    observe_schema_script,
    observe_statement,
    split_sql_script,
    verify_sql_actions,
)

ROOT = Path(__file__).resolve().parents[2] / "src/chiplog"
MAIN_TABLES = (
    "deletion_fences",
    "derivatives",
    "evidence_inbox",
    "publications",
    "records",
    "store_metadata",
    "tenant_heads",
)
MAIN_OBJECTS = tuple(("table", name) for name in MAIN_TABLES) + tuple(
    ("index", "sqlite_autoindex_" + name + "_1") for name in MAIN_TABLES if name != "store_metadata"
)
LEDGER_OBJECTS = (
    ("table", "broker_epochs"),
    ("table", "broker_generation_identities"),
    ("table", "issued_operations"),
    ("index", "one_current_broker_epoch"),
    ("index", "sqlite_autoindex_broker_epochs_1"),
    ("index", "sqlite_autoindex_broker_generation_identities_1"),
    ("index", "sqlite_autoindex_issued_operations_1"),
    ("index", "sqlite_autoindex_issued_operations_2"),
    ("index", "sqlite_autoindex_issued_operations_3"),
)
TRUST_OBJECTS = (
    ("table", "trust_decisions"),
    ("table", "trust_records"),
    ("index", "sqlite_autoindex_trust_decisions_1"),
    ("index", "sqlite_autoindex_trust_records_1"),
)


def _constant(relative, name):
    tree = ast.parse((ROOT / relative).read_text())
    matches = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == name
    ]
    assert len(matches) == 1
    return ast.literal_eval(matches[0])


def _sql(relative, prefix):
    tree = ast.parse((ROOT / relative).read_text())
    matches = [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"execute", "executemany", "executescript"}
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and node.args[0].value.lstrip().startswith(prefix)
    ]
    assert len(matches) == 1, (relative, prefix, len(matches))
    return matches[0]


def _observe(sql):
    return observe_statement(
        sql, registered_schema_sql=SCHEMA_SQL, registered_objects=MAIN_OBJECTS, foreign_keys=True
    )


def test_actual_records_insert_is_prepared_without_executing_bound_nulls() -> None:
    # Executing this INSERT with the dummy bindings would violate NOT NULL.
    sql = _sql("platform/_sqlite.py", "INSERT INTO records(")
    observed = _observe(sql)
    assert observed.sql == sql
    assert observed.bindings == 6
    assert observed.foreign_keys is True
    assert observed.actions == (("INSERT", "records", None, "main", None),)
    verify_sql_actions(observed.actions, (("INSERT", "records", None, "main", None),))
    assert _observe(sql) == observed  # fresh preparation still reports every callback


def test_actual_upsert_records_its_read_and_write_actions() -> None:
    sql = _sql("platform/_sqlite.py", "INSERT INTO tenant_heads(tenant_id, head)")
    observed = _observe(sql)
    assert observed.bindings == 2
    verify_sql_actions(
        observed.actions,
        (
            ("INSERT", "tenant_heads", None, "main", None),
            ("READ", "tenant_heads", "tenant_id", "main", None),
            ("UPDATE", "tenant_heads", "head", "main", None),
        ),
    )


def test_actual_update_predicate_mutation_adds_a_read_before_expected_refusal() -> None:
    sql = _sql("platform/_sqlite.py", "UPDATE evidence_inbox\n                       SET")
    observed = _observe(sql)
    expected = tuple(
        sorted(
            (
                *(
                    ("UPDATE", "evidence_inbox", column, "main", None)
                    for column in ("state", "attempt_id", "transport_version", "cursor")
                ),
                *(
                    ("READ", "evidence_inbox", column, "main", None)
                    for column in ("tenant_id", "source_id", "evidence_id", "state")
                ),
            )
        )
    )
    verify_sql_actions(observed.actions, expected)
    changed = sql + " AND EXISTS (SELECT 1 FROM deletion_fences WHERE frontier >= 0)"
    foreign = _observe(changed)
    assert ("READ", "deletion_fences", "frontier", "main", None) in foreign.actions
    with pytest.raises(UnregisteredSqlAccess):
        verify_sql_actions(foreign.actions, expected)


def test_actual_historical_delete_observes_predicate_and_subquery_reads() -> None:
    sql = _sql(
        "composition/r14_original_call_resolution_source.py",
        "DELETE FROM records WHERE tenant_id=? AND commit_sequence>?",
    )
    expected = (
        ("DELETE", "records", None, "main", None),
        ("READ", "records", "commit_sequence", "main", None),
        ("READ", "records", "tenant_id", "main", None),
    )
    verify_sql_actions(_observe(sql).actions, expected)
    changed = _observe(sql + " AND EXISTS (SELECT 1 FROM deletion_fences WHERE frontier >= 0)")
    assert ("READ", "deletion_fences", "frontier", "main", None) in changed.actions
    with pytest.raises(UnregisteredSqlAccess):
        verify_sql_actions(changed.actions, expected)


def test_trust_foreign_key_setting_is_explicit_and_changes_preparation_reads() -> None:
    schema = _sql("adapters/driven/deployment_trust/_sqlite.py", "CREATE TABLE IF NOT EXISTS")
    sql = _sql("adapters/driven/deployment_trust/_sqlite.py", "INSERT INTO trust_records(")
    off = observe_statement(
        sql, registered_schema_sql=schema, registered_objects=TRUST_OBJECTS, foreign_keys=False
    )
    on = observe_statement(
        sql, registered_schema_sql=schema, registered_objects=TRUST_OBJECTS, foreign_keys=True
    )
    assert off.actions == (("INSERT", "trust_records", None, "main", None),)
    assert on.actions == (
        ("INSERT", "trust_records", None, "main", None),
        ("READ", "trust_decisions", "decision_id", "main", None),
    )
    assert off.foreign_keys is False and on.foreign_keys is True


def test_actual_ledger_schema_advances_empty_state_through_partial_index() -> None:
    schema = _constant("platform/authority_ledger.py", "_SCHEMA")
    fresh = observe_schema_script(
        schema, initial_schema_sql="", registered_objects=LEDGER_OBJECTS, foreign_keys=False
    )
    assert len(fresh) == 4
    assert ("CREATE_INDEX", "one_current_broker_epoch", "broker_epochs", "main", None) in (
        fresh[1].actions
    )
    assert ("READ", "broker_epochs", "current", "main", None) in fresh[1].actions
    reopened = observe_schema_script(
        schema, initial_schema_sql=schema, registered_objects=LEDGER_OBJECTS, foreign_keys=False
    )
    assert len(reopened) == 4
    assert reopened[1].actions != fresh[1].actions  # IF NOT EXISTS skips the index body


def test_actual_bare_main_schema_is_not_rewritten_to_accept_existing_state() -> None:
    fresh = observe_schema_script(
        SCHEMA_SQL, initial_schema_sql="", registered_objects=MAIN_OBJECTS, foreign_keys=True
    )
    assert len(fresh) == 7
    assert ("CREATE_TABLE", "store_metadata", None, "main", None) in fresh[0].actions
    with pytest.raises(SqlPreparationRefused):
        observe_schema_script(
            SCHEMA_SQL,
            initial_schema_sql=SCHEMA_SQL,
            registered_objects=MAIN_OBJECTS,
            foreign_keys=True,
        )


@pytest.mark.parametrize("domain", ["trust", "read_ledger", "workspace", "gate"])
def test_actual_sidecar_create_scripts_cover_empty_and_existing_states(domain):
    if domain == "trust":
        schema = _sql("adapters/driven/deployment_trust/_sqlite.py", "CREATE TABLE IF NOT EXISTS")
        objects = TRUST_OBJECTS
    elif domain == "read_ledger":
        schema = _constant("platform/read_ledger.py", "_SCHEMA")
        objects = (
            ("table", "authority_read_state"),
            ("table", "authority_read_releases"),
            ("index", "sqlite_autoindex_authority_read_state_1"),
            ("index", "sqlite_autoindex_authority_read_releases_1"),
        )
    elif domain == "workspace":
        schema = _sql("adapters/driven/workspace_sqlite.py", "CREATE TABLE IF NOT EXISTS")
        objects = (("table", "workspace"), ("index", "sqlite_autoindex_workspace_1"))
    else:
        schema = _sql("platform/r8_gate.py", "CREATE TABLE IF NOT EXISTS")
        objects = (
            ("table", "gate_current"),
            ("table", "gate_handoffs"),
            ("table", "sqlite_sequence"),
            ("index", "sqlite_autoindex_gate_current_1"),
            ("index", "sqlite_autoindex_gate_handoffs_1"),
        )
    for initial in ("", schema):
        observed = observe_schema_script(
            schema,
            initial_schema_sql=initial,
            registered_objects=objects,
            foreign_keys=domain == "trust",
        )
        assert len(observed) == len(split_sql_script(schema))
        assert all(row.foreign_keys is (domain == "trust") for row in observed)


@pytest.mark.parametrize(
    "sql,bindings",
    [
        ("SELECT record_id FROM records WHERE tenant_id=?5 OR owner=?5", 5),
        ("SELECT record_id FROM records WHERE tenant_id=? AND '?'='?' /* ? */", 1),
    ],
)
def test_sqlite_counts_bindings_without_duplicate_or_incomplete_actions(sql, bindings):
    observed = _observe(sql)
    assert observed.bindings == bindings
    assert (
        sum(action == ("READ", "records", "record_id", "main", None) for action in observed.actions)
        == 1
    )


def test_count_star_rowid_and_current_builtins_remain_visible() -> None:
    count = _observe("SELECT count(*) FROM records")
    assert ("READ", "records", "", None, None) in count.actions
    assert ("FUNCTION", None, "count", None, None) in count.actions
    rowid = _observe("SELECT rowid FROM records")
    assert ("READ", "records", "ROWID", "main", None) in rowid.actions
    for sql, function in (
        ("SELECT max(head) FROM tenant_heads", "max"),
        ("SELECT coalesce(head, 0) FROM tenant_heads", "coalesce"),
        ("SELECT substr(record_id, 1, 2) FROM records", "substr"),
    ):
        assert ("FUNCTION", None, function, None, None) in _observe(sql).actions


@pytest.mark.parametrize(
    "sql,first,second",
    [
        ("PRAGMA main.table_info(records)", "table_info", "records"),
        ("PRAGMA user_version", "user_version", None),
        ("PRAGMA user_version = 1211188048", "user_version", "1211188048"),
        ("PRAGMA foreign_keys = ON", "foreign_keys", "ON"),
        ("PRAGMA journal_mode = WAL", "journal_mode", "WAL"),
        ("PRAGMA query_only = ON", "query_only", "ON"),
    ],
)
def test_metadata_actions_preserve_pragma_name_and_argument(sql, first, second):
    assert ("PRAGMA", first, second, "main" if first == "table_info" else None, None) in _observe(
        sql
    ).actions


def test_sqlite_script_boundaries_preserve_quoted_and_commented_semicolons() -> None:
    script = "CREATE TABLE a(x TEXT DEFAULT ';'); /* ; */CREATE TABLE b(y);-- ;\n"
    assert split_sql_script(script) == (
        "CREATE TABLE a(x TEXT DEFAULT ';');",
        " /* ; */CREATE TABLE b(y);",
    )
    assert split_sql_script("CREATE TABLE a(x); CREATE TABLE b(y)") == (
        "CREATE TABLE a(x);",
        " CREATE TABLE b(y)",
    )
    observed = observe_schema_script(
        script,
        initial_schema_sql="",
        registered_objects=(("table", "a"), ("table", "b")),
        foreign_keys=False,
    )
    assert len(observed) == 2
    assert ("CREATE_TABLE", "a", None, "main", None) in observed[0].actions
    assert ("CREATE_TABLE", "b", None, "main", None) in observed[1].actions
    with pytest.raises(SqlPreparationRefused):
        split_sql_script("CREATE TABLE a(x); CREATE TABLE b('")


@pytest.mark.parametrize(
    "tail",
    [
        "SELECT 1",
        "INSERT INTO records VALUES(NULL,NULL,NULL,NULL,NULL,NULL)",
        "CREATE TABLE records2(x)",
        "CREATE TEMP TABLE temporary(x)",
        "CREATE VIEW extra AS SELECT * FROM records",
        "CREATE TRIGGER extra AFTER INSERT ON records BEGIN DELETE FROM records; END",
        "CREATE VIRTUAL TABLE virtual USING fts5(x)",
        "ATTACH ':memory:' AS other",
        "DROP TABLE records",
        "ALTER TABLE records ADD COLUMN extra TEXT",
        "CREATE TABLE records AS SELECT 1",
    ],
)
def test_schema_execution_never_accepts_business_sql_or_unregistered_ddl(tail):
    with pytest.raises(SqlPreparationRefused):
        observe_schema_script(
            tail, initial_schema_sql=SCHEMA_SQL, registered_objects=MAIN_OBJECTS, foreign_keys=True
        )


@pytest.mark.parametrize(
    "initial, sql",
    [
        ("", "CREATE TABLE records AS SELECT 1"),
        (SCHEMA_SQL, "INSERT INTO records VALUES(NULL,NULL,NULL,NULL,NULL,NULL)"),
        (SCHEMA_SQL, "UPDATE records SET owner='changed'"),
        (SCHEMA_SQL, "DELETE FROM records"),
    ],
)
def test_schema_business_sql_refuses_at_create_guard_or_select_authorizer(initial, sql):
    with pytest.raises(SqlPreparationRefused) as failure:
        observe_schema_script(
            sql, initial_schema_sql=initial, registered_objects=MAIN_OBJECTS, foreign_keys=True
        )
    if sql.startswith("CREATE"):
        assert failure.value.__cause__.sqlite_errorcode == sqlite3.SQLITE_AUTH
    else:
        assert str(failure.value) == "schema statement is not a supported CREATE form"


def test_later_denial_or_syntax_failure_never_returns_a_successful_script_prefix() -> None:
    for tail in ("SELECT 1", "CREATE TABLE broken("):
        with pytest.raises(SqlPreparationRefused):
            observe_schema_script(
                SCHEMA_SQL + tail,
                initial_schema_sql="",
                registered_objects=MAIN_OBJECTS,
                foreign_keys=True,
            )


@pytest.mark.parametrize("prefix", ["EXPLAIN ", "EXPLAIN QUERY PLAN "])
def test_schema_observation_requires_actual_ddl_not_explanation_of_ddl(prefix):
    with pytest.raises(SqlPreparationRefused, match="schema"):
        observe_schema_script(
            prefix + "CREATE TABLE records(x)",
            initial_schema_sql="",
            registered_objects=MAIN_OBJECTS,
            foreign_keys=True,
        )


def test_index_callback_permission_does_not_authorize_standalone_reindex() -> None:
    with pytest.raises(SqlPreparationRefused, match="schema"):
        observe_schema_script(
            "REINDEX one_current_broker_epoch",
            initial_schema_sql=_constant("platform/authority_ledger.py", "_SCHEMA"),
            registered_objects=LEDGER_OBJECTS,
            foreign_keys=False,
        )


@pytest.mark.parametrize(
    "sql",
    [
        "ATTACH ':memory:' AS other",
        "DETACH other",
        "SELECT load_extension(?)",
        "SELECT unregistered_function(record_id) FROM records",
        "PRAGMA writable_schema = ON",
        "CREATE TABLE extra(x)",
        "DELETE FROM missing",
        "EXPLAIN SELECT 1",
        "SELECT 1; SELECT 2",
        "SELECT record_id FROM records WHERE tenant_id=:tenant OR owner=:tenant",
    ],
)
def test_ordinary_observation_refuses_unknown_actions_and_partial_preparation(sql):
    with pytest.raises(SqlPreparationRefused):
        _observe(sql)
