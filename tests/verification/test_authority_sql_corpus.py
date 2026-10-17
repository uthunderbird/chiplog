"""Accounting retains unknown call targets; discovery is not domain proof."""

import ast
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from chiplog.verification.authority_sql_corpus import (
    CorpusInventoryChanged,
    UnresolvedSqlCorpus,
    current_sql_corpus,
    inspect_sources,
)

SOURCE = """import sqlite3 as db
from sqlite3 import connect as open_db
from chiplog.composition.r7_planning import _open_runtime as shared_open

def query(database):
    connection = open_db(database)
    execute_query = connection.execute
    later = getattr(connection, operation)
    pass_to_callback(connection.execute)
    execute_query("SELECT record_id FROM records")
    later("SELECT tenant_id FROM tenant_heads")
    return db.connect(":memory:")

async def open_example(database):
    return shared_open(database, runtime_type=Runtime, manifest=MANIFEST)
"""


def _inspect(source=SOURCE):
    return inspect_sources((("src/chiplog/sample.py", source),))


def test_renamed_import_bound_alias_reflection_and_callback_remain_in_accounting() -> None:
    corpus = _inspect()
    expressions = {node.expression for node in corpus.nodes}
    assert "execute_query('SELECT record_id FROM records')" in expressions
    assert "later('SELECT tenant_id FROM tenant_heads')" in expressions
    assert "getattr(connection, operation)" in expressions
    assert "connection.execute" in expressions
    assert "pass_to_callback(connection.execute)" in expressions
    assert "from sqlite3 import connect as open_db" in expressions
    assert any(node.expression.startswith("shared_open(") for node in corpus.nodes)
    assert any(node.kind == "opener_declaration" for node in corpus.nodes)
    with pytest.raises(UnresolvedSqlCorpus, match="resolution is incomplete"):
        corpus.require_closed()


def test_blank_lines_preserve_identity_and_identical_calls_remain_distinct() -> None:
    source = (
        'def read(connection):\n    connection.execute("SELECT 1")\n'
        '    connection.execute("SELECT 1")\n'
    )
    original = _inspect(source)
    spaced = _inspect("\n\n" + source.replace("    connection", "\n    connection"))
    original.require_exact_inventory(spaced)
    calls = [node for node in original.nodes if node.kind == "sql_call_candidate"]
    assert len(calls) == 2
    assert calls[0].ordinal != calls[1].ordinal
    assert calls[0].shape_sha256 == calls[1].shape_sha256
    assert original.source_digests != spaced.source_digests
    with pytest.raises(FrozenInstanceError):
        calls[0].ordinal = 99


@pytest.mark.parametrize("change", ["add_mount", "remove_mount", "remove_site", "change_binding"])
def test_bidirectional_accounting_rejects_added_removed_or_rebound_source(change: str) -> None:
    original = _inspect()
    if change == "add_mount":
        changed = SOURCE + (
            "\nasync def open_other(database):\n"
            "    return shared_open(database, runtime_type=Runtime, "
            "manifest=OTHER)\n"
        )
    elif change == "remove_mount":
        changed = SOURCE[: SOURCE.index("async def open_example")]
    elif change == "remove_site":
        changed = SOURCE.replace('    later("SELECT tenant_id FROM tenant_heads")\n', "")
    else:
        changed = SOURCE.replace(
            "execute_query = connection.execute", "execute_query = other.execute"
        )
    with pytest.raises(CorpusInventoryChanged):
        _inspect(changed).require_exact_inventory(original)


def test_nested_scopes_and_unknown_dynamic_call_targets_are_retained_without_imports() -> None:
    source = """raise RuntimeError("must never execute source")
def outer(connection):
    def inner():
        return getattr(connection, operation)(payload)
    return inner()
"""
    corpus = _inspect(source)
    assert any(
        node.qualname == "outer.inner" and node.kind == "dynamic_call" for node in corpus.nodes
    )
    assert any(
        node.expression == "RuntimeError('must never execute source')" for node in corpus.nodes
    )


def test_duplicate_source_names_and_empty_source_tree_refuse() -> None:
    with pytest.raises(UnresolvedSqlCorpus):
        inspect_sources(())
    with pytest.raises(UnresolvedSqlCorpus):
        inspect_sources((("same.py", SOURCE), ("same.py", SOURCE)))
    # Empty package modules still belong to the pinned source inventory.
    empty = inspect_sources((("src/chiplog/__init__.py", ""),))
    assert len(empty.source_digests) == 1
    with pytest.raises(UnresolvedSqlCorpus):
        empty.require_closed()


def test_current_tree_accounts_every_call_and_remains_unresolved() -> None:
    root = Path(__file__).resolve().parents[2]
    corpus = current_sql_corpus(root)
    # Independent AST count includes unknown callees, not just terminal SQL names.
    calls = sum(
        isinstance(node, ast.Call)
        for path in (root / "src").rglob("*.py")
        for node in ast.walk(ast.parse(path.read_bytes()))
    )
    assert (
        sum(
            node.kind.endswith("call") or node.kind.endswith("call_candidate")
            for node in corpus.nodes
        )
        == calls
    )
    with pytest.raises(UnresolvedSqlCorpus):
        corpus.require_closed()


def test_empty_file_addition_is_not_silently_absent_from_exact_inventory() -> None:
    original = _inspect()
    changed = inspect_sources((("src/chiplog/sample.py", SOURCE), ("src/chiplog/new_empty.py", "")))
    with pytest.raises(CorpusInventoryChanged):
        changed.require_exact_inventory(original)


def test_node_cannot_reference_a_file_missing_from_source_accounting() -> None:
    corpus = _inspect()
    orphan = replace(corpus.nodes[0], module="absent.py")
    changed = replace(corpus, nodes=(orphan, *corpus.nodes[1:]))
    with pytest.raises(UnresolvedSqlCorpus, match="accounting"):
        changed.require_closed()


def test_another_src_package_cannot_hide_new_sql_or_mount_calls(tmp_path: Path) -> None:
    package = tmp_path / "src/chiplog"
    package.mkdir(parents=True)
    (package / "sample.py").write_text(SOURCE)
    baseline = current_sql_corpus(tmp_path)
    other = tmp_path / "src/another_package"
    other.mkdir()
    (other / "reader.py").write_text(
        "import sqlite3\nconnection = sqlite3.connect(path)\n"
        "connection.execute('SELECT tenant_id FROM tenant_heads')\n"
    )
    changed = current_sql_corpus(tmp_path)
    with pytest.raises(CorpusInventoryChanged):
        changed.require_exact_inventory(baseline)
    assert any(node.module == "src/another_package/reader.py" for node in changed.nodes)
