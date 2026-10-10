from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from chiplog.composition import r14_historical_suffix as suffix


@dataclass(frozen=True)
class _Record:
    record_id: str
    owner: str = "agent_loop"
    schema_id: str = "chiplog.loop.test.v1"
    canonical_bytes: bytes = b"{}"


@dataclass(frozen=True)
class _Command:
    operation_kind: str
    idempotency_key: str
    expected_head: int
    request_fingerprint: str
    records: tuple[_Record, ...]
    tenant_id: str = "tenant"


class _Runtime:
    _tenant_id = "tenant"
    _database = ":memory:"

    def __init__(self, entries, commands, owner_decisions=()) -> None:
        self.entries = entries
        self.commands = commands
        self.owner_decisions = owner_decisions

    def _authority_gate(self):
        return SimpleNamespace(require_held=lambda: None)

    def _check_database_identity(self) -> None:
        return None

    def _pending_owners(self):
        return ()

    def _pending_gate_publications(self):
        return ()

    def _loop_decisions(self):
        return SimpleNamespace(
            entries=lambda: tuple(
                (entry["operation_id"], b"decision", json.dumps(entry)) for entry in self.entries
            )
        )

    def _owner_decisions(self):
        snapshot = SimpleNamespace(
            tenant_id=self._tenant_id,
            decisions=tuple(self.owner_decisions),
            materialized_command_ids=tuple(
                decision.prepared.request.identity.command_id for decision in self.owner_decisions
            ),
        )
        return SimpleNamespace(snapshot=lambda: snapshot)

    def _publication(self, entry):
        return self.commands[entry["operation_id"]]

    def _owner_command(self, decision):
        return decision.command


def _loop(operation: str, sequence: int, predecessor: str, resulting: str):
    command = _Command(
        operation,
        f"loop-{sequence}",
        sequence - 1,
        f"fingerprint-{sequence}",
        (_Record(f"record-{sequence}"),),
    )
    entry = {
        "kind": "DECIDED",
        "operation_kind": operation,
        "operation_id": command.idempotency_key,
        "expected_head": command.expected_head,
        "fingerprint": command.request_fingerprint,
        "predecessor": predecessor,
        "resulting": resulting,
        "records": [],
    }
    return entry, command


def _owner(sequence: int, predecessor: str, resulting: str):
    operation = "effects.record_evidence"
    command = _Command(
        operation,
        f"owner-{sequence}",
        sequence - 1,
        f"fingerprint-{sequence}",
        (_Record(f"record-{sequence}", "effects", "chiplog.effects.dispatch-outcome-record.v2"),),
    )
    request = SimpleNamespace(identity=SimpleNamespace(command_id=command.idempotency_key))
    prepared = SimpleNamespace(
        request=request,
        predecessor_commitment=predecessor,
    )
    decision = SimpleNamespace(
        tenant_commit_sequence=sequence,
        prepared=prepared,
        resulting_commitment=resulting,
        command=command,
    )
    return decision, command


@contextmanager
def _connection(connection):
    yield connection


