"""Global inspection accounts for unknowns without turning local proofs into admission."""

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.verification.authority_descriptor_closure import (
    AuthorityDescriptorInspection,
    DescriptorClosureIncomplete,
    DescriptorClosureRefused,
    SourceTreeChanged,
    _from_sources,
    inspect_authority_descriptor_closure,
)

ROOT = Path(__file__).resolve().parents[2]
PATHS = (
    "src/chiplog/composition/r7_planning.py",
    "src/chiplog/platform/authority_reads.py",
    "src/chiplog/platform/_sqlite.py",
    "src/chiplog/platform/r7_leaves.py",
    "src/chiplog/verification/sqlite_preparation.py",
    "src/chiplog/architecture/r7_storage_surface.py",
    "src/chiplog/composition/r12.py",
    "src/chiplog/platform/read_ledger.py",
)
CALL_KINDS = {
    "sql_call_candidate",
    "factory_or_wrapper_call_candidate",
    "mount_call_candidate",
    "reflection_call",
    "dynamic_call",
    "other_call",
    "callable_reference_candidate",
}


def _anchor_mutation(
    actual: AuthorityDescriptorInspection, old: str, new: str
) -> tuple[tuple[str, str], ...]:
    """Change one real selected origin, leaving the complete corpus in place."""
    path = (
        "src/chiplog/composition/r7_planning.py"
        if "AuthorityCommitmentJournal(" in old
        else "src/chiplog/platform/authority_reads.py"
    )
    text = dict(actual.sources)[path]
    assert text.count(old) == 1
    changed = text.replace(old, new)
    assert changed != text
    return tuple((name, changed if name == path else source) for name, source in actual.sources)


def test_base_read_authority_graph_contains_protected_anchor_to_release_chain(
    actual: AuthorityDescriptorInspection,
) -> None:
    chains = getattr(actual, "protected_anchor_chains", ())
    assert chains, "missing protected authenticated anchor-to-release chain"
    assert len(chains) == 1
    chain = chains[0]
    assert chain.domain == "protected_commitment_anchor"
    assert tuple(stage.role for stage in chain.stages) == (
        "ANCHOR_ORIGIN",
        "RUNTIME_FORWARD",
        "ANCHOR_LOAD",
        "DESCRIPTOR_OPEN",
        "DESCRIPTOR_STREAM",
        "IDENTITY_BEFORE",
        "ANCHOR_BYTES",
        "IDENTITY_AFTER",
        "ENVELOPE",
        "AUTHENTICATION",
        "TENANT_BINDING",
        "ANCHOR_COMPARISON",
        "STATE_PUBLICATION",
        "STATE_CONSUMPTION",
        "SNAPSHOT_COMPARISON",
        "PLANNING_READ",
        "RESULT_RELEASE",
        "RESULT_DEQUEUE",
    )
    assert chain.ledger_chains == actual.fragment.ledger_chains
    assert chain.scanner_chains == actual.fragment.scanner_chains
    assert chain.runtime_preparation_chains == actual.fragment.runtime_preparation_chains
    (preparation,) = [row for row in actual.calls if row.role == "RUNTIME_PREPARATION_SQL"]
    assert preparation.site.module == "src/chiplog/platform/authority_reads.py"
    assert preparation.site.qualname == "_prepared_planning_statement"
    assert preparation.site.expression == "connection.execute('EXPLAIN ' + sql, parameters)"
    assert preparation.domain == "tenant_main"
    assert preparation.details == (
        "selected planning caller: connection.execute(uniqueness_sql, uniqueness_parameters)",
        "selected planning caller: connection.execute(publication_sql, publication_parameters)",
        "selected planning caller: connection.execute(record_sql, record_parameters)",
    )
    actual.require_exact_accounting()


def test_anchor_domain_substitution_refuses_before_graph_admission(
    actual: AuthorityDescriptorInspection,
) -> None:
    changed = _anchor_mutation(
        actual,
        "commitment_journal = AuthorityCommitmentJournal(\n"
        "                database, operator_secret, authority_gate=authority_gate\n",
        "commitment_journal = AuthorityCommitmentJournal(\n"
        "                database.with_suffix(database.suffix + '.reads.sqlite3'), "
        "operator_secret, authority_gate=authority_gate\n",
    )
    with pytest.raises(DescriptorClosureRefused, match="unexpected database role"):
        _from_sources(changed)


