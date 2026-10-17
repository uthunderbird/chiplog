from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from chiplog.architecture.r7_storage_surface import (
    AUTHORITY_STORAGE_MEMBERS,
    AUTHORITY_STORAGE_SURFACE_DIGEST,
    ReadEdge,
)
from chiplog.platform._sqlite import SQLiteMaterializer
from chiplog.platform.authority_reads import (
    AMR_FINGERPRINT,
    AuthorityReadFailure,
    BrokerAuthorityReader,
    PreparedReadEdgeAttestation,
    PreparedReadEdgeAttestationV2,
    _prepared_planning_statement,
)
from chiplog.platform.read_ledger import (
    BrokerReadLedger,
    BrokerReadState,
    ReadOperation,
    ReadRelease,
)


def _scalar(value: object) -> object:
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    return value


def _independent_commitment(path: Path) -> str:
    materialized: list[dict[str, object]] = []
    with sqlite3.connect(path) as connection:
        for member in AUTHORITY_STORAGE_MEMBERS:
            if not member.authority_bearing:
                continue
            columns = ", ".join(f'"{column}"' for column in member.columns)
            order = ", ".join(str(index) for index in range(1, len(member.columns) + 1))
            rows = connection.execute(
                f'SELECT {columns} FROM "{member.table}" ORDER BY {order}'
            ).fetchall()
            materialized.append(
                {
                    "columns": member.columns,
                    "rows": [[_scalar(value) for value in row] for row in rows],
                    "table": member.table,
                }
            )
    return hashlib.sha256(
        json.dumps(materialized, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _file_observation(path: Path) -> str:
    stat = path.stat()
    values = {
        "database_device": stat.st_dev,
        "database_inode": stat.st_ino,
        "database_path": str(path.resolve()),
        "wal_path": str(Path(f"{path}-wal").resolve()),
    }
    return hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _database(path: Path, operation_kind: str = "CREATE_INTENTION_LINE") -> None:
    with SQLiteMaterializer(path, record_contracts={"planning": "chiplog.planning.record.v1"}):
        pass
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO tenant_heads VALUES ('tenant-1', 1)")
        connection.execute(
            "INSERT INTO publications VALUES (?, ?, ?, ?, ?, ?)",
            (
                "tenant-1",
                operation_kind,
                "command-1",
                "fingerprint-1",
                1,
                "\n".join(f"record-{index}" for index in range(1, 6)),
            ),
        )
        for index in range(1, 6):
            connection.execute(
                "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "tenant-1",
                    f"record-{index}",
                    "planning",
                    "chiplog.planning.record.v1",
                    f'{{"record":{index}}}'.encode(),
                    1,
                ),
            )


def _operation(attempt: str = "attempt-1") -> ReadOperation:
    return ReadOperation(
        variant="PLANNING_PUBLICATIONS",
        request_id=f"request-{attempt}",
        read_attempt_id=attempt,
        tenant_id="tenant-1",
        broker_epoch=1,
        owner_id="projections",
        generation_id="generation-1",
        session_id="session-1",
        capability_id="planning.read",
        target_id="planning-store",
        max_rows=10,
        response_slot_id=f"slot-{attempt}",
    )


def _reader(
    tmp_path: Path,
    operation_kind: str = "CREATE_INTENTION_LINE",
) -> tuple[BrokerAuthorityReader, BrokerReadLedger, BrokerReadState, Path]:
    database = tmp_path / "data.sqlite3"
    _database(database, operation_kind)
    state = BrokerReadState(
        tenant_id="tenant-1",
        broker_epoch=1,
        credential_session_head="credential-1",
        endpoint_channel_head="endpoint-1",
        file_wal_observation=_file_observation(database),
        journal_head="journal-1",
        materialization_commitment=_independent_commitment(database),
        owner_generation="generation-1",
        owner_draining=False,
        principal_contour_head="contour-1",
        storage_mutation_generation=1,
        trust_transition_head="trust-1",
        authority_surface_digest=AUTHORITY_STORAGE_SURFACE_DIGEST,
        amr_fingerprint=AMR_FINGERPRINT,
    )
    ledger = BrokerReadLedger(tmp_path / "read.sqlite3")
    ledger.publish_initial_state(state)
    return BrokerAuthorityReader(database, "database-1", ledger), ledger, state, database


def test_verified_snapshot_releases_only_frozen_canonical_dto(tmp_path: Path) -> None:
    reader, ledger, _, _ = _reader(tmp_path)
    operation = _operation()
    result = reader.execute(operation)
    assert result.disposition == "RELEASED"
    assert result.canonical_result_bytes is not None
    assert b'"publications"' in result.canonical_result_bytes
    assert ledger.dequeue(operation) is None


@pytest.mark.parametrize("table", ["sqliteXauthority", "unknown_authority"])
def test_unknown_main_table_refuses_typed_before_read_release(tmp_path: Path, table: str) -> None:
    reader, ledger, _, database = _reader(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(f'CREATE TABLE "{table}" (value TEXT)')
    operation = _operation("unknown-table")
    with pytest.raises(AuthorityReadFailure, match="storage surface") as caught:
        reader.execute(operation)
    assert isinstance(caught.value.__cause__, ValueError)
    assert ledger.dequeue(operation) is None


def test_same_head_raw_content_substitution_fails_full_amr_recomputation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader, _, state, database = _reader(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE records SET canonical_bytes = ? WHERE record_id = 'record-1'",
            (b'{"record":"two"}',),
        )
    monkeypatch.setattr(
        "chiplog.platform.authority_reads._file_wal_observation",
        lambda _: state.file_wal_observation,
    )
    with pytest.raises(AuthorityReadFailure, match="AMR commitment"):
        reader.execute(_operation())


@pytest.mark.parametrize("change", ["added", "missing"])
def test_actual_planning_edges_are_checked_before_step_and_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    import ast
    import inspect
    import textwrap

    reader, ledger, _, _ = _reader(tmp_path)
    original = BrokerAuthorityReader._execute_planning_read
    tree = ast.parse(textwrap.dedent(inspect.getsource(original)))
    method = tree.body[0]
    assert isinstance(method, ast.FunctionDef)
    method.decorator_list = []
    (assignment,) = [
        node
        for node in method.body
        if isinstance(node, ast.Assign)
        and [ast.unparse(target) for target in node.targets] == ["publication_sql"]
    ]
    old, new = (
        (
            "FROM publications WHERE tenant_id = ? ",
            "FROM publications WHERE tenant_id = ? AND NOT EXISTS "
            "(SELECT 1 FROM deletion_fences WHERE frontier < 0) ",
        )
        if change == "added"
        else ("request_fingerprint, ", "'' AS request_fingerprint, ")
    )
    (constant,) = [
        node
        for node in ast.walk(assignment.value)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and old in node.value
    ]
    assert isinstance(constant.value, str)
    assert constant.value.count(old) == 1
    constant.value = constant.value.replace(old, new)
    namespace = dict(original.__globals__)
    exec(compile(ast.fix_missing_locations(tree), "<planning-edge-mutant>", "exec"), namespace)
    monkeypatch.setattr(
        BrokerAuthorityReader, "_execute_planning_read", staticmethod(namespace[method.name])
    )
    stepped: list[str] = []
    connect = sqlite3.connect
    marker = (
        "NOT EXISTS (SELECT 1 FROM deletion_fences WHERE frontier < 0)"
        if change == "added"
        else "'' AS request_fingerprint,"
    )

    def observe(sql: str) -> None:
        if marker in sql and not sql.startswith("EXPLAIN "):
            stepped.append(sql)

    def observed_connection(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection: sqlite3.Connection = connect(*args, **kwargs)
        connection.set_trace_callback(observe)
        return connection

    monkeypatch.setattr(sqlite3, "connect", observed_connection)
    operation = _operation("engine-" + change)
    error = None
    result = None
    try:
        result = reader.execute(operation)
    except AuthorityReadFailure as caught:
        error = caught
    assert error is not None, (
        f"unregistered {change} edge admitted: result={result!r}; stepped={stepped!r}"
    )
    assert stepped == []
    assert ledger.dequeue(operation) is None


def test_prepared_query_edge_substitution_denies_before_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader, _, _, _ = _reader(tmp_path)
    monkeypatch.setattr(
        "chiplog.platform.authority_reads.PLANNING_PUBLICATION_READ_EDGES",
        (ReadEdge("PLANNING_PUBLICATIONS", "records", ("tenant_id", "record_id")),),
    )
    with pytest.raises(AuthorityReadFailure, match="prepared query edge"):
        reader.execute(_operation())


def test_publication_with_six_records_is_denied_before_release(tmp_path: Path) -> None:
    reader, ledger, state, database = _reader(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE publications SET record_ids = ? WHERE idempotency_key = 'command-1'",
            ("\n".join(f"record-{index}" for index in range(1, 7)),),
        )
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)",
            (
                "tenant-1",
                "record-6",
                "planning",
                "chiplog.planning.record.v1",
                b'{"record":6}',
                1,
            ),
        )
    ledger.reconcile_generation(
        BrokerReadState(
            **{
                **state.model_dump(),
                "materialization_commitment": _independent_commitment(database),
            }
        )
    )
    with pytest.raises(AuthorityReadFailure, match="exact-five"):
        reader.execute(_operation("six-records"))
    assert ledger.dequeue(_operation("six-records")) is None


def test_duplicate_publication_sequence_is_denied_before_pagination(tmp_path: Path) -> None:
    reader, ledger, state, database = _reader(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO publications VALUES (?, ?, ?, ?, ?, ?)",
            (
                "tenant-1",
                "CREATE_INTENTION_LINE",
                "command-2",
                "fingerprint-2",
                1,
                "\n".join(f"record-{index}" for index in range(6, 11)),
            ),
        )
        for index in range(6, 11):
            connection.execute(
                "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "tenant-1",
                    f"record-{index}",
                    "planning",
                    "chiplog.planning.record.v1",
                    f'{{"record":{index}}}'.encode(),
                    1,
                ),
            )
    ledger.reconcile_generation(
        BrokerReadState(
            **{
                **state.model_dump(),
                "materialization_commitment": _independent_commitment(database),
            }
        )
    )
    with pytest.raises(AuthorityReadFailure, match="commit sequence is not unique"):
        reader.execute(_operation("duplicate-sequence"))
    assert ledger.dequeue(_operation("duplicate-sequence")) is None


