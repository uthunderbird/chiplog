"""The R7 base planning fragment joins proofs but never grants admission."""
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
PATHS = (RUNTIME, READER, 'src/chiplog/platform/_sqlite.py',
         'src/chiplog/platform/r7_leaves.py')


def sources():
    return tuple((p, (ROOT / p).read_text()) for p in PATHS)


def mutate(items, path, old, new):
    assert dict(items)[path].count(old) == 1
    return tuple((p, text.replace(old, new) if p == path else text) for p, text in items)


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
