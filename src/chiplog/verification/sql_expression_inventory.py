"""Snapshot-relative SQL expression evidence, not callable/domain admission."""

import ast
from dataclasses import dataclass
from itertools import product
from typing import cast

from chiplog.verification.authority_read_graph import (
    UnknownSqlExpression,
    planning_sql_alternatives,
)
from chiplog.verification.authority_sql_corpus import (
    CorpusNode,
    CurrentSqlCorpus,
    _inspect_sources_with_occurrences,
)
from chiplog.verification.constant_sql_scripts import (
    UnknownConstantSql,
    extract_constant_sql_scripts,
)
from chiplog.verification.identifier_sql_families import (
    _TARGETS,
    UnknownIdentifierSql,
    extract_current_identifier_sql,
)
from chiplog.verification.placeholder_sql_families import (
    UnknownPlaceholderSql,
    _method,
    extract_placeholder_sql_families,
)


class SqlExpressionSnapshotChanged(ValueError):
    pass


class SqlExpressionAccountingError(ValueError):
    pass


@dataclass(frozen=True)
class SqlExpressionAlternative:
    label: str
    sql: str


@dataclass(frozen=True)
class SqlExpressionResolution:
    site: CorpusNode
    kind: str
    alternatives: tuple[SqlExpressionAlternative, ...]
    source_digests: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class UnresolvedSqlExpression:
    site: CorpusNode
    reason: str


@dataclass(frozen=True)
class SqlExpressionInventory:
    corpus: CurrentSqlCorpus
    resolved: tuple[SqlExpressionResolution, ...]
    unresolved: tuple[UnresolvedSqlExpression, ...]

    def require_exact_accounting(self) -> None:
        """Check internal occurrence/dependency accounting, not SQL truth or admission."""
        candidates = tuple(node for node in self.corpus.nodes if node.kind == "sql_call_candidate")
        expected = {_identity(node): node for node in candidates}
        ranks = {_identity(node): index for index, node in enumerate(candidates)}
        if len(expected) != len(candidates):
            raise SqlExpressionAccountingError("duplicate corpus candidates")
        seen = set()
        digests = dict(self.corpus.source_digests)
        for rows in (self.resolved, self.unresolved):
            order = []
            for row in rows:
                key = _identity(row.site)
                if key in seen or expected.get(key) != row.site:
                    raise SqlExpressionAccountingError(
                        "duplicate, foreign or altered expression site"
                    )
                seen.add(key)
                order.append(ranks[key])
                if isinstance(row, UnresolvedSqlExpression):
                    if not isinstance(row.reason, str) or not row.reason.strip():
                        raise SqlExpressionAccountingError("unresolved expression lacks a reason")
                    continue
                if row.kind not in {"FINITE_SYNTAX", "PLACEHOLDER_REPRESENTATIVES"} or (
                    not row.alternatives
                    or len({item.label for item in row.alternatives}) != len(row.alternatives)
                    or any(
                        not item.label or not isinstance(item.sql, str) for item in row.alternatives
                    )
                ):
                    raise SqlExpressionAccountingError(
                        "missing or malformed expression alternatives"
                    )
                dependencies = dict(row.source_digests)
                if (
                    row.site.module not in dependencies
                    or len(dependencies) != len(row.source_digests)
                    or tuple(sorted(dependencies.items())) != row.source_digests
                    or any(digests.get(name) != digest for name, digest in dependencies.items())
                ):
                    raise SqlExpressionAccountingError("expression dependency snapshot differs")
            if order != sorted(order):
                raise SqlExpressionAccountingError("expression rows are not in corpus order")
        if seen != set(expected):
            raise SqlExpressionAccountingError("missing expression sites")


Payload = tuple[str, tuple[SqlExpressionAlternative, ...], tuple[tuple[str, str], ...]]
Payloads = dict[ast.Call, Payload]
PayloadGroup = str | tuple[str, str] | tuple[str, tuple[str, str]]


