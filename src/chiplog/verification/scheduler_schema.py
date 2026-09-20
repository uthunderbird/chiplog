"""Restricted, field-sensitive source audit of scheduler canonical dependencies.

This is not a Python verifier. Unknown syntax/calls and mutable aliases reject.
Audited codec leaves have fixed ASTs; the graph comes from actual builder operands,
including transitive local returns, not from the handwritten SCHEMA_DAG constant.
The caller supplies exact source bytes and owns build/startup manifest admission.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, field


class SchemaAuditError(ValueError):
    pass


# These fixed codec/identity adapters contain no configurable dependency policy.
# A new leaf implementation needs a separately reviewed primitive model.
LEAF_AST = {
    "materialization": {
        "_bytes": "e8335345421ba31f6e913ce0396d845950263f18a6d5792b4998487b65c27330",
        "_hash": "508a9cac830f5a04b1dbc031bcf35afa8521a7b3ae118fd95af592d682cf1d8b",
        "_reference": "96edc63a33574a29f8d5e6153c3b2e4bf3398e0edac16e1888f5a2e88f34118f",
        "_member": "027e53e367c9a02c4995f72c48f6669ea9bc103fc8b3f9a3b00f9215b389e142",
        "_serialized": "55705f88b5cba93349d3866b06bf33dc3c0eb8c1d4cd919617bfb6f3e6afe8d4",
        "validate_schema_dag": "e79c1c9cbf4f4fce0d87221651727dea1c24cd9b5e3ebceb5ab4f6ccf9589b30",
    },
    "rollover": {
        "_bytes": "4141fe8919518dd4a0adde11f97ac35a6df6f9efff22a513cb701b6f436ce9e3",
        "_digest": "4bb2c459443cdb5b3a372fe0b0bf2b180463303a4f9b2d550a5ce646e3d1ab1f",
        "rollover_payload_fingerprint": (
            "80b105765b72f56b0dfbcef6b498598b607a42940c3a1a213490127a6948f32c"
        ),
        "rollover_snapshot_fingerprint": (
            "9d6d152f33257cc0930144524c60c13df301dba8f1d289024c3b7c548f9b70aa"
        ),
        "_record": "508506382cadcc2276f3db8b7f23e6fc5382298d9e23c8382128b274ac71a761",
        "_ref": "0d8a6603f0fce8a132ad770418eceefd6a6ce0dbc17ba5fd28997c7962149829",
    },
}
IMPORT_AST = {
    "materialization": "cf582229fe2d07fdc955d0324a9095709444c23fdda455a22cb1259bd3a509e1",
    "rollover": "ebc95327888dd87074f2ac35251af85b5c898775d299de3312d87269988a3872",
}
MODELS = frozenset(
    {
        "Absent",
        "Present",
        "GenesisLease",
        "PhysicalRootBinding",
        "ExecutionLineageBinding",
        "RunRecord",
        "SchedulerRootReference",
        "IntervalParentPrimitive",
        "ScheduledBatchPrimitiveDomainV1",
        "MaterializationIdentity",
        "IndividualDisposition",
        "CoalescedDisposition",
        "MaterializedOccurrence",
        "MaterializationCommitment",
        "SkippedDisposition",
        "ScheduledIntervalDecision",
        "SchedulerIntervalResolutionDecision",
        "OverflowResolved",
        "IntervalCandidate",
        "FullEligibilityEvidence",
        "StreamingEligibilityEvidence",
        "SchedulerOverflowHold",
        "OverflowActive",
        "RolloverPredecessor",
        "SchedulerLineageView",
        "RolloverCandidate",
        "SchedulerEligibilityManifest",
        "SchedulerCanonicalMember",
    }
)
EXTERNAL = frozenset(
    {
        "validate_record",
        "validate_schema_dag",
        "verify_manifest",
        "policy_branch",
        "execution_subjects",
        "eligibility_overflows",
        "rollover_payload_fingerprint",
        "rollover_snapshot_fingerprint",
    }
)
BUILTINS = frozenset({"len", "tuple", "list", "set", "isinstance", "any", "all", "str"})


@dataclass(frozen=True)
class SchemaManifest:
    source_fingerprints: tuple[tuple[str, str], ...]
    dependencies: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True)
class _Value:
    dependencies: frozenset[str] = frozenset()
    fields: dict[str, _Value] | None = None
    element: _Value | None = None
    cardinality: frozenset[str] | None = None


def _cardinality(value: _Value) -> frozenset[str]:
    return value.dependencies if value.cardinality is None else value.cardinality


def _join(*values: _Value) -> _Value:
    dependencies = frozenset().union(*(value.dependencies for value in values))
    return _Value(dependencies)


def _object(fields: dict[str, _Value]) -> _Value:
    return _Value(_join(*fields.values()).dependencies, fields=fields)


def _controlled(value: _Value, control: frozenset[str]) -> _Value:
    return _Value(
        value.dependencies | control,
        {key: _controlled(item, control) for key, item in value.fields.items()}
        if value.fields is not None
        else None,
        _controlled(value.element, control) if value.element is not None else None,
        _cardinality(value) | control,
    )


def _merge(left: _Value, right: _Value) -> _Value:
    fields = None
    if left.fields is not None and right.fields is not None:
        fields = {
            key: _merge(left.fields.get(key, _Value()), right.fields.get(key, _Value()))
            for key in left.fields.keys() | right.fields.keys()
        }
    elif left.fields is not None:
        fields = {key: _merge(value, right) for key, value in left.fields.items()}
    elif right.fields is not None:
        fields = {key: _merge(left, value) for key, value in right.fields.items()}
    element = None
    if left.element is not None and right.element is not None:
        element = _merge(left.element, right.element)
    elif left.element is not None:
        element = _merge(left.element, right)
    elif right.element is not None:
        element = _merge(left, right.element)
    return _Value(
        left.dependencies | right.dependencies,
        fields,
        element,
        _cardinality(left) | _cardinality(right),
    )


@dataclass
class _Analyzer:
    name: str
    tree: ast.Module
    graph: dict[str, set[str]] = field(default_factory=dict)
    active: list[str] = field(default_factory=list)
    functions: dict[str, ast.FunctionDef] = field(init=False)
    globals: set[str] = field(init=False)

    def __post_init__(self) -> None:
        self.functions = {
            node.name: node for node in self.tree.body if isinstance(node, ast.FunctionDef)
        }
        self.globals = set(MODELS) | set(EXTERNAL) | set(BUILTINS) | set(self.functions)
        self.globals |= {"SCHEMA_DAG", "UINT64_MAX", "hashlib", "base64", "SchedulerDomainError"}
        imports = [
            node for node in self.tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        import_fingerprint = hashlib.sha256(
            "\n".join(ast.dump(node, include_attributes=False) for node in imports).encode()
        ).hexdigest()
        if import_fingerprint != IMPORT_AST[self.name]:
            raise SchemaAuditError("unmodeled canonical import binding")
        if len(self.functions) != sum(isinstance(node, ast.FunctionDef) for node in self.tree.body):
            raise SchemaAuditError("duplicate canonical function binding")
        for node in self.tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(
                    not isinstance(target, ast.Name)
                    or target.id
                    not in {
                        "SCHEMA_DAG",
                        "INTERVAL_SCHEMA",
                        "UINT64_MAX",
                    }
                    for target in targets
                ):
                    raise SchemaAuditError("unmodeled global canonical binding")
                if node.value is None or any(
                    isinstance(child, ast.Call) for child in ast.walk(node.value)
                ):
                    raise SchemaAuditError("computed canonical module initializer")
                if any(
                    isinstance(child, (ast.Name, ast.Attribute, ast.Subscript, ast.Lambda))
                    for child in ast.walk(node.value)
                ):
                    raise SchemaAuditError("nonliteral canonical module initializer")
            elif isinstance(node, ast.ClassDef):
                if (
                    node.decorator_list
                    or node.keywords
                    or len(node.bases) != 1
                    or not isinstance(node.bases[0], ast.Name)
                    or node.bases[0].id != "RecoveryDTO"
                ):
                    raise SchemaAuditError("decorated canonical model")
                if any(
                    isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    for child in node.body
                ):
                    raise SchemaAuditError("unmodeled canonical model method")
                for child in node.body:
                    if not isinstance(child, ast.AnnAssign) and not (
                        isinstance(child, ast.Expr)
                        and isinstance(child.value, ast.Constant)
                        and isinstance(child.value.value, str)
                    ):
                        raise SchemaAuditError("executable canonical class body")
                    if isinstance(child, ast.AnnAssign) and child.value is not None:
                        if not (
                            isinstance(child.value, (ast.Constant, ast.Call))
                            or (
                                isinstance(child.value, ast.Name)
                                and child.value.id == "INTERVAL_SCHEMA"
                            )
                        ):
                            raise SchemaAuditError("nonliteral canonical model default")
                        for nested in ast.walk(child.value):
                            if isinstance(nested, ast.Call) and not (
                                isinstance(nested.func, ast.Name)
                                and nested.func.id == "Field"
                                and not nested.args
                                and all(
                                    keyword.arg in {"ge", "gt", "min_length"}
                                    and isinstance(keyword.value, ast.Constant)
                                    for keyword in nested.keywords
                                )
                            ):
                                raise SchemaAuditError("computed canonical model default")
            elif isinstance(node, ast.FunctionDef):
                if node.decorator_list or any(
                    default is not None and not isinstance(default, ast.Constant)
                    for default in (*node.args.defaults, *node.args.kw_defaults)
                ):
                    raise SchemaAuditError("decorated/default-executing canonical helper")
            elif isinstance(node, ast.Expr):
                if not isinstance(node.value, ast.Constant) or not isinstance(
                    node.value.value, str
                ):
                    raise SchemaAuditError("executable canonical module expression")
            elif not isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.Expr)):
                raise SchemaAuditError("unmodeled canonical module statement")
        for name, expected in LEAF_AST[self.name].items():
            leaf_node = self.functions.get(name)
            if (
                leaf_node is None
                or hashlib.sha256(
                    ast.dump(leaf_node, include_attributes=False).encode()
                ).hexdigest()
                != expected
            ):
                raise SchemaAuditError(f"unmodeled codec leaf {self.name}.{name}")

    def node(self, label: str, value: _Value) -> _Value:
        self.graph.setdefault(label, set()).update(value.dependencies)
        return _Value(frozenset({label}))

    def call(self, name: str, args: list[_Value], keywords: dict[str, _Value]) -> _Value:
        if name in self.active:
            raise SchemaAuditError("recursive canonical builder")
        function = self.functions[name]
        if function.args.vararg or function.args.kwarg or function.args.posonlyargs:
            raise SchemaAuditError("unsupported builder argument binding")
        if any(
            default is not None and not isinstance(default, ast.Constant)
            for default in (*function.args.defaults, *function.args.kw_defaults)
        ):
            raise SchemaAuditError("computed helper default")
        parameters = [argument.arg for argument in function.args.args]
        if len(args) > len(parameters):
            raise SchemaAuditError("extra positional argument")
        env = dict(zip(parameters, args, strict=False))
        for parameter in (*function.args.args, *function.args.kwonlyargs):
            if parameter.arg not in env:
                env[parameter.arg] = keywords.get(parameter.arg, _Value())
        if set(keywords) - set(env):
            raise SchemaAuditError("unknown helper keyword")
        self.active.append(name)
        result = self.statements(function.body, env)
        self.active.pop()
        return result

    def statements(self, body: list[ast.stmt], env: dict[str, _Value]) -> _Value:
        returned = _Value()
        for statement in body:
            if isinstance(statement, ast.Return):
                return _merge(returned, self.expr(statement.value, env))
            if isinstance(statement, ast.Raise):
                return returned
            if isinstance(statement, ast.Expr):
                self.expr(statement.value, env)
            elif isinstance(statement, ast.Assign):
                if len(statement.targets) != 1:
                    raise SchemaAuditError("mutable alias/multiple-target assignment")
                self.assign(statement.targets[0], self.expr(statement.value, env), env)
            elif isinstance(statement, ast.AnnAssign):
                if statement.value is not None:
                    self.assign(statement.target, self.expr(statement.value, env), env)
            elif isinstance(statement, ast.If):
                condition = self.expr(statement.test, env)
                left, right = dict(env), dict(env)
                a = self.statements(statement.body, left)
                b = self.statements(statement.orelse, right)
                written = {
                    node.id
                    for branch in (*statement.body, *statement.orelse)
                    for node in ast.walk(branch)
                    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
                }
                for key in left.keys() | right.keys():
                    lv, rv = left.get(key, _Value()), right.get(key, _Value())
                    combined = _merge(lv, rv)
                    env[key] = (
                        _controlled(combined, condition.dependencies)
                        if lv != rv or key in written
                        else combined
                    )
                combined_return = _merge(a, b)
                if a != b or any(
                    isinstance(node, ast.Return)
                    for branch in (*statement.body, *statement.orelse)
                    for node in ast.walk(branch)
                ):
                    combined_return = _controlled(combined_return, condition.dependencies)
                returned = _merge(returned, combined_return)
            elif isinstance(statement, ast.Assert):
                self.expr(statement.test, env)
            elif not isinstance(statement, (ast.Return, ast.Raise, ast.Expr)):
                raise SchemaAuditError(
                    f"unsupported canonical statement {type(statement).__name__}"
                )
        return returned

    def assign(self, target: ast.expr, value: _Value, env: dict[str, _Value]) -> None:
        if isinstance(target, ast.Name):
            if target.id in self.functions or target.id in MODELS | EXTERNAL | BUILTINS:
                raise SchemaAuditError("canonical call binding shadowed")
            if self.name == "rollover" and target.id == "primitive":
                node = self.node("primitive:Rollover", value)
                value = _Value(node.dependencies, fields=value.fields)
            env[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self.assign(item, value.element or value, env)
        else:
            raise SchemaAuditError("attribute/subscript mutation is outside builder grammar")

    def expr(self, expression: ast.expr | None, env: dict[str, _Value]) -> _Value:
        if expression is None or isinstance(expression, ast.Constant):
            return _Value()
        if isinstance(expression, ast.Name):
            if expression.id in env:
                return env[expression.id]
            if expression.id in self.globals:
                return _Value()
            raise SchemaAuditError(f"unknown dependency name {expression.id}")
        if isinstance(expression, ast.Attribute):
            value = self.expr(expression.value, env)
            return (
                value.fields.get(expression.attr, _Value()) if value.fields is not None else value
            )
        if isinstance(expression, ast.Dict):
            fields: dict[str, _Value] = {}
            for key, item in zip(expression.keys, expression.values, strict=True):
                value = self.expr(item, env)
                if key is None:
                    if value.fields is None:
                        raise SchemaAuditError("opaque dictionary expansion")
                    fields.update(value.fields)
                elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                    fields[key.value] = value
                else:
                    raise SchemaAuditError("dynamic canonical field name")
            return _object(fields)
        if isinstance(expression, (ast.Tuple, ast.List, ast.Set)):
            values = [self.expr(item, env) for item in expression.elts]
            element = _Value()
            for value in values:
                element = _merge(element, value)
            cardinality = frozenset().union(
                *(
                    _cardinality(value)
                    for item, value in zip(expression.elts, values, strict=True)
                    if isinstance(item, ast.Starred)
                )
            )
            if isinstance(expression, ast.Set):
                cardinality = cardinality | _join(*values).dependencies
            return _Value(_join(*values).dependencies, element=element, cardinality=cardinality)
        if isinstance(expression, ast.Starred):
            return self.expr(expression.value, env)
        if isinstance(expression, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
            local = dict(env)
            control = frozenset[str]()
            for generator in expression.generators:
                if generator.is_async:
                    raise SchemaAuditError("async canonical comprehension")
                iterable = self.expr(generator.iter, local)
                control = control | _cardinality(iterable)
                self.assign(generator.target, iterable.element or iterable, local)
                for condition in generator.ifs:
                    control = control | self.expr(condition, local).dependencies
            value = _controlled(self.expr(expression.elt, local), control)
            cardinality = control
            if isinstance(expression, ast.SetComp):
                cardinality = cardinality | value.dependencies
            return _Value(value.dependencies, element=value, cardinality=cardinality)
        if isinstance(expression, ast.Subscript):
            value = self.expr(expression.value, env)
            key = expression.slice
            if (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and value.fields is not None
            ):
                return value.fields.get(key.value, _Value())
            if isinstance(key, ast.Constant) and type(key.value) is int:
                return value.element or value
            if (
                isinstance(key, ast.UnaryOp)
                and isinstance(key.op, ast.USub)
                and isinstance(key.operand, ast.Constant)
            ):
                return value.element or value
            raise SchemaAuditError("dynamic/sliced canonical input")
        if isinstance(expression, ast.Call):
            return self.invoke(expression, env)
        if isinstance(expression, ast.IfExp):
            control_value = self.expr(expression.test, env)
            return _controlled(
                _merge(self.expr(expression.body, env), self.expr(expression.orelse, env)),
                control_value.dependencies,
            )
        if isinstance(expression, (ast.BinOp, ast.BoolOp, ast.Compare, ast.UnaryOp)):
            return _join(
                *(
                    self.expr(child, env)
                    for child in ast.iter_child_nodes(expression)
                    if isinstance(child, ast.expr)
                )
            )
        raise SchemaAuditError(f"unsupported canonical expression {type(expression).__name__}")

    def invoke(self, call: ast.Call, env: dict[str, _Value]) -> _Value:
        args = [self.expr(argument, env) for argument in call.args]
        if any(keyword.arg is None for keyword in call.keywords):
            raise SchemaAuditError("dynamic canonical keyword expansion")
        keywords = {str(keyword.arg): self.expr(keyword.value, env) for keyword in call.keywords}
        inputs = _join(*args, *keywords.values())
        site = f"{self.name}:{self.active[-1]}:{call.lineno}:{call.col_offset}"
        if isinstance(call.func, ast.Name):
            name = call.func.id
            if name in {"_hash", "_reference", "_member", "_record"}:
                if (
                    not call.args
                    or not isinstance(call.args[0], ast.Constant)
                    or not isinstance(call.args[0].value, str)
                ):
                    raise SchemaAuditError("dynamic canonical domain")
                label = f"{self.name}:{name}:{call.args[0].value}"
                return self.node(label, inputs)
            if name == "_digest":
                return self.node(site + ":digest", inputs)
            if name in LEAF_AST[self.name]:
                return inputs
            if name in self.functions:
                return self.call(name, args, keywords)
            if name in MODELS:
                if args:
                    raise SchemaAuditError("positional model construction")
                value = _object(keywords)
                if name in {"IntervalParentPrimitive", "ScheduledBatchPrimitiveDomainV1"}:
                    node = self.node("primitive:" + name, value)
                    return _Value(node.dependencies, fields=value.fields)
                return value
            if name in EXTERNAL:
                return inputs
            if name in BUILTINS:
                if name == "set" and args:
                    value = args[0]
                    return _Value(
                        value.dependencies, value.fields, value.element, value.dependencies
                    )
                if name in {"tuple", "list", "set"} and args:
                    return args[0]
                return inputs
            raise SchemaAuditError(f"unknown canonical call {name}")
        if not isinstance(call.func, ast.Attribute):
            raise SchemaAuditError("indirect canonical call")
        method = call.func.attr
        receiver = self.expr(call.func.value, env)
        if method == "model_copy":
            if args or set(keywords) - {"update"}:
                raise SchemaAuditError("unmodeled model_copy option")
            update = keywords.get("update", _object({}))
            if receiver.fields is None or update.fields is None:
                return _join(receiver, update)
            return _object({**receiver.fields, **update.fields})
        if method == "model_dump":
            if args or set(keywords) - {"mode", "exclude"}:
                raise SchemaAuditError("unmodeled model_dump option")
            exclude = next(
                (keyword.value for keyword in call.keywords if keyword.arg == "exclude"), None
            )
            if exclude is not None:
                if not isinstance(exclude, ast.Set) or any(
                    not isinstance(item, ast.Constant) or not isinstance(item.value, str)
                    for item in exclude.elts
                ):
                    raise SchemaAuditError("dynamic canonical exclusion")
                if receiver.fields is None:
                    raise SchemaAuditError("opaque field exclusion")
                names = {str(item.value) for item in exclude.elts if isinstance(item, ast.Constant)}
                return _object(
                    {key: value for key, value in receiver.fields.items() if key not in names}
                )
            return receiver
        if (
            method == "model_validate"
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id in MODELS
        ):
            return args[0] if len(args) == 1 else inputs
        if method in {"canonical_bytes", "encode", "decode", "hexdigest", "b64encode"}:
            return _join(receiver, inputs)
        if method in {"digest", "sha256"}:
            return self.node(site + ":" + method, _join(receiver, inputs))
        raise SchemaAuditError(f"unknown canonical method {method}")


def derive_schema_manifest(materialization: bytes, rollover: bytes) -> SchemaManifest:
    graphs: dict[str, set[str]] = {}
    sources = (("materialization", materialization), ("rollover", rollover))
    for name, source in sources:
        analyzer = _Analyzer(name, ast.parse(source))
        roots = (
            ("prepare_interval", "prepare_resolution", "prepare_streamed_overflow")
            if name == "materialization"
            else ("prepare_rollover",)
        )
        for root in roots:
            if root not in analyzer.functions:
                raise SchemaAuditError("missing canonical entrypoint")
            analyzer.call(root, [], {})
        for node, dependencies in analyzer.graph.items():
            graphs.setdefault(node, set()).update(dependencies)
    active: set[str] = set()
    complete: set[str] = set()

    def visit(node: str) -> None:
        if node not in graphs or node in active:
            raise SchemaAuditError("unknown or cyclic generated canonical dependency")
        if node in complete:
            return
        active.add(node)
        for dependency in graphs[node]:
            visit(dependency)
        active.remove(node)
        complete.add(node)

    for node in graphs:
        visit(node)
    allowed_batch_inputs = {
        "primitive:IntervalParentPrimitive",
        "materialization:_reference:scheduler-parent-v1",
        "materialization:_hash:scheduled-run-v1",
        "materialization:_hash:execution-lineage-v1",
        "primitive:ScheduledBatchPrimitiveDomainV1",
    }

    def ancestors(node: str) -> set[str]:
        result: set[str] = set()
        for dependency in graphs[node]:
            result.add(dependency)
            result.update(ancestors(dependency))
        return result

    for primitive in ("primitive:IntervalParentPrimitive", "primitive:Rollover"):
        if primitive not in graphs or graphs[primitive]:
            raise SchemaAuditError("parent/rollover primitive includes derived canonical output")
    for primitive in (
        "primitive:ScheduledBatchPrimitiveDomainV1",
        "materialization:_reference:scheduled-batch-primitive-v1",
    ):
        if primitive not in graphs or not ancestors(primitive).issubset(allowed_batch_inputs):
            raise SchemaAuditError("batch primitive includes forbidden derived canonical output")
    return SchemaManifest(
        tuple((name, hashlib.sha256(source).hexdigest()) for name, source in sources),
        tuple((node, tuple(sorted(dependencies))) for node, dependencies in sorted(graphs.items())),
    )


def require_schema_manifest(
    registered: SchemaManifest, materialization: bytes, rollover: bytes
) -> None:
    if registered != derive_schema_manifest(materialization, rollover):
        raise SchemaAuditError("registered schema manifest differs from actual canonical source")
