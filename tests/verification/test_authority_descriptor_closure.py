"""Global inspection accounts for unknowns without turning local proofs into admission."""

from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.verification.authority_descriptor_closure import (
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
    "sql_call_candidate", "factory_or_wrapper_call_candidate", "mount_call_candidate",
    "reflection_call", "dynamic_call", "other_call", "callable_reference_candidate",
}


def sources():
    return tuple((name, (ROOT / name).read_text()) for name in PATHS)


@pytest.fixture(scope="module")
def actual():
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
        "tenant_main", "private_memory",
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
    assert {row.site for row in ledger_rows} == {
        row.sink for row in actual.fragment.ledger_chains}
    assert len(ledger_rows) == 11
    assert {row.domain for row in ledger_rows} == {"broker_read_ledger"}
    actual.recheck_source_tree(ROOT)
    with pytest.raises(DescriptorClosureIncomplete):
        actual.require_closed()


@pytest.mark.parametrize("mutation", [
    "omit", "duplicate", "ghost", "domain", "promote", "fragment",
])
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
            result = replace(result, fragment=replace(result.fragment, rows=tuple(
                replace(row, database_domain="read_ledger")
                if row.site == calls[index].site else row
                for row in result.fragment.rows
            )))
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
        chains[0] = replace(chains[0], expression=replace(expression, alternatives=(
            replace(expression.alternatives[0], sql="SELECT 1"),
        )))
    with pytest.raises(DescriptorClosureRefused):
        replace(result, fragment=replace(
            result.fragment, scanner_chains=tuple(chains),
        )).require_exact_accounting()


@pytest.mark.parametrize("mutation", ["omit", "ghost", "statement-label"])
def test_assembly_and_read_label_forgery_refuses(mutation):
    result = _from_sources(sources())
    if mutation == "omit":
        result = replace(result, mounts=())
    elif mutation == "ghost":
        mount = result.mounts[0]
        result = replace(result, mounts=(replace(
            mount, site=replace(mount.site, ordinal=99999),
        ),))
    else:
        rows = result.fragment.rows
        result = replace(result, fragment=replace(
            result.fragment, rows=(replace(rows[0], statement=99999), *rows[1:]),
        ))
    with pytest.raises(DescriptorClosureRefused):
        result.require_exact_accounting()


def test_literal_sql_and_renamed_callable_both_remain_unresolved_without_receiver_proof():
    result = _from_sources((*sources(), ("src/chiplog/extra.py", '''
def run(connection):
    reader = connection.execute
    reader("SELECT 1")
    foreign.execute("SELECT 2")
''')))
    rows = [row for row in result.calls if row.site.module == "src/chiplog/extra.py"]
    assert {row.site.kind for row in rows} == {
        "callable_reference_candidate", "other_call", "sql_call_candidate",
    }
    assert all(row.disposition == "UNRESOLVED" and row.domain is None for row in rows)


@pytest.mark.parametrize("mutation", ["receiver", "read-edge"])
def test_wrong_receiver_and_new_read_dependency_refuse_before_local_classification(mutation):
    path = "src/chiplog/platform/authority_reads.py"
    original = dict(sources())[path]
    if mutation == "receiver":
        old = 'sqlite3.connect(f"file:{self._database}?mode=ro", uri=True, isolation_level=None)'
        new = 'sqlite3.connect(":memory:")'
    else:
        old = '"commit_sequence, record_ids FROM publications WHERE tenant_id = ? "'
        new = old + (
            '\n            "AND EXISTS (SELECT 1 FROM deletion_fences WHERE frontier >= 0) "'
        )
    assert original.count(old) == 1
    changed = tuple((name, text.replace(old, new) if name == path else text)
                    for name, text in sources())
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
            "domain": {"domain": "tenant_main"},
            "role": {"role": "SCHEMA_SCRIPT"},
            "factory": {"factory": chains[0].factory},
            "origin": {"origin": row.factory},
            "forward": {"forwards": ()},
        }
        chains[-1] = replace(row, **changes[mutation])
    forged = replace(result, fragment=replace(result.fragment, ledger_chains=tuple(chains)))
    with pytest.raises(DescriptorClosureRefused):
        forged.require_exact_accounting()


def test_ledger_replay_dto_helper_semantics_stay_unresolved():
    items = sources()
    path = 'src/chiplog/platform/read_ledger.py'
    old = 'return self._release_from_row(operation, release)'
    assert dict(items)[path].count(old) == 1
    changed = tuple((name, text.replace(old, 'return self.foreign_replay(operation, release)')
                     if name == path else text) for name, text in items)
    result = _from_sources(changed)
    row, = [row for row in result.calls if row.site.module == path
            and row.site.expression == 'self.foreign_replay(operation, release)']
    assert row.disposition == 'UNRESOLVED' and row.domain is None
    assert any('_release_from_row' in item for item in result.fragment.residual_obligations)
