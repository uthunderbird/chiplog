"""Real opener/leaf source drives symbolic routes before policy comparison."""

from pathlib import Path

import pytest

from chiplog.verification.canonical_database_routes import (
    UnknownDatabaseRoute,
    WrongDatabaseDomain,
    extract_canonical_database_routes,
    verify_forwarded_database_roles,
)

ROOT = Path(__file__).resolve().parents[2] / "src/chiplog"


def _sources():
    return (
        (ROOT / "composition/r7_planning.py").read_text(),
        (ROOT / "platform/r7_leaves.py").read_text(),
    )


def _replace(source, old, new):
    assert source.count(old) == 1
    return source.replace(old, new)


def test_actual_canonical_constructor_routes_remain_distinct_and_unresolved() -> None:
    observed = extract_canonical_database_routes(*_sources())
    verify_forwarded_database_roles(observed)
    routes = {route.sink: route.paths for route in observed.routes}
    assert routes["planning_store.path"] == routes["runtime.database_argument"] == ("$database",)
    assert routes["read_ledger.argument"] == ("$database.reads.sqlite3",)
    assert routes["trust_store.argument"] == ("$database.trust.sqlite3",)
    assert routes["supervisor.ledger_argument"] == ("$database.broker.sqlite3",)
    assert observed.unresolved
    with pytest.raises(UnknownDatabaseRoute, match="final receiver"):
        observed.require_closed()


@pytest.mark.parametrize(
    "old,new",
    [
        ("with ExitStack() as resources:", "with foreign() as resources:"),
        ("with ExitStack() as resources:", "with ExitStack() as resources, foreign():"),
        ("with ExitStack() as resources:", "async with ExitStack() as resources:"),
        ("with startup_scope:", "with foreign_scope:"),
        ("with startup_scope:", "with startup_scope, foreign():"),
        ("with startup_scope:", "with startup_scope as foreign:"),
        ("with startup_scope:", "async with startup_scope:"),
        (
            "store = resources.enter_context(factory(database, **materializer_kwargs))",
            "store = foreign.enter_context(factory(database, **materializer_kwargs))",
        ),
        (
            "store = resources.enter_context(factory(database, **materializer_kwargs))",
            "store = resources.enter_context(factory(database, **materializer_kwargs), foreign)",
        ),
        (
            "store = resources.enter_context(factory(database, **materializer_kwargs))",
            "store = alias = resources.enter_context(factory(database, **materializer_kwargs))",
        ),
        (
            "            store = resources.enter_context(factory(database, **materializer_kwargs))",
            "            if False:\n"
            "                store = resources.enter_"
            "context(factory(database, **materializer"
            "_kwargs))",
        ),
        (
            "            planning_store = ProductionPlanningStore(database)",
            "            def later():\n"
            "                planning_store = ProductionPlanningStore(database)",
        ),
    ],
)
def test_startup_frames_and_direct_context_entry_cannot_be_substituted(old, new):
    source, leaf = _sources()
    extract_canonical_database_routes(source, leaf)
    changed = _replace(source, old, new)
    with pytest.raises(UnknownDatabaseRoute):
        extract_canonical_database_routes(changed, leaf)


def test_main_path_substitution_is_observed_before_domain_comparison() -> None:
    source, leaf = _sources()
    changed = _replace(
        source,
        "planning_store = ProductionPlanningStore(database)",
        "planning_store = ProductionPlanningStore("
        "database.with_suffix(database.suffix + '.reads.sqlite3'))",
    )
    observed = extract_canonical_database_routes(changed, leaf)
    routes = {route.sink: route.paths for route in observed.routes}
    assert routes["planning_store.path"] == ("$database.reads.sqlite3",)
    assert routes["runtime.database_argument"] == ("$database", "$database.reads.sqlite3")
    with pytest.raises(WrongDatabaseDomain):
        verify_forwarded_database_roles(observed)


@pytest.mark.parametrize("suffix", [".trust.sqlite3", ".broker.sqlite3"])
def test_read_ledger_suffix_swap_is_observed_then_rejected(suffix):
    source, leaf = _sources()
    changed = _replace(
        source, 'database.suffix + ".reads.sqlite3"', f'database.suffix + "{suffix}"'
    )
    observed = extract_canonical_database_routes(changed, leaf)
    assert next(
        route.paths for route in observed.routes if route.sink == "read_ledger.argument"
    ) == ("$database" + suffix,)
    with pytest.raises(WrongDatabaseDomain):
        verify_forwarded_database_roles(observed)


@pytest.mark.parametrize(
    "replacement",
    [
        "other_database",
        "choose_database(database)",
        "database.parent",
    ],
)
def test_unrecognized_path_expression_is_unknown_not_main(replacement):
    source, leaf = _sources()
    changed = _replace(
        source,
        "planning_store = ProductionPlanningStore(database)",
        f"planning_store = ProductionPlanningStore({replacement})",
    )
    with pytest.raises(UnknownDatabaseRoute):
        extract_canonical_database_routes(changed, leaf)


