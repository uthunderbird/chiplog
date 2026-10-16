"""Joined R7 base planning fragment; explicitly not an admission credential.

Selected planning, scanner, preparation and ledger handoffs have conditional
source-local evidence. Other assemblies, module import effects, helper semantics
and external class mutation remain outside this fragment and block global closure.
"""

import ast
from dataclasses import dataclass
from pathlib import PurePosixPath

from chiplog.verification.assembly_forwarding import (
    AssemblyEdge,
    _bindings,
    _binds,
    _import_module,
    inspect_assembly_forwarding,
)
from chiplog.verification.authority_read_graph import _prepare_reads
from chiplog.verification.authority_sql_corpus import (
    CorpusNode,
    CurrentSqlCorpus,
    _inspect_sources_with_occurrences,
)
from chiplog.verification.canonical_database_routes import (
    extract_canonical_database_routes,
    verify_forwarded_database_roles,
)
from chiplog.verification.constant_sql_scripts import extract_constant_sql_scripts
from chiplog.verification.selected_engine_evidence import (
    SelectedDomainSchemaEvidence,
    SelectedEngineEvidence,
    build_selected_engine_evidence,
)
from chiplog.verification.sql_expression_inventory import (
    SqlExpressionInventory,
    SqlExpressionResolution,
    extract_sql_expression_inventory,
)


class UnknownPlanningChain(ValueError):
    pass


@dataclass(frozen=True)
class PlanningDescriptorRow:
    site: CorpusNode
    statement: int
    alternative: int
    expression_label: str
    sql: str
    database_domain: str
    reads: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class PreparationOriginChain:
    entrypoint: str
    factory: CorpusNode
    forward: CorpusNode | None
    sink: CorpusNode
    role: str
    domain: str
    source_digests: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ReaderScannerChain:
    factory: CorpusNode
    forwards: tuple[CorpusNode, ...]
    sink: CorpusNode
    role: str
    domain: str
    expression: SqlExpressionResolution


@dataclass(frozen=True)
class ReadLedgerOriginChain:
    origin: CorpusNode
    factory: CorpusNode
    forwards: tuple[CorpusNode, ...]
    sink: CorpusNode
    role: str
    domain: str
    expression: SqlExpressionResolution


@dataclass(frozen=True)
class PlanningDescriptorFragment:
    corpus: CurrentSqlCorpus
    expressions: SqlExpressionInventory
    assembly: AssemblyEdge
    other_mounts: tuple[AssemblyEdge, ...]
    wrapper_site: CorpusNode
    rows: tuple[PlanningDescriptorRow, ...]
    preparation_chains: tuple[PreparationOriginChain, ...]
    scanner_chains: tuple[ReaderScannerChain, ...]
    ledger_chains: tuple[ReadLedgerOriginChain, ...]
    domain_schemas: tuple[SelectedDomainSchemaEvidence, ...]
    engine_evidence: tuple[SelectedEngineEvidence, ...]
    residual_obligations: tuple[str, ...]

    def require_closed(self) -> None:
        raise UnknownPlanningChain('partial planning fragment cannot grant admission')


def _require(condition, message):
    if not condition:
        raise UnknownPlanningChain(message)


def _one(nodes, message):
    _require(len(nodes) == 1, message)
    return nodes[0]


def _scope(tree, *parts):
    for part in parts:
        tree = _one([n for n in tree.body if isinstance(
            n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == part],
            'missing or ambiguous chain scope: ' + part)
    return tree


def _text(node):
    return ast.unparse(node)


def _signature(method, names):
    args = method.args
    _require(isinstance(method, ast.FunctionDef)
             and [arg.arg for arg in args.args] == list(names)
             and not (args.posonlyargs or args.kwonlyargs or args.defaults
                      or args.kw_defaults or args.vararg or args.kwarg),
             'unsupported positional chain signature: ' + method.name)


def _field(tree, target, value, constructor):
    uses = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute)
            and _text(n) == target and isinstance(n.ctx, (ast.Store, ast.Del))]
    write = _one(uses, 'field is missing or rebound: ' + target)
    declaration = _one([n for n in constructor.body if isinstance(n, ast.Assign)
                       and len(n.targets) == 1 and n.targets[0] is write],
                       'field lacks dominating constructor assignment: ' + target)
    _require(_text(declaration.value) == value, 'foreign field producer: ' + target)


def _imports(tree, module, names):
    for name, origin in names.items():
        _require(_bindings(tree, module, name) == origin, 'foreign binding: ' + name)


def _connection_uses(method, allowed_calls, *, binding=None):
    """Every connection name occurrence must be a direct registered call position.

    This checks syntax, not the implementation of residual callees. Their
    obligations are retained on the fragment rather than treated as closed.
    """
    if binding is None:
        binding = _one([arg for arg in method.args.args if arg.arg == 'connection'],
                       'missing connection parameter')
    _require([node for node in ast.walk(method) if _binds(node, 'connection')] == [binding],
             'connection binding is rebound')
    parents = {child: node for node in ast.walk(method)
               for child in ast.iter_child_nodes(node)}
    for node in ast.walk(method):
        if not isinstance(node, ast.Name) or node.id != 'connection':
            continue
        if node is binding:
            continue
        _require(isinstance(node.ctx, ast.Load), 'connection is rebound')
        parent = parents[node]
        if isinstance(parent, ast.Attribute) and parent.value is node:
            call = parents.get(parent)
            _require(isinstance(call, ast.Call) and call.func is parent
                     and parent.attr in {'execute', 'rollback'}, 'connection method escapes')
        else:
            _require(isinstance(parent, ast.Call) and _text(parent.func) in allowed_calls
                     and parent.args and parent.args[0] is node
                     and sum(isinstance(n, ast.Name) and n.id == 'connection'
                             for n in ast.walk(parent)) == 1,
                     'connection escapes the registered forwarding positions')


