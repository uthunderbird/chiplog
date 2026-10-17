"""The R7 base planning fragment joins proofs but never grants admission."""

import ast
from pathlib import Path
from typing import cast

import pytest

from chiplog.verification.authority_read_graph import _prepare_reads
from chiplog.verification.planning_descriptor_chain import (
    UnknownPlanningChain,
    build_planning_descriptor_fragment,
    verify_planning_descriptor_fragment,
)
from chiplog.verification.selected_engine_contracts import EXPECTED_SQL, schema_contract

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = "src/chiplog/composition/r7_planning.py"
READER = "src/chiplog/platform/authority_reads.py"
PREPARATION = "src/chiplog/verification/sqlite_preparation.py"
LEDGER = "src/chiplog/platform/read_ledger.py"
PATHS = (
    RUNTIME,
    READER,
    LEDGER,
    "src/chiplog/platform/_sqlite.py",
    "src/chiplog/platform/r7_leaves.py",
    PREPARATION,
    "src/chiplog/architecture/r7_storage_surface.py",
    "src/chiplog/composition/r12.py",
)


def sources() -> tuple[tuple[str, str], ...]:
    return tuple((p, (ROOT / p).read_text()) for p in PATHS)


def mutate(
    items: tuple[tuple[str, str], ...], path: str, old: str, new: str
) -> tuple[tuple[str, str], ...]:
    assert dict(items)[path].count(old) == 1
    return tuple((p, text.replace(old, new) if p == path else text) for p, text in items)


def mutate_reader_leaf(
    items: tuple[tuple[str, str], ...], old: str, new: str
) -> tuple[tuple[str, str], ...]:
    text = dict(items)[READER]
    (broker,) = [
        node
        for node in ast.parse(text).body
        if isinstance(node, ast.ClassDef) and node.name == "BrokerAuthorityReader"
    ]
    (leaf,) = [
        node
        for node in broker.body
        if isinstance(node, ast.FunctionDef) and node.name == "_execute_planning_read"
    ]
    segment = cast(str, ast.get_source_segment(text, leaf))
    assert segment.count(old) == 1
    return mutate(items, READER, segment, segment.replace(old, new))


def mutate_preparation(
    items: tuple[tuple[str, str], ...], scope: str, old: str, new: str
) -> tuple[tuple[str, str], ...]:
    text = dict(items)[PREPARATION]
    (method,) = [
        node
        for node in ast.parse(text).body
        if isinstance(node, ast.FunctionDef) and node.name == scope
    ]
    segment = cast(str, ast.get_source_segment(text, method))
    assert segment.count(old) == 1
    return mutate(items, PREPARATION, segment, segment.replace(old, new))


def expected() -> tuple[tuple[int, int, tuple[tuple[str, str, str], ...]], ...]:
    pub = tuple(
        ("main", "publications", c)
        for c in (
            "commit_sequence",
            "idempotency_key",
            "operation_kind",
            "record_ids",
            "request_fingerprint",
            "tenant_id",
        )
    )
    selection = tuple(
        ("main", "publications", c)
        for c in ("commit_sequence", "idempotency_key", "operation_kind", "tenant_id")
    )
    rec = tuple(
        ("main", "records", c)
        for c in (
            "canonical_bytes",
            "commit_sequence",
            "owner",
            "record_id",
            "schema_id",
            "tenant_id",
        )
    )
    return (
        (
            1,
            0,
            (("main", "publications", "commit_sequence"), ("main", "publications", "tenant_id")),
        ),
        (2, 0, pub),
        (2, 1, pub),
        (3, 0, (*selection, *rec)),
        (3, 1, (*selection, *rec)),
    )


def test_actual_planning_reader_chain_reaches_descriptor_fragment() -> None:
    fragment = build_planning_descriptor_fragment(sources())
    verify_planning_descriptor_fragment(fragment, expected())
    assert len(fragment.rows) == 5
    assert {r.database_domain for r in fragment.rows} == {"tenant_main"}
    assert {r.site.qualname for r in fragment.rows} == {
        "BrokerAuthorityReader._execute_planning_read"
    }
    assert fragment.wrapper_site.qualname == "R7PlanningRuntime._verified_publications"
    assert fragment.other_mounts == ()
    assert fragment.residual_obligations


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
    ],
)
def test_ledger_startup_requires_exact_direct_sync_frames(old: str, new: str) -> None:
    items = sources()
    build_planning_descriptor_fragment(items)
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(items, RUNTIME, old, new))


@pytest.mark.parametrize("wrapper", ["if False:", "def later():"])
def test_ledger_constructor_cannot_be_conditional_or_deferred(wrapper: str) -> None:
    items = sources()
    build_planning_descriptor_fragment(items)
    text = dict(items)[RUNTIME]
    tree = ast.parse(text)
    (declaration,) = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and [ast.unparse(t) for t in node.targets] == ["read_ledger"]
    ]
    lines = text.splitlines(keepends=True)
    old = "".join(lines[declaration.lineno - 1 : declaration.end_lineno])
    new = (
        "            "
        + wrapper
        + "\n"
        + "".join("    " + line for line in old.splitlines(keepends=True))
    )
    with pytest.raises(UnknownPlanningChain, match="canonical ledger constructor missing"):
        build_planning_descriptor_fragment(mutate(items, RUNTIME, old, new))