def test_anchor_selected_file_domain_substitution_refuses(
    actual: AuthorityDescriptorInspection,
) -> None:
    changed = _anchor_mutation(
        actual,
        'self._path = database.with_suffix(database.suffix + ".r7-authority-commitment.json")',
        'self._path = database.with_suffix(database.suffix + ".reads.sqlite3")',
    )
    with pytest.raises(DescriptorClosureRefused, match=r"anchor.*path|anchor.*domain"):
        _from_sources(changed)


def test_anchor_handle_escape_refuses_before_graph_admission(
    actual: AuthorityDescriptorInspection,
) -> None:
    changed = _anchor_mutation(
        actual,
        'with os.fdopen(descriptor, "rb") as stream:\n',
        'foreign(descriptor)\n        with os.fdopen(descriptor, "rb") as stream:\n',
    )
    with pytest.raises(DescriptorClosureRefused, match=r"anchor.*descriptor|anchor.*escape"):
        _from_sources(changed)


@pytest.mark.parametrize(
    ("path", "old", "new"),
    [
        (
            "src/chiplog/platform/authority_reads.py",
            'raise RuntimeError("authority commitment journal changed before read")',
            'raise RuntimeError("authority commitment journal changed before read") '
            "from foreign(descriptor)",
        ),
        (
            "src/chiplog/composition/r7_planning.py",
            'record_id = "unknown"\n',
            'foreign(self._commitment_journal)\n            record_id = "unknown"\n',
        ),
        (
            "src/chiplog/platform/authority_reads.py",
            "raise AuthorityReadFailure(\n"
            '                    "full raw AMR commitment differs from authenticated state"\n'
            "                )",
            "raise foreign(self._ledger)",
        ),
    ],
)
def test_selected_anchor_carriers_cannot_escape_in_failure_paths(
    actual: AuthorityDescriptorInspection,
    path: str,
    old: str,
    new: str,
) -> None:
    text = dict(actual.sources)[path]
    assert text.count(old) == 1
    changed = tuple((p, s.replace(old, new) if p == path else s) for p, s in actual.sources)
    with pytest.raises(DescriptorClosureRefused):
        _from_sources(changed)


@pytest.mark.parametrize(
    ("path", "old", "new"),
    [
        (
            "src/chiplog/platform/authority_reads.py",
            "class AuthorityCommitmentJournal:\n",
            "class AuthorityCommitmentJournal:\n"
            "    def __getattribute__(self, name):\n"
            "        return foreign(self, name)\n\n",
        ),
        (
            "src/chiplog/platform/read_ledger.py",
            '            raise ReadLedgerConflict("au'
            'thority read state is absent")\n        r'
            "eturn state",
            '            raise ReadLedgerConflict("authority read state is absent")\n'
            "        return foreign()",
        ),
    ],
)
def test_selected_anchor_object_model_and_persisted_state_forwarding_refuse(
    actual: AuthorityDescriptorInspection,
    path: str,
    old: str,
    new: str,
) -> None:
    text = dict(actual.sources)[path]
    assert text.count(old) == 1
    changed = tuple((p, s.replace(old, new) if p == path else s) for p, s in actual.sources)
    with pytest.raises(DescriptorClosureRefused):
        _from_sources(changed)