def _prove_chain(runtime, reader):
    rt = ast.parse(runtime)
    rd = ast.parse(reader)
    cls = _scope(rt, 'R7PlanningRuntime')
    broker = _scope(rd, 'BrokerAuthorityReader')
    for item in (cls, broker):
        _require(not item.bases and not item.keywords and not item.decorator_list,
                 'unsupported class behavior')
    _require(not any(_binds(n, '_reader') for n in ast.walk(cls)),
             'reader field has a class-level descriptor or binding')
    _require(not any(_binds(n, '_database') for n in ast.walk(broker)),
             'database field has a class-level descriptor or binding')
    _imports(rt, 'chiplog.composition.r7_planning', {
        'R7PlanningRuntime': 'chiplog.composition.r7_planning:R7PlanningRuntime',
        'BrokerAuthorityReader': 'chiplog.platform.authority_reads:BrokerAuthorityReader',
        'R7_PRODUCTION_MANIFEST': 'chiplog.architecture.r7_runtime:R7_PRODUCTION_MANIFEST',
        'asynccontextmanager': 'contextlib:asynccontextmanager',
    })
    _imports(rd, 'chiplog.platform.authority_reads', {
        'BrokerAuthorityReader': 'chiplog.platform.authority_reads:BrokerAuthorityReader',
        'closing': 'contextlib:closing',
    })
    for tree in (cls, broker):
        receiver_parents = {child: parent for parent in ast.walk(tree)
                            for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == 'self' and isinstance(node.ctx, ast.Load):
                parent = receiver_parents[node]
                _require(isinstance(parent, ast.Attribute) and parent.value is node,
                         'self receiver escapes its direct attribute positions')
        for method in tree.body:
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _require(not any(isinstance(n, ast.Name) and n.id == 'self'
                                 and isinstance(n.ctx, (ast.Store, ast.Del))
                                 for n in ast.walk(method)), 'self receiver is rebound')
        for n in ast.walk(tree):
            _require(not (isinstance(n, (ast.Assign, ast.AnnAssign))
                          and isinstance(n.value, ast.Name) and n.value.id == 'self'),
                     'self receiver escapes through a local alias')
            _require(not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                          and n.func.id in {'setattr', 'getattr', 'delattr', 'exec', 'eval',
                                            'vars', '__import__'}),
                     'reflective receiver behavior is unsupported')
            _require(not (isinstance(n, ast.Attribute) and n.attr in {
                '__dict__', '__class__', '__getattribute__', '__setattr__'}),
                'indirect receiver mutation is unsupported')
    init = _scope(cls, '__init__')
    _signature(init, ('self', 'tenant_id', 'trust', 'journal', 'database',
                      'read_ledger', 'commitment_journal', 'appender', 'supervisor'))
    _field(cls, 'self._reader',
           "BrokerAuthorityReader(database, 'planning-database', read_ledger)", init)
    _require([n for n in ast.walk(init) if _binds(n, 'database')] == [init.args.args[4]],
             'runtime database parameter is rebound')
    wrapper = _scope(cls, '_verified_publications')
    _signature(wrapper, ('self', 'owner_id'))
    _require(not wrapper.decorator_list, 'wrapper has unsupported method binding')
    _require(len([n for n in ast.walk(cls) if _binds(n, '_verified_publications')]) == 1
             and not any(isinstance(n, ast.Attribute) and n.attr == '_verified_publications'
                         and isinstance(n.ctx, (ast.Store, ast.Del))
                         for n in ast.walk(cls)), 'wrapper method is rebound')
    _require(not any(_binds(n, 'staticmethod') for n in ast.walk(rd)),
             'staticmethod builtin is shadowed')
    for n in ast.walk(cls):
        if (isinstance(n, ast.Attribute) and _text(n) == 'self._reader'
                and isinstance(n.ctx, ast.Load)):
            parents = {child: p for p in ast.walk(wrapper) for child in ast.iter_child_nodes(p)}
            parent = parents.get(n)
            call = parents.get(parent)
            _require(isinstance(parent, ast.Attribute) and parent.attr == 'execute'
                     and isinstance(call, ast.Call) and call.func is parent,
                     'reader field escapes its direct wrapper')
    reader_init = _scope(broker, '__init__')
    _signature(reader_init, ('self', 'database', 'database_instance_id', 'ledger'))
    _field(broker, 'self._database', 'database', reader_init)
    _require([n for n in ast.walk(reader_init) if _binds(n, 'database')]
             == [reader_init.args.args[1]], 'reader database parameter is rebound')
    for name in ('execute', '_execute_planning_read'):
        bindings = [n for n in ast.walk(broker) if _binds(n, name)]
        _require(len(bindings) == 1 and isinstance(bindings[0], ast.FunctionDef),
                 'reader method is rebound: ' + name)
        _require(not any(isinstance(n, ast.Attribute) and n.attr == name
                         and isinstance(n.ctx, (ast.Store, ast.Del))
                         for n in ast.walk(broker)), 'reader method field is rebound')
    execute = _scope(broker, 'execute')
    leaf = _scope(broker, '_execute_planning_read')
    _signature(execute, ('self', 'operation'))
    _signature(leaf, ('connection', 'operation', 'snapshot'))
    _require(not execute.decorator_list and not init.decorator_list
             and not reader_init.decorator_list
             and [_text(n) for n in leaf.decorator_list] == ['staticmethod'],
             'unsupported method binding')
    sqlite_imports = [n for n in rd.body if isinstance(n, ast.Import)
                      for a in n.names if a.name == 'sqlite3' and a.asname is None]
    _require(len(sqlite_imports) == 1
             and [n for n in ast.walk(rd) if _binds(n, 'sqlite3')] == sqlite_imports,
             'unknown SQLite import')
    reader_parents = {child: parent for parent in ast.walk(rd)
                      for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(rd):
        if isinstance(node, ast.Name) and node.id == 'sqlite3':
            parent = reader_parents[node]
            _require(isinstance(parent, ast.Attribute) and parent.value is node
                     and isinstance(parent.ctx, ast.Load)
                     and parent.attr in {'connect', 'Connection', 'Error'},
                     'SQLite module escapes or is mutated')
            if parent.attr == 'connect':
                call = reader_parents[parent]
                _require(isinstance(call, ast.Call) and call.func is parent,
                         'SQLite connect callable escapes')
    _require(not any(isinstance(n, (ast.Name, ast.arg)) and (
        (isinstance(n, ast.Name) and n.id == 'sqlite3' and isinstance(n.ctx, (ast.Store, ast.Del)))
        or (isinstance(n, ast.arg) and n.arg == 'sqlite3')) for n in ast.walk(rd)),
        'SQLite binding is rebound')
    contexts = [n for n in execute.body if isinstance(n, ast.With)]
    context = _one(contexts, 'expected one direct connection context')
    _require(len(context.items) == 1, 'unknown connection context')
    item = context.items[0]
    _require(_text(item.context_expr) == (
        "closing(sqlite3.connect(f'file:{self._database}?mode=ro', "
        "uri=True, isolation_level=None))")
        and isinstance(item.optional_vars, ast.Name) and item.optional_vars.id == 'connection',
        'connection does not originate in selected tenant database')
    handoff = _one([n for n in ast.walk(context) if isinstance(n, ast.Call)
                    and _text(n.func) == 'self._execute_planning_read'],
                   'missing or duplicate planning handoff')
    _require(_text(handoff) == 'self._execute_planning_read(connection, operation, snapshot)',
             'planning handoff substitutes connection')
    _connection_uses(execute, {'_schema_members', '_authority_commitment',
                              'self._execute_planning_read'}, binding=item.optional_vars)
    _connection_uses(leaf, set())


def _preparation_origins(sources, corpus):
    """Prove only the closed source-local memory receiver chains, not SQL policy."""
    path = 'src/chiplog/verification/sqlite_preparation.py'
    origin = 'chiplog.verification.sqlite_preparation'
    tree = ast.parse(dict(sources)[path])
    parents = {child: parent for parent in ast.walk(tree)
               for child in ast.iter_child_nodes(parent)}
    scopes = {name: _scope(tree, name) for name in (
        '_configure', '_schema_steps', '_physical_schema', 'observe_statement',
        'observe_schema_script', 'observe_registered_schema')}
    metadata_policy = _scope(tree, '_metadata_authorizer')
    _signature(metadata_policy, ('objects',))
    _require([n for n in ast.walk(tree) if _binds(n, '_metadata_authorizer')]
             == [metadata_policy], 'metadata authorizer binding changed')
    for name, scope in scopes.items():
        _require([n for n in ast.walk(tree) if _binds(n, name)] == [scope]
                 and not scope.decorator_list, 'preparation callable is rebound: ' + name)
    _signature(scopes['_configure'], ('connection', 'foreign_keys'))
    _signature(scopes['_schema_steps'],
               ('connection', 'script', 'objects', 'foreign_keys', 'initial_schema_sql'))
    _signature(scopes['_physical_schema'], ('connection', 'objects'))
    for name, argument, keywords in (
        ('observe_registered_schema', 'schema_sql',
         ['initial_schema_sql', 'registered_objects', 'foreign_keys']),
        ('observe_statement', 'sql',
         ['registered_schema_sql', 'registered_objects', 'foreign_keys']),
        ('observe_schema_script', 'script',
         ['initial_schema_sql', 'registered_objects', 'foreign_keys']),
    ):
        args = scopes[name].args
        _require([a.arg for a in args.args] == [argument]
                 and [a.arg for a in args.kwonlyargs] == keywords
                 and args.kw_defaults == [None] * 3
                 and not (args.posonlyargs or args.defaults or args.vararg or args.kwarg),
                 'unsupported preparation entrypoint signature')
    for name, module, symbol in (
        ('sqlite3', 'sqlite3', None), ('closing', 'contextlib', 'closing'),
    ):
        imports = [n for n in tree.body if (
            isinstance(n, ast.Import) and symbol is None
            and len(n.names) == 1 and n.names[0].name == module and n.names[0].asname is None
        ) or (
            isinstance(n, ast.ImportFrom) and n.module == module and not n.level
            and len(n.names) == 1 and n.names[0].name == symbol and n.names[0].asname is None
        )]
        declaration = _one(imports, 'unknown preparation import: ' + name)
        _require([n for n in ast.walk(tree) if _binds(n, name)] == [declaration],
                 'preparation import is rebound: ' + name)

    def frame(node):
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                                 ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp,
                                 ast.GeneratorExp)):
                return node
        return None

    def exact_call(scope, expression):
        return _one([n for n in ast.walk(scope) if isinstance(n, ast.Call)
                     and frame(n) is scope and _text(n) == expression],
                    'missing or ambiguous preparation call: ' + expression)

    factories = {}
    bindings = {}
    contexts = {}
    for name in ('observe_statement', 'observe_schema_script', 'observe_registered_schema'):
        scope = scopes[name]
        context = _one([n for n in scope.body if isinstance(n, ast.With)],
                       'expected direct private preparation context')
        _require(len(context.items) == 1, 'unsupported preparation context')
        item = context.items[0]
        _require(_text(item.context_expr) == (
            "closing(sqlite3.connect(':memory:', cached_statements=0, isolation_level=None))")
            and isinstance(item.optional_vars, ast.Name) and item.optional_vars.id == 'connection',
            'preparation connection is not private memory')
        factories[name] = item.context_expr.args[0]
        bindings[name] = item.optional_vars
        contexts[name] = item.context_expr
    forwards = {
        'observe_registered_schema': (
            exact_call(scopes['observe_registered_schema'], '_configure(connection, foreign_keys)'),
            exact_call(scopes['observe_registered_schema'],
                       "_schema_steps(connection, initial_schema_sql, objects, foreign_keys, '')"),
            exact_call(scopes['observe_registered_schema'],
                       '_schema_steps(connection, schema_sql, objects, foreign_keys, '
                       'initial_schema_sql)'),
            exact_call(scopes['observe_registered_schema'],
                       '_physical_schema(connection, objects)'),
        ),
        'observe_statement': (
            exact_call(scopes['observe_statement'], '_configure(connection, foreign_keys)'),
            exact_call(scopes['observe_statement'],
                       '_schema_steps(connection, registered_schema_sql, objects, '
                       "foreign_keys, '')"),
        ),
        'observe_schema_script': (
            exact_call(scopes['observe_schema_script'], '_configure(connection, foreign_keys)'),
            exact_call(scopes['observe_schema_script'],
                       "_schema_steps(connection, initial_schema_sql, objects, foreign_keys, '')"),
            exact_call(scopes['observe_schema_script'],
                       '_schema_steps(connection, script, objects, foreign_keys, '
                       'initial_schema_sql)'),
        ),
    }
    sinks = {
        '_configure': (("connection.execute('PRAGMA foreign_keys = ' + "
                        "('ON' if foreign_keys else 'OFF'))",
                        'FOREIGN_KEYS_PROFILE'),
                       ("connection.execute('PRAGMA foreign_keys')", 'FOREIGN_KEYS_OBSERVATION')),
        '_physical_schema': (
            ("connection.execute('SELECT type, name, tbl_name, sql FROM main.sqlite_master "
             "ORDER BY type, name')", 'PHYSICAL_CATALOG'),
            ('connection.execute(f\'PRAGMA main.table_info("{name}")\')', 'PHYSICAL_COLUMNS'),
            ('connection.execute(f\'PRAGMA main.index_list("{name}")\')', 'PHYSICAL_INDEXES'),
            ('connection.execute(f\'PRAGMA main.index_xinfo("{name}")\')',
             'PHYSICAL_INDEX_COLUMNS'),
        ),
        '_schema_steps': (('connection.execute(sql)', 'SCHEMA_UNIT'),),
        'observe_statement': (("connection.execute('EXPLAIN ' + sql)", 'EXPLAIN_FIRST'),
                              ("connection.execute('EXPLAIN ' + sql, (None,) * bindings)",
                               'EXPLAIN_BINDING_RETRY')),
        'observe_schema_script': (),
        'observe_registered_schema': (),
    }
    actual_sinks = {name: tuple((exact_call(scopes[name], expression), role)
                               for expression, role in entries) for name, entries in sinks.items()}
    for name, entries in actual_sinks.items():
        for call, _ in entries:
            parent = parents[call]
            if name == '_configure' and _text(call).startswith(
                "connection.execute('PRAGMA foreign_keys = '"):
                _require(isinstance(parent, ast.Expr) and parent.value is call,
                         'preparation profile cursor escapes')
            else:
                fetch = parents.get(parent)
                statement = parents.get(fetch)
                _require(isinstance(parent, ast.Attribute) and parent.value is call
                         and parent.attr == 'fetchall' and isinstance(fetch, ast.Call)
                         and fetch.func is parent and not fetch.args and not fetch.keywords
                         and isinstance(statement, (ast.Expr, ast.Assign))
                         and statement.value is fetch,
                         'preparation execute cursor escapes')
    allowed_forwards = {call for calls in forwards.values() for call in calls}
    for name, scope in scopes.items():
        binding = bindings[name] if name in bindings else scope.args.args[0]
        _require([n for n in ast.walk(scope) if _binds(n, 'connection')] == [binding],
                 'preparation connection is rebound')
        allowed = {call for call, _ in actual_sinks[name]}
        allowed.update(forwards.get(name, ()))
        if name == '_physical_schema':
            allowed.add(exact_call(scope,
                'connection.set_authorizer(_metadata_authorizer(objects))'))
        if name in {'_schema_steps', 'observe_statement'}:
            schema_argument = 'schema=True))' if name == '_schema_steps' else 'schema=False))'
            allowed.add(exact_call(scope, 'connection.set_authorizer(_authorizer(actions, objects, '
                                   + schema_argument))
        for node in ast.walk(scope):
            if not isinstance(node, ast.Name) or node.id != 'connection' or node is binding:
                continue
            _require(isinstance(node.ctx, ast.Load) and frame(node) is scope,
                     'preparation connection escapes or enters a nested frame')
            parent = parents[node]
            call = parents.get(parent) if isinstance(parent, ast.Attribute) else parent
            _require(call in allowed and ((isinstance(parent, ast.Attribute)
                     and call.func is parent and parent.value is node)
                     or (isinstance(parent, ast.Call) and parent.args[0] is node)),
                     'preparation connection escapes registered call positions')
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == '_metadata_authorizer':
            parent = parents.get(node)
            _require(isinstance(parent, ast.Call) and parent.func is node
                     and frame(parent) is scopes['_physical_schema']
                     and _text(parent) == '_metadata_authorizer(objects)',
                     'private metadata authorizer escapes')
        if isinstance(node, ast.Name) and node.id in scopes:
            _require(parents.get(node) in allowed_forwards
                     and parents[node].func is node, 'private preparation helper escapes')
        if isinstance(node, ast.Name) and node.id == 'closing':
            _require(parents.get(node) in contexts.values() and parents[node].func is node,
                     'private closing callable escapes')
        if isinstance(node, ast.Name) and node.id == 'sqlite3':
            parent = parents[node]
            _require(isinstance(parent, ast.Attribute) and parent.value is node
                     and isinstance(parent.ctx, ast.Load) and parent.attr in {
                'connect', 'complete_statement', 'Error', 'ProgrammingError', 'sqlite_version',
                'SQLITE_READ', 'SQLITE_SELECT', 'SQLITE_INSERT', 'SQLITE_UPDATE', 'SQLITE_DELETE',
                'SQLITE_FUNCTION', 'SQLITE_PRAGMA', 'SQLITE_TRANSACTION', 'SQLITE_CREATE_TABLE',
                'SQLITE_CREATE_INDEX', 'SQLITE_REINDEX', 'SQLITE_DENY', 'SQLITE_OK'},
                'preparation SQLite module escapes or mutates')
            if parent.attr == 'connect':
                _require(parents.get(parent) in factories.values()
                         and parents[parent].func is parent, 'preparation factory escapes')
        _require(not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                      and node.func.id in {'getattr', 'setattr', 'delattr', 'globals', 'locals',
                                          'vars', 'exec', 'eval', '__import__'}),
                 'reflective preparation lookup is unsupported')
    for other_path, source in sources:
        if other_path == path:
            continue
        module = other_path[4:].removesuffix('.py').replace('/', '.')
        other_tree = ast.parse(source)
        other_parents = {child: parent for parent in ast.walk(other_tree)
                         for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(other_tree):
            if isinstance(node, ast.Import):
                _require(not any(a.name == origin for a in node.names),
                         'private preparation module is imported')
            if isinstance(node, ast.ImportFrom):
                imported = _import_module(module, node)
                _require(not (imported == origin and any(
                    a.name in {'*', '_configure', '_schema_steps'} for a in node.names))
                    and not (imported == 'chiplog.verification' and any(
                        a.name == 'sqlite_preparation' for a in node.names)),
                    'private preparation helper is imported')
                if imported == origin:
                    for alias in node.names:
                        name = alias.asname or alias.name
                        _require(alias.name in {'observe_statement', 'observe_schema_script',
                                                'observe_registered_schema',
                                                'PhysicalSchemaObservation',
                                                'ColumnInfo', 'IndexInfo', 'IndexColumnInfo',
                                                'PhysicalCatalogRow', 'SqlAction',
                                                'SqlPreparation', 'SqlPreparationRefused'},
                                 'unsupported preparation import exposure')
                        _require([n for n in ast.walk(other_tree) if _binds(n, name)] == [node],
                                 'imported preparation binding is rebound')
                        for use in ast.walk(other_tree):
                            if not isinstance(use, ast.Name) or use.id != name:
                                continue
                            parent = other_parents[use]
                            if alias.name.startswith('observe_'):
                                _require(isinstance(parent, ast.Call) and parent.func is use,
                                         'preparation observer callable escapes')
                            else:
                                _require(not isinstance(parent, (ast.Attribute, ast.Call)),
                                         'preparation type exposes module internals')
            _require(not (isinstance(node, ast.Attribute) and node.attr == 'sqlite_preparation'),
                     'indirect preparation module reference')

    def site(name, call):
        return _one([n for n in corpus.nodes if n.module == path and n.qualname == name
                     and n.expression == _text(call)], 'preparation call lacks unique corpus node')

    digests = tuple(pair for pair in corpus.source_digests if pair[0] == path)
    chains = []
    for entrypoint in factories:
        factory = site(entrypoint, factories[entrypoint])
        for forward in forwards[entrypoint]:
            helper = forward.func.id
            for sink, role in actual_sinks[helper]:
                chains.append(PreparationOriginChain(entrypoint, factory, site(entrypoint, forward),
                    site(helper, sink), role, 'private_memory', digests))
        for sink, role in actual_sinks[entrypoint]:
            chains.append(PreparationOriginChain(entrypoint, factory, None,
                site(entrypoint, sink), role, 'private_memory', digests))
    return tuple(chains)


def _reader_scanners(corpus, occurrences, expressions):
    """Conditional origins for the selected reader's five scanner/transaction sites."""
    path = 'src/chiplog/platform/authority_reads.py'
    tree = _one([tree for site, _, tree in occurrences if site.module == path][:1],
                'missing reader source tree')
    parents = {child: parent for parent in ast.walk(tree)
               for child in ast.iter_child_nodes(parent)}
    execute = _scope(tree, 'BrokerAuthorityReader', 'execute')
    helpers = {name: _scope(tree, name) for name in (
        '_schema_members', '_authority_commitment', '_authority_snapshot_bytes')}
    _require(not any(_binds(n, 'tuple') for n in ast.walk(tree)),
             'scanner eager tuple builtin is rebound')
    for name, helper in helpers.items():
        _signature(helper, ('connection',))
        _require(not helper.decorator_list
                 and [n for n in ast.walk(tree) if _binds(n, name)] == [helper],
                 'scanner helper definition is rebound')
    for node in ast.walk(tree):
        _require(not (isinstance(node, ast.Name) and node.id in {
            'globals', 'locals', 'vars', 'getattr', 'setattr', 'delattr', 'eval', 'exec',
            '__import__', '__builtins__',
        }), 'reflective scanner binding is unsupported')
        _require(not (isinstance(node, ast.Attribute) and (
            node.attr in {'__dict__', '__setattr__', '__getattribute__'}
            or (node.attr == 'tuple' and isinstance(node.ctx, (ast.Store, ast.Del)))
        )), 'indirect scanner binding mutation is unsupported')
        if isinstance(node, ast.Name) and node.id in helpers:
            parent = parents[node]
            _require(isinstance(parent, ast.Call) and parent.func is node,
                     'scanner helper callable escapes its direct call positions')

    def call(scope, expression):
        return _one([n for n in ast.walk(scope) if isinstance(n, ast.Call)
                     and _text(n) == expression], 'scanner forward changed: ' + expression)

    schema_forward = call(execute, '_schema_members(connection)')
    commitment_forward = call(execute, '_authority_commitment(connection)')
    snapshot_forward = call(helpers['_authority_commitment'],
                            '_authority_snapshot_bytes(connection)')
    planning_forward = call(execute,
                            'self._execute_planning_read(connection, operation, snapshot)')
    context = _one([n for n in execute.body if isinstance(n, ast.With)],
                   'selected reader context changed')
    binding = context.items[0].optional_vars
    factory = context.items[0].context_expr.args[0]

    def site(scope, node):
        return _one([n for n in corpus.nodes if n.module == path and n.qualname == scope
                     and n.expression == _text(node)], 'scanner lacks unique corpus occurrence')

    scopes = {'BrokerAuthorityReader.execute': execute, **helpers}
    forwards = {
        'BrokerAuthorityReader.execute': (schema_forward, commitment_forward),
        '_authority_commitment': (snapshot_forward,),
    }
    expected = {
        'BrokerAuthorityReader.execute': (
            ('TRANSACTION_BEGIN', (), None, 1),
            ('SNAPSHOT_FRONTIER_READ', (), 'fetchone', 2),
        ),
        '_schema_members': (
            ('SCHEMA_CATALOG_METADATA', (schema_forward,), 'fetchall', 1),
            ('SCHEMA_COLUMN_METADATA', (schema_forward,), 'fetchall', 1),
        ),
        '_authority_snapshot_bytes': (
            ('AUTHORITY_COMMITMENT_SCAN', (commitment_forward, snapshot_forward), 'fetchall', 1),
        ),
        '_authority_commitment': (),
    }
    sql_templates = {
        'TRANSACTION_BEGIN': repr('BEGIN'),
        'SNAPSHOT_FRONTIER_READ': repr('SELECT head FROM tenant_heads WHERE tenant_id = ?'),
        'SCHEMA_CATALOG_METADATA': repr(
            "SELECT name FROM main.sqlite_master WHERE type = 'table' "
            "AND name NOT GLOB 'sqlite_*' ORDER BY name"),
        'SCHEMA_COLUMN_METADATA': 'f\'PRAGMA main.table_info("{member.table}")\'',
        'AUTHORITY_COMMITMENT_SCAN':
            'f\'SELECT {columns} FROM main."{member.table}" ORDER BY {order}\'',
    }
    resolved = {row.site: row for row in expressions.resolved}
    chains = []
    for name, scope in scopes.items():
        _require(not any(isinstance(n, (ast.Yield, ast.YieldFrom, ast.Await))
                         for n in ast.walk(scope)), 'scanner helper execution is deferred')
        sinks = [(node, sql_call) for node, sql_call, _ in occurrences
                 if node.module == path and node.qualname == name]
        _require(len(sinks) == len(expected[name]), 'reader scanner SQL sites changed')
        allowed = set(forwards.get(name, ()))
        for (sink, sql_call), (role, path_calls, fetch_method, argc) in zip(
            sinks, expected[name], strict=True
        ):
            _require(_text(sql_call.func) == 'connection.execute' and not sql_call.keywords
                     and len(sql_call.args) == argc,
                     'scanner uses a foreign SQL receiver/arguments')
            _require(ast.dump(sql_call.args[0]) == ast.dump(
                ast.parse(sql_templates[role], mode='eval').body),
                'scanner SQL no longer has its registered role shape')
            parent = parents[sql_call]
            if fetch_method is None:
                _require(isinstance(parent, ast.Expr) and parent.value is sql_call,
                         'scanner transaction cursor escapes')
            else:
                consume = parents.get(parent)
                _require(isinstance(parent, ast.Attribute) and parent.value is sql_call
                         and parent.attr == fetch_method and isinstance(consume, ast.Call)
                         and consume.func is parent and not consume.args and not consume.keywords,
                         'scanner cursor escapes immediate consumption')
            expression = resolved.get(sink)
            _require(expression is not None and expression.kind == 'FINITE_SYNTAX',
                     'scanner SQL lacks finite same-snapshot expression evidence')
            forward_sites = tuple(site(
                '_authority_commitment' if forward is snapshot_forward
                else 'BrokerAuthorityReader.execute', forward
            ) for forward in path_calls)
            chains.append(ReaderScannerChain(site('BrokerAuthorityReader.execute', factory),
                forward_sites, sink, role, 'tenant_main', expression))
            allowed.add(sql_call)
        if scope is execute:
            allowed.add(call(scope, 'connection.rollback()'))
            allowed.add(planning_forward)
            declaration = binding
        else:
            declaration = scope.args.args[0]
        _require([n for n in ast.walk(scope) if _binds(n, 'connection')] == [declaration],
                 'scanner connection is rebound')
        for node in ast.walk(scope):
            if not isinstance(node, ast.Name) or node.id != 'connection' or node is declaration:
                continue
            _require(isinstance(node.ctx, ast.Load), 'scanner connection is rebound')
            ancestor = parents[node]
            while ancestor is not scope:
                _require(not isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef,
                    ast.ClassDef, ast.Lambda, ast.ListComp, ast.DictComp, ast.SetComp)),
                    'scanner connection enters an unsupported nested frame')
                if isinstance(ancestor, ast.GeneratorExp):
                    consumer = parents[ancestor]
                    _require(isinstance(consumer, ast.Call)
                             and _text(consumer.func) == 'tuple' and consumer.args == [ancestor]
                             and not consumer.keywords
                             and all(not part.is_async for part in ancestor.generators),
                             'scanner connection escapes through a lazy generator')
                ancestor = parents[ancestor]
            parent = parents[node]
            receiver_call = parents.get(parent) if isinstance(parent, ast.Attribute) else parent
            _require(receiver_call in allowed and (
                (isinstance(parent, ast.Attribute) and receiver_call.func is parent
                 and parent.value is node) or
                (isinstance(parent, ast.Call) and not parent.keywords and (
                    parent.args == [node] or
                    (parent is planning_forward and parent.args[0] is node)))),
                'scanner connection escapes its exact forwarding/receiver position')
    return tuple(chains)


