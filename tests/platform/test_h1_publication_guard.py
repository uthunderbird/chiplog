from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from chiplog.platform._sqlite import (
    EventAppender,
    FenceAdvanceCommand,
    PhysicalPublicationCommand,
    PhysicalRecord,
    PublicationVerificationMode,
    SQLiteMaterializer,
    StoreAdmissionError,
    VerifiedOwnerPublication,
)

V2_OPERATION = "agent_loop.complete_acceptance.v2"
LOCAL_INTENT_SCHEMA = "chiplog.effects.h1-local-prepared-commentary-intent.v1"


def _record(record_id: str = "complete") -> PhysicalRecord:
    body = b"complete acceptance"
    return PhysicalRecord(
        record_id, "agent_loop", "chiplog.agent-loop.record.v1", body, sha256(body).hexdigest()
    )


def _command(
    *, operation: str = V2_OPERATION, records: tuple[PhysicalRecord, ...] | None = None
) -> PhysicalPublicationCommand:
    return PhysicalPublicationCommand(
        tenant_id="tenant",
        operation_kind=operation,
        idempotency_key="complete",
        request_fingerprint="request",
        expected_head=0,
        fence_generation="fence",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=records or (_record(),),
    )


async def _appender(path: Path) -> tuple[SQLiteMaterializer, EventAppender]:
    store = SQLiteMaterializer(
        path,
        record_contracts={
            "agent_loop": "chiplog.agent-loop.record.v1",
            "effects": LOCAL_INTENT_SCHEMA,
        },
    )
    appender = EventAppender(store, capacity=2)
    await appender.advance_fence(FenceAdvanceCommand("tenant", "fence", 0))
    return store, appender


async def test_v2_absent_refuses_without_mounted_resolver_even_without_command_guards(
    tmp_path: Path,
) -> None:
    store, appender = await _appender(tmp_path / "store.sqlite")

    assert (await appender.submit(_command())).disposition == "DENIED"

    assert store.durable_records() == ()
    await appender.close()


async def test_v2_replay_refuses_without_mounted_resolver_even_without_command_guards(
    tmp_path: Path,
) -> None:
    store, appender = await _appender(tmp_path / "store.sqlite")
    connection = store._connection
    connection.execute(
        "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)",
        (
            "tenant",
            "complete",
            "agent_loop",
            "chiplog.agent-loop.record.v1",
            b"complete acceptance",
            1,
        ),
    )
    connection.execute(
        "INSERT INTO publications VALUES (?, ?, ?, ?, ?, ?)",
        ("tenant", V2_OPERATION, "complete", "request", 1, "complete"),
    )
    connection.execute("INSERT INTO tenant_heads VALUES (?, ?)", ("tenant", 1))
    connection.commit()

    assert (await appender.submit(_command())).disposition == "DENIED"

    assert store.durable_records() == (
        (
            "tenant",
            "complete",
            "agent_loop",
            "chiplog.agent-loop.record.v1",
            b"complete acceptance",
            1,
        ),
    )
    await appender.close()


async def test_h1_local_intent_under_an_alternate_operation_is_refused(tmp_path: Path) -> None:
    store, appender = await _appender(tmp_path / "store.sqlite")
    body = b"intent"
    local_intent = PhysicalRecord(
        "intent", "effects", LOCAL_INTENT_SCHEMA, body, sha256(body).hexdigest()
    )

    with pytest.raises(StoreAdmissionError, match="H1-only record"):
        await appender.submit(_command(operation="ordinary", records=(local_intent,)))

    assert store.durable_records() == ()
    await appender.close()


class _Resolver:
    def __init__(self, command: PhysicalPublicationCommand) -> None:
        self.command = command
        self.modes: list[PublicationVerificationMode] = []

    def verify(
        self, command: PhysicalPublicationCommand, mode: PublicationVerificationMode
    ) -> VerifiedOwnerPublication:
        self.modes.append(mode)
        return VerifiedOwnerPublication.from_command(
            command,
            binding_fingerprint="binding",
            selected_identity=None if mode == "PRESELECT" else "selected",
            selected_fingerprint=None if mode == "PRESELECT" else "selected-fingerprint",
            expected_resulting=None,
            expected_commit_sequence=1,
        )


