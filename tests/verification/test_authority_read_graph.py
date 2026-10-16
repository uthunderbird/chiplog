"""Observe real SQL before comparing its independently specified read graph."""

from pathlib import Path

import pytest

from chiplog.platform._sqlite import SCHEMA_SQL
from chiplog.verification.authority_read_graph import (
    StatementReads,
    UnknownSqlExpression,
    UnregisteredReadEdge,
    planning_read_graph,
    verify_read_graph,
)

SOURCE = Path(__file__).resolve().parents[2] / "src/chiplog/platform/authority_reads.py"
PUBLICATION_COLUMNS = (
    "commit_sequence", "idempotency_key", "operation_kind", "record_ids",
    "request_fingerprint", "tenant_id",
)
RECORD_COLUMNS = (
    "canonical_bytes", "commit_sequence", "owner", "record_id", "schema_id", "tenant_id",
)


def _expected():
    publications = tuple(("main", "publications", column) for column in PUBLICATION_COLUMNS)
    selection = tuple(("main", "publications", column) for column in (
        "commit_sequence", "idempotency_key", "operation_kind", "tenant_id",
    ))
    records = tuple(("main", "records", column) for column in RECORD_COLUMNS)
    return (
        StatementReads(1, 0, (("main", "publications", "commit_sequence"),
                              ("main", "publications", "tenant_id"))),
        StatementReads(2, 0, publications), StatementReads(2, 1, publications),
        StatementReads(3, 0, (*selection, *records)),
        StatementReads(3, 1, (*selection, *records)),
    )


def _replace(source, before, after):
    assert source.count(before) == 1
    return source.replace(before, after)


def test_actual_planning_source_has_exact_per_site_and_branch_reads():
    observed = planning_read_graph(SOURCE.read_text(), SCHEMA_SQL)
    verify_read_graph(observed, _expected())
    assert observed == _expected()


def test_predicate_subquery_changes_extracted_business_edges():
    source = SOURCE.read_text()
    # This fragment belongs only to publication_sql, not record_sql or actual_edges.
    changed = _replace(source,
        '"commit_sequence, record_ids FROM publications WHERE tenant_id = ? "',
        '"commit_sequence, record_ids FROM publications WHERE tenant_id = ? "\n'
        '            "AND EXISTS (SELECT 1 FROM deletion_fences WHERE frontier >= 0) "',
    )
    observed = planning_read_graph(changed, SCHEMA_SQL)
    for row in observed:
        if row.site == 2:
            assert ("main", "deletion_fences", "frontier") in row.reads
    assert [row for row in observed if row.site != 2] == [
        row for row in _expected() if row.site != 2
    ]
    with pytest.raises(UnregisteredReadEdge, match="actual SQL"):
        verify_read_graph(observed, _expected())


def test_ghost_edge_and_omitted_branch_are_not_registered():
    observed = planning_read_graph(SOURCE.read_text(), SCHEMA_SQL)
    ghost = StatementReads(99, 0, (("main", "records", "tenant_id"),))
    for expected in ((*_expected(), ghost), _expected()[:-1]):
        with pytest.raises(UnregisteredReadEdge):
            verify_read_graph(observed, expected)


@pytest.mark.parametrize("mutation", ["dynamic", "forward", "conditional", "rebind"])
def test_unsupported_expression_or_lexical_binding_refuses(mutation):
    source = SOURCE.read_text()
    if mutation == "dynamic":
        changed = _replace(source, "        scope_sql = (",
                           "        scope_sql = external_sql()\n        unused_sql = (")
    elif mutation == "forward":
        changed = _replace(source, "        publication_sql = (",
                           "        publications = connection.execute(publication_sql)\n"
                           "        publication_sql = (")
    elif mutation == "conditional":
        changed = _replace(source, "        scope_sql = (",
                           "        if operation.variant:\n            scope_sql = (")
    else:
        changed = _replace(source, "        scope_sql = (",
                           "        connection = other_connection\n        scope_sql = (")
    with pytest.raises(UnknownSqlExpression):
        planning_read_graph(changed, SCHEMA_SQL)


def test_identical_sql_at_distinct_sites_has_independent_callbacks():
    source = SOURCE.read_text()
    changed = _replace(source,
        '        duplicate_sequence = connection.execute(\n',
        '        repeated = connection.execute(publication_sql)\n'
        '        duplicate_sequence = connection.execute(\n',
    )
    observed = planning_read_graph(changed, SCHEMA_SQL)
    first = [row.reads for row in observed if row.site == 1]
    repeated = [row.reads for row in observed if row.site == 3]
    assert first == repeated == [_expected()[1].reads, _expected()[2].reads]


