"""Constructor argument forwarding only; no filesystem or runtime admission."""

import ast
from dataclasses import dataclass


class UnknownDatabaseRoute(ValueError):
    pass


class WrongDatabaseDomain(ValueError):
    pass


@dataclass(frozen=True)
class ForwardedDatabaseRoute:
    sink: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class CanonicalDatabaseRoutes:
    routes: tuple[ForwardedDatabaseRoute, ...]
    unresolved: tuple[str, ...]

    def require_closed(self) -> None:
        raise UnknownDatabaseRoute("constructor forwarding does not prove final receiver domains")


def extract_canonical_database_routes(
    opener_source: str, leaf_source: str
) -> CanonicalDatabaseRoutes:
    tree = ast.parse(opener_source)
    _require_imports(tree)
    _require_path_leaf(ast.parse(leaf_source))
    methods = [node for node in tree.body
               if isinstance(node, ast.AsyncFunctionDef) and node.name == "_open_runtime"]
    if len(methods) != 1:
        raise UnknownDatabaseRoute("expected one canonical opener")
    method, = methods
    parents = {child: node for node in ast.walk(method) for child in ast.iter_child_nodes(node)}

    def definition(name: str) -> ast.Assign | ast.AnnAssign:
        definitions = [node for node in method.body if (
            isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name
        ) or (
            isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
            and node.target.id == name
        )]
        stores = [node for node in ast.walk(method) if isinstance(node, ast.Name)
                  and node.id == name and isinstance(node.ctx, (ast.Store, ast.Del))]
        if len(definitions) != 1 or len(stores) != 1:
            raise UnknownDatabaseRoute(f"route lacks one dominating definition: {name}")
        if definitions[0].value is None:
            raise UnknownDatabaseRoute(f"route has no produced value: {name}")
        return definitions[0]

    gate = definition("authority_gate")
    _call(gate.value, "AuthorityGate.for_database", 1)
    if ast.unparse(gate.value.args[0]) != "database" or gate.value.keywords:
        raise UnknownDatabaseRoute("gate does not receive the selected database parameter")
    canonical = definition("database")
    if ast.unparse(canonical.value) != "authority_gate.database" or canonical.lineno <= gate.lineno:
        raise UnknownDatabaseRoute("database is not normalized through the selected gate")
    for node in ast.walk(method):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)) and (
            isinstance(node.value, ast.Name) and node.value.id in {
                "planning_store", "authority_gate",
            }
        ):
            raise UnknownDatabaseRoute("route carrier attribute is rebound")

    planning = definition("planning_store")
    planning_call = _call(planning.value, "ProductionPlanningStore", 1)
    if planning_call.keywords:
        raise UnknownDatabaseRoute("unknown planning leaf arguments")
    if planning.lineno <= canonical.lineno:
        raise UnknownDatabaseRoute("planning leaf precedes canonical database definition")
    planning_paths = _paths(planning_call.args[0], ())
    routes = [ForwardedDatabaseRoute("planning_store.path", planning_paths)]
    producers = (
        ("journal", "IndependentTenantDecisionJournal.for_authority_bundle", 0, "journal.argument"),
        ("trust_store", "SQLiteTrustMaterializer.for_authority_bundle", 0, "trust_store.argument"),
        ("read_ledger", "BrokerReadLedger", 0, "read_ledger.argument"),
        ("supervisor", "R7RuntimeSupervisor", 1, "supervisor.ledger_argument"),
    )
    for name, callee, index, sink in producers:
        declaration = definition(name)
        if declaration.lineno <= canonical.lineno:
            raise UnknownDatabaseRoute(f"{name} precedes canonical database definition")
        call = _call(declaration.value, callee, index + 1, exact=False)
        _require_gate_argument(call)
        routes.append(ForwardedDatabaseRoute(sink, _paths(call.args[index], planning_paths)))

    factory = definition("factory")
    value = factory.value
    if not isinstance(value, ast.IfExp) or (
        ast.unparse(value.test) != "runtime_type._checkpoint_bundle_admission"
        or ast.unparse(value.body) != "SQLiteMaterializer._open_for_broker_bootstrap"
        or ast.unparse(value.orelse) != "SQLiteMaterializer"
    ):
        raise UnknownDatabaseRoute("unknown materializer factory alternatives")
    kwargs = definition("materializer_kwargs")
    if not isinstance(kwargs.value, ast.Dict) or any(key is None for key in kwargs.value.keys):
        raise UnknownDatabaseRoute("materializer keyword provenance is unknown")
    gate_values = [value for key, value in zip(kwargs.value.keys, kwargs.value.values, strict=True)
                   if isinstance(key, ast.Constant) and key.value == "authority_gate"]
    if len(gate_values) != 1 or ast.unparse(gate_values[0]) != "authority_gate":
        raise UnknownDatabaseRoute("materializer does not receive the selected gate")
    materializer = _one_call(method, "factory")
    _call(materializer, "factory", 1)
    if (
        not isinstance(parents.get(materializer), ast.withitem)
        or factory.lineno >= materializer.lineno or kwargs.lineno >= materializer.lineno
        or canonical.lineno >= materializer.lineno
        or len(materializer.keywords) != 1 or materializer.keywords[0].arg is not None
        or ast.unparse(materializer.keywords[0].value) != "materializer_kwargs"
    ):
        raise UnknownDatabaseRoute("unknown main materializer construction placement or arguments")
    routes.append(ForwardedDatabaseRoute("materializer.argument",
                                         _paths(materializer.args[0], planning_paths)))

    runtime = _one_call(method, "runtime_type")
    _call(runtime, "runtime_type", 4, exact=False)
    owner = parents.get(runtime)
    if (
        not isinstance(owner, ast.Assign) or owner.value is not runtime
        or planning.lineno >= runtime.lineno or canonical.lineno >= runtime.lineno
    ):
        raise UnknownDatabaseRoute("runtime is not a direct consumer after path production")
    routes.append(ForwardedDatabaseRoute("runtime.database_argument",
                                         _paths(runtime.args[3], planning_paths)))
    commitment = _one_call(method, "AuthorityCommitmentJournal")
    _call(commitment, "AuthorityCommitmentJournal", 1, exact=False)
    _require_gate_argument(commitment)
    routes.append(ForwardedDatabaseRoute("commitment.argument",
                                         _paths(commitment.args[0], planning_paths)))
    return CanonicalDatabaseRoutes(tuple(sorted(routes, key=lambda route: route.sink)), (
        "preflight callback effects", "store_setup callback effects",
        "runtime_setup callback effects", "extra_leaves and supervisor path-carrier effects",
        "caller runtime_type and manifest binding",
        "downstream constructor, inherited receiver and filesystem identity contracts",
    ))