@pytest.mark.parametrize(
    ("path", "scope", "old", "new"),
    [
        (
            PATHS[1],
            "AuthorityCommitmentJournal._load",
            "hmac.new(self._secret, payload, hashlib.sha256)",
            'hmac.new(b"foreign", payload, hashlib.sha256)',
        ),
        (
            PATHS[1],
            "AuthorityCommitmentJournal._load",
            'not hmac.compare_digest(envelope["mac"], expected)',
            "False",
        ),
        (
            PATHS[1],
            "AuthorityCommitmentJournal._load",
            'value["tenant_id"] != tenant_id',
            'value["tenant_id"] == tenant_id',
        ),
        (
            PATHS[1],
            "AuthorityCommitmentJournal._load",
            "object_pairs_hook=self._unique_object",
            "object_pairs_hook=dict",
        ),
        (
            PATHS[1],
            "AuthorityCommitmentJournal._load",
            'return str(value["commitment"])',
            'return str(envelope["mac"])',
        ),
        (
            PATHS[1],
            "AuthorityCommitmentJournal._check_descriptor",
            "or named.st_nlink != 1",
            "or named.st_nlink != 2",
        ),
        (
            PATHS[1],
            "AuthorityCommitmentJournal._load",
            "raw = stream.read()",
            "raw = foreign(stream)",
        ),
        (
            PATHS[1],
            "AuthorityCommitmentJournal.load",
            "return self._load(tenant_id)",
            'return self._load("foreign")',
        ),
        (
            PATHS[0],
            "R7PlanningRuntime.__init__",
            "self._commitment_journal = commitment_journal",
            "self._commitment_journal = read_ledger",
        ),
        (
            PATHS[0],
            "_open_runtime",
            "                commitment_journal,\n",
            "                read_ledger,\n",
        ),
        (
            PATHS[0],
            "R7PlanningRuntime._locked_reconcile_read_generation",
            "not secrets.compare_digest(authenticated_commitment, commitment)",
            "False",
        ),
        (
            PATHS[0],
            "R7PlanningRuntime._locked_reconcile_read_generation",
            "materialization_commitment=authenticated_commitment",
            "materialization_commitment=commitment",
        ),
        (
            PATHS[0],
            "R7PlanningRuntime._locked_reconcile_read_generation",
            "if populated:",
            "if False:",
        ),
        (
            PATHS[0],
            "R7PlanningRuntime._locked_reconcile_read_generation",
            'record_id = "unknown"',
            "self._commitment_journal.commit(self._tenant_id, commitment)\n"
            '            record_id = "unknown"',
        ),
        (
            PATHS[0],
            "R7PlanningRuntime._locked_reconcile_read_generation",
            'record_id = "unknown"',
            'foreign(self._read_ledger)\n            record_id = "unknown"',
        ),
        (PATHS[7], "BrokerReadLedger.reconcile_generation", "state.canonical_bytes()", "foreign()"),
        (
            PATHS[7],
            "BrokerReadLedger.current_state_or_none",
            "if state.fingerprint() != str(row[1]):",
            "if False:",
        ),
        (
            PATHS[7],
            "BrokerReadLedger.current_state_or_none",
            '"""Absence before first bootstrap is distinct from corrupt durable state."""',
            "foreign(self._path)",
        ),
        (
            PATHS[7],
            "BrokerReadLedger.current_state_or_none",
            '"""Absence before first bootstrap is distinct from corrupt durable state."""',
            "self._path",
        ),
        (
            PATHS[1],
            "BrokerAuthorityReader.execute",
            "observed_schema = _schema_members(connection)",
            "observed_schema = _schema_members(connection)\n                return foreign()",
        ),
        (
            PATHS[1],
            "BrokerAuthorityReader.execute",
            "observed_schema = _schema_members(connection)",
            "observed_schema = _schema_members(connection)\n                return None",
        ),
        (
            PATHS[1],
            "BrokerAuthorityReader.execute",
            "commitment != state.materialization_commitment",
            "False",
        ),
        (
            PATHS[1],
            "BrokerAuthorityReader.execute",
            "self._ledger.release(operation, state, proof_fingerprint, result_bytes)",
            'self._ledger.release(operation, state, proof_fingerprint, b"foreign")',
        ),
        (
            PATHS[1],
            "BrokerAuthorityReader.execute",
            "canonical_result_bytes=emitted",
            'canonical_result_bytes=b"foreign"',
        ),
    ],
)
def test_anchor_security_guards_and_exact_state_edges_refuse(
    path: str,
    scope: str,
    old: str,
    new: str,
) -> None:
    selected = dict(sources())
    text = selected[path]
    node: ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef = ast.parse(text)
    for part in scope.split("."):
        matches = [
            n
            for n in node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and n.name == part
        ]
        assert len(matches) == 1
        node = matches[0]
    lines = text.splitlines(keepends=True)
    assert not isinstance(node, ast.Module) and node.end_lineno is not None
    begin, end = node.lineno - 1, node.end_lineno
    target = "".join(lines[begin:end])
    assert target.count(old) == 1
    changed = target.replace(old, new)
    assert changed != target
    selected[path] = "".join(lines[:begin]) + changed + "".join(lines[end:])
    with pytest.raises(DescriptorClosureRefused):
        _from_sources(tuple(selected.items()))


