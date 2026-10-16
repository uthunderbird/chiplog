"""Finite base-reader anchor provenance, conditional on source-local bindings.

This is not a registry/admission proof. Authentication and descriptor guards below
are checked, not assumed helper effects. Commit durability, capture/trust helper
semantics, other callers, import effects and external replacement stay residual.
"""

import ast
from dataclasses import dataclass

from chiplog.verification.assembly_forwarding import _binds
from chiplog.verification.authority_sql_corpus import CorpusNode
from chiplog.verification.planning_descriptor_chain import (
    PlanningDescriptorFragment,
    ReaderScannerChain,
    ReadLedgerOriginChain,
    RuntimePlanningPreparationChain,
    UnknownPlanningChain,
    _field,
    _imports,
)

_RUNTIME = "src/chiplog/composition/r7_planning.py"
_READER = "src/chiplog/platform/authority_reads.py"
_LEDGER = "src/chiplog/platform/read_ledger.py"


@dataclass(frozen=True)
class AnchorStage:
    role: str
    site: CorpusNode
    domain: str | None


@dataclass(frozen=True)
class ProtectedAnchorChain:
    domain: str
    stages: tuple[AnchorStage, ...]
    ledger_chains: tuple[ReadLedgerOriginChain, ...]
    scanner_chains: tuple[ReaderScannerChain, ...]
    runtime_preparation_chains: tuple[RuntimePlanningPreparationChain, ...]
    publication_sql: tuple[CorpusNode, ...]
    bootstrap: str
    residual: tuple[str, ...]


def _require(condition: object, detail: str) -> None:
    if not condition:
        raise UnknownPlanningChain("protected anchor " + detail)


def _eq(node: ast.AST | None, expression: str) -> bool:
    return node is not None and ast.dump(
        ast.parse(ast.unparse(node), mode="eval").body
    ) == ast.dump(ast.parse(expression, mode="eval").body)


def _one[T](items: list[T], detail: str) -> T:
    _require(len(items) == 1, detail)
    return items[0]


def _scope(tree: ast.AST, name: str) -> ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef:
    body = getattr(tree, "body", ())
    return _one(
        [
            n
            for n in body
            if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == name
        ],
        "missing/duplicate scope " + name,
    )


def _method(tree: ast.AST, name: str, args: tuple[str, ...]) -> ast.FunctionDef:
    node = _scope(tree, name)
    _require(isinstance(node, ast.FunctionDef), "unsupported method " + name)
    assert isinstance(node, ast.FunctionDef)
    _require(
        tuple(a.arg for a in node.args.args) == args
        and not (
            node.args.posonlyargs
            or node.args.kwonlyargs
            or node.args.defaults
            or node.args.vararg
            or node.args.kwarg
        ),
        "method signature " + name,
    )
    _require(not node.decorator_list, "method decorator " + name)
    _require([n for n in ast.walk(tree) if _binds(n, name)] == [node], "method binding " + name)
    for arg in node.args.args:
        _require(
            [n for n in ast.walk(node) if _binds(n, arg.arg)] == [arg],
            "method parameter rebound " + arg.arg,
        )
    return node


def _shape(body: list[ast.stmt], kinds: tuple[type[ast.stmt], ...], detail: str) -> None:
    _require(tuple(type(n) for n in body) == kinds, detail + " control flow")


def _assign(node: ast.stmt, target: str, value: str) -> ast.expr:
    _require(
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and _eq(node.targets[0], target)
        and _eq(node.value, value),
        "binding/forwarding " + target,
    )
    assert isinstance(node, ast.Assign)
    return node.value


def _raise(node: ast.stmt, exception: str, cause: str | None = None) -> None:
    _require(
        isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Call)
        and _eq(node.exc.func, exception)
        and len(node.exc.args) == 1
        and isinstance(node.exc.args[0], ast.Constant)
        and isinstance(node.exc.args[0].value, str)
        and not node.exc.keywords
        and (node.cause is None if cause is None else _eq(node.cause, cause)),
        "rejecting guard must raise " + exception,
    )


def _guard(node: ast.stmt, condition: str, exception: str, cause: str | None = None) -> ast.If:
    _require(
        isinstance(node, ast.If)
        and _eq(node.test, condition)
        and len(node.body) == 1
        and not node.orelse,
        "guard " + condition,
    )
    assert isinstance(node, ast.If)
    _raise(node.body[0], exception, cause)
    return node


def _return(node: ast.stmt, value: str) -> None:
    _require(isinstance(node, ast.Return) and _eq(node.value, value), "return " + value)


def _expr(node: ast.stmt, expression: str) -> ast.expr:
    _require(isinstance(node, ast.Expr) and _eq(node.value, expression), "forwarding " + expression)
    assert isinstance(node, ast.Expr)
    return node.value


def _context(node: ast.stmt, expression: str, binding: str | None) -> ast.With:
    _require(isinstance(node, ast.With) and len(node.items) == 1, "descriptor context")
    assert isinstance(node, ast.With)
    item = node.items[0]
    _require(
        _eq(item.context_expr, expression)
        and (item.optional_vars is None if binding is None else _eq(item.optional_vars, binding)),
        "descriptor context/forwarding",
    )
    return node


