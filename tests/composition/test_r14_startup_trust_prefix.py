"""Real historical bundle refusal must precede all mutating startup constructors."""

import base64
import gzip
import hashlib
import json
import os
import sqlite3
import stat
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest

from chiplog.adapters.driven.deployment_trust import (
    IndependentTenantDecisionJournal,
    SQLiteTrustMaterializer,
)
from chiplog.adapters.driven.deployment_trust._journal import (
    AuthenticatedJournalSnapshot,
    read_authenticated_journal_snapshot,
)
from chiplog.composition import r7_planning
from chiplog.composition.r7_supervisor import R7RuntimeSupervisor
from chiplog.platform._sqlite import EventAppender, SQLiteMaterializer
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.read_ledger import BrokerReadLedger
from chiplog.platform.startup_trust_prefix import StartupTrustPrefixIntegrityError
from tests.support.r14_legacy_genesis_capture import _CapturedBundle


def _restore_legacy(directory: Path) -> tuple[Path, bytes]:
    fixture = Path(__file__).with_name("fixtures") / "r14_legacy_genesis_v1.json.gz"
    raw = fixture.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "187a44494e4d41286ebd5207e0811bfe5bb516fb769d0699a0ad95d3a83f5543"
    )
    capture: _CapturedBundle = json.loads(gzip.decompress(raw))
    for name, item in capture["files"].items():
        assert Path(name).name == name
        body = base64.b64decode(item["bytes_b64"], validate=True)
        assert hashlib.sha256(body).hexdigest() == item["sha256"]
        path = directory / name
        path.write_bytes(body)
        path.chmod(item["mode"])
    return directory / capture["database_name"], capture["operator_secret"].encode()


def _files(directory: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for path in directory.iterdir()
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption",
    [
        "partial",
        "recomputed",
        "orphan",
        "gap",
        "digest",
        "missing_table",
        "extra_decision",
        "nonprefix",
    ],
)
async def test_incomplete_trust_companions_refuse_before_startup_constructors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corruption: str
) -> None:
    database, secret = _restore_legacy(tmp_path)
    trust_database = database.with_suffix(database.suffix + ".trust.sqlite3")
    with closing(sqlite3.connect(trust_database)) as connection:
        rows = connection.execute(
            "SELECT decision_id, ordinal, canonical_bytes FROM trust_records"
        ).fetchall()
        initialized = [
            (decision_id, ordinal)
            for decision_id, ordinal, raw in rows
            if json.loads(raw)["operation_kind"] == "INITIALIZE"
        ]
        assert sorted(ordinal for _, ordinal in initialized) == list(range(5))
        decision_id = initialized[0][0]
        assert all(current == decision_id for current, _ in initialized)
        if corruption in {"partial", "gap"}:
            connection.execute(
                "DELETE FROM trust_records WHERE decision_id = ? AND ordinal = ?",
                (decision_id, 4 if corruption == "partial" else 1),
            )
        elif corruption == "recomputed":
            connection.execute(
                "UPDATE trust_records SET canonical_bytes = ? "
                "WHERE decision_id = ? AND ordinal = 0",
                (b"authenticated-byte-substitution", decision_id),
            )
            altered = connection.execute(
                "SELECT canonical_bytes FROM trust_records WHERE decision_id = ? ORDER BY ordinal",
                (decision_id,),
            ).fetchall()
            connection.execute(
                "UPDATE trust_decisions SET records_digest = ? WHERE decision_id = ?",
                (hashlib.sha256(b"\x00".join(row[0] for row in altered)).hexdigest(), decision_id),
            )
        elif corruption == "orphan":
            connection.execute("INSERT INTO trust_records VALUES ('orphan', 0, X'00')")
        elif corruption == "digest":
            connection.execute("UPDATE trust_decisions SET records_digest = 'wrong'")
        elif corruption == "missing_table":
            connection.execute("DROP TABLE trust_records")
        elif corruption == "extra_decision":
            connection.execute("INSERT INTO trust_decisions VALUES ('foreign', 'wrong')")
        else:
            # The genuine fixture has INITIALIZE and BOOTSTRAP. Keep only the latter.
            assert connection.execute("SELECT count(*) FROM trust_decisions").fetchone()[0] >= 2
            connection.execute("DELETE FROM trust_records WHERE decision_id = ?", (decision_id,))
            connection.execute("DELETE FROM trust_decisions WHERE decision_id = ?", (decision_id,))
        connection.commit()
    before = _files(tmp_path)
    entered: list[str] = []

    def observe[**P, R](owner: object, attribute: str, name: str, original: Callable[P, R]) -> None:
        def construct(*args: P.args, **kwargs: P.kwargs) -> R:
            entered.append(name)
            return original(*args, **kwargs)

        monkeypatch.setattr(owner, attribute, construct)

    observe(
        IndependentTenantDecisionJournal,
        "for_authority_bundle",
        "journal",
        IndependentTenantDecisionJournal.for_authority_bundle,
    )
    observe(
        SQLiteTrustMaterializer,
        "for_authority_bundle",
        "materializer",
        SQLiteTrustMaterializer.for_authority_bundle,
    )
    observe(r7_planning, "R7RuntimeSupervisor", "supervisor", R7RuntimeSupervisor)
    observe(r7_planning, "BrokerReadLedger", "ledger", BrokerReadLedger)
    observe(r7_planning, "SQLiteMaterializer", "main-factory", SQLiteMaterializer)
    error: Exception | None = None
    try:
        async with r7_planning.open_r7_runtime(
            database, tenant_id="legacy-fixture-tenant", operator_secret=secret
        ):
            entered.append("runtime-admitted")
    except Exception as observed:
        error = observed
    assert isinstance(error, StartupTrustPrefixIntegrityError), repr(error)
    assert error.__cause__ is not None
    assert "operation=startup_trust_prefix tenant=legacy-fixture-tenant record=" in str(error)
    assert entered == [], f"integrity refusal happened after startup effects: {entered}"
    assert _files(tmp_path) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("materialization", ["complete", "prefix", "empty_prefix", "absent"])
