from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.capabilities.planning import CreateIntentionLine
from chiplog.composition.r8 import R8_SURFACES, command_bytes, open_r8_runtime
from chiplog.domain_primitives import RecordId, TenantId
from chiplog.platform._sqlite import PhysicalPublicationCommand, PublicationResult
from chiplog.platform.deployment_gate import (
    DeploymentGateRequest,
)
from chiplog.platform.r8_gate import (
    BrokerDeploymentGate,
)
from tests.support.deployment_gate import KEY, entitlement, request, signature


@pytest.mark.parametrize("authorized", [False, True])
@pytest.mark.parametrize("fault", ["none", "before_commit", "after_commit"])
def test_canonical_r8_runtime_traces_before_durable_planning_publication(
    tmp_path: Path, authorized: bool, fault: Literal["none", "before_commit", "after_commit"]
) -> None:
    database = tmp_path / "planning.sqlite"
    tenant = TenantId("t")
    command = CreateIntentionLine(
        RecordId(tenant, "op"),
        RecordId(tenant, "line"),
        RecordId(tenant, "revision"),
        "synthetic fixture",
        "act",
    )
    saved_request: DeploymentGateRequest | None = None

    async def run() -> None:
        nonlocal saved_request
        async with open_r8_runtime(database, tenant_id="t", operator_secret=b"fixture") as runtime:
            await runtime.bootstrap(
                database_instance_id="db",
                principal_id="p",
                credential_id="c",
                session_id="s",
                token="token",
            )
            gate_request = None
            if authorized:
                fixture_gate = BrokerDeploymentGate(
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
                value = entitlement().model_copy(
                    update={
                        "bounds": entitlement().bounds.model_copy(
                            update={"capability_id": "planning.create_intention_line"}
                        )
                    }
                )
                assert fixture_gate.import_current(value, signature(value), expected=None)
                runtime._gate = fixture_gate
                gate_request = request(value).model_copy(
                    update={
                        "surface_id": "planning.create",
                        "payload_digest": hashlib.sha256(command_bytes(command)).hexdigest(),
                    }
                )
                saved_request = gate_request
                for field in ("command_id", "intention_line_id", "revision_id"):
                    foreign_id = RecordId(TenantId("foreign"), getattr(command, field).value)
                    foreign = replace(
                        command,
                        command_id=foreign_id if field == "command_id" else command.command_id,
                        intention_line_id=(
                            foreign_id
                            if field == "intention_line_id"
                            else command.intention_line_id
                        ),
                        revision_id=foreign_id if field == "revision_id" else command.revision_id,
                    )
                    rejected = await runtime.create(
                        principal_id="p",
                        credential_id="c",
                        session_id="s",
                        command=foreign,
                        gate_request=gate_request,
                    )
                    assert rejected.disposition == "DENIED" and rejected.result is None
            if authorized and fault != "none":
                submit = runtime._appender.submit

                async def fail_publication(
                    command: PhysicalPublicationCommand,
                ) -> PublicationResult:
                    return await submit(replace(command, fault=fault))

                runtime._appender.submit = fail_publication  # type: ignore[method-assign]
                with pytest.raises(RuntimeError):
                    await runtime.create(
                        principal_id="p",
                        credential_id="c",
                        session_id="s",
                        command=command,
                        gate_request=gate_request,
                    )
                assert runtime._gate is not None
                pending = runtime._gate.pending()
                assert len(pending) == 1 and pending[0][1]
                return
            outcome = await runtime.create(
                principal_id="p",
                credential_id="c",
                session_id="s",
                command=command,
                gate_request=gate_request,
            )
            assert outcome.disposition == ("COMMITTED" if authorized else "DENIED")
            if authorized:
                assert runtime._traced_request is not None
                assert b"deployment_trust.current" in runtime._traced_request.authority_trace_bytes
                repeated = await runtime.create(
                    principal_id="p",
                    credential_id="c",
                    session_id="s",
                    command=command,
                    gate_request=saved_request,
                )
                assert repeated.disposition == "REPLAY" and repeated.result == outcome.result
                denied = await runtime.create(
                    principal_id="foreign",
                    credential_id="c",
                    session_id="s",
                    command=command,
                    gate_request=saved_request,
                )
                assert denied.disposition == "DENIED" and denied.result is None
                changed = replace(command, purpose="changed")
                assert saved_request is not None
                conflict = await runtime.create(
                    principal_id="p",
                    credential_id="c",
                    session_id="s",
                    command=changed,
                    gate_request=saved_request.model_copy(
                        update={
                            "payload_digest": hashlib.sha256(command_bytes(changed)).hexdigest(),
                        }
                    ),
                )
                assert conflict.disposition == "CONFLICT" and conflict.result is None

    asyncio.run(run())
    if authorized and fault != "none":

        async def recover() -> None:
            # No current entitlement adapter is supplied: this is materialization
            # of an earlier exact decision, never fresh authorization.
            async with open_r8_runtime(
                database, tenant_id="t", operator_secret=b"fixture"
            ) as runtime:
                assert runtime._gate is not None
                assert runtime._gate.pending() == ()
                journal = IndependentTenantDecisionJournal(
                    database.with_suffix(database.suffix + ".gate-journal")
                )
                decisions_before = journal.entries()
                repeated = await runtime.create(
                    principal_id="p",
                    credential_id="c",
                    session_id="s",
                    command=command,
                    gate_request=saved_request,
                )
                assert repeated.disposition == "REPLAY" and repeated.result is not None
                assert repeated.result.intention_line_id == command.intention_line_id
                assert journal.entries() == decisions_before

        asyncio.run(recover())
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM records").fetchone() == (
            (5 if authorized else 0),
        )


