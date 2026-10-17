"""Real H1 cutoff SQL and correlated parameter branches."""

import ast
from hashlib import sha256
from pathlib import Path

import pytest

from chiplog.platform._sqlite import SCHEMA_SQL
from chiplog.verification.cutoff_sql_families import UnknownCutoffSql, extract_h1_cutoff_sql
from chiplog.verification.sqlite_preparation import (
    UnregisteredSqlAccess,
    observe_statement,
    verify_sql_actions,
)

SOURCE = Path(__file__).resolve().parents[2] / "src/chiplog/composition/h1_owner_inventory.py"
TABLES = (
    "deletion_fences",
    "derivatives",
    "evidence_inbox",
    "publications",
    "records",
    "store_metadata",
    "tenant_heads",
)
OBJECTS = tuple(("table", name) for name in TABLES) + tuple(
    ("index", "sqlite_autoindex_" + name + "_1") for name in TABLES if name != "store_metadata"
)
COLUMNS = (
    (
        "publications",
        (
            "operation_kind",
            "idempotency_key",
            "request_fingerprint",
            "commit_sequence",
            "record_ids",
            "tenant_id",
        ),
    ),
    (
        "records",
        ("record_id", "owner", "schema_id", "canonical_bytes", "commit_sequence", "tenant_id"),
    ),
)


def _expected(index):
    table, columns = COLUMNS[index]
    return tuple(
        sorted(
            [
                ("SELECT", None, None, None, None),
                *(("READ", table, column, "main", None) for column in columns),
            ]
        )
    )


def _prepare(sql):
    return observe_statement(
        sql, registered_schema_sql=SCHEMA_SQL, registered_objects=OBJECTS, foreign_keys=True
    )


def _replace(source, old, new):
    assert source.count(old) == 1
    return source.replace(old, new)


def test_actual_both_sites_keep_two_correlated_branches_and_independent_actions() -> None:
    source = SOURCE.read_text()
    families = extract_h1_cutoff_sql(source)
    assert [(family.site, family.receiver) for family in families] == [
        (2, "connection"),
        (3, "connection"),
    ]
    for index, family in enumerate(families):
        assert family.parameters == (("scope.tenant",), ("scope.tenant", "boundary"))
        assert family.condition == "boundary is None"
        assert family.source_sha256 == sha256(source.encode()).hexdigest()
        assert "limit" in family.expression
        for arity, sql in enumerate(family.sql, 1):
            observed = _prepare(sql)
            assert observed.bindings == arity
            verify_sql_actions(observed.actions, _expected(index))


def test_actual_predicate_mutation_reaches_engine_before_graph_refusal() -> None:
    source = _replace(
        SOURCE.read_text(),
        '+ " ORDER BY commit_sequence,operation_kind,idempotency_key"',
        '+ " AND EXISTS (SELECT 1 FROM deletion_fences WHERE frontier>=0)"'
        ' + " ORDER BY commit_sequence,operation_kind,idempotency_key"',
    )
    family, _ = extract_h1_cutoff_sql(source)
    for sql in family.sql:
        observed = _prepare(sql)
        assert ("READ", "deletion_fences", "frontier", "main", None) in observed.actions
        with pytest.raises(UnregisteredSqlAccess):
            verify_sql_actions(observed.actions, _expected(0))


@pytest.mark.parametrize(
    "old,new",
    (
        (
            "(scope.tenant,) if boundary is None else (scope.tenant, boundary)",
            "(scope.tenant,) if boundary is not None else (scope.tenant, boundary)",
        ),
        ("else (scope.tenant, boundary)", "else (scope.tenant, other)"),
        (
            'limit = "" if boundary is None else " AND commit_sequence <= ?"',
            'limit = "" if boundary is None else " AND commit_sequence >= ?"',
        ),
        (
            "params: tuple[object, ...] = (",
            "boundary = None\n                params: tuple[object, ...] = (",
        ),
        (
            "publications = tuple(\n                    connection.execute(",
            "boundary = None\n                publications = tuple(\n"
            "                    connection.execute(",
        ),
        (
            "publications = tuple(\n                    connection.execute(",
            'limit = ""\n                publications '
            "= tuple(\n                    connection."
            "execute(",
        ),
        (
            "+ limit\n                        "
            '+ " ORDER BY commit_sequence,operation_kind,idempotency_key"',
            '+ ""\n                        + " ORDER B'
            "Y commit_sequence,operation_kind,idempot"
            'ency_key"',
        ),
        (
            "+ limit\n                        "
            '+ " ORDER BY commit_sequence,operation_kind,idempotency_key"',
            "+ opaque(limit)\n                        "
            '+ " ORDER BY commit_sequence,operation_kind,idempotency_key"',
        ),
        (
            '"WHERE tenant_id=?" + limit + " ORDER BY commit_sequence,record_id"',
            '"WHERE tenant_id=?" + limit + " ORDER BY commit_sequence,record_id" + limit',
        ),
        (
            'limit = "" if boundary is None else " AND commit_sequence <= ?"',
            "if flag:\n                    "
            'limit = "" if boundary is None else " AND commit_sequence <= ?"',
        ),
    ),
)
def test_actual_unknown_relation_or_missing_family_refuses(old: str, new: str) -> None:
    with pytest.raises(UnknownCutoffSql):
        extract_h1_cutoff_sql(_replace(SOURCE.read_text(), old, new))


def test_nested_consumer_frame_refuses_with_cutoff_error() -> None:
    tree = ast.parse(SOURCE.read_text())
    (method,) = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "read_h1_scoped_owner_inventory"
    ]
    calls = [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        and node.args
        and any(
            isinstance(part, ast.Name) and part.id == "limit" for part in ast.walk(node.args[0])
        )
    ]
    target = min(calls, key=lambda node: node.lineno)

    class Wrap(ast.NodeTransformer):
        def visit_Call(self, node):
            if node is target:
                wrapper = ast.parse("(lambda: None)()", mode="eval").body
                wrapper.func.body = node
                return ast.copy_location(wrapper, node)
            return self.generic_visit(node)

    changed = ast.unparse(ast.fix_missing_locations(Wrap().visit(tree)))
    with pytest.raises(UnknownCutoffSql):
        extract_h1_cutoff_sql(changed)


def test_global_boundary_is_not_local_correlation_proof() -> None:
    source = _replace(
        SOURCE.read_text(),
        "        boundary: int | None = None",
        "        global boundary\n        boundary = None",
    )
    with pytest.raises(UnknownCutoffSql):
        extract_h1_cutoff_sql(source)
