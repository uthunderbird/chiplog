import hashlib
import hmac
import time
from dataclasses import replace

import pytest

from chiplog.composition.postgres_planning import PostgresPlanningRuntime
from chiplog.platform import postgres_gate
from chiplog.platform.deployment_gate import DeploymentGateRequest
from chiplog.platform.postgres import PostgresServer
from tests.support.deployment_gate import KEY, request, signature
from tests.support.r8_journey import (
    IDENTITY,
    SECRET,
    command,
    planning_entitlement,
    planning_request,
)


def verifier(payload: bytes, proof: bytes) -> bool:
    return hmac.compare_digest(hmac.digest(KEY, payload, "sha256"), proof)


async def test_postgres_planning_bootstrap_create_reopen_replay_projection_and_revocation(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "time_ns", lambda: 10)
    database = postgres_server.user_database("t")
    database.provision()
    runtime = PostgresPlanningRuntime(database, "t", SECRET, verifier)
    await runtime.bootstrap(database_instance_id="db", token="token", **IDENTITY)
    value = command()
    assert (
        await runtime.create(**IDENTITY, command=value, gate_request=None)
    ).disposition == "DENIED"
    with pytest.raises(PermissionError, match="HOLD"):
        await runtime.render(**IDENTITY, gate_request=None)
    current = planning_entitlement()
    with pytest.raises(PermissionError, match="proof"):
        runtime.import_entitlement(current, b"invalid")
    runtime.import_entitlement(current, signature(current))
    outcome = await runtime.create(**IDENTITY, command=value, gate_request=planning_request(value))
    assert outcome.disposition == "COMMITTED" and outcome.result is not None
    assert outcome.result.commit_sequence == 1
    before = database.snapshot()
    assert len(before.batches[0].records) == 5
    runtime = PostgresPlanningRuntime(replace(database), "t", SECRET, verifier)
    replay = await runtime.create(**IDENTITY, command=value, gate_request=planning_request(value))
    assert replay.disposition == "REPLAY" and replay.result == outcome.result
    assert database.snapshot() == before
    foreign = {**IDENTITY, "principal_id": "another"}
    with pytest.raises(PermissionError, match="authenticated"):
        await runtime.create(**foreign, command=value, gate_request=planning_request(value))
    rendered = ("tenant=t records=5", "intention-1", "purpose=Prepare release")
    read_current = current.model_copy(
        update={
            "entitlement_head": "read-entitlement",
            "bounds": current.bounds.model_copy(
                update={
                    "capability_id": "planning.read",
                }
            ),
        }
    )
    runtime.import_entitlement(
        read_current,
        signature(read_current),
        previous=postgres_gate.canonical(current.model_dump(mode="json")),
    )
    read_request = request(read_current, "show").model_copy(
        update={
            "surface_id": "cli.render",
            "payload_digest": hashlib.sha256(postgres_gate.canonical(rendered)).hexdigest(),
        }
    )
    assert await runtime.render(**IDENTITY, gate_request=read_request) == rendered
    runtime.revoke("credential:1")
    with pytest.raises(PermissionError, match="authenticated"):
        await runtime.render(**IDENTITY, gate_request=read_request)
    with pytest.raises(PermissionError, match="authenticated"):
        await runtime.create(**IDENTITY, command=value, gate_request=planning_request(value))


async def test_planning_and_chat_records_share_one_atomic_user_head(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chiplog.platform.postgres import Record

    monkeypatch.setattr(time, "time_ns", lambda: 10)
    database = postgres_server.user_database("t")
    database.provision()
    runtime = PostgresPlanningRuntime(database, "t", SECRET, verifier)
    await runtime.bootstrap(database_instance_id="db", token="token", **IDENTITY)
    database.publish("other", "key", "fp", (Record("other", "owner", "schema", b"bytes"),), 0)
    current = planning_entitlement()
    runtime.import_entitlement(current, signature(current))
    value = command()
    result = await runtime.create(**IDENTITY, command=value, gate_request=planning_request(value))
    assert result.disposition == "COMMITTED" and result.result is not None
    assert result.result.commit_sequence == 2
    assert database.snapshot().head == 2


async def test_planning_batch_and_deployment_cap_commit_together(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "time_ns", lambda: 10)
    database = postgres_server.user_database("t")
    database.provision()
    runtime = PostgresPlanningRuntime(database, "t", SECRET, verifier)
    await runtime.bootstrap(database_instance_id="db", token="token", **IDENTITY)
    current = planning_entitlement()
    runtime.import_entitlement(current, signature(current))
    value = command()
    original = postgres_gate.PostgresDeploymentGate.crossed

    def fail_after_insert(
        self: postgres_gate.PostgresDeploymentGate,
        request: DeploymentGateRequest,
        payload: bytes,
    ) -> None:
        original(self, request, payload)
        raise RuntimeError("lost before PostgreSQL commit")

    with monkeypatch.context() as patched:
        patched.setattr(postgres_gate.PostgresDeploymentGate, "crossed", fail_after_insert)
        with pytest.raises(RuntimeError, match="before PostgreSQL commit"):
            await runtime.create(**IDENTITY, command=value, gate_request=planning_request(value))
    assert database.snapshot().head == 0
    with database.read() as connection:
        assert connection.execute("SELECT count(*) FROM gate_handoffs").fetchone() == (0,)
    assert (
        await runtime.create(**IDENTITY, command=value, gate_request=planning_request(value))
    ).disposition == "COMMITTED"