def test_preparation_paths_join_all_metadata_and_statement_sinks_to_private_factories() -> None:
    fragment = build_planning_descriptor_fragment(sources())
    chains = fragment.preparation_chains
    assert len(chains) == 17
    assert {(row.sink.qualname, row.sink.ordinal, row.role) for row in chains} == {
        ("_configure", 5, "FOREIGN_KEYS_PROFILE"),
        ("_configure", 8, "FOREIGN_KEYS_OBSERVATION"),
        ("_physical_schema", 7, "PHYSICAL_CATALOG"),
        ("_physical_schema", 16, "PHYSICAL_COLUMNS"),
        ("_physical_schema", 21, "PHYSICAL_INDEXES"),
        ("_physical_schema", 26, "PHYSICAL_INDEX_COLUMNS"),
        ("_schema_steps", 19, "SCHEMA_UNIT"),
        ("observe_statement", 17, "EXPLAIN_FIRST"),
        ("observe_statement", 25, "EXPLAIN_BINDING_RETRY"),
    }
    assert {row.entrypoint for row in chains} == {
        "observe_statement",
        "observe_schema_script",
        "observe_registered_schema",
    }
    assert {row.factory.qualname for row in chains} == {
        "observe_statement",
        "observe_schema_script",
        "observe_registered_schema",
    }
    for row in chains:
        assert row.factory in fragment.corpus.nodes and row.sink in fragment.corpus.nodes
        assert row.domain == "private_memory"
        if row.forward is not None:
            assert row.forward in fragment.corpus.nodes
        assert row.source_digests == tuple(
            pair for pair in fragment.corpus.source_digests if pair[0] == PREPARATION
        )


def test_selected_reader_scanners_retain_five_exact_paths_and_finite_expressions() -> None:
    fragment = build_planning_descriptor_fragment(sources())
    chains = fragment.scanner_chains
    assert len(chains) == 5
    assert {(row.role, len(row.forwards)) for row in chains} == {
        ("TRANSACTION_BEGIN", 0),
        ("SNAPSHOT_FRONTIER_READ", 0),
        ("SCHEMA_CATALOG_METADATA", 1),
        ("SCHEMA_COLUMN_METADATA", 1),
        ("AUTHORITY_COMMITMENT_SCAN", 2),
    }
    assert len({row.sink for row in chains}) == 5
    assert len({row.factory for row in chains}) == 1
    assert len({site for row in chains for site in row.forwards}) == 3
    expressions = {row.site: row for row in fragment.expressions.resolved}
    for row in chains:
        assert row.factory in fragment.corpus.nodes and row.sink in fragment.corpus.nodes
        assert all(site in fragment.corpus.nodes for site in row.forwards)
        assert row.domain == "tenant_main"
        assert row.expression == expressions[row.sink]


@pytest.mark.parametrize(
    "old,new",
    [
        (
            "observed_schema = _schema_members(connection)",
            "observed_schema = _schema_members(other_connection)",
        ),
        (
            "            commitment = _authority_commitment(connection)",
            "            commitment = _authority_commitment(other_connection)",
        ),
        (
            "return hashlib.sha256(_authority_snapshot_bytes(connection)).hexdigest()",
            "return hashlib.sha256(_authority_snapshot_bytes(other_connection)).hexdigest()",
        ),
        (
            "def _authority_commitment(connection: sqlite3.Connection)",
            "def _authority_commitment(connection: sqlite3.Connection, extra=None)",
        ),
        (
            '    rows = connection.execute(\n        "SELECT name FROM main.sqlite_master',
            "    connection = foreign\n    rows = connection.execute(\n"
            '        "SELECT name FROM main.sqlite_master',
        ),
        (
            '    rows = connection.execute(\n        "SELECT name FROM main.sqlite_master',
            '    def later():\n        return connection.execute("SELECT 1")\n'
            '    rows = connection.execute(\n        "SELECT name FROM main.sqlite_master',
        ),
        (
            "    materialized: list[dict[str, object]] = []",
            "    leaked = connection\n    materialized: list[dict[str, object]] = []",
        ),
        (
            "    materialized: list[dict[str, object]] = []",
            '    connection.execute("SELECT 1")\n    materialized: list[dict[str, object]] = []',
        ),
        (
            '            connection.execute("BEGIN")',
            '            leaked = connection.execute("BEGIN").connection',
        ),
        (
            "    return tuple(\n        (\n            member.table,",
            "    return (\n        (\n            member.table,",
        ),
        (
            "    return tuple(\n        (\n            member.table,",
            "    return unknown(\n        (\n            member.table,",
        ),
        (
            '    rows = connection.execute(\n        "SELECT name FROM main.sqlite_master',
            "    tuple = unknown\n    rows = connection.execute(\n"
            '        "SELECT name FROM main.sqlite_master',
        ),
        (
            '    rows = connection.execute(\n        "SELECT name FROM main.sqlite_master',
            "    if False:\n        yield None\n    rows = connection.execute(\n"
            '        "SELECT name FROM main.sqlite_master',
        ),
        ("import sqlite3", "import sqlite3\ntuple = unknown"),
        ("import sqlite3", 'import sqlite3\nglobals()["tuple"] = unknown'),
        (
            '    rows = connection.execute(\n        "SELECT name FROM main.sqlite_master',
            '    return connection.execute("SELECT 1")\n'
            '    rows = connection.execute(\n        "SELECT name FROM main.sqlite_master',
        ),
        ('            connection.execute("BEGIN")', '            connection.execute("SELECT 1")'),
        (
            '"SELECT head FROM tenant_heads WHERE tenant_id = ?"',
            '"SELECT frontier FROM deletion_fences WHERE tenant_id = ?"',
        ),
        (
            "f'PRAGMA main.table_info(\"{member.table}\")'",
            "f'SELECT * FROM main.\"{member.table}\"'",
        ),
        (
            "f'SELECT {columns} FROM main.\"{member.table}\" ORDER BY {order}'",
            "f'SELECT {columns} FROM temp.\"{member.table}\" ORDER BY {order}'",
        ),
    ],
)
def test_scanner_connection_forwarding_and_eager_consumption_mutants_refuse(
    old: str, new: str
) -> None:
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(sources(), READER, old, new))


