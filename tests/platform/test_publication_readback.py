"""Exact physical membership using the actual SQLite writer and fresh read cuts."""

import sqlite3
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.platform._sqlite import EventAppender, PhysicalPublicationCommand
from chiplog.platform.publication_readback import inspect_publication
from tests.platform.test_sqlite_substrate import command, opened, record


@pytest.fixture
async def physical(
    tmp_path: Path,
) -> AsyncIterator[tuple[Path, EventAppender, PhysicalPublicationCommand]]:
    path = tmp_path / "readback.sqlite"
    _, appender = await opened(path)
    try:
        yield path, appender, command()
    finally:
        await appender.close()


def _inspect(path: Path, expected: PhysicalPublicationCommand, sequence: int = 1) -> str:
    with sqlite3.connect(path) as connection:
        connection.execute("BEGIN")
        before = connection.total_changes
        result = inspect_publication(connection, expected, sequence)
        assert connection.in_transaction
        assert connection.total_changes == before
        return result


async def test_absent_then_complete_from_actual_event_appender(
    physical: tuple[Path, EventAppender, PhysicalPublicationCommand],
) -> None:
    path, appender, expected = physical
    assert _inspect(path, expected) == "ABSENT"
    committed = await appender.submit(expected)
    assert committed.disposition == "COMMITTED"
    assert committed.commit_sequence == 1
    assert _inspect(path, expected) == "COMPLETE"


@pytest.mark.parametrize(
    "mutation",
    (
        "DELETE FROM records WHERE record_id = 'record-1'",
        "DELETE FROM records",
        "DELETE FROM publications",
        "UPDATE records SET canonical_bytes = x'7878' WHERE record_id = 'record-1'",
        "UPDATE records SET owner = 'foreign' WHERE record_id = 'record-1'",
        "UPDATE records SET schema_id = 'foreign' WHERE record_id = 'record-1'",
        "UPDATE records SET commit_sequence = 2 WHERE record_id = 'record-1'",
        "UPDATE publications SET record_ids = 'record-2' || char(10) || 'record-1'",
        "UPDATE publications SET record_ids = 'record-1'",
        "UPDATE publications SET request_fingerprint = 'foreign'",
        "UPDATE publications SET commit_sequence = 2",
        "UPDATE publications SET operation_kind = 'foreign'",
        "UPDATE publications SET idempotency_key = 'foreign'",
        "INSERT INTO records VALUES ('tenant-1', 'extra', 'foreign', 'foreign', x'78', 1)",
        "INSERT INTO publications VALUES ('tenant-1', 'extra', 'extra', 'extra', 1, '')",
    ),
)
async def test_partial_extra_or_changed_materialization_is_conflict(
    physical: tuple[Path, EventAppender, PhysicalPublicationCommand],
    mutation: str,
) -> None:
    path, appender, expected = physical
    await appender.submit(expected)
    with sqlite3.connect(path) as connection:
        connection.execute(mutation)
    assert _inspect(path, expected) == "CONFLICT"


@pytest.mark.parametrize(
    "mutation",
    (
        "INSERT INTO records VALUES ('tenant-1', 'extra', 'foreign', 'foreign', x'78', 1)",
        "INSERT INTO publications VALUES ('tenant-1', 'extra', 'extra', 'extra', 1, '')",
        "INSERT INTO records VALUES "
        "('tenant-1', 'record-1', 'fixture-owner', 'schema.v1', x'6f6e65', 7)",
        "INSERT INTO publications VALUES ('tenant-1', 'fixture', 'key-1', 'fingerprint-1', 7, '')",
    ),
)
async def test_absence_requires_no_expected_identity_or_rows_at_selected_sequence(
    physical: tuple[Path, EventAppender, PhysicalPublicationCommand],
    mutation: str,
) -> None:
    path, _, expected = physical
    with sqlite3.connect(path) as connection:
        connection.execute(mutation)
    assert _inspect(path, expected) == "CONFLICT"