def extract_sql_expression_inventory(
    sources: tuple[tuple[str, str], ...], corpus: CurrentSqlCorpus
) -> SqlExpressionInventory:
    """Join syntax evidence for every lexical candidate; unknown callees remain unproved.

    Placeholder n=0/1/2 strings are representative probes, never exhaustive SQL.
    Call argument validity, receiver semantics and global corpus closure are separate.
    """
    canonical, occurrences = _inspect_sources_with_occurrences(sources)
    if canonical != corpus:
        raise SqlExpressionSnapshotChanged("complete source/corpus snapshot differs")
    texts = dict(sources)
    digests = dict(canonical.source_digests)
    trees = {site.module: tree for site, _, tree in occurrences}
    by_call = {call: site for site, call, _ in occurrences}
    cache: dict[PayloadGroup, Payloads] = {}
    resolved, unresolved = [], []
    for site, call, _tree in occurrences:
        try:
            if not call.args:
                raise _UnknownExpression("SQL candidate has no first argument")
            try:
                sqls = _finite_strings(call.args[0])
            except _UnknownExpression as original:
                payloads = _named_payloads(
                    site.module, site.qualname, texts, trees, digests, by_call, cache, call
                )
                if call not in payloads:
                    raise _UnknownExpression(
                        "no supported expression route for this occurrence"
                    ) from original
                kind, alternatives, dependencies = payloads[call]
            else:
                kind = "FINITE_SYNTAX"
                alternatives = tuple(
                    SqlExpressionAlternative(f"branch={index}", sql)
                    for index, sql in enumerate(sqls)
                )
                dependencies = ((site.module, digests[site.module]),)
            resolved.append(SqlExpressionResolution(site, kind, alternatives, dependencies))
        except (
            _UnknownExpression,
            UnknownSqlExpression,
            UnknownPlaceholderSql,
            UnknownConstantSql,
            UnknownIdentifierSql,
        ) as error:
            unresolved.append(UnresolvedSqlExpression(site, f"{type(error).__name__}: {error}"))
    result = SqlExpressionInventory(canonical, tuple(resolved), tuple(unresolved))
    result.require_exact_accounting()
    return result


class _UnknownExpression(ValueError):
    pass


def _identity(node: CorpusNode) -> tuple[str, str, int]:
    return node.module, node.qualname, node.ordinal


def _finite_strings(expression: ast.expr) -> tuple[str, ...]:
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        return (expression.value,)
    if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Add):
        return tuple(
            left + right
            for left, right in product(
                _finite_strings(expression.left),
                _finite_strings(expression.right),
            )
        )
    if isinstance(expression, ast.IfExp):
        return (*_finite_strings(expression.body), *_finite_strings(expression.orelse))
    raise _UnknownExpression("nonliteral SQL expression")


_PREFIX = "src/chiplog/"
_CONSTANTS = {
    ("platform/_sqlite.py", "_expected_schema_manifest"): "SCHEMA_SQL",
    ("platform/_sqlite.py", "SQLiteMaterializer._admit"): "SCHEMA_SQL",
    ("platform/authority_ledger.py", "BrokerAuthorityLedger.__init__"): "_SCHEMA",
    ("platform/read_ledger.py", "BrokerReadLedger.__init__"): "_SCHEMA",
}
_PLACEHOLDERS = {
    ("adapters/driven/planning_sqlite.py", "SQLitePlanningRepository.committed_publications"),
    ("platform/_sqlite.py", "SQLiteMaterializer._register_derivative"),
    ("composition/r14_native_accepted_open_cut.py", "build_accepted_open_recovery_cut"),
    ("composition/r14_original_call_resolution_source.py", "_require_resolution_absent"),
    ("composition/r14_original_call_resolution_publication.py", "_validate_physical_catalog"),
    ("composition/r14_recovery_profile_publication.py", "_assert_v2_precedes_covered_lineage"),
}
_IDENTIFIERS = {(module, qualname) for module, qualname, _, _ in _TARGETS}
_IDENTIFIER_SOURCES = {module for module, _, _, _ in _TARGETS} | {
    "architecture/r7_storage_surface.py",
}


def _calls(tree: ast.Module, qualname: str, *, planning: bool = False) -> list[ast.Call]:
    method = _method(tree, qualname)

    def sql_calls(node: ast.AST) -> list[ast.Call]:
        return sorted(
            (
                call
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr in {"execute", "executemany", "executescript"}
            ),
            key=lambda call: (call.lineno, call.col_offset),
        )

    if planning:
        return [call for statement in method.body for call in sql_calls(statement)]
    return sql_calls(method)