def _read_ledger_origins(sources, corpus, occurrences, expressions):
    "Selected base-runtime ledger provenance; other users and semantics stay residual."
    texts = dict(sources)
    path = "src/chiplog/platform/read_ledger.py"
    tree = _one(
        [tree for site, _, tree in occurrences if site.module == path][:1],
        "missing ledger source tree",
    )
    runtime = ast.parse(texts["src/chiplog/composition/r7_planning.py"])
    reader = ast.parse(texts["src/chiplog/platform/authority_reads.py"])
    cls = _scope(tree, "BrokerReadLedger")
    _require(
        not cls.bases and not cls.keywords and not cls.decorator_list,
        "unsupported ledger class behavior",
    )
    _imports(
        runtime,
        "chiplog.composition.r7_planning",
        {"BrokerReadLedger": "chiplog.platform.read_ledger:BrokerReadLedger"},
    )
    _imports(
        tree,
        "chiplog.platform.read_ledger",
        {
            "BrokerReadLedger": "chiplog.platform.read_ledger:BrokerReadLedger",
            "checked_file_identity": "chiplog.platform.authority_gate:checked_file_identity",
        },
    )
    for module in ("contextlib", "sqlite3"):
        imported = [
            n
            for n in tree.body
            if isinstance(n, ast.Import)
            and any(a.name == module and a.asname is None for a in n.names)
        ]
        _require(
            len(imported) == 1 and [n for n in ast.walk(tree) if _binds(n, module)] == imported,
            "ledger module binding changed: " + module,
        )
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    protected = {
        "_path",
        "_authority_gate",
        "_identity",
        "__init__",
        "current_state",
        "current_state_or_none",
        "begin",
        "release",
        "dequeue",
        "_authority_scope",
    }
    def namespace_nodes(node):
        # Class assignments bind through Name targets; method-local row names do not.
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return
        for child in ast.iter_child_nodes(node):
            yield from namespace_nodes(child)

    for name in protected:
        bindings = [n for n in namespace_nodes(cls) if _binds(n, name)]
        if name.startswith("_") and name in {"_path", "_authority_gate", "_identity"}:
            _require(not bindings, "ledger field has class binding: " + name)
        else:
            _require(
                len(bindings) == 1 and isinstance(bindings[0], ast.FunctionDef),
                "ledger method is rebound: " + name,
            )
    for node in ast.walk(tree):
        _require(
            not (
                isinstance(node, ast.Name)
                and node.id
                in {
                    "getattr",
                    "setattr",
                    "delattr",
                    "globals",
                    "locals",
                    "vars",
                    "eval",
                    "exec",
                    "__import__",
                    "__builtins__",
                }
            ),
            "reflective ledger access is unsupported",
        )
        _require(
            not (
                isinstance(node, ast.Attribute)
                and node.attr
                in {
                    "__dict__",
                    "__class__",
                    "__getattribute__",
                    "__setattr__",
                    "__code__",
                    "__globals__",
                    "__defaults__",
                }
            ),
            "indirect ledger mutation is unsupported",
        )
        if isinstance(node, ast.Name) and node.id in {"sqlite3", "contextlib"}:
            parent = parents[node]
            _require(
                isinstance(parent, ast.Attribute)
                and parent.value is node
                and isinstance(parent.ctx, ast.Load),
                "ledger module escapes or is rebound",
            )
            if node.id == "sqlite3":
                _require(
                    parent.attr in {"connect", "IntegrityError"}, "unknown ledger SQLite member"
                )
            else:
                _require(
                    parent.attr in {"contextmanager", "nullcontext"},
                    "unknown ledger context member",
                )
            if parent.attr in {"connect", "nullcontext"}:
                call = parents[parent]
                _require(
                    isinstance(call, ast.Call) and call.func is parent,
                    "ledger factory callable escapes",
                )
        if isinstance(node, ast.Name) and node.id == "self" and isinstance(node.ctx, ast.Load):
            parent = parents[node]
            _require(
                isinstance(parent, ast.Attribute) and parent.value is node,
                "ledger receiver escapes direct attribute positions",
            )
        if (isinstance(node, ast.Attribute) and node.attr in protected
                and isinstance(node.ctx, (ast.Store, ast.Del))):
            _require(
                _text(node) in {"self._path", "self._authority_gate", "self._identity"},
                "ledger method is replaced",
            )
    init = _scope(cls, "__init__")
    args = init.args
    _require(
        [a.arg for a in args.args] == ["self", "path"]
        and [a.arg for a in args.kwonlyargs] == ["authority_gate"]
        and len(args.kw_defaults) == 1
        and _text(args.kw_defaults[0]) == "None"
        and not (args.posonlyargs or args.defaults or args.vararg or args.kwarg)
        and not init.decorator_list,
        "ledger constructor signature changed",
    )
    _field(cls, "self._path", "path", init)
    _field(cls, "self._authority_gate", "authority_gate", init)
    path_writes = [n for n in ast.walk(init) if _binds(n, "path")]
    normalization = _one(
        [
            n
            for n in init.body
            if isinstance(n, ast.Assign) and [_text(t) for t in n.targets] == ["path"]
        ],
        "ledger path normalization missing",
    )
    _require(
        _text(normalization.value)
        == ("path.resolve(strict=False) if authority_gate is not None else path")
        and path_writes == [args.args[1], normalization.targets[0]],
        "ledger path normalization or binding changed",
    )
    _require(
        [n for n in ast.walk(init) if _binds(n, "authority_gate")] == [args.kwonlyargs[0]],
        "ledger authority gate rebound",
    )
    identity_writes = [
        n
        for n in ast.walk(cls)
        if isinstance(n, ast.Attribute)
        and _text(n) == "self._identity"
        and isinstance(n.ctx, (ast.Store, ast.Del))
    ]
    _require(
        len(identity_writes) == 2
        and all(n in list(ast.walk(init)) for n in identity_writes)
        and [_text(parents[n]) for n in identity_writes]
        == [
            "self._identity: FileIdentity | None = None",
            "self._identity = checked_file_identity(path, prior)",
        ],
        "ledger identity producer changed",
    )
    path_field = _one(
        [
            n
            for n in init.body
            if isinstance(n, ast.Assign) and [_text(t) for t in n.targets] == ["self._path"]
        ],
        "ledger path field missing",
    )
    gate_field = _one(
        [
            n
            for n in init.body
            if isinstance(n, ast.Assign)
            and [_text(t) for t in n.targets] == ["self._authority_gate"]
        ],
        "ledger gate field missing",
    )
    _require(
        init.body.index(gate_field) < init.body.index(normalization) < init.body.index(path_field),
        "ledger path/gate assignment order changed",
    )
    for method in cls.body:
        if (
            isinstance(method, ast.FunctionDef)
            and method.args.args
            and method.args.args[0].arg == "self"
        ):
            _require(
                [n for n in ast.walk(method) if _binds(n, "self")] == [method.args.args[0]],
                "ledger self receiver rebound",
            )
    scope = _scope(cls, "_authority_scope")
    _signature(scope, ("self",))
    _require(
        [_text(n) for n in scope.decorator_list] == ["contextlib.contextmanager"]
        and [_text(n) for n in scope.body]
        == [
            (
                "gate = contextlib.nullcontext() if self._authority_gate is N"
                "one else self._authority_gate.hold()"
            ),
            (
                "with gate:\n    if self._identity is not None:\n        checke"
                "d_file_identity(self._path, self._identity)\n    try:\n       "
                " yield\n    finally:\n        if self._identity is not None:\n "
                "           checked_file_identity(self._path, self._identity)"
            ),
        ],
        "ledger authority context shape changed",
    )
    rt = _scope(runtime, "R7PlanningRuntime")
    broker = _scope(reader, "BrokerAuthorityReader")
    selected_classes = {'BrokerReadLedger', 'BrokerAuthorityReader', 'R7PlanningRuntime'}
    reflective_names = {'getattr', 'setattr', 'delattr', 'globals', 'locals', 'vars',
                        'eval', 'exec', '__import__', '__builtins__'}
    for module_tree in (tree, runtime, reader):
        module_parents = {child: parent for parent in ast.walk(module_tree)
                          for child in ast.iter_child_nodes(parent)}
        annotations = set()
        for node in ast.walk(module_tree):
            annotation = (node.annotation if isinstance(node, (ast.arg, ast.AnnAssign))
                          else node.returns if isinstance(
                              node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None)
            if annotation is not None:
                annotations.update(ast.walk(annotation))
        for node in ast.walk(module_tree):
            if isinstance(node, ast.ImportFrom):
                _require(not any(alias.name in selected_classes and alias.asname
                                 and alias.asname != alias.name for alias in node.names),
                         'selected producer class has an imported alias')
            _require(not (isinstance(node, ast.Name) and node.id in reflective_names),
                     'selected ledger producer uses direct reflection')
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id in selected_classes):
                _require(not isinstance(node.ctx, (ast.Store, ast.Del)),
                         'selected producer class is mutated')
            if (isinstance(node, ast.Name) and node.id in selected_classes
                    and isinstance(node.ctx, ast.Load) and node not in annotations):
                parent = module_parents[node]
                _require((isinstance(parent, ast.Call) and parent.func is node)
                         or (node.id == 'R7PlanningRuntime' and isinstance(parent, ast.keyword)
                             and parent.arg == 'runtime_type'
                             and _text(module_parents[parent].func) == '_open_runtime'),
                         'selected producer class escapes direct constructor/type positions')
    for owner in (cls, rt, broker):
        for node in ast.walk(owner):
            name = (node.name if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else
                node.id if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) else "")
            _require(not (name.startswith("__") and name.endswith("__")
                          and name != "__init__"),
                     "selected ledger chain has unsupported object-model hook")
    rt_init = _scope(rt, "__init__")
    broker_init = _scope(broker, "__init__")
    _require(
        not any(_binds(n, "_read_ledger") for n in ast.walk(rt))
        and not any(_binds(n, "_ledger") for n in ast.walk(broker)),
        "ledger handoff field has a descriptor/class binding",
    )
    _field(rt, "self._read_ledger", "read_ledger", rt_init)
    _field(broker, "self._ledger", "ledger", broker_init)
    _require(
        [n for n in ast.walk(rt_init) if _binds(n, "read_ledger")] == [rt_init.args.args[5]]
        and [n for n in ast.walk(broker_init) if _binds(n, "ledger")] == [broker_init.args.args[3]],
        "ledger handoff parameter rebound",
    )
    canonical = _scope(runtime, "_open_runtime")
    resources = _one(
        [n for n in canonical.body
         if isinstance(n, ast.With) and len(n.items) == 1
         and _text(n.items[0].context_expr) == "ExitStack()"
         and n.items[0].optional_vars is not None
         and _text(n.items[0].optional_vars) == "resources"],
        "canonical ledger resources frame changed",
    )
    startup = _one(
        [n for n in resources.body
         if isinstance(n, ast.With) and len(n.items) == 1
         and _text(n.items[0].context_expr) == "startup_scope"
         and n.items[0].optional_vars is None],
        "canonical ledger startup frame changed",
    )
    declaration = _one(
        [
            n
            for n in startup.body
            if isinstance(n, ast.Assign) and [_text(t) for t in n.targets] == ["read_ledger"]
        ],
        "canonical ledger constructor missing",
    )
    _require(
        _text(declaration.value)
        == (
            "BrokerReadLedger(database.with_suffix(database.suffix + '.re"
            "ads.sqlite3'), authority_gate=authority_gate)"
        )
        and [n for n in ast.walk(canonical) if _binds(n, "read_ledger")] == declaration.targets,
        "canonical ledger origin changed",
    )

    def find_call(scope, expression):
        return _one(
            [n for n in ast.walk(scope) if isinstance(n, ast.Call) and _text(n) == expression],
            "ledger forward changed: " + expression,
        )

    runtime_call = _one(
        [
            n
            for n in ast.walk(canonical)
            if isinstance(n, ast.Call) and _text(n.func) == "runtime_type"
        ],
        "runtime ledger handoff missing",
    )
    _require(
        len(runtime_call.args) == 8
        and not runtime_call.keywords
        and _text(runtime_call.args[4]) == "read_ledger",
        "runtime ledger argument changed",
    )
    supervisor = _one(
        [
            n
            for n in ast.walk(canonical)
            if isinstance(n, ast.Call) and _text(n.func) == "R7RuntimeSupervisor"
        ],
        "supervisor handoff missing",
    )
    supervisor_kw = _one(
        [n for n in supervisor.keywords if n.arg == "read_ledger"],
        "supervisor ledger alias changed",
    )
    _require(_text(supervisor_kw.value) == "read_ledger", "supervisor ledger argument changed")
    for node in ast.walk(canonical):
        if (
            isinstance(node, ast.Name)
            and node.id == "read_ledger"
            and isinstance(node.ctx, ast.Load)
        ):
            _require(
                node is runtime_call.args[4] or node is supervisor_kw.value,
                "canonical ledger escapes known runtime/supervisor positions",
            )
    reader_call = find_call(
        rt_init, "BrokerAuthorityReader(database, 'planning-database', read_ledger)"
    )
    execute = _scope(broker, "execute")
    direct = {
        name: find_call(execute, text)
        for name, text in {
            "current_state": "self._ledger.current_state(operation.tenant_id)",
            "begin": "self._ledger.begin(operation, state)",
            "release": "self._ledger.release(operation, state, proof_fingerprint, result_bytes)",
            "dequeue": "self._ledger.dequeue(operation)",
        }.items()
    }
    reader_parents = {c: p for p in ast.walk(broker) for c in ast.iter_child_nodes(p)}
    for node in ast.walk(broker):
        if (
            isinstance(node, ast.Attribute)
            and _text(node) == "self._ledger"
            and isinstance(node.ctx, ast.Load)
        ):
            parent = reader_parents[node]
            _require(
                reader_parents.get(parent) in direct.values(),
                "reader ledger escapes selected calls",
            )
    current = _scope(cls, "current_state")
    _signature(current, ("self", "tenant_id"))
    _require(
        not current.decorator_list
        and not any(
            isinstance(n, (ast.Yield, ast.YieldFrom, ast.Await, ast.Lambda))
            or (
                n is not current
                and isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            )
            for n in ast.walk(current)
        ),
        "ledger current_state execution is indirect",
    )
    current_forward = find_call(current, "self.current_state_or_none(tenant_id)")

    def site(module, qualname, call):
        return _one(
            [
                n
                for n in corpus.nodes
                if n.module == module and n.qualname == qualname and n.expression == _text(call)
            ],
            "ledger lacks unique canonical occurrence",
        )

    origin = site("src/chiplog/composition/r7_planning.py", "_open_runtime", declaration.value)
    prefix = (
        site("src/chiplog/composition/r7_planning.py", "_open_runtime", runtime_call),
        site("src/chiplog/composition/r7_planning.py", "R7PlanningRuntime.__init__", reader_call),
    )
    expected = {
        "__init__": (("SCHEMA_SCRIPT",), ("self", "path")),
        "current_state_or_none": (("AUTHORITY_STATE_READ",), ("self", "tenant_id")),
        "begin": (("RELEASE_CREATE",), ("self", "operation", "state")),
        "release": (
            (
                "TRANSACTION_BEGIN",
                "AUTHORITY_STATE_READ",
                "RELEASE_STATE_READ",
                "RELEASE_UPDATE",
                "RELEASE_UPDATE",
            ),
            ("self", "operation", "expected_state", "proof_fingerprint", "result_bytes"),
        ),
        "dequeue": (
            ("TRANSACTION_BEGIN", "RELEASE_STATE_READ", "RELEASE_UPDATE"),
            ("self", "operation"),
        ),
    }
    # Exact source SQL role shapes; expression inventory independently supplies payloads.
    sql_templates = {
        '__init__': (
            None,  # constant script producer is joined by expression inventory
        ),
        'current_state_or_none': (
            (
                'SELECT canonical_state, state_fingerprint FROM authority_read_state WHERE '
                'tenant_id = ?'
            ),
        ),
        'begin': (
            (
                'INSERT INTO authority_read_releases VALUES\n'
                "                       (?, ?, ?, ?, 'PENDING', NULL, NULL, ?, NULL, 0, 0)"
            ),
        ),
        'release': (
            'BEGIN IMMEDIATE',
            'SELECT state_fingerprint FROM authority_read_state WHERE tenant_id = ?',
            (
                'SELECT request_fingerprint, initial_state_fingerprint, state,\n'
                '                          result_digest, recipient_fingerprint, '
                'proof_fingerprint,\n'
                '                          enqueued, dequeued\n'
                '                   FROM authority_read_releases\n'
                '                   WHERE tenant_id = ? AND read_attempt_id = ?'
            ),
            (
                "UPDATE authority_read_releases SET state = 'RELEASED',\n"
                '                       result_digest = ?, result_bytes = ?, '
                'proof_fingerprint = ?, enqueued = 1\n'
                '                       WHERE tenant_id = ? AND read_attempt_id = ? AND '
                "state = 'PENDING'"
            ),
            (
                'UPDATE authority_read_releases\n'
                "                       SET state = 'STALE_OR_INDETERMINATE_READ', "
                'proof_fingerprint = ?\n'
                '                       WHERE tenant_id = ? AND read_attempt_id = ? AND '
                "state = 'PENDING'"
            ),
        ),
        'dequeue': (
            'BEGIN IMMEDIATE',
            (
                'SELECT result_bytes, recipient_fingerprint, state, enqueued, dequeued\n'
                '                   FROM authority_read_releases\n'
                '                   WHERE tenant_id = ? AND read_attempt_id = ?'
            ),
            (
                'UPDATE authority_read_releases SET dequeued = 1\n'
                '                   WHERE tenant_id = ? AND read_attempt_id = ? AND '
                'dequeued = 0'
            ),
        ),
    }
    resolved = {row.site: row for row in expressions.resolved}
    chains = []
    for name, (roles, signature) in expected.items():
        method = _scope(cls, name)
        if name != "__init__":
            _signature(method, signature)
        _require(
            not method.decorator_list
            and not any(
                isinstance(n, (ast.Yield, ast.YieldFrom, ast.Await, ast.Lambda))
                or (
                    n is not method
                    and isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                )
                for n in ast.walk(method)
            ),
            "ledger selected method execution is indirect",
        )
        factories = [
            n
            for n in ast.walk(method)
            if isinstance(n, ast.Call) and _text(n.func) == "sqlite3.connect"
        ]
        factory = _one(factories, "ledger selected factory count changed")
        _require(
            _text(factory)
            == ("sqlite3.connect(path)" if name == "__init__" else "sqlite3.connect(self._path)"),
            "ledger factory path changed",
        )
        item = parents[factory]
        _require(
            isinstance(item, ast.withitem)
            and item.context_expr is factory
            and isinstance(item.optional_vars, ast.Name)
            and item.optional_vars.id == "connection",
            "ledger factory not in direct connection context",
        )
        if name == "__init__":
            ancestor = parents[factory]
            while parents.get(ancestor) is not init:
                ancestor = parents[ancestor]
            _require(
                init.body.index(path_field) < init.body.index(ancestor),
                "ledger factory precedes normalized path field",
            )
        binding = item.optional_vars
        _require(
            [n for n in ast.walk(method) if _binds(n, "connection")] == [binding],
            "ledger connection rebound",
        )
        sinks = [
            (n, c)
            for n, c, _ in occurrences
            if n.module == path and n.qualname == "BrokerReadLedger." + name
        ]
        _require(len(sinks) == len(roles), "ledger SQL site count changed")
        allowed = set()
        for index, ((sink, call), role) in enumerate(zip(sinks, roles, strict=True)):
            terminal = "executescript" if role == "SCHEMA_SCRIPT" else "execute"
            _require(
                _text(call.func) == "connection." + terminal
                and not call.keywords
                and len(call.args) == (1 if role in {"SCHEMA_SCRIPT", "TRANSACTION_BEGIN"} else 2),
                "ledger SQL receiver/argument shape changed",
            )
            template = sql_templates[name][index]
            _require(
                (_text(call.args[0]) == "_SCHEMA" if template is None else
                 isinstance(call.args[0], ast.Constant) and call.args[0].value == template),
                "ledger SQL role shape changed",
            )
            parent = parents[call]
            if role in {"AUTHORITY_STATE_READ", "RELEASE_STATE_READ"}:
                consume = parents.get(parent)
                _require(
                    isinstance(parent, ast.Attribute)
                    and parent.value is call
                    and parent.attr == "fetchone"
                    and isinstance(consume, ast.Call)
                    and consume.func is parent
                    and not consume.args
                    and not consume.keywords,
                    "ledger cursor escapes immediate fetchone",
                )
            else:
                _require(isinstance(parent, ast.Expr), "ledger ignored cursor escapes")
            expression = resolved.get(sink)
            _require(
                expression is not None and expression.kind == "FINITE_SYNTAX",
                "ledger SQL lacks same-snapshot finite expression",
            )
            forwards = () if name == "__init__" else prefix
            if name != "__init__":
                direct_name = "current_state" if name == "current_state_or_none" else name
                forwards += (
                    site(
                        "src/chiplog/platform/authority_reads.py",
                        "BrokerAuthorityReader.execute",
                        direct[direct_name],
                    ),
                )
                if name == "current_state_or_none":
                    forwards += (site(path, "BrokerReadLedger.current_state", current_forward),)
            chains.append(
                ReadLedgerOriginChain(
                    origin,
                    site(path, "BrokerReadLedger." + name, factory),
                    forwards,
                    sink,
                    role,
                    "broker_read_ledger",
                    expression,
                )
            )
            allowed.add(call)
        for node in ast.walk(method):
            if not isinstance(node, ast.Name) or node.id != "connection" or node is binding:
                continue
            ancestor = parents[node]
            while ancestor is not method:
                _require(
                    not isinstance(
                        ancestor,
                        (
                            ast.GeneratorExp,
                            ast.ListComp,
                            ast.SetComp,
                            ast.DictComp,
                            ast.FunctionDef,
                            ast.AsyncFunctionDef,
                            ast.ClassDef,
                            ast.Lambda,
                        ),
                    ),
                    "ledger connection enters nested frame",
                )
                ancestor = parents[ancestor]
            parent = parents[node]
            call = parents.get(parent)
            _require(
                isinstance(node.ctx, ast.Load)
                and isinstance(parent, ast.Attribute)
                and parent.value is node
                and call in allowed
                and call.func is parent,
                "ledger connection escapes exact SQL receiver position",
            )
    return tuple(chains)