async def test_genuine_complete_prefix_and_absent_recover_exactly_without_journal_append(
    tmp_path: Path, materialization: str
) -> None:
    database, secret = _restore_legacy(tmp_path)
    trust = database.with_suffix(database.suffix + ".trust.sqlite3")
    journal = database.with_suffix(database.suffix + ".trust-journal")
    journal_before = _journal_files(journal)
    with closing(sqlite3.connect(trust)) as connection:
        expected_decisions = connection.execute(
            "SELECT * FROM trust_decisions ORDER BY rowid"
        ).fetchall()
        expected_records = connection.execute(
            "SELECT * FROM trust_records ORDER BY rowid"
        ).fetchall()
        assert len(expected_decisions) >= 2
        if materialization in {"prefix", "empty_prefix"}:
            removed = expected_decisions[-1:] if materialization == "prefix" else expected_decisions
            for decision_id, _ in removed:
                connection.execute(
                    "DELETE FROM trust_records WHERE decision_id = ?", (decision_id,)
                )
                connection.execute(
                    "DELETE FROM trust_decisions WHERE decision_id = ?", (decision_id,)
                )
            connection.commit()
    if materialization == "absent":
        trust.unlink()
    for _ in range(2):
        async with r7_planning.open_r7_runtime(
            database, tenant_id="legacy-fixture-tenant", operator_secret=secret
        ) as runtime:
            observed = runtime._trust.verify()
            assert observed is not None
            assert observed.tenant_id == "legacy-fixture-tenant"
        with closing(sqlite3.connect(trust)) as connection:
            assert (
                connection.execute("SELECT * FROM trust_decisions ORDER BY rowid").fetchall()
                == expected_decisions
            )
            assert (
                connection.execute("SELECT * FROM trust_records ORDER BY rowid").fetchall()
                == expected_records
            )
        assert _journal_files(journal) == journal_before