@pytest.mark.parametrize(
    "scope,old,new",
    [
        ("observe_statement", '":memory:"', '"tenant.sqlite"'),
        ("observe_schema_script", '":memory:"', '"tenant.sqlite"'),
        ("observe_registered_schema", '":memory:"', '"tenant.sqlite"'),
        (
            "_configure",
            'connection.execute("PRAGMA foreign_keys").fetchall()',
            'connection.execute("PRAGMA user_version").fetchall()',
        ),
        (
            "_physical_schema",
            "connection.execute(f'PRAGMA main.index_xinfo(\"{name}\")').fetchall()",
            "connection.execute(f'PRAGMA main.table_info(\"{name}\")').fetchall()",
        ),
        (
            "observe_statement",
            "        _configure(connection, foreign_keys)",
            "        leak = [connection]\n        _configure(connection, foreign_keys)",
        ),
        (
            "observe_statement",
            "        _configure(connection, foreign_keys)",
            "        return connection\n        _configure(connection, foreign_keys)",
        ),
        (
            "observe_statement",
            "        _configure(connection, foreign_keys)",
            '        def later():\n            return connection.execute("SELECT 1")\n'
            "        _configure(connection, foreign_keys)",
        ),
        (
            "observe_statement",
            "        _configure(connection, foreign_keys)",
            "        def connection(): pass\n        _configure(connection, foreign_keys)",
        ),
        (
            "observe_statement",
            "        _configure(connection, foreign_keys)",
            "        class connection: pass\n        _configure(connection, foreign_keys)",
        ),
        (
            "observe_statement",
            "        _configure(connection, foreign_keys)",
            "        from foreign import connection\n        _configure(connection, foreign_keys)",
        ),
        (
            "observe_statement",
            "        _configure(connection, foreign_keys)",
            "        try: pass\n        except Exception as connection: pass\n"
            "        _configure(connection, foreign_keys)",
        ),
        (
            "observe_statement",
            "        _configure(connection, foreign_keys)",
            "        match None:\n            case connection: pass\n"
            "        _configure(connection, foreign_keys)",
        ),
        (
            "_schema_steps",
            "connection: Connection,\n    script: str,",
            "script: str,\n    connection: Connection,",
        ),
        (
            "_configure",
            '"PRAGMA foreign_keys = " + ("ON" if foreign_keys else "OFF")',
            '"PRAGMA user_version"',
        ),
        (
            "observe_statement",
            'connection.execute("EXPLAIN " + sql).fetchall()',
            'leaked = connection.execute("EXPLAIN " + sql).connection',
        ),
        (
            "_schema_steps",
            "connection.execute(sql).fetchall()",
            "(escaped := connection.execute(sql)).fetchall()",
        ),
    ],
)
def test_private_preparation_source_substitutions_refuse(scope: str, old: str, new: str) -> None:
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate_preparation(sources(), scope, old, new))


@pytest.mark.parametrize(
    "tail",
    [
        "\nhelper_alias = _schema_steps\n",
        "\nmetadata_alias = _physical_schema\n",
        "\npolicy_alias = _metadata_authorizer\n",
        "\ndef other(tenant_connection):\n"
        '    return _schema_steps(tenant_connection, "", (), False, "")\n',
        "\nconnect_alias = sqlite3.connect\n",
        "\nfrom foreign import closing\n",
        "\nobserve_statement.__code__ = foreign.__code__\n",
        "\nobserve_schema_script.__defaults__ = (foreign,)\n",
    ],
)
def test_preparation_helper_and_factory_exposures_refuse(tail: str) -> None:
    items = sources()
    original = dict(items)[PREPARATION]
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(items, PREPARATION, original, original + tail))


