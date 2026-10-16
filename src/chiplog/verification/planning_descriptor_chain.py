"""Joined R7 base planning fragment; explicitly not an admission credential.

Only the direct planning-method connection handoff is proved. Other inputs to
BrokerAuthorityReader.execute, other assemblies, module import effects and
external class mutation remain outside this fragment and block global closure.
"""

import ast
from dataclasses import dataclass
from pathlib import PurePosixPath

from chiplog.verification.assembly_forwarding import (
    AssemblyEdge,
    _bindings,
    _binds,
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
from chiplog.verification.sql_expression_inventory import extract_sql_expression_inventory


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
class PlanningDescriptorFragment:
    corpus: CurrentSqlCorpus
    assembly: AssemblyEdge
    other_mounts: tuple[AssemblyEdge, ...]
    wrapper_site: CorpusNode
    rows: tuple[PlanningDescriptorRow, ...]
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


def build_planning_descriptor_fragment(sources) -> PlanningDescriptorFragment:
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
        expressions = extract_sql_expression_inventory(sources, corpus)
        expressions.require_exact_accounting()
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
        return PlanningDescriptorFragment(corpus, edge,
            tuple(e for e in assembly.edges if e != edge), wrapper_site, tuple(rows), (
                'fragment only: supplied source set is not independently proven exhaustive',
                'other assembly mounts and external class mutation are unproved',
                'execute schema/commitment helper domains and callees remain unproved',
                'execute ledger current_state/begin/release/dequeue '
                'use separate read-ledger authority',
                'module import effects, runtime callbacks and all downstream constructor effects',
                'global receiver/domain closure, physical schema/AMR, '
                'CURRENT and V2 startup admission',
            ))
    except UnknownPlanningChain:
        raise
    except (ValueError, KeyError, SyntaxError, TypeError) as error:
        raise UnknownPlanningChain('planning fragment proof failed: ' + str(error)) from error


def verify_planning_descriptor_fragment(fragment, expected):
    """Exact independent READ rows; success certifies this fragment only."""
    observed = tuple((row.statement, row.alternative, row.reads) for row in fragment.rows)
    if observed != expected:
        raise UnknownPlanningChain('planning descriptor READ fragment differs')