def build_planning_descriptor_fragment(
    sources: tuple[tuple[str, str], ...],
) -> PlanningDescriptorFragment:
    """Join all evidence from one supplied snapshot, leaving global closure false."""
    try:
        sources = tuple(sources)
        names = [name for name, _ in sources]
        _require(len(set(names)) == len(names), 'duplicate source path')
        for name in names:
            _require(name.startswith('src/chiplog/') and name.endswith('.py')
                     and str(PurePosixPath(name)) == name
                     and '..' not in PurePosixPath(name).parts,
                     'source path must normalize by exactly one src/ prefix')
        texts = dict(sources)
        runtime_path = 'src/chiplog/composition/r7_planning.py'
        reader_path = 'src/chiplog/platform/authority_reads.py'
        runtime, reader = texts[runtime_path], texts[reader_path]
        _prove_chain(runtime, reader)
        corpus, occurrences = _inspect_sources_with_occurrences(sources)
        preparation_chains = _preparation_origins(sources, corpus)
        expressions = extract_sql_expression_inventory(sources, corpus)
        expressions.require_exact_accounting()
        scanner_chains = _reader_scanners(corpus, occurrences, expressions)
        ledger_chains = _read_ledger_origins(sources, corpus, occurrences, expressions)
        assembly = inspect_assembly_forwarding(tuple((p[4:], s) for p, s in sources))
        edge = _one([e for e in assembly.edges if e.module == runtime_path[4:]
                     and e.caller == 'open_r7_runtime'], 'missing base R7 mount')
        _require(edge.runtime is not None and edge.runtime.kind == 'symbol'
                 and edge.runtime.origin == 'chiplog.composition.r7_planning:R7PlanningRuntime'
                 and edge.manifest.kind == 'symbol'
                 and edge.manifest.origin == (
                     'chiplog.architecture.r7_runtime:R7_PRODUCTION_MANIFEST'),
                 'base R7 mount changed runtime or manifest')
        opener = _scope(ast.parse(runtime), 'open_r7_runtime')
        _require([_text(n) for n in opener.decorator_list] == ['asynccontextmanager']
                 and [a.arg for a in opener.args.args] == ['database']
                 and [a.arg for a in opener.args.kwonlyargs]
                 == ['tenant_id', 'operator_secret']
                 and not (opener.args.posonlyargs or opener.args.defaults
                          or opener.args.vararg or opener.args.kwarg)
                 and opener.args.kw_defaults == [None, None],
                 'base mount signature or decorator changed')
        _require([n for n in ast.walk(opener) if _binds(n, 'database')]
                 == [opener.args.args[0]], 'base mount database parameter is rebound')
        calls = [n for n in ast.walk(opener) if isinstance(n, ast.Call)]
        mount = _one(calls, 'base R7 opener has extra callback or other call')
        _require(_text(mount) == ('_open_runtime(database, tenant_id=tenant_id, '
            'operator_secret=operator_secret, runtime_type=R7PlanningRuntime, '
            'manifest=R7_PRODUCTION_MANIFEST)'), 'base R7 mount arguments changed')
        canonical = _scope(ast.parse(runtime), '_open_runtime')
        _require([_text(n) for n in canonical.decorator_list] == ['asynccontextmanager']
                 and [a.arg for a in canonical.args.args] == ['database']
                 and not canonical.args.posonlyargs and not canonical.args.defaults
                 and not canonical.args.vararg and not canonical.args.kwarg
                 and [a.arg for a in canonical.args.kwonlyargs] == [
                     'tenant_id', 'operator_secret', 'runtime_type', 'manifest',
                     'extra_leaves', 'runtime_setup', 'preflight', 'store_setup']
                 and canonical.args.kw_defaults[:4] == [None] * 4
                 and all(isinstance(n, ast.Constant) and n.value is None
                         for n in canonical.args.kw_defaults[4:]),
                 'canonical runtime binding signature changed')
        _require([n for n in ast.walk(canonical) if _binds(n, 'runtime_type')]
                 == [canonical.args.kwonlyargs[2]], 'runtime type parameter is rebound')
        database_stores = [n for n in ast.walk(canonical) if isinstance(n, ast.Name)
                           and n.id == 'database' and isinstance(n.ctx, ast.Store)]
        _require(len(database_stores) == 1 and [
            n for n in ast.walk(canonical) if _binds(n, 'database')
        ] == [canonical.args.args[0], *database_stores],
            'canonical database parameter has unknown bindings')
        routes = extract_canonical_database_routes(
            runtime, texts['src/chiplog/platform/r7_leaves.py'])
        verify_forwarded_database_roles(routes)
        scripts = extract_constant_sql_scripts(texts['src/chiplog/platform/_sqlite.py'],
                                               '_expected_schema_manifest', 'SCHEMA_SQL')
        schema = _one(list(scripts), 'ambiguous schema producer').sql
        wrapper_sites = [site for site, _, _ in occurrences if site.module == runtime_path
                         and site.qualname == 'R7PlanningRuntime._verified_publications']
        wrapper_site = _one(wrapper_sites, 'missing or extra wrapper execute')
        _require(wrapper_site.expression.startswith('self._reader.execute('),
                 'wrapper receiver changed')
        sites = [(site, call) for site, call, _ in occurrences if site.module == reader_path
                 and site.qualname == 'BrokerAuthorityReader._execute_planning_read']
        _require(len(sites) == 3, 'planning statement sites changed')
        resolved = {row.site: row for row in expressions.resolved}
        rows = []
        for statement, (site, call) in enumerate(sites, 1):
            _require(_text(call.func) == 'connection.execute' and not call.keywords,
                     'foreign planning SQL receiver')
            row = resolved.get(site)
            _require(row is not None and row.kind == 'FINITE_SYNTAX',
                     'planning expression is unresolved')
            for alternative, expression in enumerate(row.alternatives):
                rows.append(PlanningDescriptorRow(site, statement, alternative, expression.label,
                    expression.sql, 'tenant_main', _prepare_reads(schema, expression.sql)))
        domain_schemas, engine_evidence = build_selected_engine_evidence(
            sources, corpus, expressions, rows, scanner_chains, ledger_chains, schema)
        return PlanningDescriptorFragment(corpus, expressions, edge,
            tuple(e for e in assembly.edges if e != edge), wrapper_site, tuple(rows),
            preparation_chains, scanner_chains, ledger_chains, domain_schemas, engine_evidence, (
                'fragment only: supplied source set is not independently proven exhaustive',
                'other assembly mounts and external class mutation are unproved',
                'selected scanner origins are conditional; full helper semantics, other '
                'incoming contexts and global engine graph/AMR remain unproved',
                'preparation origins are source-local only; authorizer policy and external '
                'runtime mutation/indirect reflective access require independent evidence',
                'selected broker_read_ledger provenance only; runtime._read_ledger and '
                'supervisor aliases and their other users remain residual',
                'ledger SQLite transaction contexts do not prove connection lifetime/closure; '
                'begin/release/dequeue do not enter _authority_scope',
                'ledger release->_release_from_row, model/fingerprint/identity helper '
                'semantics and all external mutation remain residual',
                'module import effects, runtime callbacks and all downstream constructor effects',
                'global receiver/domain closure, physical schema/AMR, '
                'CURRENT and V2 startup admission',
                'selected schemas/actions/metadata are disposable UNREGISTERED expectations; '
                'live physical schema, runtime default FK, WAL/locks, Python implicit '
                'transactions and admitted engine artifact remain unproved',
            ))
    except UnknownPlanningChain:
        raise
    except (ValueError, KeyError, SyntaxError, TypeError) as error:
        raise UnknownPlanningChain('planning fragment proof failed: ' + str(error)) from error


def verify_planning_descriptor_fragment(
    fragment: PlanningDescriptorFragment,
    expected: tuple[tuple[int, int, tuple[tuple[str, str, str], ...]], ...],
) -> None:
    """Exact independent READ rows; success certifies this fragment only."""
    observed = tuple((row.statement, row.alternative, row.reads) for row in fragment.rows)
    if observed != expected:
        raise UnknownPlanningChain('planning descriptor READ fragment differs')