@pytest.mark.parametrize(
    "source",
    [
        "from chiplog.verification.sqlite_preparation import _schema_steps\n",
        "import chiplog.verification.sqlite_preparation as prep\n"
        'def other(connection):\n    return prep._schema_steps(connection, "", (), False, "")\n',
        "from chiplog.verification.sqlite_preparation import observe_statement as observe\n"
        "def other(connection):\n"
        '    return observe.__globals__["_schema_steps"](connection, "", (), False, "")\n',
    ],
)
def test_supplied_other_module_cannot_supply_tenant_preparation_connection(source: str) -> None:
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(
            (*sources(), ("src/chiplog/composition/other_preparation.py", source))
        )


@pytest.mark.parametrize(
    "old,new",
    [
        (
            'self._reader = BrokerAuthorityReader(database, "planning-database", read_ledger)',
            'self._reader = ForeignReader(database, "planning-database", read_ledger)',
        ),
        (
            "        self._appender = appender",
            "        self._reader = foreign\n        self._appender = appender",
        ),
        (
            "        session = self._supervisor.runtime().session(owner_id)",
            '        setattr(self, "_reader", foreign)\n'
            "        session = self._supervisor.runtime().session(owner_id)",
        ),
    ],
)
def test_reader_field_rebind_or_foreign_constructor_refuses(old: str, new: str) -> None:
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(sources(), RUNTIME, old, new))


def test_reader_database_substitution_refuses() -> None:
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(
            mutate(
                sources(),
                READER,
                "        self._database = database",
                "        self._database = other_database",
            )
        )


def test_wrapper_added_downstream_read_requires_descriptor_edge() -> None:
    changed = mutate_reader_leaf(
        sources(),
        '"commit_sequence, record_ids FROM publications WHERE tenant_id = ? "',
        '"commit_sequence, record_ids FROM publications WHERE tenant_id = ? "\n'
        '            "AND EXISTS (SELECT 1 FROM deletion_fences WHERE frontier >= 0) "',
    )
    sql = next(
        sql
        for name, ordinal, _, sql in EXPECTED_SQL
        if name == "BrokerAuthorityReader._execute_planning_read" and ordinal == 44
    )
    sql = sql.replace(
        "ORDER BY commit_sequence",
        "AND EXISTS (SELECT 1 FROM deletion_fences WHERE frontier >= 0) ORDER BY commit_sequence",
    )
    assert ("main", "deletion_fences", "frontier") in _prepare_reads(
        schema_contract("tenant_main")[0], sql
    )
    with pytest.raises(UnknownPlanningChain, match="independent contract"):
        build_planning_descriptor_fragment(changed)


def test_descriptor_missing_or_ghost_leaf_refuses() -> None:
    fragment = build_planning_descriptor_fragment(sources())
    for rows in (expected()[:-1], (*expected(), (99, 0, ()))):
        with pytest.raises(UnknownPlanningChain, match="descriptor"):
            verify_planning_descriptor_fragment(fragment, rows)


@pytest.mark.parametrize(
    "old,new",
    [
        ("runtime_type=R7PlanningRuntime,", "runtime_type=OtherRuntime,"),
        ("manifest=R7_PRODUCTION_MANIFEST,", "manifest=OTHER_MANIFEST,"),
        (
            "        manifest=R7_PRODUCTION_MANIFEST,",
            "        manifest=R7_PRODUCTION_MANIFEST, preflight=external_reader,",
        ),
    ],
)
def test_nondefault_runtime_or_manifest_is_not_defaulted(old: str, new: str) -> None:
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(sources(), RUNTIME, old, new))


@pytest.mark.parametrize(
    "extra",
    [
        ("chiplog/platform/authority_reads.py", ""),
        ("src/../src/chiplog/platform/authority_reads.py", ""),
        (READER, ""),
    ],
)
def test_corpus_assembly_path_join_requires_exact_normalization(extra: tuple[str, str]) -> None:
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment((*sources(), extra))


def test_partial_chain_cannot_admit_runtime() -> None:
    fragment = build_planning_descriptor_fragment(sources())
    with pytest.raises(UnknownPlanningChain, match="partial"):
        fragment.require_closed()
    with pytest.raises(ValueError):
        fragment.corpus.require_closed()


@pytest.mark.parametrize(
    "old,new",
    [
        pytest.param(
            (
                'f"file:{self._database}?mode=ro",\n                uri=True,\n          '
                "      isolation_level=None,\n                cached_statements=0,"
            ),
            ('":memory:", cached_statements=0'),
            id=(
                'f"file:{self._database}?mode=ro", uri=True,\n                isolation_'
                'level=None, cached_statements=0-":memory:", cached_statements=0'
            ),
        ),
        (
            "self._execute_planning_read(connection, operation, snapshot)",
            "self._execute_planning_read(other_connection, operation, snapshot)",
        ),
        ("        publication_sql = (", "        leak(connection)\n        publication_sql = ("),
    ],
)
def test_connection_origin_forwarding_and_escape_refuse(old: str, new: str) -> None:
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(sources(), READER, old, new))