def _attach(
    payloads: Payloads,
    *,
    module: str,
    qualname: str,
    site: int,
    receiver: str,
    alternatives: tuple[SqlExpressionAlternative, ...] | list[SqlExpressionAlternative],
    dependencies: tuple[tuple[str, str], ...],
    trees: dict[str, ast.Module],
    digests: dict[str, str],
    by_call: dict[ast.Call, CorpusNode],
    planning: bool = False,
    kind: str = "FINITE_SYNTAX",
) -> None:
    calls = _calls(trees[module], qualname, planning=planning)
    if site < 1 or site > len(calls):
        raise _UnknownExpression("family ordinal has no AST occurrence")
    call = calls[site - 1]
    actual = by_call.get(call)
    if (
        actual is None
        or actual.qualname != qualname
        or (ast.unparse(cast(ast.Attribute, call.func).value) != receiver)
        or call in payloads
    ):
        raise _UnknownExpression("family occurrence, scope or receiver differs")
    if any(digests.get(name) != digest for name, digest in dependencies):
        raise _UnknownExpression("family producer source digest differs")
    payloads[call] = kind, tuple(alternatives), tuple(sorted(dependencies))


def _named_payloads(
    module: str,
    qualname: str,
    texts: dict[str, str],
    trees: dict[str, ast.Module],
    digests: dict[str, str],
    by_call: dict[ast.Call, CorpusNode],
    cache: dict[PayloadGroup, Payloads],
    call: ast.Call,
) -> Payloads:
    if not module.startswith(_PREFIX):
        return {}
    key = module.removeprefix(_PREFIX), qualname
    terminal = call.func.attr if isinstance(call.func, ast.Attribute) else None
    constant = key in _CONSTANTS and terminal == "executescript"
    group = ("constant", key) if constant else ("identifiers" if key in _IDENTIFIERS else key)
    if group in cache:
        return cache[group]
    payloads: Payloads = {}

    def attach(
        site: int,
        receiver: str,
        alternatives: tuple[SqlExpressionAlternative, ...] | list[SqlExpressionAlternative],
        dependencies: tuple[tuple[str, str], ...],
        *,
        target_module: str = module,
        target_qualname: str = qualname,
        planning: bool = False,
        kind: str = "FINITE_SYNTAX",
    ) -> None:
        _attach(
            payloads,
            module=target_module,
            qualname=target_qualname,
            site=site,
            receiver=receiver,
            alternatives=alternatives,
            dependencies=dependencies,
            trees=trees,
            digests=digests,
            by_call=by_call,
            planning=planning,
            kind=kind,
        )

    if constant:
        for constant_item in extract_constant_sql_scripts(texts[module], qualname, _CONSTANTS[key]):
            alternatives = (SqlExpressionAlternative("literal-script", constant_item.sql),)
            attach(
                constant_item.site,
                constant_item.receiver,
                alternatives,
                ((module, constant_item.source_sha256),),
            )
    elif key in _PLACEHOLDERS:
        for placeholder_item in extract_placeholder_sql_families(texts[module], qualname):
            attach(
                placeholder_item.site,
                placeholder_item.receiver,
                tuple(
                    SqlExpressionAlternative(
                        f"cardinality={number};representative", placeholder_item.sql_for(number)
                    )
                    for number in (0, 1, 2)
                ),
                ((module, placeholder_item.source_sha256),),
                kind="PLACEHOLDER_REPRESENTATIVES",
            )
    elif key in _IDENTIFIERS:
        if not all(_PREFIX + name in texts for name in _IDENTIFIER_SOURCES):
            raise _UnknownExpression("missing identifier producer/consumer sources")
        inputs = tuple((name, texts[_PREFIX + name]) for name in sorted(_IDENTIFIER_SOURCES))
        for identifier_item in extract_current_identifier_sql(inputs):
            attach(
                identifier_item.site,
                identifier_item.receiver,
                tuple(
                    SqlExpressionAlternative(repr(alternative.bindings), alternative.sql)
                    for alternative in identifier_item.alternatives
                ),
                tuple((_PREFIX + name, digest) for name, digest in identifier_item.source_digests),
                target_module=_PREFIX + identifier_item.module,
                target_qualname=identifier_item.qualname,
            )
    elif key == ("platform/authority_reads.py", "BrokerAuthorityReader._execute_planning_read"):
        grouped: dict[int, list[SqlExpressionAlternative]] = {}
        for planning_item in planning_sql_alternatives(texts[module]):
            grouped.setdefault(planning_item.site, []).append(
                SqlExpressionAlternative(f"branch={planning_item.alternative}", planning_item.sql)
            )
        for site, grouped_alternatives in grouped.items():
            attach(
                site,
                "connection",
                grouped_alternatives,
                ((module, digests[module]),),
                planning=True,
            )
    cache[group] = payloads
    return payloads