def _setup(
    monkeypatch,
    loop_steps,
    *,
    owner_steps=(),
    head=None,
    anchor=None,
    omit_record=None,
    extra_publication=None,
):
    resolution_op = "agent_loop.original-call-resolution.v1"
    resolution_command = _Command(
        resolution_op,
        "resolution-1",
        0,
        "resolution-fingerprint",
        (_Record("resolution-record"),),
    )
    resolution_entry = {
        "kind": "DECIDED",
        "operation_kind": resolution_op,
        "operation_id": resolution_command.idempotency_key,
        "expected_head": 0,
        "fingerprint": resolution_command.request_fingerprint,
        "predecessor": "commit-0",
        "resulting": "commit-1",
        "records": [],
    }
    entries = [resolution_entry, *(entry for entry, _ in loop_steps)]
    commands = {resolution_command.idempotency_key: resolution_command}
    commands.update({command.idempotency_key: command for _, command in loop_steps})
    owner_decisions = tuple(decision for decision, _ in owner_steps)
    commands.update({command.idempotency_key: command for _, command in owner_steps})
    runtime = _Runtime(entries, commands, owner_decisions)
    current_head = (
        head
        if head is not None
        else max(
            [
                1,
                *(command.expected_head + 1 for _, command in loop_steps),
                *(command.expected_head + 1 for _, command in owner_steps),
            ]
        )
    )
    anchor_head = current_head if anchor is None else anchor
    runtime._commitment_journal = SimpleNamespace(load=lambda _tenant: f"commit-{anchor_head}")
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        "CREATE TABLE tenant_heads (tenant_id TEXT, head INTEGER);"
        "CREATE TABLE records (tenant_id TEXT, record_id TEXT, owner TEXT, "
        "schema_id TEXT, canonical_bytes BLOB, commit_sequence INTEGER);"
        "CREATE TABLE publications (tenant_id TEXT, operation_kind TEXT, "
        "idempotency_key TEXT, request_fingerprint TEXT, commit_sequence INTEGER, "
        "record_ids TEXT);"
    )
    connection.execute("INSERT INTO tenant_heads VALUES ('tenant', ?)", (current_head,))
    all_commands = [(resolution_entry, resolution_command), *loop_steps]
    all_commands.extend(
        ({"predecessor": None, "resulting": None}, command) for _, command in owner_steps
    )
    for _entry, command in all_commands:
        sequence = command.expected_head + 1
        for record in command.records:
            if record.record_id != omit_record:
                connection.execute(
                    "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        "tenant",
                        record.record_id,
                        record.owner,
                        record.schema_id,
                        record.canonical_bytes,
                        sequence,
                    ),
                )
        connection.execute(
            "INSERT INTO publications VALUES (?, ?, ?, ?, ?, ?)",
            (
                "tenant",
                command.operation_kind,
                command.idempotency_key,
                command.request_fingerprint,
                sequence,
                "\n".join(record.record_id for record in command.records),
            ),
        )
    if extra_publication is not None:
        connection.execute(
            "INSERT INTO publications VALUES (?, ?, ?, ?, ?, ?)",
            (
                "tenant",
                "agent_loop.foreign.v1",
                "extra",
                "extra-fingerprint",
                extra_publication,
                "extra-record",
            ),
        )
    connection.commit()

    def commitment(db, _tenant):
        row = db.execute("SELECT head FROM tenant_heads WHERE tenant_id='tenant'").fetchone()
        return f"commit-{0 if row is None else row[0]}"

    def inspect(db, command, sequence):
        publication = db.execute(
            "SELECT 1 FROM publications WHERE tenant_id='tenant' AND operation_kind=? "
            "AND idempotency_key=? AND commit_sequence=?",
            (command.operation_kind, command.idempotency_key, sequence),
        ).fetchone()
        records = db.execute(
            "SELECT record_id FROM records WHERE tenant_id='tenant' AND commit_sequence=?",
            (sequence,),
        ).fetchall()
        return "COMPLETE" if publication and len(records) == len(command.records) else "CONFLICT"

    monkeypatch.setattr(suffix, "read_connection", lambda _db: _connection(connection))
    monkeypatch.setattr(suffix, "capture_authority_snapshot_commitment", commitment)
    monkeypatch.setattr(suffix, "inspect_publication", inspect)
    from chiplog.composition import r14_original_call_resolution_publication as resolution

    monkeypatch.setattr(
        resolution,
        "_selected_entries",
        lambda _runtime: [("r", b"", resolution_entry, resolution_command)],
    )
    monkeypatch.setattr(
        resolution,
        "validate_selected_command",
        lambda _runtime, _entry, _command: object(),
    )
    from chiplog.composition import r14_loop_semantic_reduction_publication as reduction

    monkeypatch.setattr(reduction, "validate_selected_command", lambda *_args: object())
    monkeypatch.setattr(reduction, "validate_loop_semantic_reduction_startup", lambda *_args: None)
    return runtime, connection, current_head