@pytest.mark.parametrize(
    "path,old,new",
    [
        (
            READER,
            "        state = self._ledger.current_state(operation.tenant_id)",
            (
                "        self = foreign\n"
                "        state = self._ledger.current_state(operation.tenant_id)"
            ),
        ),
        (
            READER,
            "    def execute(self, operation: ReadOperation)",
            "    execute = foreign\n\n    def execute(self, operation: ReadOperation)",
        ),
        (
            READER,
            "        self._ledger = ledger",
            "        self._execute_planning_read = foreign\n        self._ledger = ledger",
        ),
        (
            RUNTIME,
            "    source_root = _SOURCE_ROOT",
            "    runtime_type = OtherRuntime\n    source_root = _SOURCE_ROOT",
        ),
        (READER, "import sqlite3", "import sqlite3\nfrom foreign import sqlite3"),
    ],
)
def test_local_receiver_method_or_import_rebinding_refuses(path: str, old: str, new: str) -> None:
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(sources(), path, old, new))


def test_other_mounts_remain_visible_and_residual() -> None:
    additional = (
        "src/chiplog/composition/additional_mount.py",
        """
from chiplog.composition.r7_planning import _open_runtime, R7PlanningRuntime
from chiplog.architecture.r7_runtime import R7_PRODUCTION_MANIFEST
async def open_additional(database, tenant_id, operator_secret):
    async with _open_runtime(database, tenant_id=tenant_id, operator_secret=operator_secret,
                             runtime_type=R7PlanningRuntime,
                             manifest=R7_PRODUCTION_MANIFEST) as runtime:
        yield runtime
""",
    )
    fragment = build_planning_descriptor_fragment((*sources(), additional))
    assert len(fragment.other_mounts) == 1
    assert fragment.other_mounts[0].caller == "open_additional"
    with pytest.raises(UnknownPlanningChain, match="partial"):
        fragment.require_closed()


@pytest.mark.parametrize(
    "old,new",
    [
        (
            "        verify_invalidator_registry()",
            "        self.execute = lambda operation: None\n        verify_invalidator_registry()",
        ),
        (
            "        verify_invalidator_registry()",
            "        self._execute_planning_read = lambda *args: None\n"
            "        verify_invalidator_registry()",
        ),
        ("import sqlite3", "import sqlite3\nimport math as sqlite3"),
        ("import sqlite3", "import sqlite3\ndef sqlite3(*args): pass"),
        ("import sqlite3", "import sqlite3\nsqlite3.connect = foreign_connect"),
        ("import sqlite3", "import sqlite3\nconnect_alias = sqlite3.connect"),
    ],
)
def test_actual_reader_lookup_mutants_refuse(old: str, new: str) -> None:
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(sources(), READER, old, new))


def test_wrapper_property_decorator_refuses() -> None:
    changed = mutate(
        sources(),
        RUNTIME,
        "    def _verified_publications(",
        "    @property\n    def _verified_publications(",
    )
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(changed)


@pytest.mark.parametrize(
    "capture",
    [
        "alias, = (self,)",
        "alias = [self][0]",
        'alias = {"receiver": self}["receiver"]',
    ],
)
def test_container_receiver_alias_cannot_substitute_database(capture: str) -> None:
    changed = mutate(
        sources(),
        READER,
        "        self._database = database",
        "        self._database = database\n"
        f"        {capture}\n        alias._database = foreign_database",
    )
    with pytest.raises(UnknownPlanningChain, match="receiver"):
        build_planning_descriptor_fragment(changed)


@pytest.mark.parametrize(
    "rebind",
    [
        "class connection:\n            pass",
        "def connection():\n            pass",
        "from foreign import connection",
    ],
)
def test_non_name_connection_bindings_refuse(rebind: str) -> None:
    changed = mutate(
        sources(), READER, "        scope_sql = (", f"        {rebind}\n        scope_sql = ("
    )
    with pytest.raises(UnknownPlanningChain, match="connection"):
        build_planning_descriptor_fragment(changed)


@pytest.mark.parametrize(
    "rebind",
    [
        "def database():\n            pass",
        "class database:\n            pass",
        "from foreign import database",
    ],
)
def test_non_name_constructor_database_bindings_refuse(rebind: str) -> None:
    changed = mutate(
        sources(),
        READER,
        "        self._database = database",
        f"        {rebind}\n        self._database = database",
    )
    with pytest.raises(UnknownPlanningChain, match="database"):
        build_planning_descriptor_fragment(changed)


@pytest.mark.parametrize(
    "old,new",
    [
        (
            "    source_root = _SOURCE_ROOT",
            "    def runtime_type(*args): pass\n    source_root = _SOURCE_ROOT",
        ),
        (
            "    source_root = _SOURCE_ROOT",
            "    from foreign import database\n    source_root = _SOURCE_ROOT",
        ),
        (
            "    preflight: Callable[[AuthorityGate], object] | None = None,",
            "    preflight: Callable[[AuthorityGate], object] | None = foreign_callback,",
        ),
        (
            "    async with _open_runtime(\n        database,",
            "    database = foreign_database\n    async with _open_runtime(\n        database,",
        ),
        (
            "@asynccontextmanager\nasync def open_r7_runtime(",
            "@foreign_decorator\nasync def open_r7_runtime(",
        ),
    ],
)
def test_base_mount_binding_and_implicit_callback_changes_refuse(old: str, new: str) -> None:
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(sources(), RUNTIME, old, new))