def _journal_files(journal: Path) -> dict[str, bytes]:
    return {
        suffix: Path(str(journal) + suffix).read_bytes()
        for suffix in ("", ".key", ".head", ".lock")
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("remnant", ["-wal", "-journal", "-shm", "missing-main"])
async def test_partial_bundle_namespace_refuses_without_creating_sources(
    tmp_path: Path, remnant: str
) -> None:
    database, secret = _restore_legacy(tmp_path)
    trust = database.with_suffix(database.suffix + ".trust.sqlite3")
    if remnant == "missing-main":
        database.unlink()
    else:
        trust.unlink()
        _write_remnant(trust, remnant)
    before = _files(tmp_path)
    with pytest.raises(StartupTrustPrefixIntegrityError):
        async with r7_planning.open_r7_runtime(
            database, tenant_id="legacy-fixture-tenant", operator_secret=secret
        ):
            pytest.fail("partial namespace admitted")
    assert _files(tmp_path) == before


def _write_remnant(trust: Path, remnant: str) -> None:
    Path(str(trust) + remnant).write_bytes(b"partial-bundle-remnant")


@pytest.mark.asyncio
async def test_sql_time_journal_mutation_refuses_before_consuming_constructors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chiplog.platform import startup_trust_prefix

    database, secret = _restore_legacy(tmp_path)
    original = startup_trust_prefix.compare_trust_prefix
    journal = database.with_suffix(database.suffix + ".trust-journal")
    entered: list[str] = []

    def mutate_after_compare(
        connection: sqlite3.Connection,
        entries: tuple[tuple[str, str | None, bytes], ...],
        operator_secret: bytes,
    ) -> int:
        result = original(connection, entries, operator_secret)
        with journal.open("ab") as stream:
            stream.write(b"corrupt-after-first-authenticated-cut")
        return result

    monkeypatch.setattr(startup_trust_prefix, "compare_trust_prefix", mutate_after_compare)
    original_constructor = IndependentTenantDecisionJournal.for_authority_bundle

    def constructor(
        path: Path, *, authority_gate: AuthorityGate
    ) -> IndependentTenantDecisionJournal:
        entered.append("journal")
        return original_constructor(path, authority_gate=authority_gate)

    monkeypatch.setattr(IndependentTenantDecisionJournal, "for_authority_bundle", constructor)
    with pytest.raises(StartupTrustPrefixIntegrityError):
        async with r7_planning.open_r7_runtime(
            database, tenant_id="legacy-fixture-tenant", operator_secret=secret
        ):
            pytest.fail("changed journal consumed")
    assert entered == []


@pytest.mark.asyncio
async def test_existing_cut_is_held_through_store_setup_and_released_before_async_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chiplog.architecture.r7_runtime import R7_PRODUCTION_MANIFEST
    from chiplog.platform.authority_gate import AuthorityGateError

    database, secret = _restore_legacy(tmp_path)
    gate = AuthorityGate.for_database(database)
    original = IndependentTenantDecisionJournal.for_authority_bundle
    observed: list[str] = []

    def constructor(
        path: Path, *, authority_gate: AuthorityGate
    ) -> IndependentTenantDecisionJournal:
        gate.require_held()
        observed.append("journal")
        return original(path, authority_gate=authority_gate)

    monkeypatch.setattr(IndependentTenantDecisionJournal, "for_authority_bundle", constructor)

    def preflight(actual_gate: AuthorityGate) -> str:
        assert actual_gate == gate
        observed.append("preflight")
        return "installed-role-evidence"

    def store_setup(
        store: SQLiteMaterializer, actual_gate: AuthorityGate, evidence: object
    ) -> None:
        actual_gate.require_held()
        assert evidence == "installed-role-evidence"
        observed.append("store_setup")

    original_enter = EventAppender.__aenter__

    async def enter(appender: EventAppender) -> EventAppender:
        with pytest.raises(AuthorityGateError):
            gate.require_held()
        observed.append("async-entry")
        return await original_enter(appender)

    monkeypatch.setattr(EventAppender, "__aenter__", enter)
    async with r7_planning._open_runtime(
        database,
        tenant_id="legacy-fixture-tenant",
        operator_secret=secret,
        runtime_type=r7_planning.R7PlanningRuntime,
        manifest=R7_PRODUCTION_MANIFEST,
        preflight=preflight,
        store_setup=store_setup,
    ):
        assert observed == ["preflight", "journal", "store_setup", "async-entry"]


@pytest.mark.asyncio
@pytest.mark.parametrize("absent", [False, True])
async def test_final_journal_cut_cannot_hide_changed_trust_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, absent: bool
) -> None:
    from chiplog.platform import startup_trust_prefix

    database, secret = _restore_legacy(tmp_path)
    trust = database.with_suffix(database.suffix + ".trust.sqlite3")
    if absent:
        trust.unlink()
    original = read_authenticated_journal_snapshot
    calls: list[bool] = []

    def mutate_at_final_cut(
        path: Path, *, authority_gate: AuthorityGate
    ) -> AuthenticatedJournalSnapshot:
        snapshot = original(path, authority_gate=authority_gate)
        calls.append(True)
        if len(calls) == 2:
            with trust.open("ab") as stream:
                stream.write(b"changed-at-final-journal-cut")
        return snapshot

    monkeypatch.setattr(
        startup_trust_prefix, "read_authenticated_journal_snapshot", mutate_at_final_cut
    )
    with pytest.raises(StartupTrustPrefixIntegrityError):
        async with r7_planning.open_r7_runtime(
            database, tenant_id="legacy-fixture-tenant", operator_secret=secret
        ):
            pytest.fail("changed trust source admitted")
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["operator_auth", "logical_predecessor"])
async def test_unmaterialized_journal_suffix_is_authenticated_before_constructors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corruption: str
) -> None:
    import hmac

    from chiplog.platform.r7_trust_durability import authenticate_trust_entries

    database, secret = _restore_legacy(tmp_path)
    gate = AuthorityGate.for_database(database)
    journal = IndependentTenantDecisionJournal.for_authority_bundle(
        database.with_suffix(database.suffix + ".trust-journal"), authority_gate=gate
    )
    entries = journal.entries()
    logical = authenticate_trust_entries(entries, secret)
    envelope = json.loads(entries[-1][2])
    unsigned = {
        "kind": envelope["kind"],
        "payload": envelope["payload"],
        "predecessor": logical[-1][0] if corruption == "operator_auth" else "wrong-logical-head",
    }

    def canonical(value: object) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

    raw = canonical(
        {
            **unsigned,
            "operator_authentication": "wrong"
            if corruption == "operator_auth"
            else hmac.new(secret, canonical(unsigned), hashlib.sha256).hexdigest(),
        }
    )
    journal.append(raw, entries[-1][0])
    before = _files(tmp_path)
    entered: list[bool] = []
    original = IndependentTenantDecisionJournal.for_authority_bundle

    def constructor(
        path: Path, *, authority_gate: AuthorityGate
    ) -> IndependentTenantDecisionJournal:
        entered.append(True)
        return original(path, authority_gate=authority_gate)

    monkeypatch.setattr(IndependentTenantDecisionJournal, "for_authority_bundle", constructor)
    with pytest.raises(StartupTrustPrefixIntegrityError):
        async with r7_planning.open_r7_runtime(
            database, tenant_id="legacy-fixture-tenant", operator_secret=secret
        ):
            pytest.fail("unauthenticated journal suffix admitted")
    assert entered == []
    assert _files(tmp_path) == before


