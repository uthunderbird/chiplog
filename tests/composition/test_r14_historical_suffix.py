from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest

from chiplog.composition import r14_historical_suffix as suffix


@pytest.mark.parametrize(
    ("operation", "expected_calls"),
    [
        ("agent_loop.original-resolver-invocation.v1", 1),
        ("agent_loop.original-call-resolution.v1", 1),
        ("agent_loop.not-registered.v1", 0),
    ],
)
def test_pending_r14_suffix_dispatches_exactly_once_only_for_registered_operation(
    monkeypatch, operation: str, expected_calls
) -> None:
    class Gate:
        def require_held(self) -> None:
            pass

    runtime = SimpleNamespace(
        _authority_gate=lambda: Gate(),
        _pending=lambda: ({"operation_kind": operation},),
    )
    calls = []
    monkeypatch.setattr(
        suffix,
        "authenticate_selected_r14_suffix_held",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )

    assert suffix.authenticate_selected_pending_r14_suffix_held(runtime, "actual", "anchored") is (
        expected_calls == 1
    )
    assert len(calls) == expected_calls


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
    expected_preimage_commitment: str | None = None


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
    request = SimpleNamespace(
        identity=SimpleNamespace(command_id=command.idempotency_key), operation=operation
    )
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
    runtime, connection, head = _setup(monkeypatch, [loop], owner_steps=[owner], anchor=2)

    assert _run(runtime, connection, head, anchor=2)
    assert not _run(runtime, connection, head, anchor=1)