@pytest.mark.parametrize("revoke_before_delivery", [False, True])
def test_actual_render_releases_exact_bytes_only_with_current_entitlement(
    tmp_path: Path, revoke_before_delivery: bool
) -> None:
    database = tmp_path / "planning.sqlite"
    expected = ("tenant=t records=0",)
    payload = json.dumps(expected, separators=(",", ":")).encode()
    received: list[tuple[str, ...]] = []

    async def run() -> None:
        async with open_r8_runtime(database, tenant_id="t", operator_secret=b"fixture") as runtime:
            await runtime.bootstrap(
                database_instance_id="db",
                principal_id="p",
                credential_id="c",
                session_id="s",
                token="token",
            )
            fixture_gate = BrokerDeploymentGate(
                tmp_path / "gate.sqlite",
                tenant_id="t",
                surfaces=R8_SURFACES,
                authenticate=lambda raw, proof: hmac.compare_digest(
                    hmac.digest(KEY, raw, "sha256"), proof
                ),
                journal=IndependentTenantDecisionJournal(tmp_path / "gate.journal"),
                clock=lambda: 10,
            )
            value = entitlement().model_copy(
                update={
                    "bounds": entitlement().bounds.model_copy(
                        update={"capability_id": "planning.read"}
                    )
                }
            )
            assert fixture_gate.import_current(value, signature(value), expected=None)
            runtime._gate = fixture_gate
            attempt = request(value, "render").model_copy(
                update={
                    "surface_id": "cli.render",
                    "payload_digest": hashlib.sha256(payload).hexdigest(),
                }
            )
            assert fixture_gate.check(attempt).disposition != "HOLD"
            snapshot = runtime._projection_snapshot
            reached = False

            def observe_projection() -> bytes:
                nonlocal reached
                result = snapshot()
                reached = True
                if revoke_before_delivery:
                    revoked = value.model_copy(
                        update={
                            "status": "REVOKED",
                            "generation": value.generation.model_copy(update={"sequence": 1}),
                        }
                    )
                    assert fixture_gate.import_current(revoked, signature(revoked), expected=value)
                return result

            runtime._projection_snapshot = observe_projection  # type: ignore[method-assign]

            async def deliver() -> None:
                received.append(
                    await runtime.render(
                        principal_id="p",
                        credential_id="c",
                        session_id="s",
                        gate_request=attempt,
                    )
                )

            if revoke_before_delivery:
                with pytest.raises(PermissionError, match="eligibility changed"):
                    await deliver()
                assert fixture_gate.crossed("render") is None
            else:
                await deliver()
                assert fixture_gate.crossed("render") is not None
                with pytest.raises(PermissionError):
                    await deliver()
            assert reached

    asyncio.run(run())
    assert received == ([] if revoke_before_delivery else [expected])


@pytest.mark.parametrize("change", ["planning-read", "eligibility"])
def test_actual_publication_cut_rejects_changed_inputs_without_result(
    tmp_path: Path, change: str
) -> None:
    database = tmp_path / "planning.sqlite"
    tenant = TenantId("t")
    command = CreateIntentionLine(
        RecordId(tenant, "op"),
        RecordId(tenant, "line"),
        RecordId(tenant, "revision"),
        "fixture",
        "act",
    )

    async def run() -> None:
        async with open_r8_runtime(database, tenant_id="t", operator_secret=b"fixture") as runtime:
            await runtime.bootstrap(
                database_instance_id="db",
                principal_id="p",
                credential_id="c",
                session_id="s",
                token="token",
            )
            fixture_gate = BrokerDeploymentGate(
                tmp_path / "gate.sqlite",
                tenant_id="t",
                surfaces=R8_SURFACES,
                authenticate=lambda payload, proof: hmac.compare_digest(
                    hmac.digest(KEY, payload, "sha256"), proof
                ),
                journal=IndependentTenantDecisionJournal(tmp_path / "gate.journal"),
                clock=lambda: 10,
            )
            value = entitlement().model_copy(
                update={
                    "bounds": entitlement().bounds.model_copy(
                        update={"capability_id": "planning.create_intention_line"}
                    )
                }
            )
            assert fixture_gate.import_current(value, signature(value), expected=None)
            runtime._gate = fixture_gate
            attempt = request(value).model_copy(
                update={
                    "surface_id": "planning.create",
                    "payload_digest": hashlib.sha256(command_bytes(command)).hexdigest(),
                }
            )
            assert fixture_gate.check(attempt).disposition != "HOLD"
            submit = runtime._appender.submit
            snapshot = runtime._snapshot
            reached = False

            async def intervene(command: PhysicalPublicationCommand) -> PublicationResult:
                nonlocal reached
                reached = True
                assert runtime._traced_request is not None
                if change == "eligibility":
                    changed = value.model_copy(
                        update={
                            "status": "REVOKED",
                            "generation": value.generation.model_copy(update={"sequence": 1}),
                        }
                    )
                    assert fixture_gate.import_current(changed, signature(changed), expected=value)
                else:

                    def changed_read() -> bytes:
                        value = json.loads(snapshot())
                        value["head"] += 1
                        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

                    runtime._snapshot = changed_read  # type: ignore[method-assign]
                return await submit(command)

            runtime._appender.submit = intervene  # type: ignore[method-assign]
            outcome = await runtime.create(
                principal_id="p",
                credential_id="c",
                session_id="s",
                command=command,
                gate_request=attempt,
            )
            assert reached and outcome.disposition in {"STALE", "DENIED"}
            assert outcome.result is None and fixture_gate.crossed("op") is None

    asyncio.run(run())
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM records").fetchone() == (0,)