def _paths(expression: ast.expr, planning_paths: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(expression, ast.Name) and expression.id == "database":
        return ("$database",)
    if ast.unparse(expression) == "planning_store.path" and planning_paths:
        return planning_paths
    if isinstance(expression, ast.BoolOp) and isinstance(expression.op, ast.Or):
        return tuple(sorted({path for value in expression.values
                             for path in _paths(value, planning_paths)}))
    if (
        isinstance(expression, ast.Call) and isinstance(expression.func, ast.Attribute)
        and expression.func.attr == "with_suffix" and len(expression.args) == 1
        and not expression.keywords
    ):
        suffix = expression.args[0]
        if (
            isinstance(suffix, ast.BinOp) and isinstance(suffix.op, ast.Add)
            and isinstance(suffix.left, ast.Attribute) and suffix.left.attr == "suffix"
            and ast.dump(suffix.left.value) == ast.dump(expression.func.value)
            and isinstance(suffix.right, ast.Constant) and isinstance(suffix.right.value, str)
            and suffix.right.value.startswith(".") and "/" not in suffix.right.value
        ):
            return tuple(path + suffix.right.value
                         for path in _paths(expression.func.value, planning_paths))
    raise UnknownDatabaseRoute(f"unknown database path expression: {ast.unparse(expression)}")


def _call(expression: ast.expr, callee: str, count: int, *, exact: bool = True) -> ast.Call:
    if (
        not isinstance(expression, ast.Call) or ast.unparse(expression.func) != callee
        or len(expression.args) < count or (exact and len(expression.args) != count)
        or any(isinstance(arg, ast.Starred) for arg in expression.args)
    ):
        raise UnknownDatabaseRoute(f"unknown route producer: {callee}")
    return expression


def _one_call(method: ast.AsyncFunctionDef, callee: str) -> ast.Call:
    calls = [node for node in ast.walk(method) if isinstance(node, ast.Call)
             and ast.unparse(node.func) == callee]
    if len(calls) != 1:
        raise UnknownDatabaseRoute(f"missing or duplicate route consumer: {callee}")
    parents = {child: node for node in ast.walk(method) for child in ast.iter_child_nodes(node)}
    parent = parents.get(calls[0])
    while parent is not method:
        if parent is None or isinstance(parent, (
            ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda,
            ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
        )):
            raise UnknownDatabaseRoute(
                f"route consumer belongs to another execution frame: {callee}"
            )
        parent = parents.get(parent)
    return calls[0]


def _require_gate_argument(call: ast.Call) -> None:
    values = [keyword.value for keyword in call.keywords if keyword.arg == "authority_gate"]
    if len(values) != 1 or ast.unparse(values[0]) != "authority_gate":
        raise UnknownDatabaseRoute("constructor does not receive the selected authority gate")


def _require_imports(tree: ast.Module) -> None:
    expected = {
        "AuthorityGate": (0, "chiplog.platform.authority_gate"),
        "ProductionPlanningStore": (0, "chiplog.platform.r7_leaves"),
        "IndependentTenantDecisionJournal": (0, "chiplog.adapters.driven.deployment_trust"),
        "SQLiteTrustMaterializer": (0, "chiplog.adapters.driven.deployment_trust"),
        "BrokerReadLedger": (0, "chiplog.platform.read_ledger"),
        "R7RuntimeSupervisor": (1, "r7_supervisor"),
        "SQLiteMaterializer": (0, "chiplog.platform._sqlite"),
        "AuthorityCommitmentJournal": (0, "chiplog.platform.authority_reads"),
    }
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name) and node.id in expected
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ) or (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name in expected
        ) or (isinstance(node, ast.arg) and node.arg in expected):
            raise UnknownDatabaseRoute("constructor name is rebound or shadowed")
        if isinstance(node, ast.Import) and any(
            (alias.asname or alias.name.split(".")[0]) in expected for alias in node.names
        ):
            raise UnknownDatabaseRoute("constructor name is rebound by a module import")
    for name, origin in expected.items():
        imports = [(node.level, node.module, alias.name, node in tree.body)
                   for node in ast.walk(tree)
                   if isinstance(node, ast.ImportFrom) for alias in node.names
                   if (alias.asname or alias.name) == name]
        if imports != [(*origin, name, True)]:
            raise UnknownDatabaseRoute(f"unrecognized constructor import: {name}")


