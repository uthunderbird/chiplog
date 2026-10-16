from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import psycopg
import pytest

from chiplog.platform.postgres import PostgresIntegrityError, PostgresServer, Record


def record(identity: str, payload: bytes = b"original\x00bytes") -> Record:
    return Record(identity, "owner", "schema.v1", payload)


def test_two_users_isolation_atomic_rollback_reopen_and_replay(
    postgres_server: PostgresServer,
) -> None:
    alice = postgres_server.user_database('alice"; DROP DATABASE postgres;--')
    bob = postgres_server.user_database("bob")
    alice.provision()
    bob.provision()
    assert alice.name != bob.name
    assert len(alice.name.encode()) <= 63
    assert (
        alice.publish("op", "key", "fingerprint", (record("a"), record("b")), 0).disposition
        == "COMMITTED"
    )
    original = alice.snapshot()
    assert original.head == 1 and bob.snapshot().head == 0
    with alice.connect() as connection:
        assert connection.execute("SELECT current_database()").fetchone() == (alice.name,)
    # First member inserts, second collides: neither publication nor new member survives.
    with pytest.raises(psycopg.errors.UniqueViolation):
        alice.publish("op", "failed", "fp", (record("new"), record("a")), 1)
    assert alice.snapshot() == original
    assert replace(alice).snapshot() == original
    assert (
        alice.publish("op", "key", "fingerprint", (record("a"), record("b")), 0).disposition
        == "REPLAY"
    )
    assert (
        alice.publish(
            "op", "key", "fingerprint", (record("a", b"changed"), record("b")), 0
        ).disposition
        == "CONFLICT"
    )
    assert alice.publish("op", "stale", "fp", (record("c"),), 0).disposition == "STALE"
    with pytest.raises(PostgresIntegrityError, match="identity"):
        replace(alice, identity=bob.identity).snapshot()


def test_independent_connections_serialize_first_append(postgres_server: PostgresServer) -> None:
    database = postgres_server.user_database("first-race")
    database.provision()
    start = Barrier(2)

    def append(identity: str) -> str:
        start.wait()
        return (
            replace(database).publish("op", identity, identity, (record(identity),), 0).disposition
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(append, ("a", "b")))
    assert sorted(results) == ["COMMITTED", "STALE"]
    assert database.snapshot().head == 1


def test_consistent_read_cut_survives_independent_writer(postgres_server: PostgresServer) -> None:
    database = postgres_server.user_database("read-cut")
    database.provision()
    with database.read() as connection:
        assert database._snapshot(connection).head == 0
        database.publish("op", "a", "a", (record("a"),), 0)
        assert database._snapshot(connection).head == 0
    assert database.snapshot().head == 1


def test_guard_reads_locked_cut_and_failure_rolls_back(postgres_server: PostgresServer) -> None:
    database = postgres_server.user_database("guard")
    database.provision()

    def reject(snapshot: object) -> None:
        assert snapshot == database.snapshot()
        raise PermissionError("denied transition")

    with pytest.raises(PermissionError, match="denied"):
        database.publish("op", "a", "a", (record("a"),), 0, reject)
    assert database.snapshot().head == 0