def test_staticmethod_builtin_shadow_refuses() -> None:
    changed = mutate(
        sources(), READER, "import sqlite3", "import sqlite3\nstaticmethod = lambda fn: foreign"
    )
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(changed)


@pytest.mark.parametrize(
    "path,old,new",
    [
        (
            RUNTIME,
            "        database: Path,\n        read_ledger: BrokerReadLedger,",
            "        read_ledger: BrokerReadLedger,\n        database: Path,",
        ),
        (
            READER,
            "self, database: Path, database_instance_id: str, ledger: BrokerReadLedger",
            "self, ledger: BrokerReadLedger, database_instance_id: str, database: Path",
        ),
        (
            READER,
            "        connection: sqlite3.Connection,\n        operation: ReadOperation,",
            "        operation: ReadOperation,\n        connection: sqlite3.Connection,",
        ),
    ],
)
def test_positional_domain_parameter_reorder_refuses(path: str, old: str, new: str) -> None:
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(sources(), path, old, new))


def test_selected_read_ledger_has_five_factories_eleven_sinks_and_same_expressions() -> None:
    fragment = build_planning_descriptor_fragment(sources())
    chains = fragment.ledger_chains
    assert len(chains) == len({row.sink for row in chains}) == 11
    assert len({row.factory for row in chains}) == 5
    from collections import Counter

    assert Counter(row.role for row in chains) == {
        "SCHEMA_SCRIPT": 1,
        "TRANSACTION_BEGIN": 2,
        "AUTHORITY_STATE_READ": 2,
        "RELEASE_STATE_READ": 2,
        "RELEASE_CREATE": 1,
        "RELEASE_UPDATE": 3,
    }
    assert {row.factory.qualname for row in chains} == {
        "BrokerReadLedger." + name
        for name in ("__init__", "current_state_or_none", "begin", "release", "dequeue")
    }
    for row in chains:
        assert row.origin.module == RUNTIME and row.origin.qualname == "_open_runtime"
        assert row.origin.expression == (
            "BrokerReadLedger(database.with_suffix(database.suffix + "
            "'.reads.sqlite3'), authority_gate=authority_gate)"
        )
        if row.factory.qualname.endswith(".__init__"):
            assert row.forwards == ()
        else:
            assert tuple(site.qualname for site in row.forwards[:2]) == (
                "_open_runtime",
                "R7PlanningRuntime.__init__",
            )
            assert row.forwards[2].qualname == "BrokerAuthorityReader.execute"
            assert len(row.forwards) == (
                4 if row.factory.qualname.endswith(".current_state_or_none") else 3
            )
    assert {row.domain for row in chains} == {"broker_read_ledger"}
    expressions = {row.site: row for row in fragment.expressions.resolved}
    for row in chains:
        assert row.expression == expressions[row.sink]
        assert row.factory in fragment.corpus.nodes
        assert all(site in fragment.corpus.nodes for site in row.forwards)
        assert row.origin in fragment.corpus.nodes
    assert chains[0].expression.alternatives[0].sql.count("CREATE TABLE") == 2
    assert any("lifetime" in item for item in fragment.residual_obligations)


@pytest.mark.parametrize(
    "path,old,new",
    [
        (RUNTIME, 'database.with_suffix(database.suffix + ".reads.sqlite3")', "database"),
        (RUNTIME, "        self._read_ledger = read_ledger", "        self._read_ledger = foreign"),
        (READER, "        self._ledger = ledger", "        self._ledger = foreign"),
        (READER, "self._ledger.begin(operation, state)", "self._ledger.begin(operation, foreign)"),
        (
            LEDGER,
            "path.resolve(strict=False) if authority_gate is not None else path",
            "path.resolve(strict=False) if authority_gate is None else path",
        ),
        (LEDGER, "        self._path = path", "        self._path = foreign"),
        (LEDGER, "sqlite3.connect(path)", "sqlite3.connect(other_path)"),
        (LEDGER, "    def begin(self, operation:", "    @property\n    def begin(self, operation:"),
        (
            LEDGER,
            "    def begin(self, operation:",
            "    begin = foreign\n    def begin(self, operation:",
        ),
        (
            LEDGER,
            "        result_digest = hashlib.sha256(result_bytes).hexdigest()",
            (
                "        leaked = self\n        result_digest = "
                "hashlib.sha256(result_bytes).hexdigest()"
            ),
        ),
        (
            LEDGER,
            "            connection.executescript(_SCHEMA)",
            "            leaked = connection\n            connection.executescript(_SCHEMA)",
        ),
        (
            LEDGER,
            "            connection.executescript(_SCHEMA)",
            "            leaked = connection.executescript(_SCHEMA)",
        ),
        (
            LEDGER,
            "            connection.executescript(_SCHEMA)",
            (
                "            connection.executescript(_SCHEMA)\n            "
                'connection.execute("SELECT 1")'
            ),
        ),
        (LEDGER, "    @contextlib.contextmanager", "    @foreign.contextmanager"),
        (LEDGER, "import contextlib", "import contextlib\nfrom foreign import contextlib"),
        (LEDGER, "import sqlite3", "import sqlite3\nsqlite3.connect = foreign"),
    ],
)
def test_selected_read_ledger_origin_and_escape_mutations_refuse(
    path: str, old: str, new: str
) -> None:
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(sources(), path, old, new))


