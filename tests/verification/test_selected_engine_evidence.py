"""Selected engine facts join the same snapshot and independent fixed contracts."""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from chiplog.verification.authority_descriptor_closure import (
    DescriptorClosureIncomplete,
    DescriptorClosureRefused,
    _from_sources,
)
from chiplog.verification.selected_engine_contracts import (
    AMR_MEMBERS,
    EXPECTED_SQL,
    expected_actions,
    schema_contract,
)
from chiplog.verification.selected_engine_evidence import (
    SelectedDomainSchemaEvidence,
    SelectedEngineRefused,
    verify_domain_schema,
    verify_preparation,
)
from chiplog.verification.sqlite_preparation import (
    SqlPreparationRefused,
    observe_registered_schema,
    observe_statement,
)

ROOT = Path(__file__).resolve().parents[2]
PATHS = (
    "src/chiplog/composition/r7_planning.py",
    "src/chiplog/platform/authority_reads.py",
    "src/chiplog/platform/read_ledger.py",
    "src/chiplog/platform/_sqlite.py",
    "src/chiplog/platform/r7_leaves.py",
    "src/chiplog/verification/sqlite_preparation.py",
    "src/chiplog/architecture/r7_storage_surface.py",
    "src/chiplog/composition/r12.py",
)


def sources():
    return tuple((p, (ROOT / p).read_text()) for p in PATHS)


def test_actual_catalog_prepares_with_only_finite_glob_function_permission():
    result = _from_sources(sources())
    (chain,) = (r for r in result.fragment.scanner_chains if r.role == "SCHEMA_CATALOG_METADATA")
    from chiplog.platform._sqlite import SCHEMA_SQL

    tables = (
        "deletion_fences",
        "derivatives",
        "evidence_inbox",
        "publications",
        "records",
        "store_metadata",
        "tenant_heads",
    )
    objects = tuple(("table", t) for t in tables) + tuple(
        ("index", "sqlite_autoindex_" + t + "_1") for t in tables if t != "store_metadata"
    )
    observed = observe_statement(
        chain.expression.alternatives[0].sql,
        registered_schema_sql=SCHEMA_SQL,
        registered_objects=objects,
        foreign_keys=False,
    )
    assert ("FUNCTION", None, "glob", None, None) in observed.actions


def test_same_fragment_retains_both_domain_schemas_and_selected_engine_rows():
    result = _from_sources(sources())
    assert {r.domain for r in result.fragment.domain_schemas} == {
        "tenant_main",
        "broker_read_ledger",
    }
    assert {r.site for r in result.fragment.engine_evidence} >= {
        r.sink for r in (*result.fragment.scanner_chains, *result.fragment.ledger_chains)
    }


@pytest.fixture(scope="module")
def selected():
    return _from_sources(sources())


def test_physical_cases_units_and_metadata_do_not_become_business_amr(selected):
    fragment = selected.fragment
    assert len(fragment.engine_evidence) == 35
    assert {(r.domain, r.initial_case) for r in fragment.domain_schemas} == {
        ("tenant_main", "EMPTY"),
        ("broker_read_ledger", "EMPTY"),
        ("broker_read_ledger", "EXISTING"),
    }
    scripts = [r for r in fragment.engine_evidence if r.role == "SCHEMA_SCRIPT"]
    assert {(r.initial_case, r.unit_ordinal) for r in scripts} == {
        ("EMPTY", 1),
        ("EMPTY", 2),
        ("EXISTING", 1),
        ("EXISTING", 2),
    }
    assert all(
        r.preparation.initial_schema_sha256 == sha256(b"").hexdigest()
        for r in scripts
        if r.initial_case == "EMPTY"
    )
    metadata = [r for r in fragment.engine_evidence if r.role == "SCHEMA_COLUMN_METADATA"]
    assert len(metadata) == 7
    assert all(r.metadata_result and r.metadata_requests and not r.business_reads for r in metadata)
    (catalog,) = (r for r in fragment.engine_evidence if r.role == "SCHEMA_CATALOG_METADATA")
    assert catalog.business_reads == ()
    assert catalog.catalog_reads == (
        ("main", "sqlite_master", "name"),
        ("main", "sqlite_master", "type"),
    )
    commitment = [r for r in fragment.engine_evidence if r.role == "AUTHORITY_COMMITMENT_SCAN"]
    assert {r.business_reads[0][1] for r in commitment} == {
        t for t, _, authority in AMR_MEMBERS if authority
    }
    assert all(r.domain == "tenant_main" for r in commitment)
    with pytest.raises(DescriptorClosureIncomplete):
        selected.require_closed()