def _run(runtime, connection, head, target="commit-1", anchor=None):
    return suffix.authenticate_selected_r14_suffix_held(
        runtime,
        f"commit-{head}",
        f"commit-{head if anchor is None else anchor}",
        target_commitment=target,
    )


def test_exact_mixed_owner_loop_suffix_is_reversed_by_tenant_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = _loop("agent_loop.loop-semantic-reduction.v1", 2, "commit-1", "commit-2")
    owner = _owner(3, "commit-2", "commit-3")
    runtime, connection, head = _setup(monkeypatch, [loop], owner_steps=[owner])
    monkeypatch.setattr(
        __import__("chiplog.composition.r14_loop_semantic_reduction_publication", fromlist=["x"]),
        "validate_loop_semantic_reduction_startup",
        lambda _runtime: None,
    )

    assert _run(runtime, connection, head)


def test_one_unanchored_final_publication_is_authenticated_as_exact_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = _loop("agent_loop.loop-semantic-reduction.v1", 2, "commit-1", "commit-2")
    owner = _owner(3, "commit-2", "commit-3")
    runtime, connection, head = _setup(
        monkeypatch, [loop], owner_steps=[owner], anchor=2
    )

    assert _run(runtime, connection, head, anchor=2)
    assert not _run(runtime, connection, head, anchor=1)


def test_decision_collection_order_does_not_replace_persisted_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _loop("agent_loop.original-resolver-invocation.v1", 2, "commit-1", "commit-2")
    second = _loop("agent_loop.loop-semantic-reduction.v1", 3, "commit-2", "commit-3")
    accounting = _loop(
        "agent_loop.execution-accounting-readiness.v1", 4, "commit-3", "commit-4"
    )
    next_turn = _loop("agent_loop.execution-next-turn.v1", 5, "commit-4", "commit-5")
    runtime, connection, head = _setup(monkeypatch, [next_turn, second, accounting, first])
    from chiplog.composition import r14_next_turn_records as next_turn_records
    from chiplog.composition import r14_original_resolver_invocation_publication as invocation
    from chiplog.composition import r14_recovery_publication as recovery

    monkeypatch.setattr(
        invocation, "validate_selected_for_historical_suffix", lambda *_args: object()
    )
    monkeypatch.setattr(recovery, "validate_selected", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        next_turn_records, "validate_selected_next_turn", lambda *_args: object()
    )
    assert _run(runtime, connection, head)


@pytest.mark.parametrize(
    "mutate",
    [
        "missing_middle",
        "duplicate_sequence",
        "extra_after_anchor",
        "wrong_terminal",
        "missing_record",
        "foreign_operation",
    ],
)
def test_mixed_suffix_rejects_gaps_forks_and_incomplete_physical_state(
    monkeypatch: pytest.MonkeyPatch, mutate: str
) -> None:
    first = _loop("agent_loop.loop-semantic-reduction.v1", 2, "commit-1", "commit-2")
    second_sequence = 4 if mutate == "missing_middle" else 3
    predecessor = "commit-8" if mutate == "wrong_terminal" else f"commit-{second_sequence - 1}"
    operation = (
        "agent_loop.foreign.v1"
        if mutate == "foreign_operation"
        else "agent_loop.original-call-resolution.v1"
    )
    second = _loop(operation, second_sequence, predecessor, f"commit-{second_sequence}")
    owner_steps = [_owner(3, "commit-2", "commit-3")] if mutate == "duplicate_sequence" else []
    head = 4 if mutate == "extra_after_anchor" else second_sequence
    runtime, connection, _ = _setup(
        monkeypatch,
        [first, second],
        owner_steps=owner_steps,
        head=head,
        omit_record="record-3" if mutate == "missing_record" else None,
        extra_publication=head + 1 if mutate == "extra_after_anchor" else None,
    )
    assert not _run(runtime, connection, head)