@pytest.mark.parametrize("mutation", ["omit", "duplicate", "domain", "stage", "ledger", "sql"])
def test_anchor_chain_cannot_certify_forged_evidence(mutation: str) -> None:
    result = _from_sources(sources())
    (chain,) = result.protected_anchor_chains
    chains = result.protected_anchor_chains
    if mutation == "omit":
        chains = ()
    elif mutation == "duplicate":
        chains = (chain, chain)
    else:
        if mutation == "domain":
            chain = replace(chain, domain="broker_read_ledger")
        elif mutation == "stage":
            chain = replace(chain, stages=tuple(reversed(chain.stages)))
        elif mutation == "ledger":
            chain = replace(chain, ledger_chains=chain.ledger_chains[:-1])
        else:
            chain = replace(chain, publication_sql=chain.publication_sql[:-1])
        chains = (chain,)
    with pytest.raises(DescriptorClosureRefused):
        replace(result, protected_anchor_chains=chains).require_exact_accounting()


def sources() -> tuple[tuple[str, str], ...]:
    return tuple((name, (ROOT / name).read_text()) for name in PATHS)


@pytest.fixture(scope="module")
def actual() -> AuthorityDescriptorInspection:
    return inspect_authority_descriptor_closure(ROOT)


def test_actual_tree_joins_every_call_and_mount_but_cannot_grant_admission(actual):
    assert tuple(row.site for row in actual.calls) == tuple(
        node for node in actual.fragment.corpus.nodes if node.kind in CALL_KINDS
    )
    assert actual.fragment.expressions.corpus == actual.fragment.corpus
    assert {row.site for row in actual.calls if row.site.kind == "mount_call_candidate"} == {
        row.site for row in actual.mounts
    }
    assert any(row.disposition == "UNRESOLVED" for row in actual.calls)
    assert {row.domain for row in actual.calls if row.disposition == "CONDITIONAL_LOCAL"} >= {
        "tenant_main",
        "private_memory",
    }
    assert {row.site for row in actual.calls if row.role == "PLANNING_READ"} == {
        row.site for row in actual.fragment.rows
    }
    assert {row.site for row in actual.calls if row.role == "PREPARATION_SQL"} == {
        row.sink for row in actual.fragment.preparation_chains
    }
    assert {row.site for row in actual.calls if row.role == "READER_SCANNER"} == {
        row.sink for row in actual.fragment.scanner_chains
    }
    ledger_rows = [row for row in actual.calls if row.role == "LEDGER_SQL"]
    assert {row.site for row in ledger_rows} == {row.sink for row in actual.fragment.ledger_chains}
    assert len(ledger_rows) == 11
    assert {row.domain for row in ledger_rows} == {"broker_read_ledger"}
    actual.recheck_source_tree(ROOT)
    with pytest.raises(DescriptorClosureIncomplete):
        actual.require_closed()


@pytest.mark.parametrize(
    "mutation",
    [
        "omit",
        "duplicate",
        "ghost",
        "domain",
        "promote",
        "fragment",
    ],
)
def test_accounting_rederives_from_source_instead_of_trusting_mutually_forged_rows(mutation):
    result = _from_sources(sources())
    calls = list(result.calls)
    index = next(i for i, row in enumerate(calls) if row.role == "PLANNING_READ")
    if mutation == "omit":
        del calls[index]
    elif mutation == "duplicate":
        calls[index] = calls[index + 1]
    elif mutation == "ghost":
        calls[index] = replace(calls[index], site=replace(calls[index].site, ordinal=99999))
    elif mutation == "promote":
        index = next(i for i, row in enumerate(calls) if row.disposition == "UNRESOLVED")
        calls[index] = replace(calls[index], disposition="CONDITIONAL_LOCAL", domain="tenant_main")
    else:
        calls[index] = replace(calls[index], domain="read_ledger")
        if mutation == "fragment":
            result = replace(
                result,
                fragment=replace(
                    result.fragment,
                    rows=tuple(
                        replace(row, database_domain="read_ledger")
                        if row.site == calls[index].site
                        else row
                        for row in result.fragment.rows
                    ),
                ),
            )
    with pytest.raises(DescriptorClosureRefused):
        replace(result, calls=tuple(calls)).require_exact_accounting()


