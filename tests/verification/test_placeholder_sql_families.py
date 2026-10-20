"""Extract actual dynamic SQL before engine/independent graph comparison."""

from pathlib import Path

import pytest

from chiplog.platform._sqlite import SCHEMA_SQL
from chiplog.verification.placeholder_sql_families import (
    PlaceholderSqlFamily,
    extract_placeholder_sql_families,
    prepare_placeholder_sql_family,
)
from chiplog.verification.sqlite_preparation import (
    SqlAction,
    SqlPreparation,
    UnregisteredSqlAccess,
    verify_sql_actions,
)

ROOT = Path(__file__).resolve().parents[2] / "src/chiplog"
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
type PlaceholderCase = tuple[str, str, int, str, str, int, tuple[str, ...]]

CASES = (
    (
        "adapters/driven/planning_sqlite.py",
        "SQLitePlanningRepository.committed_publications",
        2,
        "ids",
        "ids",
        1,
        ("canonical_bytes", "record_id", "tenant_id"),
    ),
    (
        "platform/_sqlite.py",
        "SQLiteMaterializer._register_derivative",
        1,
        "registration.source_record_ids",
        "registration.source_record_ids",
        1,
        ("record_id", "tenant_id"),
    ),
)


def _extract(case: PlaceholderCase, source: str | None = None) -> tuple[PlaceholderSqlFamily, ...]:
    path, qualname, *_ = case
    return extract_placeholder_sql_families(
        (ROOT / path).read_text() if source is None else source, qualname
    )


def _prepare(family: PlaceholderSqlFamily) -> tuple[tuple[int, SqlPreparation], ...]:
    return prepare_placeholder_sql_family(
        family, schema_sql=SCHEMA_SQL, registered_objects=OBJECTS, foreign_keys=True
    )


def _expected(case: PlaceholderCase, n: int) -> tuple[SqlAction, ...]:
    *_, columns = case
    # Empty IN makes the whole WHERE false before SQLite authorizes predicates;
    # output expressions remain authorized, but tenant/schema filters disappear.
    columns = tuple(column for column in columns if n or column != "tenant_id")
    actions: list[SqlAction] = [("READ", "records", column, "main", None) for column in columns]
    actions.append(("SELECT", None, None, None, None))
    return tuple(
        sorted(actions, key=lambda row: tuple((value is not None, value or "") for value in row))
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case[1])
def test_actual_families_bind_exact_generator_and_parameter_source(
    case: PlaceholderCase,
) -> None:
    (family,) = _extract(case)
    assert (
        family.qualname,
        family.site,
        family.iterable,
        family.bound_values,
        len(family.fixed_parameters),
    ) == case[1:6]
    assert family.receiver == (
        "self._connection" if case[0] == "platform/_sqlite.py" else "connection"
    )
    observed = _prepare(family)
    assert tuple(n for n, _ in observed) == (0, 1, 2)
    for n, statement in observed:
        assert statement.bindings == len(family.fixed_parameters) + n
        verify_sql_actions(statement.actions, _expected(case, n))


def _replace(source: str, old: str, new: str) -> str:
    assert source.count(old) == 1
    return source.replace(old, new)


def test_added_predicate_edge_is_extracted_before_independent_graph_refusal() -> None:
    case = CASES[0]
    source = (ROOT / case[0]).read_text()
    changed = _replace(
        source,
        'f"WHERE tenant_id = ? AND record_id IN ({placeholders})"',
        'f"WHERE tenant_id = ? AND record_id IN ({placeholders}) "\n'
        '                        "AND EXISTS (SELECT 1 FROM deletion_fences WHERE frontier>=0)"',
    )
    (family,) = _extract(case, changed)
    for n, observed in _prepare(family):
        edge = ("READ", "deletion_fences", "frontier", "main", None)
        if n:
            assert edge in observed.actions
            with pytest.raises(UnregisteredSqlAccess):
                verify_sql_actions(observed.actions, _expected(case, n))
        else:
            assert edge not in observed.actions
            verify_sql_actions(observed.actions, _expected(case, n))
