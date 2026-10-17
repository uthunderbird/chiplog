"""All seven real finite identifier sites, with independent engine expectations."""

import ast
from hashlib import sha256
from pathlib import Path

import pytest

from chiplog.platform._sqlite import SCHEMA_SQL
from chiplog.verification.identifier_sql_families import (
    UnknownIdentifierSql,
    extract_current_identifier_sql,
)
from chiplog.verification.sqlite_preparation import (
    UnregisteredSqlAccess,
    observe_statement,
    verify_sql_actions,
)

ROOT = Path(__file__).resolve().parents[2] / "src/chiplog"
PATHS = (
    "platform/_sqlite.py",
    "composition/r7_planning.py",
    "composition/r12.py",
    "platform/authority_reads.py",
    "architecture/r7_storage_surface.py",
)
COLUMNS = {
    "deletion_fences": ("tenant_id", "generation", "frontier"),
    "derivatives": (
        "tenant_id",
        "sink",
        "derivative_id",
        "source_record_ids",
        "source_epoch",
        "provenance_fingerprint",
    ),
    "evidence_inbox": (
        "tenant_id",
        "source_id",
        "evidence_id",
        "fingerprint",
        "canonical_bytes",
        "followup_kind",
        "state",
        "attempt_id",
        "transport_version",
        "cursor",
    ),
    "publications": (
        "tenant_id",
        "operation_kind",
        "idempotency_key",
        "request_fingerprint",
        "commit_sequence",
        "record_ids",
    ),
    "records": (
        "tenant_id",
        "record_id",
        "owner",
        "schema_id",
        "canonical_bytes",
        "commit_sequence",
    ),
    "store_metadata": ("singleton", "store_version"),
    "tenant_heads": ("tenant_id", "head"),
}
OBJECTS = tuple(("table", name) for name in COLUMNS) + tuple(
    ("index", "sqlite_autoindex_" + name + "_1") for name in COLUMNS if name != "store_metadata"
)
AUTHORITY = (
    "deletion_fences",
    "evidence_inbox",
    "publications",
    "records",
    "store_metadata",
    "tenant_heads",
)
EXPECTED = (
    (
        "SQLiteMaterializer._admit",
        4,
        "self._connection",
        (
            "store_metadata",
            "tenant_heads",
            "publications",
            "records",
            "evidence_inbox",
            "deletion_fences",
            "derivatives",
        ),
    ),
    ("SQLiteMaterializer._activate_checkpoint_bundle", 2, "self._connection", ("1211188048",)),
    (
        "R7PlanningRuntime._locked_reconcile_read_generation",
        1,
        "connection",
        ("records", "publications"),
    ),
    (
        "R12Workspace._read_state",
        4,
        "connection",
        ("tenant_heads", "deletion_fences", "publications", "records"),
    ),
    ("_schema_members", 2, "connection", tuple(COLUMNS)),
    ("_authority_snapshot_bytes", 1, "connection", AUTHORITY),
    ("R7PlanningRuntime._bootstrap_storage_binding", 2, "connection", AUTHORITY),
)


def _sources():
    return tuple((path, (ROOT / path).read_text()) for path in PATHS)


def _observe(sql):
    return observe_statement(
        sql, registered_schema_sql=SCHEMA_SQL, registered_objects=OBJECTS, foreign_keys=True
    )


def _sort(actions):
    return tuple(
        sorted(actions, key=lambda row: tuple((value is not None, value or "") for value in row))
    )


