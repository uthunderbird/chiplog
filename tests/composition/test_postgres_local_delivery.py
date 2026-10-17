import json
from pathlib import Path

import pytest

from chiplog.adapters.driven.delivery_local_cli import LocalCliDeliveryProvider
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead,
    OriginSelection,
    ProviderRecipient,
)
from chiplog.composition.postgres_application import PostgresApplicationDriver
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.composition.postgres_local_delivery import PostgresLocalDeliveryBinding
from chiplog.composition.postgres_planning import PostgresPlanningRuntime
from chiplog.platform.postgres import PostgresServer
from tests.support.composition.postgres_completion import eligibility
from tests.support.composition.postgres_planning import verifier
from tests.support.deployment_gate import signature


async def test_actual_native_delivery_uses_private_local_inbox_and_recovers(
    postgres_server: PostgresServer,
    tmp_path: Path,
) -> None:
    principal, secret, key = "local-user", b"operator", b"stable-receipt"
    database = postgres_server.user_database("t")
    database.provision()
    await PostgresPlanningRuntime(database, "t", secret).bootstrap(
        database_instance_id="db",
        principal_id=principal,
        credential_id="c",
        session_id="s",
        token="bootstrap",
    )
    local = PostgresLocalDeliveryBinding(
        LocalCliDeliveryProvider(tmp_path / "inbox", key, principal), key
    )
    target = local.target_for_principal(principal)
    head = ExactHead(identity="cli", head="cli:1", fingerprint="a" * 64)
    origin = OriginSelection(
        ingress_binding=head,
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
    response = json.dumps(
        {
            "kind": "Complete",
            "tenant": "t",
            "run_id": "run",
            "turn_id": "run/turn/1",
            "deliveries": [{"payload": [{"kind": "NonAuthoritativeText", "text": "Local answer"}]}],
        }
    ).encode()
    runtime = PostgresExecutionRuntime(
        database,
        "t",
        secret,
        principal_id=principal,
        credential_id="c",
        session_id="s",
        model=HermeticModel((response,)),
        origin=origin,
        delivery_binding=local,
        authenticate_entitlement=verifier,
    )
    await runtime.create_execution("local-cli", "run", "Hello", BudgetPolicy())
    completed = await PostgresApplicationDriver(runtime).drive_run("local-cli", "run")
    (intent,) = runtime.delivery_intents("local-cli", "run")
    current, gate = eligibility(intent.canonical_bytes(), "delivery.first_send")
    with pytest.raises(PermissionError, match="HOLD"):
        await runtime.commit_delivery_send("local-cli", intent.delivery_id, gate)
    assert list((tmp_path / "inbox").glob("*.json")) == []
    runtime.import_entitlement(current, signature(current))
    await runtime.commit_delivery_send("local-cli", intent.delivery_id, gate)
    claim = await runtime.emit_delivery("local-cli", intent.delivery_id)
    transmission = intent.delivery_id + "/child/0"
    assert local.provider.read_payload(transmission) == intent.delivery.rendered_bytes
    reopened_binding = PostgresLocalDeliveryBinding(
        LocalCliDeliveryProvider(tmp_path / "inbox", key, principal), key
    )
    reopened = PostgresExecutionRuntime(
        database,
        "t",
        secret,
        principal_id=principal,
        credential_id="c",
        session_id="s",
        model=HermeticModel(()),
        origin=origin,
        delivery_binding=reopened_binding,
        authenticate_entitlement=verifier,
    )
    assert await PostgresApplicationDriver(reopened).drive_run("local-cli", "run") == completed
    assert await reopened.emit_delivery("local-cli", intent.delivery_id) == claim
    receipt = await reopened.reconcile_delivery("local-cli", intent.delivery_id)
    assert receipt is not None and receipt.outcome == "CONFIRMED"
    assert reopened.model.requests == []
    assert len(list((tmp_path / "inbox").glob("*.json"))) == 1
    assert reopened_binding.provider.read_payload(transmission) == intent.delivery.rendered_bytes
