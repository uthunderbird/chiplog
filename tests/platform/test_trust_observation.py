"""Real trust-owner bootstrap and exact local observation; no synthetic authority."""

import hashlib
import json
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, dataclass
from multiprocessing import get_context
from pathlib import Path
from subprocess import Popen, TimeoutExpired
from threading import get_ident

import pytest

from chiplog.adapters.driven.deployment_trust import (
    IndependentTenantDecisionJournal,
    SQLiteTrustMaterializer,
)
from chiplog.capabilities.deployment_trust._r7_process import _evaluate, _TrustOwnerCall
from chiplog.platform._sqlite import (
    EventAppender,
    FenceAdvanceCommand,
    PhysicalPublicationCommand,
    PhysicalRecord,
    SQLiteMaterializer,
)
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.r7_trust import encode_trust_journal
from chiplog.platform.r7_trust_durability import BrokerTrustDurability, FrozenTrustObservation

_FACTORY_WORKER = """
import sys
from multiprocessing.connection import Connection
from pathlib import Path
from chiplog.adapters.driven.deployment_trust import (
    IndependentTenantDecisionJournal, SQLiteTrustMaterializer,
)
from chiplog.platform.authority_gate import AuthorityGate
connection = Connection(int(sys.argv[1]))
try:
    gate = AuthorityGate.for_database(Path(sys.argv[2]))
    factory = (IndependentTenantDecisionJournal if sys.argv[4] == 'journal'
               else SQLiteTrustMaterializer)
    connection.send('attempting')
    instance = factory.for_authority_bundle(Path(sys.argv[3]), authority_gate=gate)
    assert instance.authority_gate == gate
    if sys.argv[4] == 'materializer':
        assert instance.records() == ()
        instance.close()
    else:
        assert instance.entries() == ()
    connection.send('initialized')
finally:
    connection.close()
"""


@pytest.mark.parametrize("kind", ["journal", "materializer"])
def test_bound_factory_creates_no_files_until_other_process_releases_gate(
    tmp_path: Path, kind: str
) -> None:
    gate = AuthorityGate.for_database(tmp_path / "authority.sqlite")
    target = tmp_path / ("trust.journal" if kind == "journal" else "trust.sqlite")
    parent, child = get_context("spawn").Pipe()
    process = None
    try:
        with gate.hold():
            existing = set(tmp_path.iterdir())
            process = Popen(
                [
                    sys.executable,
                    "-c",
                    _FACTORY_WORKER,
                    str(child.fileno()),
                    str(gate.database),
                    str(target),
                    kind,
                ],
                pass_fds=(child.fileno(),),
            )
            child.close()
            assert parent.poll(5.0), "factory child did not start"
            assert parent.recv() == "attempting"
            assert not parent.poll(0.1), "factory initialized while another process held gate"
            assert process.poll() is None
            assert set(tmp_path.iterdir()) == existing, "factory created files before gate entry"
        assert parent.poll(5.0), "factory did not initialize after gate release"
        assert parent.recv() == "initialized"
        assert process.wait(5.0) == 0
        assert target.is_file()
        if kind == "journal":
            assert target.with_suffix(".journal.key").is_file()
            assert target.with_suffix(".journal.head").is_file()
    finally:
        child.close()
        parent.close()
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(5.0)
            except TimeoutExpired:
                process.kill()
                process.wait(5.0)


@dataclass(frozen=True)
class Bundle:
    gate: AuthorityGate
    journal: IndependentTenantDecisionJournal
    materializer: SQLiteTrustMaterializer
    durability: BrokerTrustDurability
    journal_path: Path
    materializer_path: Path


@contextmanager
def _bundle(tmp_path: Path) -> Iterator[Bundle]:
    gate = AuthorityGate.for_database(tmp_path / "authority.sqlite")
    journal_path, materializer_path = tmp_path / "trust.journal", tmp_path / "trust.sqlite"
    journal = IndependentTenantDecisionJournal.for_authority_bundle(
        journal_path, authority_gate=gate
    )
    materializer = SQLiteTrustMaterializer.for_authority_bundle(
        materializer_path, authority_gate=gate
    )
    try:
        yield Bundle(
            gate,
            journal,
            materializer,
            BrokerTrustDurability(journal, materializer, b"operator-secret"),
            journal_path,
            materializer_path,
        )
    finally:
        materializer.close()