def test_all_seven_current_sites_expand_the_independent_exact_domains():
    families = extract_current_identifier_sql(_sources())
    assert len(families) == 7
    sources = dict(_sources())
    for family, (qualname, site, receiver, identifiers) in zip(families, EXPECTED, strict=True):
        assert (family.qualname, family.site, family.receiver) == (qualname, site, receiver)
        assert (
            tuple(
                dict(item.bindings).get(
                    "table", dict(item.bindings).get("CHECKPOINT_BUNDLE_PROFILE")
                )
                for item in family.alternatives
            )
            == identifiers
        )
        dependencies = {family.module}
        if qualname in (
            "_schema_members",
            "_authority_snapshot_bytes",
            "R7PlanningRuntime._bootstrap_storage_binding",
        ):
            dependencies.add("architecture/r7_storage_surface.py")
        assert family.source_digests == tuple(
            (name, sha256(sources[name].encode()).hexdigest()) for name in sorted(dependencies)
        )
        for item, identifier in zip(family.alternatives, identifiers, strict=True):
            observed = _observe(item.sql)
            if qualname.endswith("_activate_checkpoint_bundle"):
                expected = (("PRAGMA", "user_version", "1211188048", None, None),)
            elif qualname in ("_schema_members", "SQLiteMaterializer._admit"):
                database = "main" if qualname == "_schema_members" else None
                expected = (("PRAGMA", "table_info", identifier, database, None),)
            elif qualname.endswith("_locked_reconcile_read_generation"):
                expected = _sort(
                    (("SELECT", None, None, None, None), ("READ", identifier, "", None, None))
                )
            else:
                columns = COLUMNS[identifier]
                if qualname == "R12Workspace._read_state":
                    columns = (*columns, "ROWID")
                expected = _sort(
                    [
                        ("SELECT", None, None, None, None),
                        *(("READ", identifier, column, "main", None) for column in columns),
                    ]
                )
            assert observed.actions == expected, (qualname, identifier, observed.actions, expected)
            verify_sql_actions(observed.actions, expected)


def _mutate(path, old, new):
    result = []
    for name, source in _sources():
        if name == path:
            assert source.count(old) == 1
            source = source.replace(old, new)
        result.append((name, source))
    return tuple(result)


def test_actual_literal_predicate_mutation_reaches_engine_and_graph_refuses():
    sources = _mutate(
        "composition/r12.py",
        'f"SELECT * FROM {table} WHERE tenant_id = ? ORDER BY rowid"',
        'f"SELECT * FROM {table} WHERE tenant_id = ? " '
        '"AND EXISTS(SELECT 1 FROM deletion_fences WHERE frontier>=0) ORDER BY rowid"',
    )
    family = extract_current_identifier_sql(sources)[3]
    (item,) = [item for item in family.alternatives if dict(item.bindings)["table"] == "records"]
    observed = _observe(item.sql)
    assert ("READ", "deletion_fences", "frontier", "main", None) in observed.actions
    expected = _sort(
        [
            ("SELECT", None, None, None, None),
            *(
                ("READ", "records", column, "main", None)
                for column in (*COLUMNS["records"], "ROWID")
            ),
        ]
    )
    with pytest.raises(UnregisteredSqlAccess):
        verify_sql_actions(observed.actions, expected)


@pytest.mark.parametrize(
    "path,old,new",
    (
        (
            "architecture/r7_storage_surface.py",
            "class StorageMember:",
            "class StorageMember(Base):",
        ),
        (
            "architecture/r7_storage_surface.py",
            "from dataclasses import asdict, dataclass",
            "from foreign import asdict, dataclass",
        ),
        (
            "architecture/r7_storage_surface.py",
            "@dataclass(frozen=True)\nclass StorageMember:",
            "@dataclass(frozen=False)\nclass StorageMember:",
        ),
        (
            "architecture/r7_storage_surface.py",
            "    authority_bearing: bool",
            "    authority_bearing: bool\n    extra: str",
        ),
        (
            "composition/r7_planning.py",
            "    AUTHORITY_STORAGE_MEMBERS,",
            "    AUTHORITY_STORAGE_MEMBERS as ALIAS,",
        ),
        (
            "platform/authority_reads.py",
            "if not member.authority_bearing:",
            "if member.authority_bearing:",
        ),
        (
            "platform/authority_reads.py",
            "    for member in AUTHORITY_STORAGE_MEMBERS:\n",
            "    for member in AUTHORITY_STORAGE_MEMBERS:\n        member.table = 'records'\n",
        ),
        ("platform/_sqlite.py", 'f"PRAGMA table_info({table})"', 'f"PRAGMA table_info({table!r})"'),
        ("platform/_sqlite.py", 'f"PRAGMA table_info({table})"', 'f"PRAGMA table_info({table:})"'),
        (
            "platform/_sqlite.py",
            "CHECKPOINT_BUNDLE_PROFILE = 0x48314350",
            "CHECKPOINT_BUNDLE_PROFILE = True",
        ),
        (
            "platform/_sqlite.py",
            "for table, expected_columns in STORE_COLUMNS.items():",
            "for table, expected_columns in external.items():",
        ),
        (
            "composition/r12.py",
            'for table in ("tenant_heads", "deletion_fences", "publications", "records"):',
            "for table in unknown_tables:",
        ),
        (
            "platform/authority_reads.py",
            "    materialized: list[dict[str, object]] = []",
            "    len = opaque\n    materialized: list[dict[str, object]] = []",
        ),
        (
            "platform/authority_reads.py",
            '        raise ValueError("authority storage tables differ from registered schema")',
            "        pass",
        ),
    ),
)
def test_unknown_identifier_producer_frame_or_binding_refuses(path, old, new):
    with pytest.raises(UnknownIdentifierSql):
        extract_current_identifier_sql(_mutate(path, old, new))