@pytest.mark.asyncio
async def test_fresh_uninitialized_runtime_reopens_with_private_empty_journal_head(
    tmp_path: Path,
) -> None:
    previous_umask = os.umask(0o022)
    try:
        database = tmp_path / "uninitialized.sqlite"
        async with r7_planning.open_r7_runtime(
            database, tenant_id="empty-tenant", operator_secret=b"empty-runtime-test"
        ):
            pass
        head = database.with_suffix(database.suffix + ".trust-journal.head")
        assert head.read_bytes() == b""
        assert stat.S_IMODE(head.stat().st_mode) == 0o600
        async with r7_planning.open_r7_runtime(
            database, tenant_id="empty-tenant", operator_secret=b"empty-runtime-test"
        ):
            pass
    finally:
        os.umask(previous_umask)


@pytest.mark.asyncio
async def test_existing_uninitialized_journal_head_with_public_permissions_is_not_repaired(
    tmp_path: Path,
) -> None:
    database = tmp_path / "uninitialized.sqlite"
    async with r7_planning.open_r7_runtime(
        database, tenant_id="empty-tenant", operator_secret=b"empty-runtime-test"
    ):
        pass
    head = database.with_suffix(database.suffix + ".trust-journal.head")
    head.chmod(0o644)
    before = _files(tmp_path)
    with pytest.raises(StartupTrustPrefixIntegrityError, match="operation=startup_trust_prefix"):
        async with r7_planning.open_r7_runtime(
            database, tenant_id="empty-tenant", operator_secret=b"empty-runtime-test"
        ):
            pytest.fail("runtime accepted public journal head")
    assert _files(tmp_path) == before
