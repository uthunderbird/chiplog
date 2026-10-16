"""The R7 base planning fragment joins proofs but never grants admission."""
import ast
from pathlib import Path

import pytest

from chiplog.verification.planning_descriptor_chain import (
    UnknownPlanningChain,
    build_planning_descriptor_fragment,
    verify_planning_descriptor_fragment,
)

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = 'src/chiplog/composition/r7_planning.py'
READER = 'src/chiplog/platform/authority_reads.py'
PREPARATION = 'src/chiplog/verification/sqlite_preparation.py'
PATHS = (RUNTIME, READER, 'src/chiplog/platform/_sqlite.py',
         'src/chiplog/platform/r7_leaves.py', PREPARATION,
         'src/chiplog/architecture/r7_storage_surface.py',
         'src/chiplog/composition/r12.py')


def sources():
    return tuple((p, (ROOT / p).read_text()) for p in PATHS)


def mutate(items, path, old, new):
    assert dict(items)[path].count(old) == 1
    return tuple((p, text.replace(old, new) if p == path else text) for p, text in items)


def mutate_preparation(items, scope, old, new):
    text = dict(items)[PREPARATION]
    method, = [node for node in ast.parse(text).body
               if isinstance(node, ast.FunctionDef) and node.name == scope]
    segment = ast.get_source_segment(text, method)
    assert segment.count(old) == 1
    return mutate(items, PREPARATION, segment, segment.replace(old, new))


def expected():
    pub = tuple(('main', 'publications', c) for c in (
        'commit_sequence', 'idempotency_key', 'operation_kind', 'record_ids',
        'request_fingerprint', 'tenant_id'))
    selection = tuple(('main', 'publications', c) for c in (
        'commit_sequence', 'idempotency_key', 'operation_kind', 'tenant_id'))
    rec = tuple(('main', 'records', c) for c in (
        'canonical_bytes', 'commit_sequence', 'owner', 'record_id', 'schema_id', 'tenant_id'))
    return ((1, 0, (('main', 'publications', 'commit_sequence'),
                    ('main', 'publications', 'tenant_id'))),
            (2, 0, pub), (2, 1, pub), (3, 0, (*selection, *rec)),
            (3, 1, (*selection, *rec)))


def test_actual_planning_reader_chain_reaches_descriptor_fragment():
    fragment = build_planning_descriptor_fragment(sources())
    verify_planning_descriptor_fragment(fragment, expected())
    assert len(fragment.rows) == 5
    assert {r.database_domain for r in fragment.rows} == {'tenant_main'}
    assert {r.site.qualname for r in fragment.rows} == {
        'BrokerAuthorityReader._execute_planning_read'}
    assert fragment.wrapper_site.qualname == 'R7PlanningRuntime._verified_publications'
    assert fragment.other_mounts == ()
    assert fragment.residual_obligations


def test_preparation_paths_join_all_four_sinks_to_exact_private_factories():
    fragment = build_planning_descriptor_fragment(sources())
    chains = fragment.preparation_chains
    assert len(chains) == 7
    assert {(row.sink.qualname, row.sink.ordinal, row.role) for row in chains} == {
        ('_configure', 5, 'FOREIGN_KEYS_PROFILE'),
        ('_schema_steps', 18, 'SCHEMA_UNIT'),
        ('observe_statement', 17, 'EXPLAIN_FIRST'),
        ('observe_statement', 25, 'EXPLAIN_BINDING_RETRY'),
    }
    assert {row.entrypoint for row in chains} == {'observe_statement', 'observe_schema_script'}
    assert {row.factory.qualname for row in chains} == {
        'observe_statement', 'observe_schema_script'}
    for row in chains:
        assert row.factory in fragment.corpus.nodes and row.sink in fragment.corpus.nodes
        assert row.domain == 'private_memory'
        if row.forward is not None:
            assert row.forward in fragment.corpus.nodes
        assert row.source_digests == tuple(
            pair for pair in fragment.corpus.source_digests if pair[0] == PREPARATION)