async def test_later_unrelated_commit_does_not_erase_historical_complete(
    physical: tuple[Path, EventAppender, PhysicalPublicationCommand],
) -> None:
    path, appender, expected = physical
    await appender.submit(expected)
    later = command(
        key="later", fingerprint="later", expected_head=1, records=(record("later", b"later"),)
    )
    assert (await appender.submit(later)).commit_sequence == 2
    assert _inspect(path, expected) == "COMPLETE"
    assert _inspect(path, later, 2) == "COMPLETE"


async def test_foreign_tenant_rows_at_same_sequence_are_unrelated(
    physical: tuple[Path, EventAppender, PhysicalPublicationCommand],
) -> None:
    path, appender, expected = physical
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO records VALUES ('foreign', 'record-1', 'foreign', 'foreign', x'78', 1)"
        )
        connection.execute(
            "INSERT INTO publications VALUES "
            "('foreign', 'fixture', 'key-1', 'foreign', 1, 'record-1')"
        )
    assert _inspect(path, expected) == "ABSENT"
    await appender.submit(expected)
    assert _inspect(path, expected) == "COMPLETE"


async def test_changed_expected_fingerprint_and_record_order_do_not_match(
    physical: tuple[Path, EventAppender, PhysicalPublicationCommand],
) -> None:
    path, appender, expected = physical
    await appender.submit(expected)
    assert _inspect(path, replace(expected, request_fingerprint="other")) == "CONFLICT"
    assert (
        _inspect(path, replace(expected, records=tuple(reversed(expected.records)))) == "CONFLICT"
    )
    assert _inspect(path, replace(expected, expected_head=1), 2) == "CONFLICT"


@pytest.mark.parametrize(
    "invalid",
    (
        replace(command(), records=()),
        replace(command(), records=(record("same", b"one"), record("same", b"two"))),
        replace(command(), records=(record("one\ntwo", b"one"),)),
        replace(command(), records=(record("one\rtwo", b"one"),)),
        replace(command(), records=(replace(record("one", b"one"), fingerprint="0" * 64),)),
        replace(command(), records=(replace(record("one", b"one"), canonical_bytes="one"),)),  # type: ignore[arg-type]
        replace(command(), records=(record("", b"one"),)),
        replace(command(), tenant_id=""),
        replace(command(), expected_head=True),
        replace(command(), expected_head=-1),
    ),
)
async def test_invalid_expected_command_rejects_even_when_database_is_absent(
    physical: tuple[Path, EventAppender, PhysicalPublicationCommand],
    invalid: PhysicalPublicationCommand,
) -> None:
    path, _, _ = physical
    with pytest.raises(ValueError):
        _inspect(path, invalid)


@pytest.mark.parametrize("sequence", (True, 0, 2, 1.0, "1"))
async def test_selected_sequence_requires_exact_integer_successor(
    physical: tuple[Path, EventAppender, PhysicalPublicationCommand],
    sequence: object,
) -> None:
    path, _, expected = physical
    with sqlite3.connect(path) as connection:
        connection.execute("BEGIN")
        with pytest.raises(ValueError, match="selected sequence"):
            inspect_publication(connection, expected, sequence)  # type: ignore[arg-type]


async def test_requires_active_transaction_and_never_invokes_guards(
    physical: tuple[Path, EventAppender, PhysicalPublicationCommand],
) -> None:
    path, appender, expected = physical
    await appender.submit(expected)
    with (
        sqlite3.connect(path) as connection,
        pytest.raises(ValueError, match="active transaction"),
    ):
        inspect_publication(connection, expected, 1)

    def forbidden(*args: object) -> None:
        pytest.fail("readback invoked publication guard")

    guarded = replace(expected, admission_guard=forbidden, decision_guard=forbidden)
    assert _inspect(path, guarded) == "COMPLETE"