@pytest.mark.parametrize(
    "path,old,new",
    [
        (RUNTIME, "                read_ledger,", "                other_ledger,"),
        (
            RUNTIME,
            'BrokerAuthorityReader(database, "planning-database", read_ledger)',
            'BrokerAuthorityReader(database, "planning-database", other_ledger)',
        ),
        (
            READER,
            "class BrokerAuthorityReader:",
            "class BrokerAuthorityReader:\n    _ledger = property(lambda self: foreign)",
        ),
        (
            RUNTIME,
            "class R7PlanningRuntime:",
            "class R7PlanningRuntime:\n    _read_ledger = property(lambda self: foreign)",
        ),
        (
            LEDGER,
            "state = self.current_state_or_none(tenant_id)",
            "state = self.other_state(tenant_id)",
        ),
        (
            LEDGER,
            "    def current_state(self, tenant_id:",
            "    @property\n    def current_state(self, tenant_id:",
        ),
        (
            LEDGER,
            "        result_digest = hashlib.sha256(result_bytes).hexdigest()",
            (
                "        self = foreign\n        result_digest = "
                "hashlib.sha256(result_bytes).hexdigest()"
            ),
        ),
        (
            LEDGER,
            (
                "        path = path.resolve(strict=False) if authority_gate is "
                "not None else path\n        self._path = path"
            ),
            (
                "        self._path = path\n        path = "
                "path.resolve(strict=False) if authority_gate is not None else "
                "path"
            ),
        ),
        (
            LEDGER,
            "            connection.executescript(_SCHEMA)",
            "            delayed = (connection.executescript(_SCHEMA) for _ in [0])",
        ),
        (
            LEDGER,
            "            connection.executescript(_SCHEMA)",
            "            eager = [connection.executescript(_SCHEMA) for _ in [0]]",
        ),
        (
            LEDGER,
            "            connection.executescript(_SCHEMA)",
            "            connection.executescript(_SCHEMA).connection",
        ),
        (
            LEDGER,
            '"SELECT state_fingerprint FROM authority_read_state WHERE tenant_id = ?"',
            '"SELECT state_fingerprint FROM foreign_state WHERE tenant_id = ?"',
        ),
    ],
)
def test_selected_ledger_forwarding_and_nested_capture_refuse(
    path: str, old: str, new: str
) -> None:
    # These input mutations exercise the provenance proof, without executing their SQL.
    items = mutate(sources(), path, old, new)
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(items)


@pytest.mark.parametrize(
    "hook", ["__new__", "__getattribute__", "__getattr__", "__setattr__", "__delattr__"]
)
def test_selected_ledger_object_model_hooks_refuse(hook: str) -> None:
    changed = mutate(
        sources(),
        LEDGER,
        "class BrokerReadLedger:",
        "class BrokerReadLedger:\n"
        f"    def {hook}(self, *args, **kwargs):\n        return ForeignLedger()\n",
    )
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(changed)


@pytest.mark.parametrize(
    "path,klass", [(RUNTIME, "R7PlanningRuntime"), (READER, "BrokerAuthorityReader")]
)
@pytest.mark.parametrize("hook", ["__new__", "__getattribute__", "__setattr__"])
def test_selected_handoff_object_model_hooks_refuse(path: str, klass: str, hook: str) -> None:
    changed = mutate(
        sources(),
        path,
        f"class {klass}:",
        f"class {klass}:\n"
        f"    def {hook}(self, *args, **kwargs):\n        return receiver_members[args[0]]\n",
    )
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(changed)


def test_selected_ledger_local_constructor_replacement_refuses() -> None:
    items = sources()
    text = dict(items)[LEDGER]
    changed = mutate(items, LEDGER, text, text + "\nBrokerReadLedger.__new__ = foreign\n")
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(changed)


@pytest.mark.parametrize("comprehension", ["({sql} for _ in [0])", "[{sql} for _ in [0]]"])
def test_selected_ledger_select_connection_cannot_enter_comprehension_frame(
    comprehension: str,
) -> None:
    text = dict(sources())[LEDGER]
    tree = ast.parse(text)
    klass = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "BrokerReadLedger"
    )
    method = next(
        n
        for n in klass.body
        if isinstance(n, ast.FunctionDef) and n.name == "current_state_or_none"
    )
    row = next(
        n
        for n in ast.walk(method)
        if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == "row"
    )
    sql = cast(str, ast.get_source_segment(text, row.value))
    with pytest.raises(UnknownPlanningChain, match="nested frame"):
        build_planning_descriptor_fragment(
            mutate(sources(), LEDGER, sql, comprehension.format(sql=sql))
        )


