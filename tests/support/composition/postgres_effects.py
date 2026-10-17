"""Shared builders extracted without changing their behavior."""

import hashlib
import json
import time
from pathlib import Path
from typing import Literal

from tests.support.composition.postgres_execution import SECRET, origin
from tests.support.composition.postgres_planning import verifier
from tests.support.deployment_gate import entitlement, request

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.capabilities.effects.client_effect_contracts import NativeEffectIntentV1
from chiplog.composition.postgres_effects import (
    PostgresEffectsBinding,
    adopt_effect,
    preview_effect,
)
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.composition.postgres_planning import PostgresPlanningRuntime
from chiplog.platform.deployment_gate import CurrentEntitlement, DeploymentGateRequest
from chiplog.platform.postgres import PostgresServer

PEER = "local-cli"


IDENTITY = dict(principal_id="hermetic-principal", credential_id="c", session_id="s")


KEY = b"registered-pg-provider-receipt-key"


async def setup_effect(
    server: PostgresServer,
    directory: Path,
    scenario: Literal["CONFIRM", "LOST_RESPONSE_AFTER_EFFECT"],
) -> tuple[
    PostgresExecutionRuntime,
    PostgresEffectsBinding,
    str,
    NativeEffectIntentV1,
    CurrentEntitlement,
    DeploymentGateRequest,
]:
    database = server.user_database("t")
    database.provision()
    await PostgresPlanningRuntime(database, "t", SECRET).bootstrap(
        database_instance_id="db", token="token", **IDENTITY
    )
    binding = PostgresEffectsBinding(
        HermeticEffectsProvider(
            receipt_key=KEY,
            scenarios=(scenario,),
            late_receipt_scenarios=(("COMPATIBLE", "RIVAL"),),
            journal_path=directory / "provider.jsonl",
            observation_journal_path=directory / "observations.jsonl",
        ),
        KEY,
    )
    response = json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": "effect",
                    "tool": "request_self_effect",
                    "arguments": {"payload": "aGVsbG8=", "bundle_members": ["m"]},
                }
            ],
        }
    ).encode()
    runtime = PostgresExecutionRuntime(
        database,
        "t",
        SECRET,
        **IDENTITY,
        model=HermeticModel((response,)),
        origin=origin(),
        effects_binding=binding,
        authenticate_entitlement=verifier,
    )
    created = await runtime.create_execution(PEER, "run", "Send hello", BudgetPolicy())
    started = await runtime.begin_execution(PEER, "run", created.head)
    captured = await runtime.capture_execution(PEER, "run", started.head)
    await runtime.seal_execution(PEER, "run", captured.head)
    call_id = runtime.initialized_calls()[0].original_call_id
    preview = await preview_effect(runtime, binding, PEER, call_id, time.time_ns() + 10**12)
    intent = await adopt_effect(runtime, binding, PEER, preview.head, "explicit-adoption")
    now = time.time_ns()
    current = entitlement()
    current = current.model_copy(
        update={
            "freshness_leases": (("evidence", now + 10**12),),
            "bounds": current.bounds.model_copy(
                update={
                    "capability_id": "effects.first_send",
                    "starts_ns": now - 1,
                    "expires_ns": now + 10**12,
                }
            ),
        }
    )
    gate_request = request(current).model_copy(
        update={
            "surface_id": "effects.first_send",
            "payload_digest": hashlib.sha256(intent.canonical_bytes()).hexdigest(),
        }
    )
    return runtime, binding, call_id, intent, current, gate_request