@pytest.mark.parametrize(
    "path,extra",
    (
        ("architecture/r7_storage_surface.py", 'AUTHORITY_STORAGE_MEMBERS[0].table = "other"'),
        ("architecture/r7_storage_surface.py", 'StorageMember.table = "other"'),
        ("platform/_sqlite.py", "STORE_COLUMNS.clear()"),
        ("platform/_sqlite.py", "alias = STORE_COLUMNS"),
        ("architecture/r7_storage_surface.py", "mutate(AUTHORITY_STORAGE_MEMBERS)"),
    ),
)
def test_explicit_global_producer_mutation_or_escape_refuses(path, extra):
    sources = tuple(
        (name, source + "\n" + extra + "\n" if name == path else source)
        for name, source in _sources()
    )
    with pytest.raises(UnknownIdentifierSql):
        extract_current_identifier_sql(sources)


def test_relative_dataclass_import_is_not_stdlib_identity():
    sources = _mutate(
        "architecture/r7_storage_surface.py",
        "from dataclasses import asdict, dataclass",
        "from .dataclasses import asdict, dataclass",
    )
    with pytest.raises(UnknownIdentifierSql):
        extract_current_identifier_sql(sources)


def test_member_constructor_must_exist_before_manifest_evaluation():
    sources = dict(_sources())
    tree = ast.parse(sources["architecture/r7_storage_surface.py"])
    (cls,) = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "StorageMember"
    ]
    tree.body.remove(cls)
    tree.body.append(cls)
    sources["architecture/r7_storage_surface.py"] = ast.unparse(tree)
    with pytest.raises(UnknownIdentifierSql):
        extract_current_identifier_sql(tuple(sources.items()))


@pytest.mark.parametrize("name", ["member", "columns"])
def test_unknown_direct_argument_escape_between_producers_and_sql_refuses(name):
    sources = _mutate(
        "platform/authority_reads.py",
        "        rows = connection.execute(\n            f'SELECT {columns}",
        f"        mutate({name})\n"
        "        rows = connection.execute(\n            f'SELECT {columns}",
    )
    with pytest.raises(UnknownIdentifierSql):
        extract_current_identifier_sql(sources)


@pytest.mark.parametrize(
    "path,qualname",
    (
        ("composition/r7_planning.py", "_locked_reconcile_read_generation"),
        ("platform/authority_reads.py", "_schema_members"),
        ("composition/r12.py", "_read_state"),
    ),
)
def test_selected_sql_moved_into_lambda_refuses(path, qualname):
    sources = dict(_sources())
    tree = ast.parse(sources[path])
    (method,) = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == qualname
    ]
    (target,) = [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        and node.args
        and isinstance(node.args[0], ast.JoinedStr)
    ]

    class Wrap(ast.NodeTransformer):
        def visit_Call(self, node):
            if node is target:
                wrapper = ast.parse("(lambda: None)()", mode="eval").body
                wrapper.func.body = node
                return ast.copy_location(wrapper, node)
            return self.generic_visit(node)

    sources[path] = ast.unparse(ast.fix_missing_locations(Wrap().visit(tree)))
    with pytest.raises(UnknownIdentifierSql):
        extract_current_identifier_sql(tuple(sources.items()))
