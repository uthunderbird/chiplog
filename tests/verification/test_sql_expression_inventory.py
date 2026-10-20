"""Expression evidence joins exact corpus occurrences, including unknowns."""

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.platform._sqlite import SCHEMA_SQL
from chiplog.verification.authority_sql_corpus import UnresolvedSqlCorpus, inspect_sources
from chiplog.verification.sql_expression_inventory import (
    SqlExpressionAccountingError,
    SqlExpressionInventory,
    SqlExpressionSnapshotChanged,
    extract_sql_expression_inventory,
)
from chiplog.verification.sqlite_preparation import (
    UnregisteredSqlAccess,
    observe_statement,
    verify_sql_actions,
)

ROOT = Path(__file__).resolve().parents[2]
PLANNING = "src/chiplog/platform/authority_reads.py"


def _inspect(sources: tuple[tuple[str, str], ...]) -> SqlExpressionInventory:
    return extract_sql_expression_inventory(sources, inspect_sources(sources))


def test_literal_bare_duplicate_and_unknown_candidates_each_keep_their_corpus_event() -> None:
    sources = (
        (
            "src/example.py",
            """raise RuntimeError("do not execute")
def run(connection, unknown):
    value = object()
    execute("SELECT 0")
    connection.execute("SELECT 1")
    connection.execute("SELECT 1")
    connection.execute(unknown)
    connection.execute()
""",
        ),
    )
    inventory = _inspect(sources)
    inventory.require_exact_accounting()
    assert [row.alternatives[0].sql for row in inventory.resolved] == [
        "SELECT 0",
        "SELECT 1",
        "SELECT 1",
    ]
    assert len({row.site.ordinal for row in inventory.resolved}) == 3
    assert len(inventory.unresolved) == 2
    assert all(row.reason for row in inventory.unresolved)
    with pytest.raises(UnresolvedSqlCorpus):
        inventory.corpus.require_closed()


@pytest.mark.parametrize("mutation", ["omit", "duplicate", "ghost", "overlap", "digest", "shape"])
def test_accounting_refuses_missing_duplicate_foreign_or_stale_evidence(mutation: str) -> None:
    inventory = _inspect((("src/example.py", 'connection.execute("SELECT 1")'),))
    (row,) = inventory.resolved
    if mutation == "omit":
        inventory = replace(inventory, resolved=())
    elif mutation == "duplicate":
        inventory = replace(inventory, resolved=(row, row))
    elif mutation == "ghost":
        inventory = replace(
            inventory, resolved=(replace(row, site=replace(row.site, ordinal=999)),)
        )
    elif mutation == "overlap":
        from chiplog.verification.sql_expression_inventory import UnresolvedSqlExpression

        inventory = replace(inventory, unresolved=(UnresolvedSqlExpression(row.site, "unknown"),))
    elif mutation == "shape":
        inventory = replace(
            inventory, resolved=(replace(row, site=replace(row.site, shape_sha256="0" * 64)),)
        )
    else:
        inventory = replace(
            inventory, resolved=(replace(row, source_digests=((row.site.module, "0" * 64),)),)
        )
    with pytest.raises(SqlExpressionAccountingError):
        inventory.require_exact_accounting()


def test_same_call_shapes_with_changed_source_bytes_do_not_reuse_old_corpus() -> None:
    before = (("src/example.py", 'VALUE = 1\nconnection.execute("SELECT 1")'),)
    after = (("src/example.py", 'VALUE = 2\nconnection.execute("SELECT 1")'),)
    # Blank lines alone keep every corpus node shape but change byte identity.
    spaced = ((before[0][0], "\n" + before[0][1]),)
    for sources in (after, spaced):
        with pytest.raises(SqlExpressionSnapshotChanged):
            extract_sql_expression_inventory(sources, inspect_sources(before))