class _InvalidatingLedger(BrokerReadLedger):
    def release(
        self,
        operation: ReadOperation,
        expected_state: BrokerReadState,
        proof_fingerprint: str,
        result_bytes: bytes,
    ) -> ReadRelease:
        self.start_owner_drain(operation.tenant_id, expected_state.fingerprint())
        return super().release(operation, expected_state, proof_fingerprint, result_bytes)


def test_invalidator_at_release_cut_suppresses_read_bytes(tmp_path: Path) -> None:
    database = tmp_path / "data.sqlite3"
    _database(database)
    base = BrokerReadState(
        tenant_id="tenant-1",
        broker_epoch=1,
        credential_session_head="credential-1",
        endpoint_channel_head="endpoint-1",
        file_wal_observation=_file_observation(database),
        journal_head="journal-1",
        materialization_commitment=_independent_commitment(database),
        owner_generation="generation-1",
        owner_draining=False,
        principal_contour_head="contour-1",
        storage_mutation_generation=1,
        trust_transition_head="trust-1",
        authority_surface_digest=AUTHORITY_STORAGE_SURFACE_DIGEST,
        amr_fingerprint=AMR_FINGERPRINT,
    )
    ledger = _InvalidatingLedger(tmp_path / "read.sqlite3")
    ledger.publish_initial_state(base)
    result = BrokerAuthorityReader(database, "database-1", ledger).execute(_operation())
    assert result.disposition == "STALE_OR_INDETERMINATE_READ"
    assert result.canonical_result_bytes is None


