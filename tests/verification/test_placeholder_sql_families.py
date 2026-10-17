"""Extract actual dynamic SQL before engine/independent graph comparison."""

from pathlib import Path

import pytest

from chiplog.platform._sqlite import SCHEMA_SQL
from chiplog.verification.placeholder_sql_families import (
    UnknownPlaceholderSql,
    extract_placeholder_sql_families,
    prepare_placeholder_sql_family,
)
from chiplog.verification.sqlite_preparation import UnregisteredSqlAccess, verify_sql_actions

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
    (
        "composition/r14_native_accepted_open_cut.py",
        "build_accepted_open_recovery_cut",
        1,
        "target_runs",
        "(run.head for run in target_runs)",
        2,
        ("canonical_bytes", "commit_sequence", "owner", "record_id", "schema_id", "tenant_id"),
    ),
    (
        "composition/r14_original_call_resolution_source.py",
        "_require_resolution_absent",
        1,
        "_RESOLUTION_SCHEMAS",
        "_RESOLUTION_SCHEMAS",
        1,
        ("record_id", "tenant_id"),
    ),
    (
        "composition/r14_original_call_resolution_publication.py",
        "_validate_physical_catalog",
        1,
        "_SCHEMAS",
        "_SCHEMAS",
        1,
        ("canonical_bytes", "commit_sequence", "owner", "record_id", "schema_id", "tenant_id"),
    ),
    (
        "composition/r14_recovery_profile_publication.py",
        "_assert_v2_precedes_covered_lineage",
        3,
        "covered",
        "covered",
        1,
        ("commit_sequence", "tenant_id"),
    ),
)


def _extract(case, source=None):
    path, qualname, *_ = case
    return extract_placeholder_sql_families(
        (ROOT / path).read_text() if source is None else source, qualname
    )


def _prepare(family):
    return prepare_placeholder_sql_family(
        family, schema_sql=SCHEMA_SQL, registered_objects=OBJECTS, foreign_keys=True
    )


def _expected(case, n):
    path, *_, columns = case
    extra = (
        ("schema_id",)
        if n
        and path.endswith(
            (
                "r14_original_call_resolution_source.py",
                "r14_recovery_profile_publication.py",
            )
        )
        else ()
    )
    # Empty IN makes the whole WHERE false before SQLite authorizes predicates;
    # output expressions remain authorized, but tenant/schema filters disappear.
    columns = tuple(column for column in columns if n or column != "tenant_id")
    actions = [("READ", "records", column, "main", None) for column in (*columns, *extra)]
    actions.append(("SELECT", None, None, None, None))
    if path.endswith("r14_recovery_profile_publication.py"):
        actions.append(("FUNCTION", None, "min", None, None))
    return tuple(
        sorted(actions, key=lambda row: tuple((value is not None, value or "") for value in row))
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case[1])
def test_actual_six_families_bind_exact_generator_and_parameter_source(case):
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


def _replace(source, old, new):
    assert source.count(old) == 1
    return source.replace(old, new)


@pytest.mark.parametrize(
    "mutation",
    [
        "element",
        "separator",
        "filter",
        "binder",
        "rebind",
        "conversion",
        "format",
        "projection_filter",
        "projection_call",
        "conditional_definition",
        "nested_consumer",
        "iterable_rebind",
        "fixed_parameter_call",
    ],
)
def test_actual_placeholder_and_binder_mutations_are_unknown(mutation):
    case = CASES[2]
    source = (ROOT / case[0]).read_text()
    if mutation in {"element", "separator", "filter"}:
        replacement = {
            "element": '",".join(run.head for run in target_runs)',
            "separator": '" OR ".join("?" for _ in target_runs)',
            "filter": '",".join("?" for _ in target_runs if _.head)',
        }[mutation]
        changed = _replace(source, '",".join("?" for _ in target_runs)', replacement)
    elif mutation == "binder":
        changed = _replace(source, "*(run.head for run in target_runs)", "*other_runs")
    elif mutation == "rebind":
        changed = _replace(
            source,
            "                with read_connection(runtime._database)",
            '                placeholders = "injected"\n'
            "                with read_connection(runtime._database)",
        )
    elif mutation == "iterable_rebind":
        changed = _replace(
            source,
            "                with read_connection(runtime._database)",
            "                target_runs = other_runs\n"
            "                with read_connection(runtime._database)",
        )
    elif mutation == "fixed_parameter_call":
        changed = _replace(
            source,
            "(runtime._tenant_id, source_tenant_commit_sequence,",
            "(mutate(target_runs), source_tenant_commit_sequence,",
        )
    elif mutation in {"conversion", "format"}:
        changed = _replace(
            source,
            "{placeholders}",
            "{placeholders!r}" if mutation == "conversion" else "{placeholders:>20}",
        )
    elif mutation.startswith("projection_"):
        changed = _replace(
            source,
            "*(run.head for run in target_runs)",
            "*(run.head for run in target_runs if run.head)"
            if mutation == "projection_filter"
            else "*(run.head() for run in target_runs)",
        )
    elif mutation == "conditional_definition":
        changed = _replace(
            source,
            '                placeholders = ",".join',
            '                if historical:\n                    placeholders = ",".join',
        )
    else:
        # Move the real consumer into a nested frame with a foreign connection.
        start = source.index("                with read_connection(runtime._database)")
        end = source.index("                if not rows:", start)
        block = source[start:end]
        changed = (
            source[:start]
            + (
                "                def consume(connection):\n"
                + "\n".join("    " + line for line in block.split("\n"))
                + "\n"
            )
            + source[end:]
        )
    with pytest.raises(UnknownPlaceholderSql):
        _extract(case, changed)


def test_added_predicate_edge_is_extracted_before_independent_graph_refusal():
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


def test_repeated_source_sites_are_not_deduplicated_and_missing_consumer_refuses():
    case = CASES[3]
    source = (ROOT / case[0]).read_text()
    changed = _replace(
        source,
        "    if rows:\n",
        (
            "    with read_connection(runtime._database) as connection:\n"
            '        connection.execute("SELECT record_id FROM records WHERE tenant_id=? '
            'AND schema_id IN (" + placeholders + ")"'
            ", (runtime._tenant_id, *_RESOLUTION_SCHE"
            "MAS))\n"
            "    if rows:\n"
        ),
    )
    observed = _extract(case, changed)
    assert [family.site for family in observed] == [1, 2]
    assert all(family.iterable == "_RESOLUTION_SCHEMAS" for family in observed)
    missing = _replace(source, "                + placeholders\n", '                + "?"\n')
    with pytest.raises(UnknownPlaceholderSql, match="consumer"):
        _extract(case, missing)
