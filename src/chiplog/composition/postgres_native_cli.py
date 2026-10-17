"""One authenticated native CLI invocation, using the existing execution lifecycle."""

import hashlib
import hmac
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from chiplog.adapters.driven.delivery_local_cli import LocalCliDeliveryProvider
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopHold
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead,
    OriginSelection,
    ProviderRecipient,
)
from chiplog.composition.postgres_application import PostgresApplicationDriver
from chiplog.composition.postgres_execution import (
    ExecutionModelPort,
    open_postgres_execution_runtime,
)
from chiplog.composition.postgres_ingress import (
    PostgresIngressService,
    RegisteredIngressSource,
    subject,
)
from chiplog.composition.postgres_local_delivery import PostgresLocalDeliveryBinding
from chiplog.platform.deployment_gate import CurrentEntitlement, DeploymentGateRequest
from chiplog.platform.postgres_gate import PostgresDeploymentGate, canonical


@dataclass(frozen=True)
class NativeChatConfiguration:
    tenant: str
    principal: str
    credential: str
    session: str
    request_id: str
    message: str
    state_directory: Path
    entitlement: Path
    entitlement_proof: Path
    verification_key: Path


def source_identifier(principal: str) -> str:
    return f"native-cli:uid:{os.getuid()}:{principal}"


async def run_native_chat(
    config: NativeChatConfiguration,
    *,
    operator_secret: bytes,
    model: ExecutionModelPort,
    policy: BudgetPolicy,
) -> bytes:
    key = Ed25519PublicKey.from_public_bytes(config.verification_key.read_bytes())

    def verify(payload: bytes, proof: bytes) -> bool:
        try:
            key.verify(proof, payload)
        except Exception:
            return False
        return True

    grant = CurrentEntitlement.model_validate_json(config.entitlement.read_bytes())
    encoded = canonical(grant.model_dump(mode="json"))
    proof = config.entitlement_proof.read_bytes()
    if not verify(encoded, proof):
        raise PermissionError("independent entitlement proof rejected")
    scope = canonical([config.tenant, config.principal, os.getuid()])
    receipt_key = hmac.digest(operator_secret, b"native-cli.receipt.v1\x00" + scope, "sha256")
    issuer_key = hmac.digest(operator_secret, b"native-cli.ingress.v1\x00" + scope, "sha256")
    provider = LocalCliDeliveryProvider(
        config.state_directory / "inbox", receipt_key, config.principal
    )
    delivery = PostgresLocalDeliveryBinding(provider, receipt_key)
    target = delivery.target_for_principal(config.principal)
    source_id = source_identifier(config.principal)
    fingerprint = hashlib.sha256(
        canonical([source_id, config.tenant, provider.endpoint_head])
    ).hexdigest()
    ingress_head = ExactHead(
        identity=source_id, head="native-cli:" + fingerprint, fingerprint=fingerprint
    )
    origin = OriginSelection(
        ingress_binding=ingress_head,
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
    async with open_postgres_execution_runtime(
        tenant_id=config.tenant,
        principal_id=config.principal,
        credential_id=config.credential,
        session_id=config.session,
        operator_secret=operator_secret,
        model=model,
        origin=origin,
        delivery_binding=delivery,
        authenticate_entitlement=verify,
    ) as runtime:
        peer = runtime.peer
        # Import only an initial grant. Replaying must never roll current authority back.
        with runtime._transaction() as (connection, trust):
            runtime._actor(connection, trust, peer)
            selected = connection.execute("SELECT canonical_bytes FROM gate_current").fetchone()
            if selected is None:
                PostgresDeploymentGate(connection, config.tenant, verify).import_current(
                    grant, proof, None
                )
            elif bytes(selected[0]) != encoded:
                raise PermissionError("HOLD: supplied entitlement differs from current authority")
        source = RegisteredIngressSource(
            tenant_id=config.tenant,
            channel="CLI",
            source_id=source_id,
            principal_id=config.principal,
            issuer_id="native-cli.os-uid.v1",
            binding=ingress_head,
            origin=origin,
            transport_subject=subject("uid", os.getuid()),
        )
        ingress = PostgresIngressService(runtime, (source,), issuer_key=issuer_key)
        submission = ingress.issuer(source_id)(
            config.request_id, config.message.encode(), policy, subject("uid", os.getuid())
        )
        receipt = await ingress.submit(peer, submission, policy)
        if ingress.lookup(peer, submission.identity) != receipt:
            raise PermissionError("native CLI has no selected ingress custody")
        await PostgresApplicationDriver(runtime, ingress).drive(peer, receipt.inbox_id)
        result = []
        for intent in runtime.delivery_intents(peer, receipt.run_id):
            request = DeploymentGateRequest(
                operation_id=intent.delivery_id + "/local-send",
                surface_id="delivery.first_send",
                payload_digest=hashlib.sha256(intent.canonical_bytes()).hexdigest(),
                mode=grant.mode,
                generation=grant.generation,
                readiness_head=grant.readiness_head,
                entitlement_head=grant.entitlement_head,
                evidence_cursors=grant.evidence_cursors,
                freshness_leases=grant.freshness_leases,
                bounds=grant.bounds,
            )
            await runtime.commit_delivery_send(peer, intent.delivery_id, request)
            await runtime.emit_delivery(peer, intent.delivery_id)
            observed = await runtime.reconcile_delivery(peer, intent.delivery_id)
            if observed is None or observed.outcome != "CONFIRMED":
                raise LoopHold("local delivery remains unresolved; no repeated SEND")
            payload = provider.read_payload(intent.delivery_id + "/child/0")
            if payload != intent.delivery.rendered_bytes:
                raise LoopHold("local inbox differs from accepted delivery")
            result.append(payload)
        return b"\n".join(result)