@pytest.mark.parametrize("variant", ["PLANNING_PUBLICATIONS", "PLANNING_PUBLICATIONS_R13"])
def test_both_planning_variants_release_nonempty_exact_page(tmp_path: Path, variant: str) -> None:
    reader, _, _, _ = _reader(tmp_path, "planning.create_intention_line")
    operation = _operation().model_copy(update={"variant": variant})
    result = reader.execute(operation)
    assert result.disposition == "RELEASED"
    assert result.canonical_result_bytes is not None
    decoded = json.loads(result.canonical_result_bytes)
    assert len(decoded["publications"]) == 1
    assert len(decoded["records"]) == 5


@pytest.mark.parametrize("variant", ["PLANNING_PUBLICATIONS", "PLANNING_PUBLICATIONS_R13"])
@pytest.mark.parametrize("site", ["uniqueness_sql", "publication_sql", "record_sql"])
@pytest.mark.parametrize("change", ["added", "missing"])
def test_every_actual_planning_site_refuses_changed_edges_before_own_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
    site: str,
    change: str,
) -> None:
    import ast
    import inspect
    import textwrap

    reader, ledger, _, _ = _reader(tmp_path, "planning.create_intention_line")
    original = BrokerAuthorityReader._execute_planning_read
    tree = ast.parse(textwrap.dedent(inspect.getsource(original)))
    method = tree.body[0]
    assert isinstance(method, ast.FunctionDef)
    method.decorator_list = []
    (assignment,) = [
        node
        for node in method.body
        if isinstance(node, ast.Assign)
        and [ast.unparse(target) for target in node.targets] == [site]
    ]
    if change == "added":
        old = "FROM publications WHERE tenant_id = ? "
        new = old + "AND NOT EXISTS (SELECT 1 FROM deletion_fences WHERE frontier < 0) "
        # The record query contains its publication selector in the same literal.
        marker = "NOT EXISTS (SELECT 1 FROM deletion_fences WHERE frontier < 0)"
    elif site == "uniqueness_sql":
        old, new = "GROUP BY commit_sequence", "GROUP BY 1"
        marker = "GROUP BY 1"
    elif site == "publication_sql":
        old, new = "request_fingerprint, ", "'' AS request_fingerprint, "
        marker = "'' AS request_fingerprint,"
    else:
        old, new = "canonical_bytes, ", "X'' AS canonical_bytes, "
        marker = "X'' AS canonical_bytes,"
    (constant,) = [
        node
        for node in ast.walk(assignment.value)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and old in node.value
    ]
    assert isinstance(constant.value, str)
    assert constant.value.count(old) == 1
    constant.value = constant.value.replace(old, new)
    namespace = dict(original.__globals__)
    exec(compile(ast.fix_missing_locations(tree), "<planning-site-mutant>", "exec"), namespace)
    monkeypatch.setattr(
        BrokerAuthorityReader, "_execute_planning_read", staticmethod(namespace[method.name])
    )
    stepped: list[str] = []
    connect = sqlite3.connect

    def observe(sql: str) -> None:
        if marker in sql and not sql.startswith("EXPLAIN "):
            stepped.append(sql)

    def observed_connection(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection: sqlite3.Connection = connect(*args, **kwargs)
        connection.set_trace_callback(observe)
        return connection

    monkeypatch.setattr(sqlite3, "connect", observed_connection)
    operation = _operation(site + change).model_copy(update={"variant": variant})
    with pytest.raises(AuthorityReadFailure, match="prepared query edge") as caught:
        reader.execute(operation)
    if change == "added":
        assert isinstance(caught.value.__cause__, sqlite3.Error)
        assert "deletion_fences.frontier is prohibited" in str(caught.value.__cause__)
    else:
        assert isinstance(caught.value.__cause__, ValueError)
        assert "complete prepared READ set" in str(caught.value.__cause__)
    assert stepped == []
    assert ledger.dequeue(operation) is None
    with connect(ledger._path) as connection:
        row = connection.execute(
            "SELECT state, result_bytes, enqueued, dequeued FROM authority_read_releases "
            "WHERE read_attempt_id = ?",
            (operation.read_attempt_id,),
        ).fetchone()
    assert row == ("PENDING", None, 0, 0)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT length(tenant_id) FROM publications",
        "SELECT COUNT(*) FROM publications",
        "UPDATE publications SET tenant_id = tenant_id",
        "PRAGMA table_info(publications)",
        "SELECT tenant_id FROM temp.foreign_table",
        "SELECT tenant_id FROM publications_view",
    ],
)
def test_planning_policy_denies_unknown_engine_actions_functions_domains_and_sources(
    sql: str,
) -> None:
    from contextlib import closing

    with closing(sqlite3.connect(":memory:", cached_statements=0)) as connection:
        connection.executescript(
            "CREATE TABLE publications(tenant_id TEXT);"
            "CREATE TEMP TABLE foreign_table(tenant_id TEXT);"
            "CREATE VIEW publications_view AS SELECT tenant_id FROM publications;"
        )
        with (
            pytest.raises(AuthorityReadFailure) as caught,
            _prepared_planning_statement(
                connection,
                sql,
                (),
                frozenset({("main", "publications", "tenant_id")}),
                canonical_statement=(sql, ()),
                snapshot_id="helper-test",
            ),
        ):
            pytest.fail("unauthorized query reached business body")
        assert isinstance(caught.value.__cause__, sqlite3.Error)
        # Failed preflight clears policy before caller regains the private connection.
        assert connection.execute("SELECT length('cleared')").fetchone() == (7,)