@pytest.mark.parametrize(
    "qualname,ordinal,domain,extra",
    [
        (
            "BrokerAuthorityReader._execute_planning_read",
            44,
            "tenant_main",
            " AND EXISTS (SELECT 1 FROM deletion_fences WHERE frontier >= 0) ",
        ),
        ("BrokerReadLedger.release", 29, "broker_read_ledger", " AND dequeued = 0"),
    ],
)
def test_engine_observes_predicate_reads_before_independent_refusal(
    qualname, ordinal, domain, extra
):
    _, _, label, sql = next(r for r in EXPECTED_SQL if r[:2] == (qualname, ordinal))
    if domain == "tenant_main":
        changed = sql.replace("ORDER BY commit_sequence", extra + "ORDER BY commit_sequence")
        dependency = ("READ", "deletion_fences", "frontier", "main", None)
    else:
        changed = sql + extra
        dependency = ("READ", "authority_read_releases", "dequeued", "main", None)
    ddl, objects, *_ = schema_contract(domain)
    observed = observe_statement(
        changed, registered_schema_sql=ddl, registered_objects=objects, foreign_keys=False
    )
    assert dependency in observed.actions
    with pytest.raises(SelectedEngineRefused):
        verify_preparation(
            observed, sql=sql, domain=domain, actions=expected_actions(qualname, ordinal, label)
        )


@pytest.mark.parametrize(
    "old,new",
    [
        ("CHECK (singleton = 1)", "CHECK (singleton >= 1)"),
        ("head INTEGER NOT NULL", "head TEXT NOT NULL"),
        ("head INTEGER NOT NULL", "head INTEGER"),
        ("tenant_id TEXT PRIMARY KEY, generation", "tenant_id TEXT UNIQUE, generation"),
        ("source_epoch INTEGER NOT NULL", "source_epoch INTEGER NOT NULL DEFAULT 0"),
    ],
)
def test_global_rejects_schema_constraint_type_null_pk_and_default_mutants(old, new):
    path = "src/chiplog/platform/_sqlite.py"
    items = sources()
    assert dict(items)[path].count(old) == 1
    changed = tuple((p, text.replace(old, new) if p == path else text) for p, text in items)
    with pytest.raises(DescriptorClosureRefused):
        _from_sources(changed)


@pytest.mark.parametrize(
    "tail,extra_objects",
    [
        ("CREATE INDEX extra ON records(owner);", (("index", "extra"),)),
        (
            "CREATE INDEX extra ON records(owner) WHERE tenant_id IS NOT NULL;",
            (("index", "extra"),),
        ),
        ("CREATE VIEW extra AS SELECT owner FROM records;", ()),
        ("CREATE TRIGGER extra AFTER INSERT ON records BEGIN DELETE FROM records; END;", ()),
    ],
)
def test_unexpected_index_view_trigger_never_passes_physical_contract(tail, extra_objects):
    ddl, objects, *_ = schema_contract("tenant_main")
    if "INDEX" not in tail:
        with pytest.raises(SqlPreparationRefused):
            observe_registered_schema(
                ddl + tail, initial_schema_sql="", registered_objects=objects, foreign_keys=False
            )
    else:
        observation = observe_registered_schema(
            ddl + tail,
            initial_schema_sql="",
            registered_objects=(*objects, *extra_objects),
            foreign_keys=False,
        )
        # Bind the hash to the reviewed candidate to discriminate physical equality too.
        observation = replace(observation, schema_sha256=sha256(ddl.encode()).hexdigest())
        with pytest.raises(SelectedEngineRefused, match="physical schema differs"):
            verify_domain_schema(
                SelectedDomainSchemaEvidence(
                    "tenant_main", "UNREGISTERED/tenant_main/v1", "EMPTY", (), observation
                )
            )