def test_decision_collection_order_does_not_replace_persisted_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _loop("agent_loop.original-resolver-invocation.v1", 2, "commit-1", "commit-2")
    second = _loop("agent_loop.loop-semantic-reduction.v1", 3, "commit-2", "commit-3")
    accounting = _loop("agent_loop.execution-accounting-readiness.v1", 4, "commit-3", "commit-4")
    next_turn = _loop("agent_loop.execution-next-turn.v1", 5, "commit-4", "commit-5")
    runtime, connection, head = _setup(monkeypatch, [next_turn, second, accounting, first])
    from chiplog.composition import r14_next_turn_records as next_turn_records
    from chiplog.composition import r14_original_resolver_invocation_publication as invocation
    from chiplog.composition import r14_recovery_publication as recovery

    monkeypatch.setattr(
        invocation, "validate_selected_for_historical_suffix", lambda *_args: object()
    )
    monkeypatch.setattr(recovery, "validate_selected", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(next_turn_records, "validate_selected_next_turn", lambda *_args: object())
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


@pytest.mark.parametrize(
    "operation",
    [
        "effects.authorize",
        "effects.commit_send",
        "effects.record_evidence",
        "effects.reconcile",
        "dispatch.consume_effect_send",
    ],
)
@pytest.mark.parametrize("foreign", [False, True])
def test_native_suffix_keeps_unselected_effects_out_of_authenticated_run_members(
    monkeypatch, operation: str, foreign
):
    from chiplog.composition import r14_accepted_direct_terminal_records as terminal
    from chiplog.composition import (
        r16_dispatch_authority,
        r16_dispatch_outbox,
        r16_dispatch_outcomes,
    )

    entry, command = _loop("agent_loop.accepted-direct-terminal.v1", 1, "c0", "c1")
    runtime = _Runtime([entry], {command.idempotency_key: command})
    monkeypatch.setattr(
        terminal,
        "retained_accepted_direct_terminal",
        lambda _: SimpleNamespace(
            result=SimpleNamespace(accounting=SimpleNamespace(original_call_id="call-A"))
        ),
    )
    intent_id = "foreign-effect" if foreign else "effect-B"
    issuance = SimpleNamespace(
        request=SimpleNamespace(
            command=SimpleNamespace(intent=SimpleNamespace(intent_id=intent_id)),
            original_send=SimpleNamespace(
                snapshot=SimpleNamespace(intent=SimpleNamespace(intent_id=intent_id))
            ),
        )
    )
    validated = []

    def validate(batch, runtime):
        validated.append(batch.operation)
        return issuance

    monkeypatch.setattr(r16_dispatch_authority, "validate_issuance", validate)
    monkeypatch.setattr(r16_dispatch_outcomes, "validate_outcome_issuance", validate)
    monkeypatch.setattr(
        r16_dispatch_outbox,
        "validate_consumptions",
        lambda runtime, journal: validated.append("dispatch.consume_effect_send"),
    )
    monkeypatch.setattr(
        r16_dispatch_outbox.ConsumeRequest,
        "model_validate_json",
        lambda _: SimpleNamespace(intent=SimpleNamespace(subject_id=intent_id)),
    )
    journal = SimpleNamespace(
        decisions=(
            SimpleNamespace(
                tenant_commit_sequence=2,
                prepared=SimpleNamespace(
                    request=SimpleNamespace(
                        operation=operation, command=SimpleNamespace(canonical_bytes=b"consumption")
                    )
                ),
            ),
        )
    )
    if foreign:
        with pytest.raises(ValueError, match="belongs to another seal"):
            suffix.validate_native_terminal_suffix_semantics(
                runtime, journal, "call-A", allowed_intent_ids=frozenset({"effect-A", "effect-B"})
            )
    else:
        suffix.validate_native_terminal_suffix_semantics(
            runtime, journal, "call-A", allowed_intent_ids=frozenset({"effect-A", "effect-B"})
        )
    assert validated == [operation]


@pytest.mark.parametrize(
    "operation",
    [
        "agent_loop.accepted-open-accounting.v1",
        "agent_loop.accepted-open-recovery.v1",
        "agent_loop.native-one-call-cancelled-accounting.v1",
    ],
)
def test_native_suffix_covers_existing_per_call_terminal_publication_families(
    monkeypatch, operation: str
) -> None:
    """Physical sequence coverage; retained-envelope semantics have separate tests."""
    from chiplog.composition import r14_accepted_direct_terminal_records as terminal

    later = _loop(operation, 2, "commit-1", "commit-2")
    runtime, connection, head = _setup(monkeypatch, [later])
    old_entry = runtime.entries[0]
    old_command = runtime.commands[old_entry["operation_id"]]
    target_operation = "agent_loop.accepted-direct-terminal.v1"
    target_command = _Command(
        target_operation,
        old_command.idempotency_key,
        old_command.expected_head,
        old_command.request_fingerprint,
        old_command.records,
    )
    old_entry["operation_kind"] = target_operation
    runtime.commands[target_command.idempotency_key] = target_command
    connection.execute(
        "UPDATE publications SET operation_kind=? WHERE commit_sequence=1",
        (target_operation,),
    )
    connection.commit()
    monkeypatch.setattr(
        terminal,
        "retained_accepted_direct_terminal",
        lambda _: SimpleNamespace(
            result=SimpleNamespace(accounting=SimpleNamespace(original_call_id="call-A"))
        ),
    )
    checked = []
    monkeypatch.setattr(
        suffix,
        "_validate_native_suffix_loop_command",
        lambda runtime, entry, command: checked.append(command.operation_kind),
    )
    assert suffix.authenticate_selected_r14_native_terminal_suffix_held(
        runtime, f"commit-{head}", f"commit-{head}", original_call_id="call-A"
    )
    assert operation in checked


def test_native_v3_suffix_resolves_retained_evidence_reader() -> None:
    from types import SimpleNamespace

    with pytest.raises(ValueError, match="V3 evidence is absent"):
        suffix._validate_native_suffix_loop_command(
            None,
            {"operation_kind": "agent_loop.native-sealed-accounting.v3"},
            SimpleNamespace(operation_kind="agent_loop.native-sealed-accounting.v3"),
        )


@pytest.mark.parametrize("operation", ["dispatch.consume_effect_send", "dispatch.foreign"])
def test_native_suffix_authenticates_physical_send_consumption_and_holds_unknown_family(
    monkeypatch, operation: str
) -> None:
    """Physical suffix composition; the outbox validator owns ticket/SEND semantics."""
    from chiplog.composition import r14_accepted_direct_terminal_records as terminal
    from chiplog.composition import r16_dispatch_outbox as outbox

    decision, owner_command = _owner(2, "commit-1", "commit-2")
    owner_command = replace(owner_command, operation_kind=operation)
    decision.command = owner_command
    decision.prepared.request.operation = operation
    runtime, connection, head = _setup(monkeypatch, [], owner_steps=[(decision, owner_command)])
    entry = runtime.entries[0]
    old_command = runtime.commands[entry["operation_id"]]
    target_operation = "agent_loop.accepted-direct-terminal.v1"
    runtime.commands[old_command.idempotency_key] = replace(
        old_command, operation_kind=target_operation
    )
    entry["operation_kind"] = target_operation
    connection.execute(
        "UPDATE publications SET operation_kind=? WHERE commit_sequence=1", (target_operation,)
    )
    connection.commit()
    monkeypatch.setattr(
        terminal,
        "retained_accepted_direct_terminal",
        lambda _: SimpleNamespace(
            result=SimpleNamespace(accounting=SimpleNamespace(original_call_id="call-A"))
        ),
    )
    monkeypatch.setattr(suffix, "_validate_native_suffix_loop_command", lambda *_: None)
    checked = []
    monkeypatch.setattr(
        outbox,
        "validate_consumptions",
        lambda runtime, journal: checked.append(journal.decisions),
    )
    result = suffix.authenticate_selected_r14_native_terminal_suffix_held(
        runtime, f"commit-{head}", f"commit-{head}", original_call_id="call-A"
    )
    assert result is (operation == "dispatch.consume_effect_send")
    assert bool(checked) is (operation == "dispatch.consume_effect_send")


@pytest.mark.parametrize(
    "damage", [None, "missing-send", "late-send", "duplicate", "ticket", "intent"]
)
def test_consumption_source_validation_binds_exact_prior_send_without_history_reentry(
    monkeypatch, damage
) -> None:
    """Exercise real retained-body validation; journal authentication is external."""
    from chiplog.capabilities.effects.dispatch_v2 import digest, reference
    from chiplog.composition import r16_dispatch_outbox as outbox
    from chiplog.platform._owner_publication_contracts import (
        OwnerCommandBytes,
        OwnerRecordBytes,
        PublicationIdentity,
        SingleOwnerBatch,
    )
    from tests.capabilities.effects.support.dispatch_outcomes import original_send

    send = original_send()
    send_raw = send.canonical_bytes()
    send_row = OwnerRecordBytes(
        owner="effects",
        record_kind="effects.SEND_COMMITTED",
        record_id=send.record.head,
        schema_id=send.schema_id,
        canonical_bytes=send_raw,
        fingerprint=digest(send_raw),
    )
    selected_send = SimpleNamespace(
        decision_id="selected-send",
        decision_head="selected-send-head",
        decision_fingerprint=digest(b"selected send"),
        prepared=SimpleNamespace(
            request=SimpleNamespace(
                operation="effects.commit_send",
                complete_records=(send_row,),
            )
        ),
    )
    consume_request, _ticket = outbox._request(selected_send, send)
    if damage == "ticket":
        consume_request = consume_request.model_copy(
            update={
                "ticket_bytes": b"different but consistently rehashed ticket",
                "ticket_digest": digest(b"different but consistently rehashed ticket"),
            }
        )
    elif damage == "intent":
        consume_request = consume_request.model_copy(
            update={
                "intent": reference("foreign-intent", b"foreign intent"),
            }
        )
    request_raw = consume_request.canonical_bytes()
    consumption = outbox.ConsumptionRecord(
        schema_id=outbox.CONSUMPTION_SCHEMA,
        state="CONSUMED",
        request=consume_request,
    )
    record_raw = consumption.canonical_bytes()
    # Only retained-body fields consumed by this validator are constructed.
    # This fixture grants no authentication or publication authority.
    batch = SingleOwnerBatch.model_construct(
        operation="dispatch.consume_effect_send",
        identity=PublicationIdentity(
            tenant_id=consume_request.tenant_id,
            command_id=consume_request.command_id,
            command_fingerprint=digest(request_raw),
            canonicalization_version="chiplog.owner-publication.v1",
        ),
        command=OwnerCommandBytes(
            owner="broker_dispatch",
            schema_id=outbox.CONSUME_SCHEMA,
            canonical_bytes=request_raw,
            fingerprint=digest(request_raw),
        ),
        complete_records=(
            OwnerRecordBytes(
                owner="broker_dispatch",
                record_kind="send-consumption",
                record_id=consume_request.command_id,
                schema_id=outbox.CONSUMPTION_SCHEMA,
                canonical_bytes=record_raw,
                fingerprint=digest(record_raw),
            ),
        ),
    )
    selected_consumption = SimpleNamespace(prepared=SimpleNamespace(request=batch))
    decisions = (selected_send, selected_consumption)
    if damage == "missing-send":
        decisions = (selected_consumption,)
    elif damage == "late-send":
        decisions = (selected_consumption, selected_send)
    elif damage == "duplicate":
        decisions = (*decisions, selected_consumption)
    journal = SimpleNamespace(decisions=decisions)

    def reentry(*args, **kwargs) -> None:
        raise AssertionError("consumption validation must use supplied journal")

    monkeypatch.setattr(outbox, "_capture_sources", reentry)
    runtime = SimpleNamespace(_owner_decisions=reentry)
    if damage is None:
        outbox.validate_consumptions(runtime, journal)
    else:
        with pytest.raises(ValueError, match=r"missing selected SEND|duplicate child|substitutes"):
            outbox.validate_consumptions(runtime, journal)


@pytest.mark.parametrize("damage", [None, "source-head", "predecessor", "tenant"])
def test_selected_open_source_stamp_matches_publication_header(damage) -> None:
    cut = SimpleNamespace(
        tenant_id="foreign" if damage == "tenant" else "tenant",
        tenant_commit_sequence=2 if damage == "source-head" else 1,
        materialization_commitment="source-commitment",
    )
    entry = {"predecessor": "different" if damage == "predecessor" else "source-commitment"}
    command = SimpleNamespace(expected_head=1, tenant_id="tenant")
    runtime = SimpleNamespace(_tenant_id="tenant")
    if damage is None:
        suffix.validate_native_open_selected_source_stamp(
            runtime, command=command, entry=entry, cut=cut
        )
    else:
        with pytest.raises(ValueError, match="OPEN source stamp"):
            suffix.validate_native_open_selected_source_stamp(
                runtime, command=command, entry=entry, cut=cut
            )


@pytest.mark.parametrize(
    "pending_operation,has_open",
    [
        ("agent_loop.execution-suspension.v1", True),
        ("agent_loop.execution-successor.v1", True),
        ("agent_loop.accepted-open-accounting.v1", True),
        ("agent_loop.accepted-open-recovery.v1", True),
        ("agent_loop.accepted-direct-terminal.v1", True),
        ("agent_loop.execution-suspension.v1", False),
        ("agent_loop.unknown.v1", True),
    ],
)
def test_exact_open_history_reaches_semantic_validation_before_pending_lifecycle_verdict(
    tmp_path, monkeypatch, pending_operation: str, has_open
) -> None:
    """Early gate reachability only; physical/callback proof remains separate."""
    from chiplog.adapters.driven.loop_sqlite import LoopIntegrityError
    from chiplog.composition import r14_accepted_open_accounting_records as open_records
    from chiplog.composition import r14_loop_history as history
    from chiplog.composition import r14_native_recovery_historical as historical
    from chiplog.platform._sqlite import SQLiteMaterializer
    from chiplog.platform.authority_gate import AuthorityGate
    from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
    from chiplog.platform.workspace_snapshot import read_connection

    class ReachedSemanticValidation(Exception):
        pass

    database = tmp_path / "pending-open.sqlite"
    with SQLiteMaterializer(database, record_contracts={"agent_loop": "chiplog.loop.test.v1"}):
        pass
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO tenant_heads VALUES ('tenant', 1)")
        connection.execute("INSERT INTO deletion_fences VALUES ('tenant', 'r6', 0)")
    with read_connection(database) as connection:
        commitment = capture_authority_snapshot_commitment(connection, "tenant")
    open_entry, open_command = _loop(
        "agent_loop.accepted-open-recovery.v1", 1, "before", commitment
    )
    open_entry["accepted_open_recovery_evidence"] = "inert gate-reachability input"
    pending_entry, pending_command = _loop(pending_operation, 2, commitment, "later")
    selected = (
        [(open_entry, open_command), (pending_entry, pending_command)]
        if has_open
        else [(pending_entry, pending_command)]
    )
    commands = {command.idempotency_key: command for _, command in selected}
    gate = AuthorityGate(database)
    runtime = SimpleNamespace(
        _tenant_id="tenant",
        _database=database,
        _authority_gate=lambda: gate,
        _pending=lambda: (pending_entry,),
        _publication=lambda entry: commands[entry["operation_id"]],
        _loop_decisions=lambda: SimpleNamespace(
            entries=lambda: tuple(
                (
                    entry["operation_id"],
                    b"predecessor",
                    json.dumps(entry, sort_keys=True, separators=(",", ":")),
                )
                for entry, _ in selected
            )
        ),
        _owner_decisions=lambda: SimpleNamespace(snapshot=lambda: SimpleNamespace(decisions=())),
        _commitment_journal=SimpleNamespace(load=lambda _: commitment),
        _check_database_identity=lambda: None,
    )
    monkeypatch.setattr(historical, "_make_authenticated_loop_decision_cut", lambda *args: args[1])

    def reach_semantics(entry) -> None:
        raise ReachedSemanticValidation

    monkeypatch.setattr(open_records, "retained_accepted_open_recovery", reach_semantics)
    if has_open and pending_operation in (
        "agent_loop.execution-suspension.v1",
        "agent_loop.execution-successor.v1",
        "agent_loop.accepted-open-accounting.v1",
        "agent_loop.accepted-open-recovery.v1",
    ):
        with pytest.raises(ReachedSemanticValidation):
            history._read_call_history(runtime, selected_only=True, exact_historical_sources=True)
    else:
        with pytest.raises(LoopIntegrityError, match="unverified pending write"):
            history._read_call_history(runtime, selected_only=True, exact_historical_sources=True)


@pytest.mark.parametrize("family", ["suspension", "successor"])
@pytest.mark.parametrize("state", ["ABSENT", "COMPLETE", "CONFLICT"])
@pytest.mark.parametrize("allow_absent", [False, True])
def test_lifecycle_physical_validation_keeps_default_strict_and_reaches_pending_semantics(
    tmp_path, monkeypatch, family: str, state: str, allow_absent
) -> None:
    """Real readback, inert owner-envelope acquisition; budget probe is explicit."""
    import hashlib

    from chiplog.composition import r14_execution_successor as successor
    from chiplog.composition import r14_execution_suspension as suspension
    from chiplog.platform._sqlite import (
        PhysicalPublicationCommand,
        PhysicalRecord,
        SQLiteMaterializer,
    )
    from chiplog.platform.authority_gate import AuthorityGate

    class BudgetValidationReached(Exception):
        pass

    raw = b"inert canonical row"
    command = PhysicalPublicationCommand(
        tenant_id="tenant",
        operation_kind="agent_loop.execution-" + family + ".v1",
        idempotency_key="selected",
        request_fingerprint="fingerprint",
        expected_head=1,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=(
            PhysicalRecord(
                "row", "agent_loop", "chiplog.loop.test.v1", raw, hashlib.sha256(raw).hexdigest()
            ),
        ),
    )
    database = tmp_path / "pending-validator.sqlite"
    with SQLiteMaterializer(database, record_contracts={"agent_loop": "chiplog.loop.test.v1"}):
        pass
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO tenant_heads VALUES ('tenant', ?)", (1 if state == "ABSENT" else 2,)
        )
        if state != "ABSENT":
            connection.execute(
                "INSERT INTO records VALUES ('tenant','row','agent_loop',?,?,2)",
                ("chiplog.loop.test.v1", raw),
            )
        if state == "COMPLETE":
            connection.execute(
                "INSERT INTO publications VALUES ('tenant',?,'selected','fingerprint',2,'row')",
                (command.operation_kind,),
            )
    evidence = SimpleNamespace(
        request=SimpleNamespace(cut=SimpleNamespace(materialization_commitment="source")),
        actor_budget_v1=suspension.RecoveryActorBudgetV1.model_construct(),
        canonical_bytes=lambda: b"inert evidence",
    )
    entry = dict(
        fingerprint="fingerprint",
        expected_head=1,
        predecessor="source",
        execution_suspension_evidence="inert evidence",
    )
    gate = AuthorityGate(database)
    runtime = SimpleNamespace(_database=database, _authority_gate=lambda: gate)
    monkeypatch.setattr(suspension, "_retained", lambda _: evidence)
    monkeypatch.setattr(suspension, "_command", lambda _: command)
    monkeypatch.setattr(successor, "_command", lambda _: command)

    def check_budget(_) -> None:
        raise BudgetValidationReached

    monkeypatch.setattr(suspension, "validate_recovery_actor_budget", check_budget)
    kwargs = {"allow_pending_absent": True} if allow_absent else {}
    permitted = state == "COMPLETE" or (state == "ABSENT" and allow_absent)
    with gate.hold():
        if family == "suspension":
            expected = BudgetValidationReached if permitted else ValueError
            with pytest.raises(expected):
                suspension._validate_selected(
                    runtime, entry, command, check_history=False, **kwargs
                )
        elif permitted:
            assert successor._selected_matches(runtime, entry, command, evidence, **kwargs)
        else:
            with pytest.raises(ValueError, match="physical publication is incomplete"):
                successor._selected_matches(runtime, entry, command, evidence, **kwargs)