def test_planning_policy_lifetime_keeps_guard_through_body_and_clears_after_failure() -> None:
    from contextlib import closing

    with closing(sqlite3.connect(":memory:", cached_statements=0)) as connection:
        connection.executescript(
            "CREATE TABLE expected(value); CREATE TABLE foreign_table(secret);"
        )
        # Warm the foreign statement before policy installation. Its later execution
        # must authorize again; retaining a cached permissive program cannot bypass.
        connection.execute("SELECT secret FROM foreign_table").fetchall()
        with (
            pytest.raises(AuthorityReadFailure) as caught,
            _prepared_planning_statement(
                connection,
                "SELECT value FROM expected",
                (),
                frozenset({("main", "expected", "value")}),
                canonical_statement=("SELECT value FROM expected", ()),
                snapshot_id="helper-test",
            ),
        ):
            connection.execute("SELECT secret FROM foreign_table").fetchall()
        assert isinstance(caught.value.__cause__, sqlite3.Error)
        assert connection.execute("SELECT secret FROM foreign_table").fetchall() == []
        with (
            pytest.raises(RuntimeError, match="body failure"),
            _prepared_planning_statement(
                connection,
                "SELECT value FROM expected",
                (),
                frozenset({("main", "expected", "value")}),
                canonical_statement=("SELECT value FROM expected", ()),
                snapshot_id="helper-test",
            ),
        ):
            raise RuntimeError("body failure")
        assert connection.execute("SELECT secret FROM foreign_table").fetchall() == []