def _require_path_leaf(tree: ast.Module) -> None:
    for node in ast.walk(tree):
        if (isinstance(node, ast.Name) and node.id == "ProductionPlanningStore") or (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "ProductionPlanningStore"
        ) or (isinstance(node, ast.alias) and (
            node.asname or node.name.split(".")[0]
        ) == "ProductionPlanningStore"):
            raise UnknownDatabaseRoute("leaf class symbol is rebound, shadowed or escapes")
    classes = [node for node in ast.walk(tree)
               if isinstance(node, ast.ClassDef) and node.name == "ProductionPlanningStore"]
    if len(classes) != 1 or classes[0] not in tree.body:
        raise UnknownDatabaseRoute("leaf class binding is missing, duplicated or shadowed")
    leaf, = classes
    if leaf.bases or leaf.keywords or leaf.decorator_list or len(leaf.body) != 1:
        raise UnknownDatabaseRoute("path leaf has unsupported class behavior")
    constructor, = leaf.body
    if (
        not isinstance(constructor, ast.FunctionDef) or constructor.name != "__init__"
        or constructor.decorator_list or len(constructor.body) != 1
        or [arg.arg for arg in constructor.args.args] != ["self", "path"]
        or constructor.args.posonlyargs or constructor.args.kwonlyargs
        or constructor.args.vararg or constructor.args.kwarg
        or len(constructor.args.defaults) != 1
        or not isinstance(constructor.args.defaults[0], ast.Constant)
        or constructor.args.defaults[0].value is not None
    ):
        raise UnknownDatabaseRoute("path leaf constructor is not the closed holder")
    assignment, = constructor.body
    if not isinstance(assignment, ast.Assign) or (
        len(assignment.targets) != 1 or ast.unparse(assignment.targets[0]) != "self.path"
        or ast.unparse(assignment.value) != "path"
    ):
        raise UnknownDatabaseRoute("path leaf does not forward its path parameter")
    _require_declarative_leaf_module(tree)


def _require_declarative_leaf_module(tree: ast.Module) -> None:
    body = list(tree.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    if not body or ast.unparse(body[0]) != "from __future__ import annotations":
        raise UnknownDatabaseRoute("leaf module annotation evaluation is not closed")
    imports = [ast.unparse(node) for node in body
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    if sorted(imports) != sorted((
        "from __future__ import annotations", "import time", "from pathlib import Path",
    )):
        raise UnknownDatabaseRoute("leaf module import effects are not closed")
    for node in body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Assign) and (
            len(node.targets) == 1 and ast.unparse(node.targets[0]) == "__all__"
            and isinstance(node.value, ast.List)
            and all(isinstance(item, ast.Constant) and isinstance(item.value, str)
                    for item in node.value.elts)
        ):
            continue
        if isinstance(node, ast.ClassDef) and not (
            node.bases or node.keywords or node.decorator_list
        ) and all(
            isinstance(method, ast.FunctionDef) and not method.decorator_list
            and all(default is None or isinstance(default, ast.Constant)
                    for default in (*method.args.defaults, *method.args.kw_defaults))
            for method in node.body
        ):
            continue
        raise UnknownDatabaseRoute("leaf module has unsupported executable statements")


def verify_forwarded_database_roles(observed: CanonicalDatabaseRoutes) -> None:
    expected = (
        ForwardedDatabaseRoute("commitment.argument", ("$database",)),
        ForwardedDatabaseRoute("journal.argument", ("$database.trust-journal",)),
        ForwardedDatabaseRoute("materializer.argument", ("$database",)),
        ForwardedDatabaseRoute("planning_store.path", ("$database",)),
        ForwardedDatabaseRoute("read_ledger.argument", ("$database.reads.sqlite3",)),
        ForwardedDatabaseRoute("runtime.database_argument", ("$database",)),
        ForwardedDatabaseRoute("supervisor.ledger_argument", ("$database.broker.sqlite3",)),
        ForwardedDatabaseRoute("trust_store.argument", ("$database.trust.sqlite3",)),
    )
    if observed.routes != expected:
        raise WrongDatabaseDomain("canonical constructor forwards an unexpected database role")