def test_forged_empty_call_inventory_still_cannot_grant_admission():
    result = _from_sources(sources())
    with pytest.raises(DescriptorClosureIncomplete):
        replace(result, calls=(), mounts=()).require_closed()


@pytest.mark.parametrize("mutation", ["drop", "duplicate", "forward", "role", "domain", "sql"])
def test_forged_scanner_paths_and_expressions_cannot_self_certify(mutation):
    result = _from_sources(sources())
    chains = list(result.fragment.scanner_chains)
    if mutation == "drop":
        chains.pop()
    elif mutation == "duplicate":
        chains[0] = chains[1]
    elif mutation == "forward":
        index = next(i for i, chain in enumerate(chains) if len(chain.forwards) == 2)
        chains[index] = replace(chains[index], forwards=tuple(reversed(chains[index].forwards)))
    elif mutation == "role":
        chains[0] = replace(chains[0], role="SCHEMA_COLUMN_METADATA")
    elif mutation == "domain":
        chains[0] = replace(chains[0], domain="read_ledger")
    else:
        expression = chains[0].expression
        chains[0] = replace(
            chains[0],
            expression=replace(
                expression, alternatives=(replace(expression.alternatives[0], sql="SELECT 1"),)
            ),
        )
    with pytest.raises(DescriptorClosureRefused):
        replace(
            result,
            fragment=replace(
                result.fragment,
                scanner_chains=tuple(chains),
            ),
        ).require_exact_accounting()


@pytest.mark.parametrize("mutation", ["omit", "ghost", "statement-label"])
def test_assembly_and_read_label_forgery_refuses(mutation):
    result = _from_sources(sources())
    if mutation == "omit":
        result = replace(result, mounts=())
    elif mutation == "ghost":
        mount = result.mounts[0]
        result = replace(
            result,
            mounts=(
                replace(
                    mount,
                    site=replace(mount.site, ordinal=99999),
                ),
            ),
        )
    else:
        rows = result.fragment.rows
        result = replace(
            result,
            fragment=replace(
                result.fragment,
                rows=(replace(rows[0], statement=99999), *rows[1:]),
            ),
        )
    with pytest.raises(DescriptorClosureRefused):
        result.require_exact_accounting()


def test_literal_sql_and_renamed_callable_both_remain_unresolved_without_receiver_proof():
    result = _from_sources(
        (
            *sources(),
            (
                "src/chiplog/extra.py",
                """
def run(connection):
    reader = connection.execute
    reader("SELECT 1")
    foreign.execute("SELECT 2")
""",
            ),
        )
    )
    rows = [row for row in result.calls if row.site.module == "src/chiplog/extra.py"]
    assert {row.site.kind for row in rows} == {
        "callable_reference_candidate",
        "other_call",
        "sql_call_candidate",
    }
    assert all(row.disposition == "UNRESOLVED" and row.domain is None for row in rows)


@pytest.mark.parametrize("mutation", ["receiver", "read-edge"])
def test_wrong_receiver_and_new_read_dependency_refuse_before_local_classification(mutation):
    path = "src/chiplog/platform/authority_reads.py"
    original = dict(sources())[path]
    (broker,) = [
        node
        for node in ast.parse(original).body
        if isinstance(node, ast.ClassDef) and node.name == "BrokerAuthorityReader"
    ]
    method_name = "execute" if mutation == "receiver" else "_execute_planning_read"
    (method,) = [
        node
        for node in broker.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    ]
    segment = ast.get_source_segment(original, method)
    assert segment is not None
    if mutation == "receiver":
        (factory,) = [
            node
            for node in ast.walk(method)
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "sqlite3.connect"
        ]
        old = ast.get_source_segment(original, factory)
        assert old is not None
        new = 'sqlite3.connect(":memory:")'
    else:
        old = '"commit_sequence, record_ids FROM publications WHERE tenant_id = ? "'
        new = old + (
            '\n            "AND EXISTS (SELECT 1 FROM deletion_fences WHERE frontier >= 0) "'
        )
    assert segment.count(old) == 1
    assert original.count(segment) == 1
    mutated = original.replace(segment, segment.replace(old, new))
    assert mutated != original
    (canonical,) = [
        node
        for node in ast.parse(original).body
        if isinstance(node, ast.FunctionDef) and node.name == "_canonical_planning_statements"
    ]
    canonical_segment = ast.get_source_segment(original, canonical)
    assert canonical_segment is not None
    assert canonical_segment in mutated
    changed = tuple((name, mutated if name == path else text) for name, text in sources())
    with pytest.raises(DescriptorClosureRefused):
        _from_sources(changed)


