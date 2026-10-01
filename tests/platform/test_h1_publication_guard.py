from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from chiplog.composition.h1_publication_resolver_mux import H1PublicationResolverMux
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
SCOPED_INTENT_SCHEMA = "chiplog.effects.external-action-intent.v3"
DELIVERY_WORK_SCHEMA = "chiplog.delivery-work.subject.v1"
DELIVERY_WORK_GENESIS = "agent_loop.delivery_work.genesis.v1"
DELIVERY_WORK_CLAIM = "agent_loop.delivery_work.claim.v1"


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
        record_schema_variants=(
            ("effects", SCOPED_INTENT_SCHEMA),
            ("agent_loop", DELIVERY_WORK_SCHEMA),
        ),
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


@pytest.mark.parametrize("schema", (LOCAL_INTENT_SCHEMA, SCOPED_INTENT_SCHEMA))
async def test_h1_intent_under_an_alternate_operation_is_refused(
    tmp_path: Path, schema: str
) -> None:
    store, appender = await _appender(tmp_path / "store.sqlite")
    body = b"intent"
    local_intent = PhysicalRecord("intent", "effects", schema, body, sha256(body).hexdigest())

    with pytest.raises(StoreAdmissionError, match="H1-only record"):
        await appender.submit(_command(operation="ordinary", records=(local_intent,)))

    assert store.durable_records() == ()
    await appender.close()


@pytest.mark.parametrize(
    "record,operation,reason",
    (
        (
            lambda: PhysicalRecord(
                "delivery-work:subject:g0",
                "agent_loop",
                DELIVERY_WORK_SCHEMA,
                b"delivery-work",
                sha256(b"delivery-work").hexdigest(),
            ),
            "ordinary",
            "alternate operation",
        ),
        (
            lambda: PhysicalRecord(
                "delivery-work:subject:g0",
                "effects",
                "ordinary.v1",
                b"reserved-id",
                sha256(b"reserved-id").hexdigest(),
            ),
            "ordinary",
            "alternate operation",
        ),
        (
            lambda: PhysicalRecord(
                "delivery-work:subject:g0",
                "effects",
                DELIVERY_WORK_SCHEMA,
                b"alternate-owner",
                sha256(b"alternate-owner").hexdigest(),
            ),
            DELIVERY_WORK_GENESIS,
            "owner, schema, or reserved ID differs",
        ),
        (
            lambda: PhysicalRecord(
                "delivery-work:subject:g0",
                "agent_loop",
                "ordinary.v1",
                b"alternate-schema",
                sha256(b"alternate-schema").hexdigest(),
            ),
            DELIVERY_WORK_GENESIS,
            "owner, schema, or reserved ID differs",
        ),
    ),
)
async def test_delivery_work_schema_and_reserved_ids_are_refused_outside_closed_operations(
    tmp_path: Path, record, operation: str, reason: str
) -> None:
    store, appender = await _appender(tmp_path / "store.sqlite")

    with pytest.raises(StoreAdmissionError, match=reason):
        await appender.submit(_command(operation=operation, records=(record(),)))

    assert store.durable_records() == ()
    await appender.close()


@pytest.mark.parametrize("operation", (DELIVERY_WORK_GENESIS, DELIVERY_WORK_CLAIM))
async def test_delivery_work_closed_operation_refuses_without_mounted_resolver(
    tmp_path: Path, operation: str
) -> None:
    store, appender = await _appender(tmp_path / "store.sqlite")
    observer_calls: list[str] = []

    class Observer:
        def decide_publication(self, _: PhysicalPublicationCommand, __: str) -> None:
            observer_calls.append("decide")

        def publication_committed(self, _: PhysicalPublicationCommand) -> None:
            observer_calls.append("committed")

    appender.bind_publication_observer(Observer())
    body = b"delivery-work"
    record = PhysicalRecord(
        "delivery-work:subject:g0",
        "agent_loop",
        DELIVERY_WORK_SCHEMA,
        body,
        sha256(body).hexdigest(),
    )

    assert (
        await appender.submit(_command(operation=operation, records=(record,)))
    ).disposition == "DENIED"
    caller_guard_called = False

    def caller_guard(_: str) -> None:
        nonlocal caller_guard_called
        caller_guard_called = True

    assert (
        await appender.submit(
            replace(
                _command(operation=operation, records=(record,)),
                idempotency_key="caller-guard",
                decision_guard=caller_guard,
            )
        )
    ).disposition == "DENIED"

    assert store.durable_records() == ()
    assert observer_calls == [] and not caller_guard_called
    await appender.close()


@pytest.mark.parametrize("operation", (DELIVERY_WORK_GENESIS, DELIVERY_WORK_CLAIM))
async def test_delivery_work_operations_refuse_unrelated_records(
    tmp_path: Path, operation: str
) -> None:
    store, appender = await _appender(tmp_path / "store.sqlite")

    with pytest.raises(StoreAdmissionError, match="has no reserved record"):
        await appender.submit(_command(operation=operation))

    assert store.durable_records() == ()
    await appender.close()


async def test_delivery_work_operation_refuses_mixed_bundle_before_resolver_routing(
    tmp_path: Path,
) -> None:
    store, appender = await _appender(tmp_path / "store.sqlite")
    body = b"delivery-work"
    delivery_work = PhysicalRecord(
        "delivery-work:subject:g0",
        "agent_loop",
        DELIVERY_WORK_SCHEMA,
        body,
        sha256(body).hexdigest(),
    )

    with pytest.raises(StoreAdmissionError, match="has unrelated record"):
        await appender.submit(
            _command(operation=DELIVERY_WORK_GENESIS, records=(delivery_work, _record("ordinary")))
        )

    assert store.durable_records() == ()
    await appender.close()


class _Resolver:
    def __init__(self, command: PhysicalPublicationCommand) -> None:
        self.command = command
        self.modes: list[PublicationVerificationMode] = []

    def verify(
        self,
        command: PhysicalPublicationCommand,
        mode: PublicationVerificationMode,
        _connection: object,
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


def test_h1_resolver_mux_dispatches_only_registered_h1_operations(tmp_path: Path) -> None:
    store = SQLiteMaterializer(
        tmp_path / "store.sqlite", record_contracts={"agent_loop": "chiplog.agent-loop.record.v1"}
    )
    completion, genesis, claim = _Resolver(_command()), _Resolver(_command()), _Resolver(_command())
    mux = H1PublicationResolverMux(completion, genesis, claim)
    genesis_command = _command(operation=DELIVERY_WORK_GENESIS, records=(_record("work"),))

    mux.verify(_command(), "ABSENT", store._connection)
    mux.verify(genesis_command, "ABSENT", store._connection)
    mux.verify(_command(operation=DELIVERY_WORK_CLAIM), "ABSENT", store._connection)
    with pytest.raises(StoreAdmissionError, match="not registered"):
        mux.verify(
            _command(operation="agent_loop.delivery_work.unknown.v1"), "ABSENT", store._connection
        )

    assert completion.modes == ["ABSENT"]
    assert genesis.modes == ["ABSENT"]
    assert claim.modes == ["ABSENT"]
    store.close()


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
            self,
            candidate: PhysicalPublicationCommand,
            mode: PublicationVerificationMode,
            connection: object,
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
            return super().verify(candidate, mode, connection)

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