def test_selected_reader_scanners_retain_five_exact_paths_and_finite_expressions():
    fragment = build_planning_descriptor_fragment(sources())
    chains = fragment.scanner_chains
    assert len(chains) == 5
    assert {(row.role, len(row.forwards)) for row in chains} == {
        ('TRANSACTION_BEGIN', 0), ('SNAPSHOT_FRONTIER_READ', 0),
        ('SCHEMA_CATALOG_METADATA', 1), ('SCHEMA_COLUMN_METADATA', 1),
        ('AUTHORITY_COMMITMENT_SCAN', 2),
    }
    assert len({row.sink for row in chains}) == 5
    assert len({row.factory for row in chains}) == 1
    assert len({site for row in chains for site in row.forwards}) == 3
    expressions = {row.site: row for row in fragment.expressions.resolved}
    for row in chains:
        assert row.factory in fragment.corpus.nodes and row.sink in fragment.corpus.nodes
        assert all(site in fragment.corpus.nodes for site in row.forwards)
        assert row.domain == 'tenant_main'
        assert row.expression == expressions[row.sink]


@pytest.mark.parametrize('old,new', [
    ('observed_schema = _schema_members(connection)',
     'observed_schema = _schema_members(other_connection)'),
    ('            commitment = _authority_commitment(connection)',
     '            commitment = _authority_commitment(other_connection)'),
    ('return hashlib.sha256(_authority_snapshot_bytes(connection)).hexdigest()',
     'return hashlib.sha256(_authority_snapshot_bytes(other_connection)).hexdigest()'),
    ('def _authority_commitment(connection: sqlite3.Connection)',
     'def _authority_commitment(connection: sqlite3.Connection, extra=None)'),
    ('    rows = connection.execute(\n        "SELECT name FROM main.sqlite_master',
     '    connection = foreign\n    rows = connection.execute(\n'
     '        "SELECT name FROM main.sqlite_master'),
    ('    rows = connection.execute(\n        "SELECT name FROM main.sqlite_master',
     '    def later():\n        return connection.execute("SELECT 1")\n'
     '    rows = connection.execute(\n        "SELECT name FROM main.sqlite_master'),
    ('    materialized: list[dict[str, object]] = []',
     '    leaked = connection\n    materialized: list[dict[str, object]] = []'),
    ('    materialized: list[dict[str, object]] = []',
     '    connection.execute("SELECT 1")\n    materialized: list[dict[str, object]] = []'),
    ('            connection.execute("BEGIN")',
     '            leaked = connection.execute("BEGIN").connection'),
    ('    return tuple(\n        (\n            member.table,',
     '    return (\n        (\n            member.table,'),
    ('    return tuple(\n        (\n            member.table,',
     '    return unknown(\n        (\n            member.table,'),
    ('    rows = connection.execute(\n        "SELECT name FROM main.sqlite_master',
     '    tuple = unknown\n    rows = connection.execute(\n'
     '        "SELECT name FROM main.sqlite_master'),
    ('    rows = connection.execute(\n        "SELECT name FROM main.sqlite_master',
     '    if False:\n        yield None\n    rows = connection.execute(\n'
     '        "SELECT name FROM main.sqlite_master'),
    ('import sqlite3', 'import sqlite3\ntuple = unknown'),
    ('import sqlite3', 'import sqlite3\nglobals()["tuple"] = unknown'),
    ('    rows = connection.execute(\n        "SELECT name FROM main.sqlite_master',
     '    return connection.execute("SELECT 1")\n'
     '    rows = connection.execute(\n        "SELECT name FROM main.sqlite_master'),
    ('            connection.execute("BEGIN")', '            connection.execute("SELECT 1")'),
    ('"SELECT head FROM tenant_heads WHERE tenant_id = ?"',
     '"SELECT frontier FROM deletion_fences WHERE tenant_id = ?"'),
    ('f\'PRAGMA main.table_info("{member.table}")\'',
     'f\'SELECT * FROM main."{member.table}"\''),
    ('f\'SELECT {columns} FROM main."{member.table}" ORDER BY {order}\'',
     'f\'SELECT {columns} FROM temp."{member.table}" ORDER BY {order}\''),
])
def test_scanner_connection_forwarding_and_eager_consumption_mutants_refuse(old, new):
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(sources(), READER, old, new))