@pytest.mark.parametrize("mutation", ["omit", "add", "rename", "same-length"])
def test_snapshot_recheck_detects_complete_path_and_byte_changes(tmp_path, mutation):
    result = _from_sources(sources())
    for name, text in result.sources:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    result.recheck_source_tree(tmp_path.resolve())
    target = tmp_path / PATHS[0]
    if mutation == "omit":
        target.unlink()
    elif mutation == "add":
        (tmp_path / "src/chiplog/extra.py").write_text("# new module\n")
    elif mutation == "rename":
        target.rename(target.with_name("renamed.py"))
    else:
        text = target.read_text()
        assert "R7PlanningRuntime" in text
        target.write_text(text.replace("R7PlanningRuntime", "X7PlanningRuntime", 1))
    with pytest.raises(SourceTreeChanged):
        result.recheck_source_tree(tmp_path.resolve())


@pytest.mark.parametrize("mutation", ["symlink-directory", "symlink-file", "invalid-utf8"])
def test_source_capture_refuses_hidden_symlink_subtrees_and_unreadable_python(tmp_path, mutation):
    package = tmp_path / "src/chiplog"
    package.mkdir(parents=True)
    if mutation == "symlink-directory":
        outside = tmp_path / "outside"
        outside.mkdir()
        (package / "hidden").symlink_to(outside, target_is_directory=True)
    elif mutation == "symlink-file":
        (package / "hidden.py").symlink_to(ROOT / PATHS[0])
    else:
        (package / "hidden.py").write_bytes(b"\xff")
    with pytest.raises(SourceTreeChanged):
        inspect_authority_descriptor_closure(tmp_path.resolve())


@pytest.mark.parametrize(
    "mutation", ["domain", "role", "factory", "origin", "drop", "duplicate", "forward"]
)
def test_forged_selected_ledger_provenance_cannot_self_certify(mutation):
    result = _from_sources(sources())
    chains = list(result.fragment.ledger_chains)
    row = chains[-1]
    if mutation == "drop":
        chains.pop()
    elif mutation == "duplicate":
        chains.append(row)
    else:
        changes = {
            "domain": replace(row, domain="tenant_main"),
            "role": replace(row, role="SCHEMA_SCRIPT"),
            "factory": replace(row, factory=chains[0].factory),
            "origin": replace(row, origin=row.factory),
            "forward": replace(row, forwards=()),
        }
        chains[-1] = changes[mutation]
    forged = replace(result, fragment=replace(result.fragment, ledger_chains=tuple(chains)))
    with pytest.raises(DescriptorClosureRefused):
        forged.require_exact_accounting()


def test_ledger_replay_dto_helper_semantics_stay_unresolved():
    items = sources()
    path = "src/chiplog/platform/read_ledger.py"
    old = "return self._release_from_row(operation, release)"
    assert dict(items)[path].count(old) == 1
    changed = tuple(
        (
            name,
            text.replace(old, "return self.foreign_replay(operation, release)")
            if name == path
            else text,
        )
        for name, text in items
    )
    result = _from_sources(changed)
    (row,) = [
        row
        for row in result.calls
        if row.site.module == path
        and row.site.expression == "self.foreign_replay(operation, release)"
    ]
    assert row.disposition == "UNRESOLVED" and row.domain is None
    assert any("_release_from_row" in item for item in result.fragment.residual_obligations)
