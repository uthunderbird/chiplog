"""Shared builders extracted without changing their behavior."""

import hashlib
import json
import time
from pathlib import Path
from typing import Literal

from tests.support.composition.postgres_effects import IDENTITY, KEY, PEER
from tests.support.composition.postgres_execution import SECRET
from tests.support.composition.postgres_planning import verifier
from tests.support.deployment_gate import entitlement, request

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead,
    OriginSelection,
    ProviderRecipient,
)
from chiplog.capabilities.agent_loop.execution_client_transitions import ExecutionRun
from chiplog.composition.postgres_effects import PostgresEffectsBinding
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.composition.postgres_planning import PostgresPlanningRuntime
from chiplog.platform.deployment_gate import CurrentEntitlement, DeploymentGateRequest
from chiplog.platform.postgres import PostgresServer


def origin(binding: PostgresEffectsBinding) -> OriginSelection:
    target = binding.target_for_principal(IDENTITY["principal_id"])
    return OriginSelection(
        ingress_binding=ExactHead(
            identity="authenticated-ingress",
            head="ingress:1",
            fingerprint=hashlib.sha256(b"ingress").hexdigest(),
        ),
        recipient=ProviderRecipient(
            provider_id=target.provider,
            account_id=target.account,
            recipient_id=target.recipient,
            canonical_address=target.canonical_address,
            endpoint=ExactHead(
                identity=target.endpoint.subject_id,
                head=target.endpoint.head,
                fingerprint=target.endpoint.fingerprint,
            ),
            credential_binding=ExactHead(
                identity=target.credential_binding.subject_id,
                head=target.credential_binding.head,
                fingerprint=target.credential_binding.fingerprint,
            ),
        ),
    )


def completion(turn: int = 1) -> bytes:
    # Ordinary model JSON whitespace must not be mistaken for changed semantics.
    return json.dumps(
        {
            "kind": "Complete",
            "tenant": "t",
            "run_id": "run",
            "turn_id": f"run/turn/{turn}",
            "deliveries": [{"payload": [{"kind": "NonAuthoritativeText", "text": "hello"}]}],
        }
    ).encode()


def opened(
    runtime: PostgresExecutionRuntime, binding: PostgresEffectsBinding, model: HermeticModel
) -> PostgresExecutionRuntime:
    return PostgresExecutionRuntime(
        runtime.database,
        "t",
        SECRET,
        **IDENTITY,
        model=model,
        origin=origin(binding),
        effects_binding=binding,
        authenticate_entitlement=verifier,
    )


async def prepared(
    server: PostgresServer,
    directory: Path,
    scenario: Literal["CONFIRM", "LOST_RESPONSE_AFTER_EFFECT"] = "CONFIRM",
    raw: bytes | None = None,
    late: bool = False,
) -> tuple[PostgresExecutionRuntime, PostgresEffectsBinding, ExecutionRun]:
    database = server.user_database("t")
    database.provision()
    await PostgresPlanningRuntime(database, "t", SECRET).bootstrap(
        database_instance_id="db", token="token", **IDENTITY
    )
    binding = PostgresEffectsBinding(
        HermeticEffectsProvider(
            receipt_key=KEY,
            scenarios=(scenario,),
            late_receipt_scenarios=(("RIVAL",),) if late else (),
            journal_path=directory / "transfers.jsonl",
            observation_journal_path=directory / "observations.jsonl",
        ),
        KEY,
    )
    runtime = PostgresExecutionRuntime(
        database,
        "t",
        SECRET,
        **IDENTITY,
        model=HermeticModel((raw or completion(),)),
        origin=origin(binding),
        effects_binding=binding,
        authenticate_entitlement=verifier,
    )
    created = await runtime.create_execution(PEER, "run", "hello", BudgetPolicy())
    started = await runtime.begin_execution(PEER, "run", created.head)
    captured = await runtime.capture_execution(PEER, "run", started.head)
    sealed = await runtime.seal_execution(PEER, "run", captured.head)
    return runtime, binding, sealed


def eligibility(
    payload: bytes, surface: str, operation: str = "delivery-send"
) -> tuple[CurrentEntitlement, DeploymentGateRequest]:
    now = time.time_ns()
    current = entitlement()
    current = current.model_copy(
        update={
            "entitlement_head": surface + ":entitlement",
            "freshness_leases": (("evidence", now + 10**12),),
            "bounds": current.bounds.model_copy(
                update={"capability_id": surface, "starts_ns": now - 1, "expires_ns": now + 10**12}
            ),
        }
    )
    value = request(current, operation).model_copy(
        update={"surface_id": surface, "payload_digest": hashlib.sha256(payload).hexdigest()}
    )
    return current, value