@pytest.mark.parametrize('scope,old,new', [
    ('observe_statement', '":memory:"', '"tenant.sqlite"'),
    ('observe_schema_script', '":memory:"', '"tenant.sqlite"'),
    ('observe_statement', '        _configure(connection, foreign_keys)',
     '        leak = [connection]\n        _configure(connection, foreign_keys)'),
    ('observe_statement', '        _configure(connection, foreign_keys)',
     '        return connection\n        _configure(connection, foreign_keys)'),
    ('observe_statement', '        _configure(connection, foreign_keys)',
     '        def later():\n            return connection.execute("SELECT 1")\n'
     '        _configure(connection, foreign_keys)'),
    ('observe_statement', '        _configure(connection, foreign_keys)',
     '        def connection(): pass\n        _configure(connection, foreign_keys)'),
    ('observe_statement', '        _configure(connection, foreign_keys)',
     '        class connection: pass\n        _configure(connection, foreign_keys)'),
    ('observe_statement', '        _configure(connection, foreign_keys)',
     '        from foreign import connection\n        _configure(connection, foreign_keys)'),
    ('observe_statement', '        _configure(connection, foreign_keys)',
     '        try: pass\n        except Exception as connection: pass\n'
     '        _configure(connection, foreign_keys)'),
    ('observe_statement', '        _configure(connection, foreign_keys)',
     '        match None:\n            case connection: pass\n'
     '        _configure(connection, foreign_keys)'),
    ('_schema_steps', 'connection, script, objects, foreign_keys, initial_schema_sql',
     'script, connection, objects, foreign_keys, initial_schema_sql'),
    ('_configure', '"PRAGMA foreign_keys = " + ("ON" if foreign_keys else "OFF")',
     '"PRAGMA user_version"'),
    ('observe_statement', 'connection.execute("EXPLAIN " + sql).fetchall()',
     'leaked = connection.execute("EXPLAIN " + sql).connection'),
    ('_schema_steps', 'connection.execute(sql).fetchall()',
     '(escaped := connection.execute(sql)).fetchall()'),
])
def test_private_preparation_source_substitutions_refuse(scope, old, new):
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate_preparation(sources(), scope, old, new))


@pytest.mark.parametrize('tail', [
    '\nhelper_alias = _schema_steps\n',
    '\ndef other(tenant_connection):\n'
    '    return _schema_steps(tenant_connection, "", (), False, "")\n',
    '\nconnect_alias = sqlite3.connect\n',
    '\nfrom foreign import closing\n',
    '\nobserve_statement.__code__ = foreign.__code__\n',
    '\nobserve_schema_script.__defaults__ = (foreign,)\n',
])
def test_preparation_helper_and_factory_exposures_refuse(tail):
    items = sources()
    original = dict(items)[PREPARATION]
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(items, PREPARATION, original, original + tail))


@pytest.mark.parametrize('source', [
    'from chiplog.verification.sqlite_preparation import _schema_steps\n',
    'import chiplog.verification.sqlite_preparation as prep\n'
    'def other(connection):\n    return prep._schema_steps(connection, "", (), False, "")\n',
    'from chiplog.verification.sqlite_preparation import observe_statement as observe\n'
    'def other(connection):\n'
    '    return observe.__globals__["_schema_steps"](connection, "", (), False, "")\n',
])
def test_supplied_other_module_cannot_supply_tenant_preparation_connection(source):
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment((*sources(),
            ('src/chiplog/composition/other_preparation.py', source)))


@pytest.mark.parametrize('old,new', [
    ('self._reader = BrokerAuthorityReader(database, "planning-database", read_ledger)',
     'self._reader = ForeignReader(database, "planning-database", read_ledger)'),
    ('        self._appender = appender',
     '        self._reader = foreign\n        self._appender = appender'),
    ('        session = self._supervisor.runtime().session(owner_id)',
     '        setattr(self, "_reader", foreign)\n'
     '        session = self._supervisor.runtime().session(owner_id)'),
])
def test_reader_field_rebind_or_foreign_constructor_refuses(old, new):
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(sources(), RUNTIME, old, new))


def test_reader_database_substitution_refuses():
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(sources(), READER,
            '        self._database = database', '        self._database = other_database'))


