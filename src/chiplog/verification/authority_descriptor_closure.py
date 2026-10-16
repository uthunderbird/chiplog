"""Whole-tree partial evidence join, never a registered descriptor or admission token.

Every call/reference is retained. Conditional local chains do not close their
callees, imports, mutation or assembly callbacks. Source rechecks prove equality
at observations, not an authenticated shipment list or continuous race protection.
"""

import ast
import stat
from dataclasses import dataclass
from pathlib import Path

from chiplog.verification.assembly_forwarding import AssemblyEdge
from chiplog.verification.authority_sql_corpus import CorpusNode
from chiplog.verification.planning_descriptor_chain import (
    PlanningDescriptorFragment,
    UnknownPlanningChain,
    build_planning_descriptor_fragment,
    verify_planning_descriptor_fragment,
)
from chiplog.verification.protected_anchor_chain import (
    ProtectedAnchorChain,
    build_protected_anchor_chains,
)


class DescriptorClosureRefused(ValueError):
    pass


class DescriptorClosureIncomplete(ValueError):
    pass


class SourceTreeChanged(ValueError):
    pass


_CALL_KINDS = frozenset({
    "sql_call_candidate", "factory_or_wrapper_call_candidate", "mount_call_candidate",
    "reflection_call", "dynamic_call", "other_call", "callable_reference_candidate",
})

# Independently declared base-R7 READ dependencies, not generated from live SQL.
_PUBLICATION = tuple(("main", "publications", column) for column in (
    "commit_sequence", "idempotency_key", "operation_kind", "record_ids",
    "request_fingerprint", "tenant_id",
))
_SELECTION = tuple(("main", "publications", column) for column in (
    "commit_sequence", "idempotency_key", "operation_kind", "tenant_id",
))
_RECORD = tuple(("main", "records", column) for column in (
    "canonical_bytes", "commit_sequence", "owner", "record_id", "schema_id", "tenant_id",
))
_BASE_READS = (
    (1, 0, (("main", "publications", "commit_sequence"), ("main", "publications", "tenant_id"))),
    (2, 0, _PUBLICATION), (2, 1, _PUBLICATION),
    (3, 0, (*_SELECTION, *_RECORD)), (3, 1, (*_SELECTION, *_RECORD)),
)


@dataclass(frozen=True)
class CallDisposition:
    site: CorpusNode
    disposition: str
    role: str
    domain: str | None
    details: tuple[str, ...]


@dataclass(frozen=True)
class AssemblyCall:
    site: CorpusNode
    edge: AssemblyEdge


@dataclass(frozen=True)
class AuthorityDescriptorInspection:
    sources: tuple[tuple[str, str], ...]
    fragment: PlanningDescriptorFragment
    calls: tuple[CallDisposition, ...]
    mounts: tuple[AssemblyCall, ...]
    protected_anchor_chains: tuple[ProtectedAnchorChain, ...]

    def require_exact_accounting(self) -> None:
        # Recompute from bytes: mutually forged fragment and rows are not truth.
        if self != _from_sources(self.sources):
            raise DescriptorClosureRefused(
                "global evidence differs from canonical source derivation"
            )

    def recheck_source_tree(self, root: Path) -> None:
        if _read_sources(root) != self.sources:
            raise SourceTreeChanged("complete Python source path/byte snapshot changed")

    def require_closed(self) -> None:
        # Even a forged empty unknown list must never become permission.
        raise DescriptorClosureIncomplete(
            "global receiver/domain/assembly and registered shipment closure, "
            "engine policy/graph, physical schema/AMR, descriptor/tuple and CURRENT are incomplete"
        )


def _read_sources(root: Path) -> tuple[tuple[str, str], ...]:
    """Explicit directory traversal refuses symlink subtrees instead of skipping them."""
    try:
        if not root.is_absolute() or root.resolve(strict=True) != root:
            raise SourceTreeChanged("source root and ancestors must be canonical and unaliased")
        package = root / "src" / "chiplog"
        for path in (root / "src", package):
            if not stat.S_ISDIR(path.lstat().st_mode):
                raise SourceTreeChanged("source ancestors must be real directories")
        sources = []

        def visit(directory: Path) -> None:
            for path in sorted(directory.iterdir()):
                metadata = path.lstat()
                if stat.S_ISDIR(metadata.st_mode):
                    visit(path)
                elif not stat.S_ISREG(metadata.st_mode):
                    raise SourceTreeChanged("nonregular or symlink source path: " + str(path))
                elif path.suffix == ".py":
                    sources.append((path.relative_to(root).as_posix(), path.read_bytes().decode()))

        visit(package)
        if not sources:
            raise SourceTreeChanged("Python source tree is empty")
        return tuple(sorted(sources))
    except (OSError, UnicodeError) as error:
        raise SourceTreeChanged("Python source tree is unreadable or changed") from error