class _Observer:
    def decide_publication(self, command: PhysicalPublicationCommand, commitment: str) -> None:
        raise AssertionError("V2 must not synthesize its decision guard through an observer")

    def publication_committed(self, command: PhysicalPublicationCommand) -> None:
        raise AssertionError("V2 must not publish through an observer")


async def test_mounted_v2_verifier_runs_store_derived_absent_preselect_and_precommit_modes(
    tmp_path: Path,
) -> None:
    command = _command()
    store = SQLiteMaterializer(
        tmp_path / "store.sqlite", record_contracts={"agent_loop": "chiplog.agent-loop.record.v1"}
    )
    resolver = _Resolver(command)
    store._mount_owner_publication_resolver(resolver)
    appender = EventAppender(store, capacity=2)
    appender.bind_publication_observer(_Observer())
    await appender.advance_fence(FenceAdvanceCommand("tenant", "fence", 0))

    assert (await appender.submit(command)).disposition == "DENIED"

    assert resolver.modes == ["ABSENT", "PRESELECT"]
    assert store.durable_records() == ()
    await appender.close()


async def test_v2_precommit_reopens_selected_only_after_decision_guard(tmp_path: Path) -> None:
    command = _command()
    selected = False

    class Resolver(_Resolver):
        def verify(
            self, candidate: PhysicalPublicationCommand, mode: PublicationVerificationMode
        ) -> VerifiedOwnerPublication:
            if mode == "PRECOMMIT":
                self.modes.append(mode)
                assert selected
                return VerifiedOwnerPublication.from_command(
                    candidate,
                    binding_fingerprint="binding",
                    selected_identity="selected",
                    selected_fingerprint="selected-fingerprint",
                    expected_resulting="not-the-physical-result",
                    expected_commit_sequence=1,
                )
            return super().verify(candidate, mode)

    store = SQLiteMaterializer(
        tmp_path / "store.sqlite", record_contracts={"agent_loop": "chiplog.agent-loop.record.v1"}
    )
    resolver = Resolver(command)
    store._mount_owner_publication_resolver(resolver)
    appender = EventAppender(store, capacity=2)
    await appender.advance_fence(FenceAdvanceCommand("tenant", "fence", 0))

    def decide(_: str) -> None:
        nonlocal selected
        selected = True

    with pytest.raises(StoreAdmissionError, match="resulting commitment"):
        await appender.submit(replace(command, decision_guard=decide))

    assert resolver.modes == ["ABSENT", "PRESELECT", "PRECOMMIT"]
    assert store.durable_records() == ()
    await appender.close()


async def test_mounted_v2_verifier_runs_replay_mode_and_rejects_changed_sql_membership(
    tmp_path: Path,
) -> None:
    command = _command()
    store = SQLiteMaterializer(
        tmp_path / "store.sqlite", record_contracts={"agent_loop": "chiplog.agent-loop.record.v1"}
    )
    resolver = _Resolver(command)
    store._mount_owner_publication_resolver(resolver)
    appender = EventAppender(store, capacity=2)
    await appender.advance_fence(FenceAdvanceCommand("tenant", "fence", 0))
    connection = store._connection
    connection.execute(
        "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)",
        ("tenant", "complete", "agent_loop", "chiplog.agent-loop.record.v1", b"tampered", 1),
    )
    connection.execute(
        "INSERT INTO publications VALUES (?, ?, ?, ?, ?, ?)",
        ("tenant", V2_OPERATION, "complete", "request", 1, "complete"),
    )
    connection.execute("INSERT INTO tenant_heads VALUES (?, ?)", ("tenant", 1))
    connection.commit()

    with pytest.raises(StoreAdmissionError, match="membership"):
        await appender.submit(command)

    assert resolver.modes == ["REPLAY"]
    await appender.close()


async def test_non_v2_publication_remains_unchanged_without_a_resolver(tmp_path: Path) -> None:
    _store, appender = await _appender(tmp_path / "store.sqlite")

    assert (await appender.submit(_command(operation="ordinary"))).disposition == "COMMITTED"

    await appender.close()