def test_wrapper_added_downstream_read_requires_descriptor_edge():
    changed = mutate(sources(), READER,
        '"commit_sequence, record_ids FROM publications WHERE tenant_id = ? "',
        '"commit_sequence, record_ids FROM publications WHERE tenant_id = ? "\n'
        '            "AND EXISTS (SELECT 1 FROM deletion_fences WHERE frontier >= 0) "')
    fragment = build_planning_descriptor_fragment(changed)
    with pytest.raises(UnknownPlanningChain, match='descriptor'):
        verify_planning_descriptor_fragment(fragment, expected())


def test_descriptor_missing_or_ghost_leaf_refuses():
    fragment = build_planning_descriptor_fragment(sources())
    for rows in (expected()[:-1], (*expected(), (99, 0, ()))):
        with pytest.raises(UnknownPlanningChain, match='descriptor'):
            verify_planning_descriptor_fragment(fragment, rows)


@pytest.mark.parametrize('old,new', [
    ('runtime_type=R7PlanningRuntime,', 'runtime_type=OtherRuntime,'),
    ('manifest=R7_PRODUCTION_MANIFEST,', 'manifest=OTHER_MANIFEST,'),
    ('        manifest=R7_PRODUCTION_MANIFEST,',
     '        manifest=R7_PRODUCTION_MANIFEST, preflight=external_reader,'),
])
def test_nondefault_runtime_or_manifest_is_not_defaulted(old, new):
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(sources(), RUNTIME, old, new))


@pytest.mark.parametrize('extra', [
    ('chiplog/platform/authority_reads.py', ''),
    ('src/../src/chiplog/platform/authority_reads.py', ''),
    (READER, ''),
])
def test_corpus_assembly_path_join_requires_exact_normalization(extra):
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment((*sources(), extra))


def test_partial_chain_cannot_admit_runtime():
    fragment = build_planning_descriptor_fragment(sources())
    with pytest.raises(UnknownPlanningChain, match='partial'):
        fragment.require_closed()
    with pytest.raises(ValueError):
        fragment.corpus.require_closed()


@pytest.mark.parametrize('old,new', [
    ('sqlite3.connect(f"file:{self._database}?mode=ro", uri=True, isolation_level=None)',
     'sqlite3.connect(":memory:")'),
    ('self._execute_planning_read(connection, operation, snapshot)',
     'self._execute_planning_read(other_connection, operation, snapshot)'),
    ('        publication_sql = (',
     '        leak(connection)\n        publication_sql = ('),
])
def test_connection_origin_forwarding_and_escape_refuse(old, new):
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(sources(), READER, old, new))


@pytest.mark.parametrize('path,old,new', [
    (READER, '        state = self._ledger.current_state(operation.tenant_id)',
     '        self = foreign\n        state = self._ledger.current_state(operation.tenant_id)'),
    (READER, '    def execute(self, operation: ReadOperation)',
     '    execute = foreign\n\n    def execute(self, operation: ReadOperation)'),
    (READER, '        self._ledger = ledger',
     '        self._execute_planning_read = foreign\n        self._ledger = ledger'),
    (RUNTIME, '    source_root = _SOURCE_ROOT',
     '    runtime_type = OtherRuntime\n    source_root = _SOURCE_ROOT'),
    (READER, 'import sqlite3', 'import sqlite3\nfrom foreign import sqlite3'),
])
def test_local_receiver_method_or_import_rebinding_refuses(path, old, new):
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(sources(), path, old, new))


def test_other_mounts_remain_visible_and_residual():
    additional = ('src/chiplog/composition/additional_mount.py', '''
from chiplog.composition.r7_planning import _open_runtime, R7PlanningRuntime
from chiplog.architecture.r7_runtime import R7_PRODUCTION_MANIFEST
async def open_additional(database, tenant_id, operator_secret):
    async with _open_runtime(database, tenant_id=tenant_id, operator_secret=operator_secret,
                             runtime_type=R7PlanningRuntime,
                             manifest=R7_PRODUCTION_MANIFEST) as runtime:
        yield runtime
''')
    fragment = build_planning_descriptor_fragment((*sources(), additional))
    assert len(fragment.other_mounts) == 1
    assert fragment.other_mounts[0].caller == 'open_additional'
    with pytest.raises(UnknownPlanningChain, match='partial'):
        fragment.require_closed()