def _site(
    fragment: PlanningDescriptorFragment,
    module: str,
    scope: str,
    call: ast.expr,
    occurrence: int = 0,
) -> CorpusNode:
    candidates = [
        n
        for n in fragment.corpus.nodes
        if n.module == module and n.qualname == scope and n.expression == ast.unparse(call)
    ]
    _require(0 <= occurrence < len(candidates), "missing corpus occurrence " + ast.unparse(call))
    # Repeated descriptor checks are selected by their independently checked order.
    _require(
        len(candidates)
        == (2 if ast.unparse(call) == "self._check_descriptor(stream.fileno())" else 1),
        "ambiguous corpus occurrence",
    )
    return candidates[occurrence]


def _identity_and_authentication(reader: ast.Module) -> dict[str, ast.expr]:
    cls = _scope(reader, "AuthorityCommitmentJournal")
    _require(
        isinstance(cls, ast.ClassDef) and not (cls.bases or cls.keywords or cls.decorator_list),
        "unsupported journal object model",
    )
    for node in ast.walk(cls):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _require(
                not (
                    node.name.startswith("__")
                    and node.name.endswith("__")
                    and node.name != "__init__"
                ),
                "journal object-model hook",
            )
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
            _require(
                node.attr not in {"load", "_load", "_check_descriptor", "_unique_object"},
                "journal method replacement",
            )
    init = _scope(cls, "__init__")
    _require(isinstance(init, ast.FunctionDef), "journal constructor")
    assert isinstance(init, ast.FunctionDef)
    _require(
        [a.arg for a in init.args.args] == ["self", "database", "secret"]
        and [a.arg for a in init.args.kwonlyargs] == ["authority_gate"]
        and not (
            init.args.posonlyargs
            or init.args.defaults
            or init.args.vararg
            or init.args.kwarg
            or init.decorator_list
        )
        and len(init.args.kw_defaults) == 1
        and _eq(init.args.kw_defaults[0], "None"),
        "journal constructor signature",
    )
    _shape(init.body, (ast.Assign, ast.If, ast.Assign, ast.Assign, ast.Assign), "journal path")
    _assign(init.body[0], "database", "database.resolve(strict=False)")
    _guard(
        init.body[1],
        "authority_gate is not None and database != authority_gate.database",
        "ValueError",
    )
    _assign(
        init.body[2],
        "self._path",
        "database.with_suffix(database.suffix + '.r7-authority-commitment.json')",
    )
    _assign(init.body[3], "self._secret", "secret")
    _assign(init.body[4], "self._authority_gate", "authority_gate")
    for field, value in (
        ("_path", "database.with_suffix(database.suffix + '.r7-authority-commitment.json')"),
        ("_secret", "secret"),
        ("_authority_gate", "authority_gate"),
    ):
        _require(not any(_binds(n, field) for n in ast.walk(cls)), "journal class field " + field)
        _field(cls, "self." + field, value, init)
    check = _method(cls, "_check_descriptor", ("self", "descriptor"))
    _shape(check.body, (ast.Assign, ast.Assign, ast.If, ast.Return), "descriptor identity")
    _assign(check.body[0], "actual", "os.fstat(descriptor)")
    _assign(check.body[1], "named", "self._path.lstat()")
    _guard(
        check.body[2],
        "self._path.resolve(strict=True) != self._path "
        "or not stat.S_ISREG(actual.st_mode) or not stat.S_ISREG(named.st_mode) "
        "or actual.st_nlink != 1 or named.st_nlink != 1 "
        "or (actual.st_dev, actual.st_ino) != (named.st_dev, named.st_ino)",
        "RuntimeError",
    )
    _return(check.body[3], "actual")
    unique = _scope(cls, "_unique_object")
    _require(
        isinstance(unique, ast.FunctionDef)
        and [ast.unparse(n) for n in unique.decorator_list] == ["staticmethod"],
        "duplicate-key decoder binding",
    )
    assert isinstance(unique, ast.FunctionDef)
    _require(
        [n for n in ast.walk(cls) if _binds(n, "_unique_object")] == [unique]
        and [n for n in ast.walk(unique) if _binds(n, "pairs")] == [unique.args.args[0]],
        "duplicate-key decoder rebinding",
    )
    _shape(unique.body, (ast.AnnAssign, ast.For, ast.Return), "duplicate-key decoder")
    declaration, loop, returned = unique.body
    assert isinstance(declaration, ast.AnnAssign) and isinstance(loop, ast.For)
    _require(
        _eq(declaration.target, "result")
        and _eq(declaration.value, "{}")
        and _eq(loop.target, "(key, value)")
        and _eq(loop.iter, "pairs")
        and not loop.orelse
        and [a.arg for a in unique.args.args] == ["pairs"]
        and not (
            unique.args.posonlyargs
            or unique.args.kwonlyargs
            or unique.args.defaults
            or unique.args.vararg
            or unique.args.kwarg
        ),
        "duplicate-key decoder input",
    )
    _shape(loop.body, (ast.If, ast.Assign), "duplicate-key decoder loop")
    _guard(loop.body[0], "key in result", "ValueError")
    _assign(loop.body[1], "result[key]", "value")
    _return(returned, "result")
    load = _method(cls, "load", ("self", "tenant_id"))
    _shape(load.body, (ast.With,), "load gate")
    hold = _context(
        load.body[0],
        "self._authority_gate.hold() if self._authority_gate is not None else nullcontext()",
        None,
    )
    _shape(hold.body, (ast.Return,), "load gate forwarding")
    _return(hold.body[0], "self._load(tenant_id)")
    leaf = _method(cls, "_load", ("self", "tenant_id"))
    # Exact present lifecycle grammar: no extra descriptor/callback/alias positions.
    _shape(
        leaf.body, (ast.Try, ast.If, ast.Assign, ast.With, ast.Try), "descriptor acquisition/escape"
    )
    metadata, regular, descriptor, stream, authentication = leaf.body
    assert isinstance(metadata, ast.Try) and isinstance(authentication, ast.Try)
    _require(
        not (metadata.orelse or metadata.finalbody) and len(metadata.handlers) == 1,
        "metadata absence branch",
    )
    _shape(metadata.body, (ast.Assign,), "metadata acquisition")
    _assign(metadata.body[0], "metadata", "self._path.lstat()")
    missing = metadata.handlers[0]
    _require(_eq(missing.type, "FileNotFoundError") and missing.name is None, "metadata exception")
    _shape(missing.body, (ast.If, ast.Return), "absent path")
    _guard(
        missing.body[0], "self._path.resolve(strict=False) != self._path", "RuntimeError", "None"
    )
    _return(missing.body[1], "None")
    _guard(regular, "not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1", "RuntimeError")
    opened = _assign(
        descriptor, "descriptor", "os.open(self._path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)"
    )
    context = _context(stream, "os.fdopen(descriptor, 'rb')", "stream")
    _shape(
        context.body,
        (ast.Assign, ast.If, ast.Assign, ast.Assign, ast.If),
        "descriptor identity/read ordering or escape",
    )
    before = _assign(context.body[0], "before", "self._check_descriptor(stream.fileno())")
    _guard(
        context.body[1],
        "(before.st_dev, before.st_ino) != (metadata.st_dev, metadata.st_ino)",
        "RuntimeError",
    )
    raw = _assign(context.body[2], "raw", "stream.read()")
    after = _assign(context.body[3], "after", "self._check_descriptor(stream.fileno())")
    _guard(
        context.body[4],
        "(before.st_size, before.st_mtime_ns, before.st_ctime_ns) != "
        "(after.st_size, after.st_mtime_ns, after.st_ctime_ns)",
        "RuntimeError",
    )
    _require(
        not (authentication.orelse or authentication.finalbody)
        and len(authentication.handlers) == 1,
        "authentication exception control",
    )
    handler = authentication.handlers[0]
    _require(
        _eq(handler.type, "(ValueError, TypeError, UnicodeError)")
        and handler.name == "error"
        and len(handler.body) == 1,
        "authentication failure branch",
    )
    _raise(handler.body[0], "RuntimeError", "error")
    assert isinstance(handler.body[0], ast.Raise)
    _require(_eq(handler.body[0].cause, "error"), "authentication failure cause")
    _shape(
        authentication.body,
        (
            ast.Assign,
            ast.If,
            ast.Assign,
            ast.If,
            ast.Assign,
            ast.Assign,
            ast.If,
            ast.If,
            ast.Return,
        ),
        "authentication",
    )
    envelope = _assign(
        authentication.body[0], "envelope", "json.loads(raw, object_pairs_hook=self._unique_object)"
    )
    _guard(
        authentication.body[1],
        "not isinstance(envelope, dict) or set(envelope) != {'payload', 'mac'}",
        "ValueError",
    )
    _assign(authentication.body[2], "value", "envelope['payload']")
    _guard(
        authentication.body[3],
        "not isinstance(value, dict) or "
        "set(value) != {'tenant_id', 'commitment'} or not isinstance(value['tenant_id'], str) "
        "or not isinstance(value['commitment'], str) or not isinstance(envelope['mac'], str)",
        "ValueError",
    )
    _assign(
        authentication.body[4],
        "payload",
        "json.dumps(value, sort_keys=True, separators=(',', ':')).encode()",
    )
    _assign(
        authentication.body[5],
        "expected",
        "hmac.new(self._secret, payload, hashlib.sha256).hexdigest()",
    )
    auth = _guard(
        authentication.body[6], "not hmac.compare_digest(envelope['mac'], expected)", "ValueError"
    )
    _guard(authentication.body[7], "value['tenant_id'] != tenant_id", "ValueError")
    _return(authentication.body[8], "str(value['commitment'])")
    assert isinstance(auth.test, ast.UnaryOp) and isinstance(authentication.body[8], ast.Return)
    assert authentication.body[8].value is not None
    returned_load = hold.body[0]
    assert isinstance(returned_load, ast.Return) and returned_load.value is not None
    return {
        "open": opened,
        "stream": context.items[0].context_expr,
        "before": before,
        "bytes": raw,
        "after": after,
        "envelope": envelope,
        "authentication": auth.test.operand,
        "tenant": authentication.body[8].value,
        "load": returned_load.value,
    }