def _replace_first_query(source, sql):
    old = (
        '"SELECT 1 FROM publications WHERE tenant_id = ? "\n'
        '            "GROUP BY commit_sequence HAVING COUNT(*) > 1 LIMIT 1"'
    )
    return _replace(source, old, repr(sql))


def test_engine_binding_count_ignores_question_marks_in_literals_and_comments():
    changed = _replace_first_query(SOURCE.read_text(),
        "SELECT record_id FROM records WHERE tenant_id=? AND '?'='?' /* ? */")
    observed = planning_read_graph(changed, SCHEMA_SQL)
    assert observed[0].reads == (("main", "records", "record_id"),
                                 ("main", "records", "tenant_id"))


def test_count_star_preserves_table_level_dependency():
    changed = _replace_first_query(SOURCE.read_text(), "SELECT count(*) FROM main.records")
    observed = planning_read_graph(changed, SCHEMA_SQL)
    assert observed[0].reads == (("main", "records", ""),)


def test_schema_preparation_cannot_attach_and_create_external_file(tmp_path):
    sentinel = tmp_path / "escaped.sqlite"
    schema = SCHEMA_SQL + (
        f"\nATTACH DATABASE '{sentinel}' AS escaped;"
        " CREATE TABLE IF NOT EXISTS escaped.marker(value TEXT);"
    )
    rejected = False
    try:
        planning_read_graph(SOURCE.read_text(), schema)
    except UnknownSqlExpression:
        rejected = True
    assert (rejected, sentinel.exists()) == (True, False)


@pytest.mark.parametrize("sql", [
    "UPDATE records SET owner=owner", "SELECT max(record_id) FROM records",
    "PRAGMA user_version", "BEGIN",
])
def test_shared_engine_permissions_do_not_broaden_planning_read_policy(sql):
    changed = _replace_first_query(SOURCE.read_text(), sql)
    with pytest.raises(UnknownSqlExpression, match="prepare completely"):
        planning_read_graph(changed, SCHEMA_SQL)


@pytest.mark.parametrize("tail", [
    "INSERT INTO store_metadata VALUES (1, 1);",
    "CREATE TABLE store_metadata AS SELECT 1 AS singleton, 1 AS store_version;",
])
def test_supplied_schema_rejects_business_dml_and_select(tail):
    if tail.startswith("INSERT"):
        schema = SCHEMA_SQL + tail
    else:
        original = (
            "CREATE TABLE store_metadata (\n"
            "    singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
            "store_version INTEGER NOT NULL\n);"
        )
        schema = _replace(SCHEMA_SQL, original, tail)
    with pytest.raises(UnknownSqlExpression):
        planning_read_graph(SOURCE.read_text(), schema)


@pytest.mark.parametrize("sql", [
    "SELECT missing FROM records", "SELECT load_extension(?)",
    "ATTACH DATABASE ? AS foreign_db", "PRAGMA user_version = 17",
    "SELECT record_id FROM records; SELECT tenant_id FROM records",
])
def test_incomplete_preparation_or_denied_action_never_returns_partial_graph(sql):
    changed = _replace_first_query(SOURCE.read_text(), sql)
    with pytest.raises(UnknownSqlExpression, match="prepare completely"):
        planning_read_graph(changed, SCHEMA_SQL)


@pytest.mark.parametrize("store", [
    'scope_sql += " "', 'del scope_sql', 'for scope_sql in ("",):\n            pass',
    '(scope_sql := " ")',
])
def test_unsupported_rebinding_of_sql_local_is_not_resolved_from_first_assignment(store):
    changed = _replace(SOURCE.read_text(), "        publication_sql = (",
                       f"        {store}\n        publication_sql = (")
    with pytest.raises(UnknownSqlExpression, match="dominating definition"):
        planning_read_graph(changed, SCHEMA_SQL)


@pytest.mark.parametrize("expression", [
    "lambda connection: connection.execute(publication_sql)",
    "connection.execute(publication_sql) if operation.variant else None",
])
def test_nested_or_conditional_execution_is_not_a_direct_statement(expression):
    changed = _replace(SOURCE.read_text(), "        duplicate_sequence = connection.execute(\n",
                       f"        unused = {expression}\n"
                       "        duplicate_sequence = connection.execute(\n")
    with pytest.raises(UnknownSqlExpression, match="placement"):
        planning_read_graph(changed, SCHEMA_SQL)