@pytest.mark.parametrize("mutation", ["rebind", "conditional", "foreign_import", "factory"])
def test_non_dominating_rebound_or_unknown_producer_has_no_route_proof(mutation):
    source, leaf = _sources()
    if mutation == "rebind":
        changed = _replace(
            source,
            "            planning_store = ProductionPlanningStore(database)",
            "            database = other_database\n"
            "            planning_store = ProductionPlanningStore(database)",
        )
    elif mutation == "conditional":
        changed = _replace(
            source,
            "            planning_store = ProductionPlanningStore(database)",
            "            if manifest:\n"
            "                planning_store = ProductionPlanningStore(database)",
        )
    elif mutation == "foreign_import":
        changed = _replace(
            source,
            "from chiplog.platform.r7_leaves import ProductionClock, ProductionPlanningStore",
            "from somewhere_else import ProductionClock, ProductionPlanningStore",
        )
    else:
        changed = _replace(
            source,
            "                else SQLiteMaterializer\n",
            "                else OtherMaterializer\n",
        )
    with pytest.raises(UnknownDatabaseRoute):
        extract_canonical_database_routes(changed, leaf)


def test_leaf_constructor_must_really_forward_its_path_parameter() -> None:
    source, leaf = _sources()
    # There are two analogous leaf classes; change only the production class.
    start = leaf.index("class ProductionPlanningStore:")
    end = leaf.index("class EvaluationPlanningStore:")
    production = leaf[start:end]
    changed = (
        leaf[:start]
        + _replace(production, "self.path = path", "self.path = foreign_path")
        + leaf[end:]
    )
    with pytest.raises(UnknownDatabaseRoute):
        extract_canonical_database_routes(source, changed)


@pytest.mark.parametrize("placement", ["local", "module", "class"])
def test_imported_constructor_name_cannot_be_shadowed(placement):
    source, leaf = _sources()
    if placement == "local":
        changed = _replace(
            source,
            "            planning_store = ProductionPlanningStore(database)",
            "            ProductionPlanningStore = OtherStore\n"
            "            planning_store = ProductionPlanningStore(database)",
        )
    elif placement == "module":
        changed = source + "\nProductionPlanningStore = OtherStore\n"
    else:
        changed = source + "\nclass ProductionPlanningStore:\n    pass\n"
    with pytest.raises(UnknownDatabaseRoute, match=r"constructor.*(bound|shadow)"):
        extract_canonical_database_routes(changed, leaf)


def test_same_module_import_alias_cannot_substitute_a_different_leaf() -> None:
    source, leaf = _sources()
    changed = _replace(
        source,
        "from chiplog.platform.r7_leaves import ProductionClock, ProductionPlanningStore",
        "from chiplog.platform.r7_leaves import ProductionClock, "
        "EvaluationPlanningStore as ProductionPlanningStore",
    )
    with pytest.raises(UnknownDatabaseRoute, match="constructor import"):
        extract_canonical_database_routes(changed, leaf)


@pytest.mark.parametrize(
    "replacement",
    [
        "ProductionPlanningStore = EvaluationPlanningStore\n",
        "if True:\n    class ProductionPlanningStore:\n        pass\n",
    ],
)
def test_leaf_module_cannot_rebind_the_inspected_class_before_import(replacement):
    source, leaf = _sources()
    changed = leaf + "\n" + replacement
    with pytest.raises(UnknownDatabaseRoute, match=r"leaf.*(bound|shadow)"):
        extract_canonical_database_routes(source, changed)


def test_nested_function_database_parameter_is_not_the_canonical_database() -> None:
    source, leaf = _sources()
    changed = _replace(
        source,
        "            store = resources.enter_context(factory(database, **materializer_kwargs))",
        "            store = resources.enter_context("
        "other_factory(database, **materializer_kwargs))",
    )
    changed = _replace(
        changed,
        "    runtime: R7PlanningRuntime | None = None\n",
        "    def unused(database):\n"
        "        with factory(database, **materializer_kwargs):\n"
        "            pass\n"
        "    runtime: R7PlanningRuntime | None = None\n",
    )
    with pytest.raises(UnknownDatabaseRoute, match=r"placement|frame"):
        extract_canonical_database_routes(changed, leaf)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown_or"])
def test_missing_duplicate_or_unknown_consumer_route_refuses(mutation):
    source, leaf = _sources()
    if mutation == "missing":
        changed = _replace(
            source,
            "store = resources.enter_context(factory(database, **materializer_kwargs))",
            "store = resources.enter_context(other_factory(database, **materializer_kwargs))",
        )
    elif mutation == "duplicate":
        changed = _replace(
            source,
            "    runtime: R7PlanningRuntime | None = None\n",
            "    extra = factory(database, **materializer_kwargs)\n"
            "    runtime: R7PlanningRuntime | None = None\n",
        )
    else:
        changed = _replace(
            source, "planning_store.path or database,", "planning_store.path or other_database,"
        )
    with pytest.raises(UnknownDatabaseRoute):
        extract_canonical_database_routes(changed, leaf)


def test_unsupported_leaf_module_execution_cannot_delete_export_binding() -> None:
    source, leaf = _sources()
    changed = leaf + (
        "\ntry:\n    raise ValueError()\nexcept ValueError as ProductionPlanningStore:\n    pass\n"
    )
    with pytest.raises(UnknownDatabaseRoute, match="leaf module"):
        extract_canonical_database_routes(source, changed)