def test_actual_reprepare_cannot_add_edge_after_successful_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader, ledger, _, _ = _reader(tmp_path)
    connect = sqlite3.connect
    stepped: list[str] = []
    cache_options: list[int] = []

    class RepreparingConnection(sqlite3.Connection):
        def execute(
            self,
            sql: str,
            parameters: Any = (),
            /,
        ) -> sqlite3.Cursor:
            if sql.startswith("SELECT tenant_id, operation_kind, idempotency_key"):
                sql = sql.replace(
                    "FROM publications WHERE tenant_id = ? ",
                    "FROM publications WHERE tenant_id = ? AND NOT EXISTS "
                    "(SELECT 1 FROM deletion_fences WHERE frontier < 0) ",
                )
            return super().execute(sql, parameters)

    def observed_connection(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        if "cached_statements" in kwargs:
            cache_options.append(kwargs["cached_statements"])
            kwargs["factory"] = RepreparingConnection
        connection: sqlite3.Connection = connect(*args, **kwargs)
        connection.set_trace_callback(
            lambda sql: stepped.append(sql) if "NOT EXISTS" in sql else None
        )
        return connection

    monkeypatch.setattr(sqlite3, "connect", observed_connection)
    operation = _operation("reprepare")
    with pytest.raises(AuthorityReadFailure) as caught:
        reader.execute(operation)
    assert isinstance(caught.value.__cause__, sqlite3.Error)
    assert cache_options == [0]
    assert stepped == []
    assert ledger.dequeue(operation) is None


def test_original_v1_attestation_wire_stays_frozen_and_v2_is_distinct() -> None:
    fields = {
        "read_attempt_id": "attempt",
        "query_variant": "PLANNING_PUBLICATIONS",
        "prepared_query_fingerprint": "old-fingerprint",
        "actual_edges": (),
        "authority_surface_digest": "surface",
        "read_graph_version": 1,
        "snapshot_id": "snapshot",
    }
    wire = (
        b'{"read_attempt_id":"attempt","query_variant":"PLANNING_PUBLICATIONS",'
        b'"prepared_query_fingerprint":"old-fingerprint","actual_edges":[], '
        b'"authority_surface_digest":"surface","read_graph_version":1,"snapshot_id":"snapshot"}'
    ).replace(b"[], ", b"[],")
    assert PreparedReadEdgeAttestation.model_validate_json(wire).model_dump_json().encode() == wire
    assert PreparedReadEdgeAttestation.model_validate(fields).model_dump_json().encode() == wire
    v2 = PreparedReadEdgeAttestationV2.model_validate(fields).model_dump()
    assert v2["attestation_version"] == 2
    assert v2["fingerprint_domain"] == "planning-prepared-read/v2"


@pytest.mark.parametrize("change", ["operation", "duplicate", "reordered", "unknown"])
def test_same_named_planning_graph_substitution_is_denied_before_business_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    from chiplog.architecture.r7_storage_surface import PLANNING_PUBLICATION_READ_EDGES

    reader, ledger, _, _ = _reader(tmp_path)
    pub, records = PLANNING_PUBLICATION_READ_EDGES
    columns = pub.columns
    operation = pub.operation
    if change == "operation":
        operation = "FOREIGN_OPERATION"
    elif change == "duplicate":
        columns = (*columns, columns[0])
    elif change == "reordered":
        columns = tuple(reversed(columns))
    else:
        columns = (*columns, "unknown_column")
    monkeypatch.setattr(
        "chiplog.platform.authority_reads.PLANNING_PUBLICATION_READ_EDGES",
        (ReadEdge(operation, pub.table, columns), records),
    )
    attempt = _operation(change)
    with pytest.raises(AuthorityReadFailure, match="prepared query edge") as caught:
        reader.execute(attempt)
    assert isinstance(caught.value.__cause__, ValueError)
    assert ledger.dequeue(attempt) is None


def test_v2_fingerprint_binds_all_actual_prepared_sql_parameters_and_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections.abc import Iterator
    from contextlib import closing, contextmanager

    from chiplog.platform.authority_reads import VerifiedAuthorityReadSnapshot

    reader, _, _, database = _reader(tmp_path)
    prepared: list[tuple[str, tuple[str | int, ...]]] = []

    @contextmanager
    def observe_preparation(
        connection: sqlite3.Connection,
        sql: str,
        parameters: tuple[str | int, ...],
        expected_reads: frozenset[tuple[str, str, str]],
        *,
        canonical_statement: tuple[str, tuple[str | int, ...]],
        snapshot_id: str,
        count: bool = False,
    ) -> Iterator[set[tuple[str, str, str]]]:
        prepared.append((sql, parameters))
        with _prepared_planning_statement(
            connection,
            sql,
            parameters,
            expected_reads,
            canonical_statement=canonical_statement,
            snapshot_id=snapshot_id,
            count=count,
        ) as observed:
            yield observed

    monkeypatch.setattr(
        "chiplog.platform.authority_reads._prepared_planning_statement",
        observe_preparation,
    )
    snapshot = VerifiedAuthorityReadSnapshot.model_construct(
        snapshot_id="verified-snapshot-one",
        sqlite_snapshot_frontier=1,
    )
    with closing(sqlite3.connect(database, cached_statements=0)) as connection:
        connection.execute("BEGIN")
        _, attestation = reader._execute_planning_read(connection, _operation(), snapshot)
        assert len(prepared) == 3
        assert "COUNT(*)" in prepared[0][0]
        expected = hashlib.sha256(
            json.dumps(
                {
                    "domain": "planning-prepared-read/v2",
                    "queries": tuple(sql for sql, _ in prepared),
                    "parameters": tuple(params for _, params in prepared),
                    "snapshot_id": snapshot.snapshot_id,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        assert attestation.prepared_query_fingerprint == expected
        prepared.clear()
        _, changed_snapshot = reader._execute_planning_read(
            connection,
            _operation(),
            snapshot.model_copy(update={"snapshot_id": "snapshot-two"}),
        )
        assert changed_snapshot.prepared_query_fingerprint != expected
        _, changed_params = reader._execute_planning_read(
            connection,
            _operation().model_copy(update={"max_rows": 11}),
            snapshot,
        )
        assert changed_params.prepared_query_fingerprint != expected


@pytest.mark.parametrize("change", ["same_edges_sql", "coherent_parameters"])
def test_actual_planning_query_and_parameters_match_canonical_contract_before_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    import ast
    import inspect
    import textwrap

    reader, ledger, _, _ = _reader(tmp_path)
    original = BrokerAuthorityReader._execute_planning_read
    tree = ast.parse(textwrap.dedent(inspect.getsource(original)))
    method = tree.body[0]
    assert isinstance(method, ast.FunctionDef)
    method.decorator_list = []
    assignments = {
        node.targets[0].id: node
        for node in method.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }
    if change == "same_edges_sql":
        old = "FROM publications WHERE tenant_id = ? "
        (constant,) = [
            node
            for node in ast.walk(assignments["publication_sql"].value)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and old in node.value
        ]
        assert isinstance(constant.value, str)
        constant.value = constant.value.replace(old, old + "AND 1 = 1 ")
        marker = "AND 1 = 1"
    else:
        for name, indices in (("publication_parameters", (0,)), ("record_parameters", (0, 1))):
            values = assignments[name].value
            assert isinstance(values, ast.Tuple)
            for index in indices:
                values.elts[index] = ast.Constant(value="tenant-other")
        marker = "tenant-other"
    namespace = dict(original.__globals__)
    exec(
        compile(ast.fix_missing_locations(tree), "<planning-fingerprint-mutant>", "exec"), namespace
    )
    monkeypatch.setattr(
        BrokerAuthorityReader, "_execute_planning_read", staticmethod(namespace[method.name])
    )
    stepped: list[str] = []
    connect = sqlite3.connect

    def observed_connection(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection: sqlite3.Connection = connect(*args, **kwargs)
        connection.set_trace_callback(
            lambda sql: (
                stepped.append(sql) if marker in sql and not sql.startswith("EXPLAIN ") else None
            )
        )
        return connection

    monkeypatch.setattr(sqlite3, "connect", observed_connection)
    operation = _operation(change)
    error = None
    result = None
    try:
        result = reader.execute(operation)
    except AuthorityReadFailure as caught:
        error = caught
    assert error is not None, f"noncanonical query admitted: result={result!r}; stepped={stepped!r}"
    assert isinstance(error.__cause__, ValueError)
    assert stepped == []
    assert ledger.dequeue(operation) is None