@pytest.mark.parametrize(
    "family", ["suspension", "successor", "accepted-open-accounting", "accepted-open-recovery"]
)
@pytest.mark.parametrize("damage", [None, "anchor-advanced", "predecessor", "unselected", "orphan"])
def test_completed_pending_lifecycle_requires_exact_final_batch_and_current_anchor(
    tmp_path, monkeypatch, family: str, damage
) -> None:
    import hashlib

    from chiplog.platform._sqlite import (
        PhysicalPublicationCommand,
        PhysicalRecord,
        SQLiteMaterializer,
    )
    from chiplog.platform.authority_gate import AuthorityGate
    from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
    from chiplog.platform.workspace_snapshot import read_connection

    database = tmp_path / "completed-pending.sqlite"
    with SQLiteMaterializer(database, record_contracts={"agent_loop": "chiplog.loop.test.v1"}):
        pass
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO tenant_heads VALUES ('tenant', 1)")
        connection.execute(
            "INSERT INTO records VALUES ('tenant','first','agent_loop','chiplog.loop.test.v1',?,1)",
            (b"first",),
        )
        connection.execute(
            "INSERT INTO publications VALUES ('tenant','source','first','first',1,'first')"
        )
    with read_connection(database) as connection:
        predecessor = capture_authority_snapshot_commitment(connection, "tenant")
    raw = b"later"
    command = PhysicalPublicationCommand(
        tenant_id="tenant",
        operation_kind="agent_loop."
        + ("execution-" if family in ("suspension", "successor") else "")
        + family
        + ".v1",
        idempotency_key="pending",
        request_fingerprint="selected",
        expected_head=1,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=(
            PhysicalRecord(
                "later", "agent_loop", "chiplog.loop.test.v1", raw, hashlib.sha256(raw).hexdigest()
            ),
        ),
    )
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE tenant_heads SET head=2")
        connection.execute(
            "INSERT INTO records VALUES ('tenant','later','agent_loop','chiplog.loop.test.v1',?,2)",
            (raw,),
        )
        connection.execute(
            "INSERT INTO publications VALUES ('tenant',?,'pending','selected',2,'later')",
            (command.operation_kind,),
        )
        if damage == "orphan":
            connection.execute(
                "INSERT INTO records VALUES "
                "('tenant','orphan','agent_loop','chiplog.loop.test.v1',?,2)",
                (b"orphan",),
            )
    with read_connection(database) as connection:
        actual = capture_authority_snapshot_commitment(connection, "tenant")
    anchored = actual if damage == "anchor-advanced" else predecessor
    entry = dict(
        operation_kind=command.operation_kind,
        operation_id=command.idempotency_key,
        expected_head=command.expected_head,
        fingerprint=command.request_fingerprint,
        predecessor="wrong" if damage == "predecessor" else predecessor,
        resulting=actual,
    )
    gate = AuthorityGate(database)
    runtime = SimpleNamespace(
        _tenant_id="tenant",
        _database=database,
        _database_identity=("database", 1, 2),
        _publication=lambda _: command,
        _authority_gate=lambda: gate,
        _pending=lambda: (entry,),
        _pending_owners=lambda: (),
        _pending_gate_publications=lambda: (),
        _commitment_journal=SimpleNamespace(load=lambda _: anchored),
        _check_database_identity=lambda: None,
    )
    selected = () if damage == "unselected" else ((command, entry),)
    with gate.hold(), read_connection(database) as connection:
        kwargs = dict(pending_publication=entry, selected=selected, frontier=2)
        assert suffix.authenticate_native_open_pending_lifecycle_held(
            runtime, connection, actual, anchored, **kwargs
        ) is (damage in (None, "anchor-advanced"))
    if (
        family in ("suspension", "accepted-open-accounting", "accepted-open-recovery")
        and damage != "unselected"
    ):
        # Canonical owner acquisition is inert; final-batch checks use real SQLite.
        from chiplog.composition import r14_accepted_open_accounting_records as records
        from chiplog.composition.r14_recovery_publication import (
            authenticate_selected_recovery_prefix_held,
        )

        evidence = SimpleNamespace(
            request=SimpleNamespace(
                cut=SimpleNamespace(
                    tenant_id="tenant",
                    database_id="database",
                    materialization_commitment=predecessor,
                    tenant_commit_sequence=command.expected_head,
                )
            )
        )
        if family == "suspension":
            from chiplog.composition import r14_execution_suspension as suspension

            monkeypatch.setattr(suspension, "_retained", lambda _: evidence)
            monkeypatch.setattr(
                suspension,
                "_command",
                lambda _: replace(command, expected_preimage_commitment=predecessor),
            )
        else:
            recovery = family == "accepted-open-recovery"
            monkeypatch.setattr(
                records,
                (
                    "retained_accepted_open_recovery"
                    if recovery
                    else "retained_accepted_open_accounting"
                ),
                lambda _: evidence,
            )
            monkeypatch.setattr(
                records,
                (
                    "accepted_open_recovery_command"
                    if recovery
                    else "accepted_open_accounting_command"
                ),
                lambda _: command,
            )
        with gate.hold():
            assert authenticate_selected_recovery_prefix_held(runtime, actual, anchored) is (
                damage in (None, "anchor-advanced")
            )
