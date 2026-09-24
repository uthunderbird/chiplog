"""Read-only expectations reject malformed inputs before inspecting durable rows."""

import hashlib
import sqlite3
from contextlib import closing
from dataclasses import replace
from typing import cast

import pytest

from chiplog.platform.publication_inspection import (
    ExpectedPublicationRecord,
    PublicationExpectation,
    inspect_publication,
)


def expectation() -> PublicationExpectation:
    return PublicationExpectation(
        "tenant",
        "operation",
        "key",
        "fingerprint",
        0,
        (
            ExpectedPublicationRecord(
                "record", "owner", "schema", b"payload", hashlib.sha256(b"payload").hexdigest()
            ),
        ),
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "wrong_type",
        "empty_tenant",
        "boolean_head",
        "empty_records",
        "list_records",
        "wrong_member",
        "newline_id",
        "duplicate_id",
        "wrong_hash",
        "text_payload",
    ),
)
def test_invalid_expectation_rejects_before_sql(mutation: str) -> None:
    value = expectation()
    member = value.records[0]
    candidates: dict[str, object] = {
        "wrong_type": object(),
        "empty_tenant": replace(value, tenant_id=""),
        "boolean_head": replace(value, expected_head=True),
        "empty_records": replace(value, records=()),
        "list_records": replace(
            value, records=cast(tuple[ExpectedPublicationRecord, ...], [member])
        ),
        "wrong_member": replace(value, records=(cast(ExpectedPublicationRecord, object()),)),
        "newline_id": replace(value, records=(replace(member, record_id="record\nother"),)),
        "duplicate_id": replace(value, records=(member, member)),
        "wrong_hash": replace(value, records=(replace(member, fingerprint="0" * 64),)),
        "text_payload": replace(
            value, records=(replace(member, canonical_bytes=cast(bytes, "payload")),)
        ),
    }
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("BEGIN")
        with pytest.raises(ValueError):
            inspect_publication(connection, cast(PublicationExpectation, candidates[mutation]), 1)
        assert connection.total_changes == 0


def test_requires_transaction_and_exact_selected_sequence() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        with pytest.raises(ValueError, match="active transaction"):
            inspect_publication(connection, expectation(), 1)
        connection.execute("BEGIN")
        for sequence in (0, 2, True):
            with pytest.raises(ValueError, match="exact predecessor"):
                inspect_publication(connection, expectation(), sequence)