def test_second_if_not_exists_body_cannot_hide_behind_existing_success():
    ddl, objects, *_ = schema_contract("broker_read_ledger")
    changed = ddl.replace("dequeued IN (0, 1)", "dequeued IN (0, 1, 2)")
    existing = observe_registered_schema(
        changed, initial_schema_sql=ddl, registered_objects=objects, foreign_keys=False
    )
    fresh = observe_registered_schema(
        changed, initial_schema_sql="", registered_objects=objects, foreign_keys=False
    )
    assert (
        existing.catalog
        == observe_registered_schema(
            ddl, initial_schema_sql=ddl, registered_objects=objects, foreign_keys=False
        ).catalog
    )
    assert fresh.catalog != existing.catalog
    path = "src/chiplog/platform/read_ledger.py"
    with pytest.raises(DescriptorClosureRefused):
        _from_sources(
            tuple(
                (p, s.replace("dequeued IN (0, 1)", "dequeued IN (0, 1, 2)"))
                if p == path
                else (p, s)
                for p, s in sources()
            )
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "drop",
        "duplicate",
        "domain",
        "role",
        "label",
        "sql",
        "bindings",
        "fk",
        "version",
        "schema_hash",
        "dependencies",
        "initial_case",
        "unit",
        "recipe",
        "observer_recipe",
        "actions",
        "metadata",
    ],
)
def test_mutually_forged_engine_records_fail_global_rederivation(selected, mutation):
    rows = list(selected.fragment.engine_evidence)
    index = next(i for i, r in enumerate(rows) if r.role == "SCHEMA_COLUMN_METADATA")
    row = rows[index]
    if mutation == "drop":
        rows.pop(index)
    elif mutation == "duplicate":
        rows.append(row)
    else:
        changes = {
            "domain": {"domain": "broker_read_ledger"},
            "role": {"role": "PLANNING_READ"},
            "label": {"alternative_label": "foreign"},
            "dependencies": {"expression_dependency_digests": ()},
            "initial_case": {"initial_case": "EMPTY"},
            "unit": {"unit_ordinal": 7},
            "recipe": {"runtime_recipe_id": "foreign"},
            "observer_recipe": {"observer_recipe": "foreign"},
            "metadata": {"metadata_result": ()},
        }
        if mutation in changes:
            rows[index] = replace(row, **changes[mutation])
        else:
            prep_changes = {
                "sql": {"sql": "PRAGMA table_info(records)"},
                "bindings": {"bindings": 99},
                "fk": {"foreign_keys": True},
                "version": {"sqlite_version": "forged-engine"},
                "schema_hash": {"initial_schema_sha256": "0" * 64},
                "actions": {"actions": ()},
            }
            rows[index] = replace(
                row, preparation=replace(row.preparation, **prep_changes[mutation])
            )
    forged = replace(selected, fragment=replace(selected.fragment, engine_evidence=tuple(rows)))
    with pytest.raises(DescriptorClosureRefused):
        forged.require_exact_accounting()


