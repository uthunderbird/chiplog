"""Retained user semantics exercised through R8, without historical CLI substitution."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.support.deployment_gate import request, signature
from tests.support.r8_journey import (
    IDENTITY,
    authoritative_snapshot,
    authorized_runtime,
    command,
    planning_entitlement,
    planning_request,
)


async def test_authorized_planning_persists_multiple_commands_and_renders_after_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "planning.sqlite"
    first, second = command(), command("2", "Prepare rollout")
    async with authorized_runtime(database) as runtime:
        for value in (first, second):
            result = await runtime.create(
                **IDENTITY, command=value, gate_request=planning_request(value)
            )
            assert result.disposition == "COMMITTED"
        repeated = await runtime.create(
            **IDENTITY, command=first, gate_request=planning_request(first)
        )
        assert repeated.disposition == "REPLAY"
    before = authoritative_snapshot(database)
    async with authorized_runtime(database) as runtime:
        repeated = await runtime.create(
            **IDENTITY, command=first, gate_request=planning_request(first)
        )
        assert repeated.disposition == "REPLAY"
        assert authoritative_snapshot(database) == before
        expected = (
            "tenant=t records=10",
            "intention-1",
            "purpose=Prepare release",
            "intention-2",
            "purpose=Prepare rollout",
        )
        entitlement = planning_entitlement()
        read = entitlement.model_copy(
            update={
                "generation": entitlement.generation.model_copy(update={"sequence": 1}),
                "bounds": entitlement.bounds.model_copy(update={"capability_id": "planning.read"}),
            }
        )
        assert runtime._gate is not None
        assert runtime._gate.import_current(read, signature(read), expected=entitlement)
        payload = json.dumps(expected, separators=(",", ":")).encode()
        attempt = request(read, "render").model_copy(
            update={
                "surface_id": "cli.render",
                "payload_digest": hashlib.sha256(payload).hexdigest(),
            }
        )
        assert await runtime.render(**IDENTITY, gate_request=attempt) == expected


@pytest.mark.parametrize("field", ["principal_id", "credential_id", "session_id"])
async def test_current_identity_substitution_denies_without_publication(
    tmp_path: Path, field: str
) -> None:
    database = tmp_path / "planning.sqlite"
    async with authorized_runtime(database) as runtime:
        before = authoritative_snapshot(database)
        value = command()
        result = await runtime.create(
            **{**IDENTITY, field: "foreign"}, command=value, gate_request=planning_request(value)
        )
        assert result.disposition in {"DENIED", "STALE"} and result.result is None
        assert authoritative_snapshot(database) == before
        assert runtime._gate is not None and runtime._gate.crossed(value.command_id.value) is None
        valid = await runtime.create(
            **IDENTITY, command=value, gate_request=planning_request(value)
        )
        assert valid.disposition == "COMMITTED"


async def test_invalid_planning_command_leaves_authoritative_state_unchanged(
    tmp_path: Path,
) -> None:
    database = tmp_path / "planning.sqlite"
    async with authorized_runtime(database) as runtime:
        before = authoritative_snapshot(database)
        value = command(purpose="   ")
        result = await runtime.create(
            **IDENTITY, command=value, gate_request=planning_request(value)
        )
        assert result.disposition == "DENIED" and result.result is None
        assert authoritative_snapshot(database) == before
        assert runtime._gate is not None and runtime._gate.crossed(value.command_id.value) is None


async def test_runtime_records_authenticated_identity_and_exact_owner_inputs(
    tmp_path: Path,
) -> None:
    async with authorized_runtime(tmp_path / "planning.sqlite") as runtime:
        value = command()
        result = await runtime.create(
            **IDENTITY, command=value, gate_request=planning_request(value)
        )
        assert result.disposition == "COMMITTED"
        assert runtime._traced_request is not None
        trace = json.loads(runtime._traced_request.authority_trace_bytes)
        assert (trace["tenant_id"], trace["principal_id"]) == ("t", "p")
        assert [read["kind"] for read in trace["reads"]] == ["TRUST", "PLANNING", "REGISTRY"]
        trust = json.loads(base64.b64decode(trace["reads"][0]["canonical_value"]))
        assert trust["peer_credential"] == f"uid:{os.getuid()}"
        assert trace["registry_inputs"] == [["authority-row", "direct-principal-create-v1"]]
        assert trace["reads"][1]["source_id"] == "planning.committed"
        assert (
            runtime._gate is not None and runtime._gate.crossed(value.command_id.value) is not None
        )