def _call(scope: ast.AST, expression: str) -> ast.Call:
    return _one(
        [n for n in ast.walk(scope) if isinstance(n, ast.Call) and _eq(n, expression)],
        "missing/duplicate forwarding " + expression,
    )


def _local_uses(scope: ast.AST, name: str, parents: tuple[str, ...]) -> None:
    """Exact parent positions exclude aliases, hidden captures and later replacement."""
    ancestry = {child: node for node in ast.walk(scope) for child in ast.iter_child_nodes(node)}
    observed = sorted(
        ast.unparse(ancestry[n])
        for n in ast.walk(scope)
        if isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Load)
    )
    _require(observed == sorted(parents), "carrier use/escape " + name)


def _namespace(tree: ast.AST) -> list[ast.AST]:
    result = [tree]
    if isinstance(tree, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return result
    for child in ast.iter_child_nodes(tree):
        result.extend(_namespace(child))
    return result


def _receiver_uses(scope: ast.AST, field: str, calls: tuple[str, ...]) -> None:
    parents = {child: node for node in ast.walk(scope) for child in ast.iter_child_nodes(node)}
    observed = []
    for node in ast.walk(scope):
        if isinstance(node, ast.Attribute) and _eq(node, field):
            parent = parents[node]
            call = parents.get(parent)
            _require(
                isinstance(node.ctx, ast.Load)
                and isinstance(parent, ast.Attribute)
                and isinstance(call, ast.Call)
                and call.func is parent
                and any(_eq(call, text) for text in calls),
                "receiver capability escape " + field,
            )
            assert isinstance(call, ast.Call)
            observed.append(ast.unparse(call))
    _require(
        sorted(observed)
        == sorted(ast.unparse(ast.parse(call, mode="eval").body) for call in calls),
        "receiver effect multiplicity " + field,
    )


def _reconciliation(
    runtime: ast.Module, ledger: ast.Module
) -> tuple[ast.expr, ast.expr, ast.expr, tuple[ast.expr, ...]]:
    cls = _scope(runtime, "R7PlanningRuntime")
    init = _scope(cls, "__init__")
    _require(isinstance(init, ast.FunctionDef), "runtime constructor")
    assert isinstance(init, ast.FunctionDef)
    _field(cls, "self._commitment_journal", "commitment_journal", init)
    _field(cls, "self._tenant_id", "tenant_id", init)
    _require(
        [n for n in ast.walk(init) if _binds(n, "commitment_journal")] == [init.args.args[6]],
        "runtime journal parameter rebound",
    )
    _require(
        [n for n in ast.walk(init) if _binds(n, "tenant_id")] == [init.args.args[1]],
        "runtime tenant parameter rebound",
    )
    _require(
        not any(_binds(n, "_commitment_journal") for n in ast.walk(cls)),
        "runtime journal class binding",
    )
    reconcile = _method(cls, "_locked_reconcile_read_generation", ("self",))
    _receiver_uses(
        reconcile,
        "self._commitment_journal",
        (
            "self._commitment_journal.load(self._tenant_id)",
            "self._commitment_journal.commit(self._tenant_id, commitment)",
        ),
    )
    _shape(
        reconcile.body,
        (ast.Assign, ast.Assign, ast.Assign, ast.Assign, ast.Assign, ast.If, ast.If, ast.Expr),
        "reconciliation",
    )
    _assign(reconcile.body[0], "runtime", "self._supervisor.runtime()")
    _assign(reconcile.body[1], "session", "runtime.session('planning')")
    _assign(
        reconcile.body[2],
        "(commitment, observation)",
        "capture_authority_storage_state(self._database)",
    )
    _assign(reconcile.body[3], "trust_state", "self._trust.verify()")
    loaded = _assign(
        reconcile.body[4],
        "authenticated_commitment",
        "self._commitment_journal.load(self._tenant_id)",
    )
    absent = reconcile.body[5]
    _require(
        isinstance(absent, ast.If)
        and _eq(absent.test, "authenticated_commitment is None")
        and not absent.orelse,
        "bootstrap selection",
    )
    assert isinstance(absent, ast.If)
    _shape(absent.body, (ast.With, ast.If, ast.If, ast.Expr, ast.Assign), "bootstrap")
    empty = _context(absent.body[0], "sqlite3.connect(self._database)", "connection")
    _shape(empty.body, (ast.Assign,), "bootstrap empty storage")
    _assign(
        empty.body[0],
        "populated",
        "any(connection.execute(f'SELECT EXISTS(SELECT 1 FROM "
        "{table})').fetchone()[0] for table in ('records', 'publications'))",
    )
    _guard(absent.body[1], "populated", "RuntimeError")
    _guard(absent.body[2], "trust_state is not None", "RuntimeError")
    _expr(absent.body[3], "self._commitment_journal.commit(self._tenant_id, commitment)")
    _assign(absent.body[4], "authenticated_commitment", "commitment")
    mismatch = reconcile.body[6]
    _require(
        isinstance(mismatch, ast.If)
        and _eq(mismatch.test, "not secrets.compare_digest(authenticated_commitment, commitment)")
        and not mismatch.orelse
        and isinstance(mismatch.body[-1], ast.Raise),
        "reconciliation commitment rejecting guard",
    )
    assert isinstance(mismatch, ast.If) and isinstance(mismatch.test, ast.UnaryOp)
    # The mismatch diagnostic may inspect corrupt records, but cannot recover/return.
    _require(
        not any(
            isinstance(
                n,
                (
                    ast.Return,
                    ast.Yield,
                    ast.YieldFrom,
                    ast.Await,
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                ),
            )
            for n in ast.walk(mismatch)
        ),
        "reconciliation mismatch bypass",
    )
    publication = reconcile.body[7]
    _require(
        isinstance(publication, ast.Expr) and isinstance(publication.value, ast.Call),
        "state publication",
    )
    assert isinstance(publication, ast.Expr) and isinstance(publication.value, ast.Call)
    call = publication.value
    _receiver_uses(reconcile, "self._read_ledger", (ast.unparse(call),))
    _require(
        _eq(call.func, "self._read_ledger.reconcile_generation")
        and len(call.args) == 1
        and not call.keywords
        and isinstance(call.args[0], ast.Call),
        "state publication receiver",
    )
    state = call.args[0]
    assert isinstance(state, ast.Call)
    _require(_eq(state.func, "BrokerReadState") and not state.args, "state producer")
    expected = {
        "tenant_id": "self._tenant_id",
        "broker_epoch": "session.broker_epoch",
        "credential_session_head": (
            "str(trust_state.materialization_head if trust_state else 'unavailable')"
        ),
        "endpoint_channel_head": "'local-cli'",
        "file_wal_observation": "observation",
        "journal_head": "str(trust_state.materialization_head if trust_state else 'unavailable')",
        "materialization_commitment": "authenticated_commitment",
        "owner_generation": "session.generation_id",
        "owner_draining": "False",
        "principal_contour_head": "str(trust_state.trust_head if trust_state else 'unavailable')",
        "storage_mutation_generation": "0",
        "trust_transition_head": "str(trust_state.trust_head if trust_state else 'unavailable')",
        "authority_surface_digest": "AUTHORITY_STORAGE_SURFACE_DIGEST",
        "amr_fingerprint": "AMR_FINGERPRINT",
    }
    _require(
        len(state.keywords) == len(expected)
        and {k.arg for k in state.keywords} == set(expected)
        and all(k.arg is not None and _eq(k.value, expected[k.arg]) for k in state.keywords),
        "state fields/commitment/observation forwarding",
    )
    _local_uses(
        reconcile,
        "authenticated_commitment",
        (
            "authenticated_commitment is None",
            "secrets.compare_digest(authenticated_commitment, commitment)",
            "materialization_commitment=authenticated_commitment",
        ),
    )
    _local_uses(reconcile, "observation", ("file_wal_observation=observation",))
    for name, count in (("authenticated_commitment", 2), ("commitment", 1), ("observation", 1)):
        _require(
            len([n for n in ast.walk(reconcile) if _binds(n, name)]) == count,
            "reconciliation carrier rebinding " + name,
        )
    method = _method(_scope(ledger, "BrokerReadLedger"), "reconcile_generation", ("self", "state"))
    body = (
        method.body[1:]
        if isinstance(method.body[0], ast.Expr) and isinstance(method.body[0].value, ast.Constant)
        else method.body
    )
    _shape(body, (ast.With,), "ledger state publication")
    context = body[0]
    assert isinstance(context, ast.With)
    _require(
        len(context.items) == 2
        and _eq(context.items[0].context_expr, "self._authority_scope()")
        and context.items[0].optional_vars is None
        and _eq(context.items[1].context_expr, "sqlite3.connect(self._path)")
        and _eq(context.items[1].optional_vars, "connection"),
        "ledger publication origin",
    )
    _shape(context.body, (ast.Expr, ast.Expr, ast.Expr), "ledger publication transaction")
    begin = _expr(context.body[0], "connection.execute('BEGIN IMMEDIATE')")
    stale = _expr(
        context.body[1],
        'connection.execute("UPDATE authority_read_releases\\n'
        "                       SET state = 'STALE_OR_INDETERMINATE_READ', result_digest = NULL,\\n"
        "                           result_bytes = NULL, enqueued = 0\\n"
        "                       WHERE tenant_id = ? AND state = 'PENDING'\", (state.tenant_id,))",
    )
    persisted = _expr(
        context.body[2],
        "connection.execute('INSERT INTO authority_read_state VALUES (?, ?, ?)\\n"
        "                       ON CONFLICT(tenant_id) DO UPDATE SET\\n"
        "                         canonical_state = excluded.canonical_state,\\n"
        "                         state_fingerprint = excluded.state_fingerprint', "
        "(state.tenant_id, state.canonical_bytes(), state.fingerprint()))",
    )
    ledger_cls = _scope(ledger, "BrokerReadLedger")
    current = _method(ledger_cls, "current_state", ("self", "tenant_id"))
    _shape(current.body, (ast.Assign, ast.If, ast.Return), "persisted state forwarding")
    _assign(current.body[0], "state", "self.current_state_or_none(tenant_id)")
    _guard(current.body[1], "state is None", "ReadLedgerConflict")
    _return(current.body[2], "state")
    decoded = _method(ledger_cls, "current_state_or_none", ("self", "tenant_id"))
    _shape(decoded.body, (ast.Expr, ast.With), "persisted state decoder scope")
    _require(
        isinstance(decoded.body[0], ast.Expr)
        and isinstance(decoded.body[0].value, ast.Constant)
        and isinstance(decoded.body[0].value.value, str),
        "decoder documentary expression",
    )
    decode_body = _context(decoded.body[1], "self._authority_scope()", None).body
    _shape(
        decode_body, (ast.With, ast.If, ast.Assign, ast.If, ast.Return), "persisted state decoding"
    )
    _require(
        isinstance(decode_body[1], ast.If)
        and _eq(decode_body[1].test, "row is None")
        and not decode_body[1].orelse
        and len(decode_body[1].body) == 1,
        "absent persisted state",
    )
    assert isinstance(decode_body[1], ast.If)
    _return(decode_body[1].body[0], "None")
    _assign(decode_body[2], "state", "BrokerReadState.model_validate_json(bytes(row[0]))")
    _guard(decode_body[3], "state.fingerprint() != str(row[1])", "ReadLedgerConflict")
    _return(decode_body[4], "state")
    return loaded, mismatch.test.operand, call, (begin, stale, persisted)


def _reader_release(reader: ast.Module) -> dict[str, ast.expr]:
    execute = _method(_scope(reader, "BrokerAuthorityReader"), "execute", ("self", "operation"))
    _receiver_uses(
        execute,
        "self._ledger",
        (
            "self._ledger.current_state(operation.tenant_id)",
            "self._ledger.begin(operation, state)",
            "self._ledger.release(operation, state, proof_fingerprint, result_bytes)",
            "self._ledger.dequeue(operation)",
        ),
    )
    _shape(execute.body, (ast.Assign, ast.Expr, ast.Expr, ast.With), "reader state dominance")
    current = _assign(execute.body[0], "state", "self._ledger.current_state(operation.tenant_id)")
    _expr(execute.body[1], "self._validate_request_state(operation, state)")
    _expr(execute.body[2], "self._ledger.begin(operation, state)")
    context = execute.body[3]
    assert isinstance(context, ast.With)
    # Connection origin and confinement are independently checked by the fragment.
    _shape(
        context.body,
        (
            ast.Expr,
            ast.Try,
            ast.Assign,
            ast.If,
            ast.Assign,
            ast.If,
            ast.Assign,
            ast.If,
            ast.Assign,
            ast.Assign,
            ast.Assign,
            ast.Assign,
            ast.If,
            ast.Assign,
            ast.Assign,
            ast.Assign,
            ast.If,
            ast.Assign,
            ast.If,
            ast.Return,
            ast.Expr,
        ),
        "reader snapshot/release order",
    )
    body = context.body
    _assign(body[4], "observation", "_file_wal_observation(self._database)")
    for index, condition in (
        (5, "observation != state.file_wal_observation"),
        (7, "commitment != state.materialization_commitment"),
    ):
        guard = body[index]
        _require(
            isinstance(guard, ast.If)
            and _eq(guard.test, condition)
            and not guard.orelse
            and len(guard.body) == 1
            and isinstance(guard.body[0], ast.Raise),
            "reader snapshot comparison " + condition,
        )
    commitment = _assign(body[6], "commitment", "_authority_commitment(connection)")
    snapshot = body[10]
    _require(
        isinstance(snapshot, ast.Assign)
        and len(snapshot.targets) == 1
        and _eq(snapshot.targets[0], "snapshot")
        and isinstance(snapshot.value, ast.Call)
        and _eq(snapshot.value.func, "VerifiedAuthorityReadSnapshot")
        and not snapshot.value.args,
        "reader snapshot producer",
    )
    assert isinstance(snapshot, ast.Assign) and isinstance(snapshot.value, ast.Call)
    fields = {k.arg: k.value for k in snapshot.value.keywords}
    _require(
        len(fields) == len(snapshot.value.keywords)
        and _eq(fields.get("materialization_commitment"), "commitment")
        and _eq(fields.get("file_wal_observation"), "observation"),
        "snapshot fields",
    )
    planning = _assign(
        body[11],
        "(result_bytes, attestation)",
        "self._execute_planning_read(connection, operation, snapshot)",
    )
    _assign(
        body[13],
        "proof_bytes",
        "json.dumps({'attestation': attestation.model_dump(), "
        "'registry_fingerprint': self._registry_fingerprint, 'snapshot': snapshot.model_dump()}, "
        "sort_keys=True, separators=(',', ':')).encode()",
    )
    _assign(body[14], "proof_fingerprint", "hashlib.sha256(proof_bytes).hexdigest()")
    _guard(body[12], "_file_wal_observation(self._database) != observation", "AuthorityReadFailure")
    release = _assign(
        body[15],
        "release",
        "self._ledger.release(operation, state, proof_fingerprint, result_bytes)",
    )
    release_guard = body[16]
    _require(
        isinstance(release_guard, ast.If)
        and _eq(release_guard.test, "release.state != 'RELEASED'")
        and not release_guard.orelse
        and len(release_guard.body) == 1,
        "release disposition guard",
    )
    assert isinstance(release_guard, ast.If)
    _return(
        release_guard.body[0],
        "AuthorityReadResult(disposition='STALE_OR_INDETERMINATE_READ', "
        "read_attempt_id=operation.read_attempt_id, result_digest=None, "
        "canonical_result_bytes=None, "
        "proof_fingerprint=proof_fingerprint)",
    )
    dequeue = _assign(body[17], "emitted", "self._ledger.dequeue(operation)")
    _guard(body[18], "emitted is None", "AuthorityReadFailure")
    _return(
        body[19],
        "AuthorityReadResult(disposition='RELEASED', read_attempt_id=operation.read_attempt_id, "
        "result_digest=hashlib.sha256(emitted).hexdigest(), canonical_result_bytes=emitted, "
        "proof_fingerprint=proof_fingerprint)",
    )
    _expr(body[20], "connection.rollback()")
    _require(
        {node for node in ast.walk(execute) if isinstance(node, ast.Return)}
        == {release_guard.body[0], body[19]},
        "reader return bypasses snapshot/release",
    )
    for name in (
        "state",
        "commitment",
        "observation",
        "snapshot",
        "result_bytes",
        "attestation",
        "proof_bytes",
        "proof_fingerprint",
        "release",
        "emitted",
    ):
        _require(
            len([n for n in ast.walk(execute) if _binds(n, name)]) == 1,
            "reader carrier rebound " + name,
        )
    _local_uses(
        execute,
        "state",
        (
            "self._validate_request_state(operation, state)",
            "self._ledger.begin(operation, state)",
            "state.file_wal_observation",
            "state.file_wal_observation",
            "state.materialization_commitment",
            "state.fingerprint",
            "state.trust_transition_head",
            "state.journal_head",
            "state.storage_mutation_generation",
            "self._ledger.release(operation, state, proof_fingerprint, result_bytes)",
        ),
    )
    _local_uses(
        execute,
        "result_bytes",
        ("self._ledger.release(operation, state, proof_fingerprint, result_bytes)",),
    )
    _local_uses(
        execute,
        "snapshot",
        ("snapshot.model_dump", "self._execute_planning_read(connection, operation, snapshot)"),
    )
    return {
        "current": current,
        "commitment": commitment,
        "planning": planning,
        "release": release,
        "dequeue": dequeue,
    }


def build_protected_anchor_chains(
    sources: tuple[tuple[str, str], ...], fragment: PlanningDescriptorFragment
) -> tuple[ProtectedAnchorChain, ...]:
    """Derive only the actual selected base assembly; no defining-class domain inference."""
    texts = dict(sources)
    runtime, reader, ledger = (ast.parse(texts[path]) for path in (_RUNTIME, _READER, _LEDGER))
    _imports(
        runtime,
        "chiplog.composition.r7_planning",
        {
            "AuthorityCommitmentJournal": (
                "chiplog.platform.authority_reads:AuthorityCommitmentJournal"
            ),
            "BrokerReadState": "chiplog.platform.read_ledger:BrokerReadState",
        },
    )
    _imports(
        reader,
        "chiplog.platform.authority_reads",
        {
            "AuthorityCommitmentJournal": (
                "chiplog.platform.authority_reads:AuthorityCommitmentJournal"
            ),
            "nullcontext": "contextlib:nullcontext",
        },
    )
    # Primitive module/builtin bindings used by authentication cannot be shadowed.
    for module in ("os", "stat", "json", "hmac", "hashlib"):
        imports = [
            n
            for n in reader.body
            if isinstance(n, ast.Import)
            and len(n.names) == 1
            and n.names[0].name == module
            and n.names[0].asname is None
        ]
        _require(
            len(imports) == 1
            and [n for n in _namespace(reader) if _binds(n, module)] == imports
            and not any(
                _binds(n, module) for n in ast.walk(_scope(reader, "AuthorityCommitmentJournal"))
            ),
            "authentication primitive binding " + module,
        )
        _require(
            not any(
                isinstance(n, ast.Attribute)
                and isinstance(n.ctx, (ast.Store, ast.Del))
                and isinstance(n.value, ast.Name)
                and n.value.id == module
                for n in ast.walk(reader)
            ),
            "authentication primitive member replacement",
        )
    for name in (
        "str",
        "set",
        "dict",
        "isinstance",
        "staticmethod",
        "ValueError",
        "RuntimeError",
        "FileNotFoundError",
        "TypeError",
        "UnicodeError",
    ):
        _require(
            not any(_binds(n, name) for n in ast.walk(reader)), "authentication builtin " + name
        )
    opener = _scope(runtime, "_open_runtime")
    assert isinstance(opener, ast.AsyncFunctionDef)
    for name in ("operator_secret", "tenant_id"):
        argument = _one(
            [a for a in opener.args.kwonlyargs if a.arg == name], "origin parameter " + name
        )
        _require(
            [n for n in ast.walk(opener) if _binds(n, name)] == [argument],
            "origin parameter rebound " + name,
        )
    origin = _call(
        opener,
        "AuthorityCommitmentJournal(database, operator_secret, authority_gate=authority_gate)",
    )
    consumer = _call(
        opener,
        "runtime_type(tenant_id, trust, journal, planning_store.path or database, "
        "read_ledger, commitment_journal, appender, supervisor)",
    )
    parents = {
        child: parent for parent in ast.walk(opener) for child in ast.iter_child_nodes(parent)
    }
    _require(
        isinstance(parents[origin], ast.Assign) and isinstance(parents[consumer], ast.Assign),
        "origin/consumer assignments",
    )
    declaration, runtime_declaration = parents[origin], parents[consumer]
    assert isinstance(declaration, ast.Assign) and isinstance(runtime_declaration, ast.Assign)
    _assign(declaration, "commitment_journal", ast.unparse(origin))
    _assign(runtime_declaration, "runtime", ast.unparse(consumer))
    frame = parents[parents[origin]]
    _require(
        isinstance(frame, ast.AsyncWith)
        and parents[parents[consumer]] is frame
        and frame.body[:2] == [parents[origin], parents[consumer]],
        "origin/consumer dominance",
    )
    _require(
        len([n for n in ast.walk(opener) if _binds(n, "commitment_journal")]) == 1,
        "origin journal rebound",
    )
    _local_uses(opener, "commitment_journal", (ast.unparse(consumer),))
    auth = _identity_and_authentication(reader)
    loaded, compared, publication, sql = _reconciliation(runtime, ledger)
    release = _reader_release(reader)
    # Existing chains must be actual same-snapshot instances, not class-name hints.
    _require(
        fragment.ledger_chains and fragment.scanner_chains and fragment.runtime_preparation_chains,
        "missing ledger/scanner/preparation joins",
    )
    _require(
        all(c.domain == "broker_read_ledger" for c in fragment.ledger_chains), "ledger domain join"
    )
    consumer_site = _site(fragment, _RUNTIME, "_open_runtime", consumer)
    _require(
        all(
            consumer_site in c.forwards
            for c in fragment.ledger_chains
            if c.sink.qualname != "BrokerReadLedger.__init__"
        ),
        "ledger exact assembly join",
    )
    current_site = _site(fragment, _READER, "BrokerAuthorityReader.execute", release["current"])
    release_site = _site(fragment, _READER, "BrokerAuthorityReader.execute", release["release"])
    dequeue_site = _site(fragment, _READER, "BrokerAuthorityReader.execute", release["dequeue"])
    _require(
        all(
            any(site in c.forwards for c in fragment.ledger_chains)
            for site in (current_site, release_site, dequeue_site)
        ),
        "ledger exact consumer joins",
    )
    comparison_site = _site(
        fragment, _READER, "BrokerAuthorityReader.execute", release["commitment"]
    )
    _require(
        any(comparison_site in c.forwards for c in fragment.scanner_chains),
        "scanner exact snapshot join",
    )
    stages = (
        AnchorStage(
            "ANCHOR_ORIGIN",
            _site(fragment, _RUNTIME, "_open_runtime", origin),
            "protected_commitment_anchor",
        ),
        AnchorStage("RUNTIME_FORWARD", consumer_site, None),
        AnchorStage(
            "ANCHOR_LOAD",
            _site(
                fragment, _RUNTIME, "R7PlanningRuntime._locked_reconcile_read_generation", loaded
            ),
            "protected_commitment_anchor",
        ),
        *(
            AnchorStage(
                role,
                _site(fragment, _READER, "AuthorityCommitmentJournal._load", auth[key], index),
                "protected_commitment_anchor",
            )
            for role, key, index in (
                ("DESCRIPTOR_OPEN", "open", 0),
                ("DESCRIPTOR_STREAM", "stream", 0),
                ("IDENTITY_BEFORE", "before", 0),
                ("ANCHOR_BYTES", "bytes", 0),
                ("IDENTITY_AFTER", "after", 1),
                ("ENVELOPE", "envelope", 0),
                ("AUTHENTICATION", "authentication", 0),
                ("TENANT_BINDING", "tenant", 0),
            )
        ),
        AnchorStage(
            "ANCHOR_COMPARISON",
            _site(
                fragment, _RUNTIME, "R7PlanningRuntime._locked_reconcile_read_generation", compared
            ),
            "tenant_main",
        ),
        AnchorStage(
            "STATE_PUBLICATION",
            _site(
                fragment,
                _RUNTIME,
                "R7PlanningRuntime._locked_reconcile_read_generation",
                publication,
            ),
            "broker_read_ledger",
        ),
        AnchorStage("STATE_CONSUMPTION", current_site, "broker_read_ledger"),
        AnchorStage("SNAPSHOT_COMPARISON", comparison_site, "tenant_main"),
        AnchorStage(
            "PLANNING_READ",
            _site(fragment, _READER, "BrokerAuthorityReader.execute", release["planning"]),
            "tenant_main",
        ),
        AnchorStage("RESULT_RELEASE", release_site, "broker_read_ledger"),
        AnchorStage("RESULT_DEQUEUE", dequeue_site, "broker_read_ledger"),
    )
    return (
        ProtectedAnchorChain(
            "protected_commitment_anchor",
            stages,
            fragment.ledger_chains,
            fragment.scanner_chains,
            fragment.runtime_preparation_chains,
            tuple(
                _site(fragment, _LEDGER, "BrokerReadLedger.reconcile_generation", node)
                for node in sql
            ),
            "absent anchor: only empty records/publications and absent trust; commit then compare; "
            "conditional bootstrap provenance, not an independently authenticated persisted root",
            (
                "commit durability and protected registry latestness are unproved",
                "capture/trust/model and ledger identity helper effects remain conditional",
                "module import effects, other assemblies/callbacks and external replacement "
                "remain unproved",
            ),
        ),
    )