def test_named_planning_families_join_after_non_sql_and_bare_sql_events() -> None:
    source = (ROOT / PLANNING).read_text()
    marker = "        duplicate_sequence = connection.execute(\n"
    assert source.count(marker) == 1
    source = source.replace(
        marker, '        extra = object()\n        execute("SELECT 99")\n' + marker
    )
    inventory = _inspect(((PLANNING, source),))
    rows = [
        row
        for row in inventory.resolved
        if row.site.qualname == "BrokerAuthorityReader._execute_planning_read"
    ]
    assert any(row.alternatives[0].sql == "SELECT 99" for row in rows)
    dynamic = [row for row in rows if "execute(publication_sql" in row.site.expression]
    assert len(dynamic) == 1 and len(dynamic[0].alternatives) == 2
    assert all("FROM publications" in item.sql for item in dynamic[0].alternatives)
    inventory.require_exact_accounting()


def test_partial_identifier_sources_keep_missing_dependency_reason() -> None:
    path = "src/chiplog/platform/_sqlite.py"
    inventory = _inspect(((path, (ROOT / path).read_text()),))
    rows = [row for row in inventory.unresolved if row.site.qualname == "SQLiteMaterializer._admit"]
    assert len(rows) == 1 and "source" in rows[0].reason.lower()
    inventory.require_exact_accounting()


def test_dynamic_predicate_mutation_is_retained_in_joined_sql_before_engine_observation() -> None:
    source = (ROOT / PLANNING).read_text()
    marker = '"commit_sequence, record_ids FROM publications WHERE tenant_id = ? "'
    (broker,) = [
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "BrokerAuthorityReader"
    ]
    (leaf,) = [
        node
        for node in broker.body
        if isinstance(node, ast.FunctionDef) and node.name == "_execute_planning_read"
    ]
    segment = ast.get_source_segment(source, leaf)
    assert segment.count(marker) == 1
    changed = segment.replace(
        marker,
        '"commit_sequence, record_ids FROM publications WHERE tenant_id = ? "\n'
        '            "AND EXISTS (SELECT 1 FROM deletion_fences WHERE frontier >= 0) "',
    )
    assert source.count(segment) == 1
    source = source.replace(segment, changed)
    inventory = _inspect(((PLANNING, source),))
    (row,) = [
        row
        for row in inventory.resolved
        if row.site.qualname == "BrokerAuthorityReader._execute_planning_read"
        and "execute(publication_sql" in row.site.expression
    ]
    tables = (
        "deletion_fences",
        "derivatives",
        "evidence_inbox",
        "publications",
        "records",
        "store_metadata",
        "tenant_heads",
    )
    objects = tuple(("table", name) for name in tables) + tuple(
        ("index", "sqlite_autoindex_" + name + "_1") for name in tables if name != "store_metadata"
    )
    for alternative in row.alternatives:
        observed = observe_statement(
            alternative.sql,
            registered_schema_sql=SCHEMA_SQL,
            registered_objects=objects,
            foreign_keys=True,
        )
        assert ("READ", "deletion_fences", "frontier", "main", None) in observed.actions
        expected = tuple(
            sorted(
                [
                    ("SELECT", None, None, None, None),
                    *(
                        ("READ", "publications", column, "main", None)
                        for column in (
                            "tenant_id",
                            "operation_kind",
                            "idempotency_key",
                            "request_fingerprint",
                            "commit_sequence",
                            "record_ids",
                        )
                    ),
                ]
            )
        )
        with pytest.raises(UnregisteredSqlAccess):
            verify_sql_actions(observed.actions, expected)