def _bootstrap_request() -> bytes:
    return json.dumps(
        {
            "credential_id": "credential-1",
            "database_instance_id": "database-1",
            "expected_peer": "uid:test",
            "peer": "uid:test",
            "principal_id": "principal-1",
            "session_id": "session-1",
            "tenant_id": "tenant-1",
            "token_fingerprint": "bootstrap-token-fingerprint",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _bootstrap(bundle: Bundle) -> None:
    before = bundle.durability.capture_verified_observation()
    evaluated = _evaluate(
        _TrustOwnerCall(
            mode="BOOTSTRAP",
            snapshot_bytes=before.snapshot_bytes,
            request_bytes=_bootstrap_request(),
        )
    )
    assert evaluated.reference_bytes is not None
    bundle.durability.apply_authorized(evaluated.reference_bytes)


def test_empty_observation_then_real_bootstrap_binds_exact_snapshot_and_sources(
    tmp_path: Path,
) -> None:
    with _bundle(tmp_path) as bundle:
        empty = bundle.durability.capture_verified_observation()
        assert empty.snapshot_bytes == encode_trust_journal(())
        assert empty.journal_head is None
        assert empty.bundle_path == str(bundle.gate.database)
        paths = (
            bundle.journal_path,
            bundle.journal_path.with_suffix(".journal.key"),
            bundle.journal_path.with_suffix(".journal.head"),
            bundle.materializer_path,
        )
        assert empty.sources == tuple(
            (str(path.resolve()), path.stat().st_dev, path.stat().st_ino) for path in paths
        )
        with pytest.raises(FrozenInstanceError):
            setattr(empty, "journal_head", "substituted")  # noqa: B010
        _bootstrap(bundle)
        actual = bundle.durability.capture_verified_observation()
        assert actual != empty
        assert actual.snapshot_bytes == encode_trust_journal(
            bundle.durability.owner_snapshot_entries()
        )
        assert actual.journal_head == bundle.journal.entries()[-1][0]
        assert actual.sources[0:2] == empty.sources[0:2]
        assert actual.sources[3] == empty.sources[3]
        assert actual.sources[2] != empty.sources[2]
        assert bundle.durability.capture_verified_observation() == actual


def test_second_bootstrap_uses_real_active_snapshot_and_is_denied_without_mutation(
    tmp_path: Path,
) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        before = bundle.durability.capture_verified_observation()
        response = _evaluate(
            _TrustOwnerCall(
                mode="BOOTSTRAP",
                snapshot_bytes=before.snapshot_bytes,
                request_bytes=_bootstrap_request(),
            )
        )
        assert response.disposition == "DENIED"
        assert response.reference_bytes is None
        assert bundle.durability.capture_verified_observation() == before


@pytest.mark.parametrize("source_index", [0, 1, 3], ids=["journal", "key", "materializer"])
def test_identical_copied_source_replacement_is_rejected(
    tmp_path: Path,
    source_index: int,
) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        before = bundle.durability.capture_verified_observation()
        path = Path(before.sources[source_index][0])
        original = path.read_bytes()
        replacement = tmp_path / "copied-source"
        replacement.write_bytes(original)
        replacement.chmod(path.stat().st_mode & 0o777)
        replacement.replace(path)
        assert path.read_bytes() == original
        assert path.stat().st_ino != before.sources[source_index][2]
        with pytest.raises(RuntimeError, match=r"replaced|aliased"):
            bundle.durability.capture_verified_observation()


def test_loaded_journal_key_bytes_cannot_change_in_place(tmp_path: Path) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        before = bundle.durability.capture_verified_observation()
        key = Path(before.sources[1][0])
        original = key.read_bytes()
        key.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        assert key.stat().st_ino == before.sources[1][2]
        with pytest.raises(RuntimeError, match="key changed"):
            bundle.durability.capture_verified_observation()


@pytest.mark.parametrize("mutation", ["changed", "missing", "extra"])
def test_materialized_record_corruption_rejects_despite_retained_decision_row(
    tmp_path: Path,
    mutation: str,
) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        bundle.durability.capture_verified_observation()
        decision_id = bundle.journal.entries()[0][0]
        with sqlite3.connect(bundle.materializer_path) as connection:
            if mutation == "changed":
                connection.execute(
                    "UPDATE trust_records SET canonical_bytes = ? WHERE rowid = "
                    "(SELECT MIN(rowid) FROM trust_records)",
                    (b"{}",),
                )
            elif mutation == "missing":
                connection.execute(
                    "DELETE FROM trust_records WHERE rowid = (SELECT MIN(rowid) FROM trust_records)"
                )
            else:
                connection.execute(
                    "INSERT INTO trust_records(decision_id,ordinal,canonical_bytes) VALUES(?,?,?)",
                    (decision_id, 999, b"{}"),
                )
        assert bundle.materializer.materialized(decision_id)
        with pytest.raises(RuntimeError, match="materialization differs"):
            bundle.durability.capture_verified_observation()


def test_empty_journal_cannot_hide_orphan_materialized_record(tmp_path: Path) -> None:
    with _bundle(tmp_path) as bundle:
        bundle.durability.capture_verified_observation()
        with sqlite3.connect(bundle.materializer_path) as connection:
            connection.execute(
                "INSERT INTO trust_decisions VALUES(?,?)",
                ("orphan", hashlib.sha256(b"{}").hexdigest()),
            )
            connection.execute("INSERT INTO trust_records VALUES(?,?,?)", ("orphan", 0, b"{}"))
        assert bundle.journal.entries() == ()
        with pytest.raises(RuntimeError, match="materialization differs"):
            bundle.durability.capture_verified_observation()


async def test_actual_appender_writer_thread_can_capture_same_verified_observation(
    tmp_path: Path,
) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        expected = bundle.durability.capture_verified_observation()
        caller_thread = get_ident()
        observations: list[tuple[int, FrozenTrustObservation]] = []

        def capture(_commitment: str) -> None:
            observations.append((get_ident(), bundle.durability.capture_verified_observation()))

        # This physical fixture only exercises callback thread placement, not
        # an authorization protocol; trust bootstrap above uses the actual owner.
        with SQLiteMaterializer(
            bundle.gate.database, record_contracts={"fixture": "fixture.v1"}
        ) as store:
            async with EventAppender(store, capacity=1) as writer:
                await writer.advance_fence(FenceAdvanceCommand("tenant-1", "fence", 0))
                result = await writer.submit(
                    PhysicalPublicationCommand(
                        tenant_id="tenant-1",
                        operation_kind="fixture",
                        idempotency_key="capture",
                        request_fingerprint=hashlib.sha256(b"capture").hexdigest(),
                        expected_head=0,
                        fence_generation="fence",
                        expected_fence_frontier=0,
                        minimum_fence_frontier=0,
                        records=(
                            PhysicalRecord(
                                "record",
                                "fixture",
                                "fixture.v1",
                                b"{}",
                                hashlib.sha256(b"{}").hexdigest(),
                            ),
                        ),
                        decision_guard=capture,
                    )
                )
                assert result.disposition == "COMMITTED"
        assert len(observations) == 1
        thread, actual = observations[0]
        assert thread != caller_thread
        assert actual == expected


def test_ungated_capture_cannot_be_used_as_a_verified_observation(tmp_path: Path) -> None:
    journal = IndependentTenantDecisionJournal(tmp_path / "trust.journal")
    materializer = SQLiteTrustMaterializer(tmp_path / "trust.sqlite")
    try:
        trust = BrokerTrustDurability(journal, materializer, b"operator-secret")
        with pytest.raises(RuntimeError, match="bound authority gate"):
            trust.capture_verified_observation()
    finally:
        materializer.close()


@pytest.mark.parametrize("unbound_materializer", [False, True])
def test_mismatched_component_gates_reject_at_construction(
    tmp_path: Path,
    unbound_materializer: bool,
) -> None:
    first = AuthorityGate.for_database(tmp_path / "first.sqlite")
    second = AuthorityGate.for_database(tmp_path / "second.sqlite")
    journal = IndependentTenantDecisionJournal.for_authority_bundle(
        tmp_path / "trust.journal", authority_gate=first
    )
    materializer = (
        SQLiteTrustMaterializer(tmp_path / "trust.sqlite")
        if unbound_materializer
        else SQLiteTrustMaterializer.for_authority_bundle(
            tmp_path / "trust.sqlite", authority_gate=second
        )
    )
    try:
        with pytest.raises(RuntimeError, match="gate binding mismatch"):
            BrokerTrustDurability(journal, materializer, b"operator-secret")
    finally:
        materializer.close()


@pytest.mark.parametrize(
    "factory",
    [
        IndependentTenantDecisionJournal.for_authority_bundle,
        SQLiteTrustMaterializer.for_authority_bundle,
    ],
)
def test_bound_factory_rejects_missing_gate_before_creating_files(
    tmp_path: Path,
    factory: object,
) -> None:
    from typing import Any, cast

    path = tmp_path / "must-not-exist"
    with pytest.raises(TypeError, match="requires an AuthorityGate"):
        cast(Any, factory)(path, authority_gate=None)
    assert list(tmp_path.iterdir()) == []
