"""Read-only trust consistency before startup effects; never CURRENT admission."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from chiplog.adapters.driven.deployment_trust._journal import (
    AuthenticatedJournalSnapshot,
    read_authenticated_journal_snapshot,
)
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.r7_trust_durability import (
    BrokerTrustDurability,
    authenticate_trust_entries,
    strict_trust_envelope,
)
from chiplog.platform.readonly_sqlite_snapshot import readonly_authority_sql_snapshot


class StartupTrustPrefixIntegrityError(RuntimeError):
    """An existing authoritative trust cut cannot be consumed safely."""


@dataclass(frozen=True)
class StartupTrustPrefix:
    classification: Literal[
        "COMPLETE", "COMPLETE_DECISION_PREFIX", "ABSENT_MATERIALIZATION", "UNINITIALIZED"
    ]
    materialized_count: int
    journal: AuthenticatedJournalSnapshot


def _present(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def has_existing_bundle(gate: AuthorityGate) -> bool:
    """Classify before acquiring a potentially provisioning gate."""
    database = gate.database
    if _present(database):
        return True
    # Include authority and runtime companions even when MAIN is missing.
    if not database.parent.exists():
        return False
    return any(
        path.name.startswith(database.name + suffix)
        for path in database.parent.iterdir()
        for suffix in (".", "-")
    )


def compare_trust_prefix(
    connection: sqlite3.Connection,
    entries: tuple[tuple[str, str | None, bytes], ...],
    operator_secret: bytes,
) -> int:
    """Enumerate both whole tables; only exact complete decision batches may lag."""
    decisions = connection.execute(
        "SELECT decision_id, records_digest FROM trust_decisions ORDER BY rowid"
    ).fetchall()
    records = connection.execute(
        "SELECT decision_id, ordinal, canonical_bytes FROM trust_records ORDER BY rowid"
    ).fetchall()
    ids = tuple(row[0] for row in decisions)
    if ids != tuple(entry[0] for entry in entries[: len(ids)]):
        raise RuntimeError(f"decisions={ids!r}: materialization is not a journal prefix")
    expected_rows: list[tuple[str, int, bytes]] = []
    for (decision_id, digest), (_, _, raw) in zip(decisions, entries, strict=False):
        envelope = strict_trust_envelope(raw, operator_secret)
        kind, payload = envelope["kind"], envelope["payload"]
        assert isinstance(kind, str) and isinstance(payload, dict)
        expected = BrokerTrustDurability._expected_records(decision_id, kind, payload)
        if digest != hashlib.sha256(b"\x00".join(expected)).hexdigest():
            raise RuntimeError(f"decision={decision_id}: records digest differs")
        expected_rows.extend(
            (decision_id, ordinal, value) for ordinal, value in enumerate(expected)
        )
    if records != expected_rows:
        raise RuntimeError(f"decisions={ids!r}: exact records/ordinals/order differ")
    return len(ids)


def inspect_startup_trust_prefix(
    gate: AuthorityGate, *, tenant_id: str, operator_secret: bytes
) -> StartupTrustPrefix:
    """Observe existing MAIN and TRUST under the caller's uninterrupted held gate.

    MAIN is a legitimate SQLite cut only; it is not bound to journal authority here.
    Empty anchored journals retain the old uninitialized reopen behavior only with
    present, initialized, empty materialization. Missing materialization needs genesis.
    """
    record = str(gate.database)
    try:
        gate.require_held()
        journal_path = gate.database.with_suffix(gate.database.suffix + ".trust-journal")
        journal = read_authenticated_journal_snapshot(journal_path, authority_gate=gate)
        entries = journal.entries
        if entries:
            authenticate_trust_entries(entries, operator_secret)
            for decision_id, _, raw in entries:
                record = decision_id
                envelope = strict_trust_envelope(raw, operator_secret)
                if envelope["kind"] == "INITIALIZE":
                    payload = envelope["payload"]
                    assert isinstance(payload, dict)
                    genesis = payload["genesis"]
                    assert isinstance(genesis, dict)
                    binding = payload["binding"]
                    assert isinstance(binding, dict)
                    if any(
                        binding[key] != genesis[key]
                        for key in ("tenant_id", "database_instance_id")
                    ):
                        raise RuntimeError("INITIALIZE binding/genesis identity differs")
                    if genesis["tenant_id"] != tenant_id:
                        raise RuntimeError("INITIALIZE tenant differs from requested tenant")
                    if decision_id != entries[0][0]:
                        raise RuntimeError("repeated INITIALIZE is unsupported")
        trust = gate.database.with_suffix(gate.database.suffix + ".trust.sqlite3")
        with ExitStack() as cuts:
            cuts.enter_context(readonly_authority_sql_snapshot(gate.database, authority_gate=gate))
            absent = not _present(trust)
            if absent:
                if any(
                    _present(Path(str(trust) + suffix)) for suffix in ("-wal", "-journal", "-shm")
                ):
                    raise RuntimeError("absent trust database has companion remnants")
                if not entries:
                    raise RuntimeError("absent trust database lacks authenticated genesis")
                result = StartupTrustPrefix("ABSENT_MATERIALIZATION", 0, journal)
            else:
                connection = cuts.enter_context(
                    readonly_authority_sql_snapshot(trust, authority_gate=gate, role="TRUST")
                )
                count = compare_trust_prefix(connection, entries, operator_secret)
                result = StartupTrustPrefix(
                    (
                        "UNINITIALIZED"
                        if not entries
                        else "COMPLETE"
                        if count == len(entries)
                        else "COMPLETE_DECISION_PREFIX"
                    ),
                    count,
                    journal,
                )
            if read_authenticated_journal_snapshot(journal_path, authority_gate=gate) != journal:
                raise RuntimeError("journal changed across SQL observation")
            if absent and any(
                _present(Path(str(trust) + suffix)) for suffix in ("", "-wal", "-journal", "-shm")
            ):
                raise RuntimeError("absent trust namespace changed across observation")
        return result
    except (RuntimeError, OSError, sqlite3.Error, ValueError, TypeError, KeyError) as error:
        raise StartupTrustPrefixIntegrityError(
            f"operation=startup_trust_prefix tenant={tenant_id} record={record}: {error}"
        ) from error