def test_current_tree_accounts_all_candidates_without_hiding_verifier_sql_or_unknowns() -> None:
    sources = tuple(
        (str(path.relative_to(ROOT)), path.read_text())
        for path in sorted((ROOT / "src").rglob("*.py"))
    )
    inventory = _inspect(sources)
    candidates = [node for node in inventory.corpus.nodes if node.kind == "sql_call_candidate"]
    assert len(inventory.resolved) + len(inventory.unresolved) == len(candidates)
    assert any(
        row.site.module.startswith("src/chiplog/verification/") for row in inventory.unresolved
    )
    known = {
        ("platform/_sqlite.py", "_expected_schema_manifest"),
        ("platform/_sqlite.py", "SQLiteMaterializer._admit"),
        ("platform/_sqlite.py", "SQLiteMaterializer._activate_checkpoint_bundle"),
        ("platform/_sqlite.py", "SQLiteMaterializer._register_derivative"),
        ("platform/authority_ledger.py", "BrokerAuthorityLedger.__init__"),
        ("platform/read_ledger.py", "BrokerReadLedger.__init__"),
        ("composition/r7_planning.py", "R7PlanningRuntime._locked_reconcile_read_generation"),
        ("composition/r7_planning.py", "R7PlanningRuntime._bootstrap_storage_binding"),
        ("composition/r12.py", "R12Workspace._read_state"),
        ("platform/authority_reads.py", "_schema_members"),
        ("platform/authority_reads.py", "_authority_snapshot_bytes"),
        ("platform/authority_reads.py", "BrokerAuthorityReader._execute_planning_read"),
        ("adapters/driven/planning_sqlite.py", "SQLitePlanningRepository.committed_publications"),
    }
    assert not [
        row
        for row in inventory.unresolved
        if (row.site.module.removeprefix("src/chiplog/"), row.site.qualname) in known
    ]
    dynamic = []
    for row in inventory.resolved:
        if (row.site.module.removeprefix("src/chiplog/"), row.site.qualname) in known:
            expression = ast.parse(row.site.expression, mode="eval").body
            assert isinstance(expression, ast.Call)
            if not isinstance(expression.args[0], ast.Constant):
                dynamic.append(row)
    # The uniqueness statement now has an explicit SQL local, joining the other
    # planning sites rather than remaining a literal execute argument.
    assert len(dynamic) == 16
    (preparation,) = [
        row for row in inventory.unresolved if row.site.qualname == "_prepared_planning_statement"
    ]
    assert preparation.site.expression == "connection.execute('EXPLAIN ' + sql, parameters)"
    for row in inventory.resolved:
        if row.site.qualname == "SQLitePlanningRepository.committed_publications" and (
            "placeholders" in row.site.expression
        ):
            assert row.kind == "PLACEHOLDER_REPRESENTATIVES"
            assert len(row.alternatives) == 3
            break
    else:
        pytest.fail("actual placeholder family missing")
    inventory.require_exact_accounting()


def test_named_route_refusal_keeps_each_dynamic_candidate_with_its_reason() -> None:
    source = (ROOT / PLANNING).read_text()
    marker = "        scope_sql = ("
    assert source.count(marker) == 1
    source = source.replace(marker, "        scope_sql = external_sql()\n        unused_sql = (")
    inventory = _inspect(((PLANNING, source),))
    rows = [
        row
        for row in inventory.unresolved
        if row.site.qualname == "BrokerAuthorityReader._execute_planning_read"
    ]
    assert len(rows) == 3
    assert all(row.reason.startswith("UnknownSqlExpression:") for row in rows)
    inventory.require_exact_accounting()


def test_nested_sql_keeps_its_nearest_scope_when_enclosing_family_refuses() -> None:
    source = (ROOT / PLANNING).read_text()
    marker = "            duplicate_sequence = connection.execute(\n"
    assert source.count(marker) == 1
    source = source.replace(
        marker,
        '            def inner():\n                connection.execute("SELECT 88")\n' + marker,
    )
    inventory = _inspect(((PLANNING, source),))
    (child,) = [
        row
        for row in inventory.resolved
        if row.site.qualname == "BrokerAuthorityReader._execute_planning_read.inner"
    ]
    assert child.alternatives[0].sql == "SELECT 88"
    assert (
        len(
            [
                row
                for row in inventory.unresolved
                if row.site.qualname == "BrokerAuthorityReader._execute_planning_read"
            ]
        )
        == 3
    )
    inventory.require_exact_accounting()