@pytest.mark.parametrize('old,new', [
    ('        verify_invalidator_registry()',
     '        self.execute = lambda operation: None\n        verify_invalidator_registry()'),
    ('        verify_invalidator_registry()',
     '        self._execute_planning_read = lambda *args: None\n'
     '        verify_invalidator_registry()'),
    ('import sqlite3', 'import sqlite3\nimport math as sqlite3'),
    ('import sqlite3', 'import sqlite3\ndef sqlite3(*args): pass'),
    ('import sqlite3', 'import sqlite3\nsqlite3.connect = foreign_connect'),
    ('import sqlite3', 'import sqlite3\nconnect_alias = sqlite3.connect'),
])
def test_actual_reader_lookup_mutants_refuse(old, new):
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(sources(), READER, old, new))


def test_wrapper_property_decorator_refuses():
    changed = mutate(sources(), RUNTIME, '    def _verified_publications(',
                     '    @property\n    def _verified_publications(')
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(changed)


@pytest.mark.parametrize('capture', [
    'alias, = (self,)',
    'alias = [self][0]',
    'alias = {"receiver": self}["receiver"]',
])
def test_container_receiver_alias_cannot_substitute_database(capture):
    changed = mutate(sources(), READER, '        self._database = database',
        '        self._database = database\n'
        f'        {capture}\n        alias._database = foreign_database')
    with pytest.raises(UnknownPlanningChain, match='receiver'):
        build_planning_descriptor_fragment(changed)


@pytest.mark.parametrize('rebind', [
    'class connection:\n            pass',
    'def connection():\n            pass',
    'from foreign import connection',
])
def test_non_name_connection_bindings_refuse(rebind):
    changed = mutate(sources(), READER, '        scope_sql = (',
                     f'        {rebind}\n        scope_sql = (')
    with pytest.raises(UnknownPlanningChain, match='connection'):
        build_planning_descriptor_fragment(changed)


@pytest.mark.parametrize('rebind', [
    'def database():\n            pass',
    'class database:\n            pass',
    'from foreign import database',
])
def test_non_name_constructor_database_bindings_refuse(rebind):
    changed = mutate(sources(), READER, '        self._database = database',
                     f'        {rebind}\n        self._database = database')
    with pytest.raises(UnknownPlanningChain, match='database'):
        build_planning_descriptor_fragment(changed)


@pytest.mark.parametrize('old,new', [
    ('    source_root = _SOURCE_ROOT',
     '    def runtime_type(*args): pass\n    source_root = _SOURCE_ROOT'),
    ('    source_root = _SOURCE_ROOT',
     '    from foreign import database\n    source_root = _SOURCE_ROOT'),
    ('    preflight: Callable[[AuthorityGate], object] | None = None,',
     '    preflight: Callable[[AuthorityGate], object] | None = foreign_callback,'),
    ('    async with _open_runtime(\n        database,',
     '    database = foreign_database\n    async with _open_runtime(\n        database,'),
    ('@asynccontextmanager\nasync def open_r7_runtime(',
     '@foreign_decorator\nasync def open_r7_runtime('),
])
def test_base_mount_binding_and_implicit_callback_changes_refuse(old, new):
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(sources(), RUNTIME, old, new))


def test_staticmethod_builtin_shadow_refuses():
    changed = mutate(sources(), READER, 'import sqlite3',
                     'import sqlite3\nstaticmethod = lambda fn: foreign')
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(changed)


@pytest.mark.parametrize('path,old,new', [
    (RUNTIME, '        database: Path,\n        read_ledger: BrokerReadLedger,',
     '        read_ledger: BrokerReadLedger,\n        database: Path,'),
    (READER, 'self, database: Path, database_instance_id: str, ledger: BrokerReadLedger',
     'self, ledger: BrokerReadLedger, database_instance_id: str, database: Path'),
    (READER, '        connection: sqlite3.Connection,\n        operation: ReadOperation,',
     '        operation: ReadOperation,\n        connection: sqlite3.Connection,'),
])
def test_positional_domain_parameter_reorder_refuses(path, old, new):
    with pytest.raises(UnknownPlanningChain):
        build_planning_descriptor_fragment(mutate(sources(), path, old, new))