@pytest.mark.parametrize(
    "path,tail",
    [
        (RUNTIME, '\nglobals()["BrokerReadLedger"] = foreign\n'),
        (RUNTIME, '\nsetattr(BrokerReadLedger, "__new__", foreign)\n'),
        (RUNTIME, "\nBrokerReadLedger.__new__ = foreign\n"),
        (RUNTIME, "\nLedgerAlias = BrokerReadLedger\nLedgerAlias.__new__ = foreign\n"),
        (READER, '\nglobals()["BrokerAuthorityReader"] = foreign\n'),
        (LEDGER, "\nLedgerAlias = BrokerReadLedger\nLedgerAlias.__new__ = foreign\n"),
    ],
)
def test_selected_producer_module_reflection_and_class_aliases_refuse(path: str, tail: str) -> None:
    items = sources()
    text = dict(items)[path]
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(items, path, text, text + tail))


def test_selected_ledger_async_allocation_hook_refuses() -> None:
    changed = mutate(
        sources(),
        LEDGER,
        "class BrokerReadLedger:",
        "class BrokerReadLedger:\n"
        "    async def __new__(cls, *args, **kwargs):\n        return ForeignLedger()\n",
    )
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(changed)


@pytest.mark.parametrize("path", [RUNTIME, READER, LEDGER])
def test_selected_producer_imported_class_alias_cannot_replace_allocation(path: str) -> None:
    items = sources()
    text = dict(items)[path]
    tail = (
        "\nfrom chiplog.platform.read_ledger import BrokerReadLedger as LedgerAlias\n"
        "LedgerAlias.__new__ = foreign\n"
    )
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(items, path, text, text + tail))


def test_runtime_preparation_retains_each_actual_sql_and_parameter_forwarding() -> None:
    fragment = build_planning_descriptor_fragment(sources())
    chains = fragment.runtime_preparation_chains
    assert len(chains) == 3
    assert tuple((row.sql_local, row.parameters_local) for row in chains) == (
        ("uniqueness_sql", "uniqueness_parameters"),
        ("publication_sql", "publication_parameters"),
        ("record_sql", "record_parameters"),
    )
    assert {row.database_domain for row in chains} == {"tenant_main"}
    assert {row.sink.qualname for row in chains} == {"_prepared_planning_statement"}
    assert {row.sink.expression for row in chains} == {
        "connection.execute('EXPLAIN ' + sql, parameters)"
    }
    assert {row.caller for row in chains} == {row.site for row in fragment.rows}
    with pytest.raises(UnknownPlanningChain):
        fragment.require_closed()


@pytest.mark.parametrize(
    "old,new",
    [
        pytest.param(
            ("isolation_level=None,\n                cached_statements=0,"),
            ("isolation_level=None,\n                cached_statements=128,"),
            id=(
                "isolation_level=None, cached_statements=0-isolation_level=None, cached"
                "_statements=128"
            ),
        ),
        pytest.param(
            (
                "connection,\n            publication_sql,\n            publication_param"
                "eters,\n            publication_reads,"
            ),
            (
                "connection,\n            record_sql,\n            publication_parameters"
                ",\n            publication_reads,"
            ),
            id=(
                "connection, publication_sql, publication_parameters, publication_reads"
                ",-connection, record_sql, publication_parameters, publication_reads,"
            ),
        ),
        (
            "canonical_statement=canonical_statements[1]",
            "canonical_statement=canonical_statements[2]",
        ),
        (
            "canonical_statements = _canonical_planning_statements(operation)",
            "canonical_statements = _canonical_planning_statements(foreign_operation)",
        ),
        (
            "connection.execute(publication_sql, publication_parameters)",
            "connection.execute(publication_sql, foreign_parameters)",
        ),
        (
            "connection.execute(publication_sql, publication_parameters).fetchall()",
            "connection.execute(publication_sql, publication_parameters)",
        ),
        (
            'connection.execute("EXPLAIN " + sql, parameters)',
            'connection.execute("EXPLAIN " + sql, foreign_parameters)',
        ),
        ("connection.set_authorizer(None)", "connection.set_authorizer(foreign)"),
        ("connection.set_authorizer(authorize)", "foreign(connection)"),
        (
            "as publication_observed:\n            publications =",
            "as publication_observed:\n            leak(connection)\n            publications =",
        ),
    ],
)
def test_runtime_preparation_rejects_foreign_sql_parameters_policy_or_cursor_escape(
    old: str, new: str
) -> None:
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(sources(), READER, old, new))


@pytest.mark.parametrize("position", ["before", "after", "rollback"])
def test_planning_fragment_refuses_raw_connection_operations_outside_guards(position: str) -> None:
    items = sources()
    if position == "before":
        old = "        canonical_statements = _canonical_planning_statements(operation)"
        new = '        connection.execute("SELECT 17").fetchall()\n' + old
    else:
        old = "        observed = uniqueness_observed | publication_observed | record_observed"
        action = (
            "connection.rollback()"
            if position == "rollback"
            else 'connection.execute("SELECT 17").fetchall()'
        )
        new = "        " + action + "\n" + old
    with pytest.raises(UnknownPlanningChain, match="unguarded raw connection"):
        build_planning_descriptor_fragment(mutate(items, READER, old, new))