def _assembly_calls(fragment: PlanningDescriptorFragment) -> tuple[AssemblyCall, ...]:
    candidates = tuple(n for n in fragment.corpus.nodes if n.kind == "mount_call_candidate")
    result = []
    for edge in (fragment.assembly, *fragment.other_mounts):
        sites = [n for n in candidates if n.module == "src/" + edge.module
                 and n.qualname == edge.caller]
        if not 1 <= edge.ordinal <= len(sites):
            raise DescriptorClosureRefused("assembly edge lacks its exact corpus occurrence")
        site = sites[edge.ordinal - 1]
        call = ast.parse(site.expression, mode="eval").body
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or (
            call.func.id != edge.callee.rsplit(":", 1)[-1]
        ):
            raise DescriptorClosureRefused("assembly edge and corpus callee differ")
        result.append(AssemblyCall(site, edge))
    by_site = {row.site: row for row in result}
    if len(by_site) != len(result) or set(by_site) != set(candidates):
        raise DescriptorClosureRefused("assembly and corpus mount accounting differs")
    return tuple(by_site[site] for site in candidates)


def _classifications(
    fragment: PlanningDescriptorFragment, mounts: tuple[AssemblyCall, ...],
    anchors: tuple[ProtectedAnchorChain, ...],
) -> tuple[CallDisposition, ...]:
    local: dict[CorpusNode, tuple[str, str | None, list[str]]] = {}

    def add(site: CorpusNode, role: str, domain: str | None, detail: str) -> None:
        if site not in fragment.corpus.nodes:
            raise DescriptorClosureRefused("local evidence refers to a foreign corpus node")
        previous = local.setdefault(site, (role, domain, []))
        if previous[:2] != (role, domain):
            raise DescriptorClosureRefused("one occurrence has incompatible local roles")
        if detail not in previous[2]:
            previous[2].append(detail)

    for row in fragment.rows:
        add(row.site, "PLANNING_READ", "tenant_main",
            f"base R7 only; statement={row.statement}/alternative={row.alternative}")
    add(fragment.wrapper_site, "PLANNING_FORWARD", None, "base R7 reader field forwarding only")
    for chain in fragment.preparation_chains:
        detail = chain.entrypoint + "/" + chain.role
        add(chain.factory, "PREPARATION_FACTORY", "private_memory", detail)
        add(chain.sink, "PREPARATION_SQL", "private_memory", detail)
        if chain.forward is not None:
            add(chain.forward, "PREPARATION_FORWARD", "private_memory", detail)
    for scanner in fragment.scanner_chains:
        add(scanner.factory, "READER_FACTORY", "tenant_main", scanner.role)
        add(scanner.sink, "READER_SCANNER", "tenant_main", scanner.role)
        for forward in scanner.forwards:
            add(forward, "SCANNER_FORWARD", "tenant_main", scanner.role)
    for ledger in fragment.ledger_chains:
        add(ledger.origin, "LEDGER_ORIGIN", "broker_read_ledger", "selected base runtime only")
        add(ledger.factory, "LEDGER_FACTORY", "broker_read_ledger", ledger.role)
        add(ledger.sink, "LEDGER_SQL", "broker_read_ledger", ledger.role)
        for forward in ledger.forwards:
            add(forward, "LEDGER_FORWARD", "broker_read_ledger", ledger.role)
    for mount in mounts:
        if mount.edge == fragment.assembly:
            add(mount.site, "BASE_ASSEMBLY", None, "base runtime/manifest forwarding only")
    for preparation in fragment.runtime_preparation_chains:
        add(preparation.sink, "RUNTIME_PREPARATION_SQL", preparation.database_domain,
            "selected planning caller: " + preparation.caller.expression)
    for anchor in anchors:
        for stage in anchor.stages:
            # Shared consumer occurrences retain their existing narrower role.
            if stage.site in local:
                role, domain, _ = local[stage.site]
                add(stage.site, role, domain, "protected anchor chain: " + stage.role)
            else:
                role = ("PLANNING_EXECUTION_FORWARD" if stage.role == "PLANNING_READ"
                        else stage.role)
                add(stage.site, role, stage.domain, "selected base runtime only")
        for site in anchor.publication_sql:
            add(site, "STATE_PUBLICATION_SQL", "broker_read_ledger", "protected anchor chain")
    result = []
    for site in fragment.corpus.nodes:
        if site.kind not in _CALL_KINDS:
            continue
        if site in local:
            role, domain, details = local[site]
            result.append(CallDisposition(site, "CONDITIONAL_LOCAL", role, domain, tuple(details)))
        else:
            result.append(CallDisposition(site, "UNRESOLVED", site.kind, None,
                ("callee/receiver/domain effects require closure; lexical SQL is not proof",)))
    return tuple(result)


def _from_sources(sources: tuple[tuple[str, str], ...]) -> AuthorityDescriptorInspection:
    """Canonical derivation for inspection; supplied bytes are never shipment authority."""
    try:
        sources = tuple(sorted(sources))
        fragment = build_planning_descriptor_fragment(sources)
        verify_planning_descriptor_fragment(fragment, _BASE_READS)
        mounts = _assembly_calls(fragment)
        anchors = build_protected_anchor_chains(sources, fragment)
        calls = _classifications(fragment, mounts, anchors)
        return AuthorityDescriptorInspection(sources, fragment, calls, mounts, anchors)
    except UnknownPlanningChain as error:
        raise DescriptorClosureRefused("local descriptor proof refused: " + str(error)) from error


def inspect_authority_descriptor_closure(root: Path) -> AuthorityDescriptorInspection:
    result = _from_sources(_read_sources(root))
    result.recheck_source_tree(root)
    return result