@pytest.mark.parametrize("mutation", ["drop", "swap", "columns", "indexes", "catalog", "version"])
def test_domain_schema_forgery_fails_global_consumer(selected, mutation):
    rows = list(selected.fragment.domain_schemas)
    if mutation == "drop":
        rows.pop()
    elif mutation == "swap":
        rows.reverse()
    else:
        row = rows[0]
        observed = row.observation
        changes = {
            "columns": {"columns": ()},
            "indexes": {"index_columns": ()},
            "catalog": {"catalog": ()},
            "version": {"sqlite_version": "forged"},
        }
        rows[0] = replace(row, observation=replace(observed, **changes[mutation]))
    forged = replace(selected, fragment=replace(selected.fragment, domain_schemas=tuple(rows)))
    with pytest.raises(DescriptorClosureRefused):
        forged.require_exact_accounting()


def test_semantic_comparison_records_engine_version_without_host_hardpin(selected):
    row = selected.fragment.engine_evidence[0]
    verify_preparation(
        replace(row.preparation, sqlite_version="another-engine-candidate"),
        sql=row.preparation.sql,
        domain=row.domain,
        actions=expected_actions(row.site.qualname, row.site.ordinal, row.alternative_label),
    )


def test_unregistered_function_is_still_refused_and_pragma_qualification_is_visible():
    ddl, objects, *_ = schema_contract("tenant_main")
    with pytest.raises(SqlPreparationRefused):
        observe_statement(
            "SELECT abs(head) FROM tenant_heads",
            registered_schema_sql=ddl,
            registered_objects=objects,
            foreign_keys=False,
        )
    unqualified = observe_statement(
        'PRAGMA table_info("records")',
        registered_schema_sql=ddl,
        registered_objects=objects,
        foreign_keys=False,
    )
    assert unqualified.actions == (("PRAGMA", "table_info", "records", None, None),)
    with pytest.raises(SelectedEngineRefused):
        verify_preparation(
            unqualified,
            sql='PRAGMA main.table_info("records")',
            domain="tenant_main",
            actions=(("PRAGMA", "table_info", "records", "main", None),),
        )


@pytest.mark.parametrize("mutation", ["column", "filter", "amr"])
def test_catalog_and_amr_mutations_refuse_global_independent_truth(mutation):
    items = sources()
    if mutation == "amr":
        path = "src/chiplog/architecture/r7_storage_surface.py"
        old = "        False,\n"
        new = "        True,\n"
    else:
        path = "src/chiplog/platform/authority_reads.py"
        old = "SELECT name FROM main.sqlite_master WHERE type = 'table'"
        new = (
            "SELECT name, sql FROM main.sqlite_master WHERE type = 'table'"
            if mutation == "column"
            else "SELECT name FROM main.sqlite_master WHERE type IN ('table', 'view')"
        )
    assert dict(items)[path].count(old) == 1
    with pytest.raises(DescriptorClosureRefused):
        _from_sources(tuple((p, s.replace(old, new) if p == path else s) for p, s in items))


def test_schema_unit_check_dependencies_remain_raw_and_not_business_reads(selected):
    rows = [r for r in selected.fragment.engine_evidence if r.role == "SCHEMA_SCRIPT"]
    assert all(r.business_reads == () for r in rows)
    (fresh,) = (r for r in rows if r.initial_case == "EMPTY" and r.unit_ordinal == 2)
    assert (
        "READ",
        "authority_read_releases",
        "dequeued",
        "main",
        None,
    ) in fresh.preparation.actions
    metadata = [r for r in selected.fragment.engine_evidence if r.metadata_result]
    assert all(r.metadata_schema_case == "EMPTY" for r in metadata)


def test_selected_semantics_refuse_a_different_actual_foreign_key_setup():
    ddl, objects, *_ = schema_contract("tenant_main")
    observed = observe_statement(
        "BEGIN", registered_schema_sql=ddl, registered_objects=objects, foreign_keys=True
    )
    assert observed.foreign_keys is True
    with pytest.raises(SelectedEngineRefused, match="profile"):
        verify_preparation(
            observed,
            sql="BEGIN",
            domain="tenant_main",
            actions=(("TRANSACTION", "BEGIN", None, None, None),),
        )
