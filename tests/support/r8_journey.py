"""Independent entitlement fixtures for exercising the canonical R8 runtime."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.capabilities.planning import CreateIntentionLine
from chiplog.composition.r8 import R8_SURFACES, R8PlanningRuntime, command_bytes, open_r8_runtime
from chiplog.domain_primitives import RecordId, TenantId
from chiplog.platform.deployment_gate import CurrentEntitlement, DeploymentGateRequest
from chiplog.platform.r8_gate import BrokerDeploymentGate
from tests.support.deployment_gate import KEY, entitlement, request, signature

IDENTITY = {"principal_id": "p", "credential_id": "c", "session_id": "s"}
SECRET = b"r8-journey-secret"


def command(suffix: str = "1", purpose: str = "Prepare release") -> CreateIntentionLine:
    tenant = TenantId("t")
    return CreateIntentionLine(
        RecordId(tenant, f"command-{suffix}"),
        RecordId(tenant, f"intention-{suffix}"),
        RecordId(tenant, f"revision-{suffix}"),
        purpose,
        f"act-{suffix}",
    )


def planning_entitlement() -> CurrentEntitlement:
    value = entitlement()
    return value.model_copy(
        update={
            "bounds": value.bounds.model_copy(
                update={"capability_id": "planning.create_intention_line", "cap": 4}
            )
        }
    )


def planning_request(value: CreateIntentionLine) -> DeploymentGateRequest:
    return request(planning_entitlement(), value.command_id.value).model_copy(
        update={
            "surface_id": "planning.create",
            "payload_digest": hashlib.sha256(command_bytes(value)).hexdigest(),
        }
    )


@asynccontextmanager
async def authorized_runtime(database: Path) -> AsyncIterator[R8PlanningRuntime]:
    initialize = not await asyncio.to_thread(database.exists)
    async with open_r8_runtime(database, tenant_id="t", operator_secret=SECRET) as runtime:
        if initialize:
            await runtime.bootstrap(database_instance_id="db", token="token", **IDENTITY)
        gate = BrokerDeploymentGate(
            database.with_suffix(database.suffix + ".gate.sqlite3"),
            tenant_id="t",
            surfaces=R8_SURFACES,
            authenticate=lambda payload, proof: hmac.compare_digest(
                hmac.digest(KEY, payload, "sha256"), proof
            ),
            journal=IndependentTenantDecisionJournal(
                database.with_suffix(database.suffix + ".gate-journal")
            ),
            clock=lambda: 10,
        )
        if gate.observe() is None:
            value = planning_entitlement()
            assert gate.import_current(value, signature(value), expected=None)
        runtime._gate = gate
        yield runtime


def authoritative_snapshot(database: Path) -> dict[str, tuple[tuple[object, ...], ...]]:
    with sqlite3.connect(database) as connection:
        return {
            table: tuple(connection.execute(f"SELECT * FROM {table} ORDER BY 1, 2"))
            for table in ("deletion_fences", "tenant_heads", "publications", "records")
        }
